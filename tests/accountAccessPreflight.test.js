import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import {
  accountAccessErrorKind,
  runAccountAccessPreflight,
} from '../src/accountAccessPreflight.js';
import { BalancedReadScheduler } from '../src/balancedReadScheduler.js';
import { summarizeExecutionGroup } from '../src/executionGroupPersistence.js';
import { buildStandaloneTokenAccountImport } from '../src/standaloneAuth.js';

function apiError(status, code, message = `status ${status}`) {
  const error = new Error(message);
  error.status = status;
  error.code = code;
  return error;
}

function noWait() {
  const sleeps = [];
  return { sleeps, sleep: async (ms) => { sleeps.push(ms); } };
}

test('account access retries a timeout and ECONNRESET through the bounded backoff path', async () => {
  for (const firstError of [
    Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' }),
    Object.assign(new Error('fetch failed'), { cause: { code: 'ECONNRESET' } }),
  ]) {
    const waits = noWait();
    let calls = 0;
    const profile = await runAccountAccessPreflight({
      accountId: 'offline-account',
      retryDelaysMs: [7, 11],
      sleep: waits.sleep,
      readProfile: async () => {
        calls += 1;
        if (calls === 1) throw firstError;
        return { nickname: 'offline-profile' };
      },
    });
    assert.equal(profile.nickname, 'offline-profile');
    assert.equal(calls, 2);
    assert.deepEqual(waits.sleeps, [7]);
  }
});

test('account access retries rate-limit and service responses, then succeeds', async () => {
  for (const firstError of [
    Object.assign(apiError(429, 'RATE_LIMIT'), { retryAfterMs: 13 }),
    apiError(503, 'SERVICE_UNAVAILABLE'),
  ]) {
    const waits = noWait();
    let calls = 0;
    await runAccountAccessPreflight({
      accountId: 'offline-account',
      retryDelaysMs: [7],
      sleep: waits.sleep,
      readProfile: async () => {
        calls += 1;
        if (calls === 1) throw firstError;
        return { nickname: 'transient-profile' };
      },
    });
    assert.equal(calls, 2);
    assert.deepEqual(waits.sleeps, [firstError.status === 429 ? 13 : 7]);
  }
});

test('account access stops after finite transient attempts and exposes only safe diagnostics', async () => {
  const waits = noWait();
  let calls = 0;
  let writeCalls = 0;
  const secret = 'do-not-persist-this-token';
  await assert.rejects(
    runAccountAccessPreflight({
      accountId: 'offline-account',
      maxAttempts: 3,
      retryDelaysMs: [3, 5],
      sleep: waits.sleep,
      readProfile: async () => {
        calls += 1;
        throw Object.assign(new Error(`fetch failed ${secret}`), {
          code: 'ECONNRESET',
          cause: { code: 'SOCKET_CLOSED', secret },
          body: { access_token: secret, raw: secret },
        });
      },
    }),
    (error) => {
      assert.equal(calls, 3);
      assert.deepEqual(waits.sleeps, [3, 5]);
      assert.equal(error.account_access_diagnostic.operation, 'account_access');
      assert.equal(error.account_access_diagnostic.endpoint_family, 'users_me');
      assert.equal(error.account_access_diagnostic.attempt_count, 3);
      assert.equal(error.account_access_diagnostic.error_kind, 'network');
      assert.equal(error.account_access_diagnostic.code, 'ECONNRESET');
      assert.equal(error.account_access_diagnostic.cause_code, 'SOCKET_CLOSED');
      assert.doesNotMatch(JSON.stringify(error.account_access_diagnostic), /do-not-persist-this-token/);
      assert.equal(writeCalls, 0);
      return true;
    },
  );
  void writeCalls;
});

test('401 refreshes exactly once and revalidates with the refreshed token', async () => {
  const phases = [];
  let refreshes = 0;
  const profile = await runAccountAccessPreflight({
    accountId: 'offline-account',
    readProfile: async ({ phase }) => {
      phases.push(phase);
      if (phase === 'initial') throw apiError(401, 'INVALID_TOKEN');
      return { nickname: 'refreshed-profile' };
    },
    refreshToken: async () => { refreshes += 1; },
  });
  assert.equal(profile.nickname, 'refreshed-profile');
  assert.deepEqual(phases, ['initial', 'after_refresh']);
  assert.equal(refreshes, 1);
});

test('401 refresh failure and a second 401 never trigger a second refresh', async () => {
  let refreshes = 0;
  await assert.rejects(
    runAccountAccessPreflight({
      accountId: 'offline-account',
      readProfile: async ({ phase }) => {
        throw apiError(401, phase === 'initial' ? 'INVALID_TOKEN' : 'INVALID_TOKEN_AGAIN');
      },
      refreshToken: async () => { refreshes += 1; },
    }),
    (error) => {
      assert.equal(refreshes, 1);
      assert.equal(error.account_access_diagnostic.attempt_count, 2);
      assert.equal(error.account_access_diagnostic.refresh_count, 1);
      assert.equal(error.account_access_diagnostic.error_kind, 'unauthorized');
      return true;
    },
  );
});

test('403 and business errors do not receive blind retries', async () => {
  for (const error of [apiError(403, 'FORBIDDEN'), apiError(422, 'BUSINESS_RULE')]) {
    const waits = noWait();
    let calls = 0;
    await assert.rejects(
      runAccountAccessPreflight({
        accountId: 'offline-account',
        sleep: waits.sleep,
        refreshToken: async () => { throw new Error('must not refresh'); },
        readProfile: async () => {
          calls += 1;
          throw error;
        },
      }),
      (failure) => {
        assert.equal(calls, 1);
        assert.deepEqual(waits.sleeps, []);
        assert.equal(failure.account_access_diagnostic.attempt_count, 1);
        return true;
      },
    );
  }
});

test('preflight reads share the scheduler with background refresh and never run concurrently', async () => {
  const scheduler = new BalancedReadScheduler({
    initialLimit: 1,
    maxLimit: 1,
    perAccountLimit: 1,
    detailLimit: 1,
    activityLimit: 1,
    detailPerAccountLimit: 1,
    activityPerAccountLimit: 1,
    fallbackPerAccount: 1,
  });
  let active = 0;
  let peak = 0;
  let releaseBackground;
  const background = scheduler.schedule({ accountId: 'A', key: 'background', kind: 'activity' }, async () => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => { releaseBackground = resolve; });
    active -= 1;
    return true;
  });
  let preflightCalls = 0;
  const preflight = runAccountAccessPreflight({
    accountId: 'A',
    readProfile: ({ phase }) => scheduler.schedule({
      accountId: 'A',
      key: `preflight|${phase}`,
      kind: 'account_access',
    }, async () => {
      preflightCalls += 1;
      active += 1;
      peak = Math.max(peak, active);
      active -= 1;
      return { nickname: 'scheduled-profile' };
    }),
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(preflightCalls, 0);
  releaseBackground();
  await Promise.all([background, preflight]);
  assert.equal(preflightCalls, 1);
  assert.equal(peak, 1);
});

test('preflight disables the scheduler retry layer so its attempt count matches wire attempts', async () => {
  const scheduler = new BalancedReadScheduler({
    initialLimit: 1,
    maxLimit: 1,
    perAccountLimit: 1,
    detailLimit: 1,
    activityLimit: 1,
    detailPerAccountLimit: 1,
    activityPerAccountLimit: 1,
    fallbackPerAccount: 1,
  });
  let wireCalls = 0;
  const waits = noWait();
  const profile = await runAccountAccessPreflight({
    accountId: 'A',
    retryDelaysMs: [4],
    sleep: waits.sleep,
    readProfile: ({ phase }) => scheduler.schedule({
      accountId: 'A',
      key: `preflight-wire|${phase}`,
      kind: 'account_access',
      retry: false,
    }, async () => {
      wireCalls += 1;
      if (wireCalls === 1) throw apiError(503, 'SERVICE_UNAVAILABLE');
      return { nickname: 'wire-profile' };
    }),
  });
  assert.equal(profile.nickname, 'wire-profile');
  assert.equal(wireCalls, 2);
  assert.deepEqual(waits.sleeps, [4]);
});

test('account preflight failure is visible in group accounting and cannot look like product success', () => {
  const summary = summarizeExecutionGroup({
    action: 'cancel',
    children: [{
      job_id: 'job-account-failure',
      account_id: 'offline-account',
      status: 'failed',
      result: {
        ok: false,
        accounting_complete: false,
        account_access_diagnostic: { operation: 'account_access', endpoint_family: 'users_me' },
        execution: {
          relation_count: 0,
          success: 0,
          failed: 0,
          skipped: 0,
          account_access_failure_count: 1,
          accounting_complete: false,
          terminal_counts: {
            relation_count: 0,
            success: 0,
            failed: 0,
            skipped: 0,
            platform_pending: 0,
            unresolved: 1,
            classified_count: 0,
            is_closed: false,
          },
        },
      },
    }],
  });
  assert.equal(summary.success, 0);
  assert.equal(summary.request_success_count, 0);
  assert.equal(summary.account_access_failure_count, 1);
  assert.equal(summary.accounting_complete, false);
  assert.ok(summary.incomplete_reasons.includes('account_access_failed'));
});

test('standalone account import requires complete credentials and preserves account identity', () => {
  const migrated = buildStandaloneTokenAccountImport({
    token: {
      user_id: '2651442567',
      access_token: 'fixture-access',
      refresh_token: 'fixture-refresh',
      token_type: 'Bearer',
      expires_at: '2026-08-26T07:10:12+08:00',
      scope: 'read write',
      redirect_uri: 'https://xingtupro1020.com/oauth/callback/',
    },
    config: {
      client_id: 'fixture-client',
      client_secret: 'fixture-secret',
    },
    profile: { display_name: '湖北', site_id: 'CBT' },
  });
  assert.equal(migrated.token.user_id, '2651442567');
  assert.equal(migrated.profile.nickname, '湖北');
  assert.equal(migrated.profile.site_id, 'CBT');
  assert.equal(migrated.clientId, 'fixture-client');
  assert.equal(migrated.clientSecret, 'fixture-secret');
  assert.throws(
    () => buildStandaloneTokenAccountImport({
      token: { user_id: '2651442567', access_token: 'fixture-access' },
      config: { client_id: 'fixture-client', client_secret: 'fixture-secret' },
    }),
    /refresh_token/,
  );
});

test('server migrates standalone auth once and uses the unified Node refresh path', () => {
  const source = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  assert.match(source, /getDb\(\);\s*importStandaloneAccountToEncryptedStore\(\);/);
  assert.match(source, /buildStandaloneTokenAccountImport/);
  assert.match(source, /saveTokenAccount\(input\)/);
  assert.match(source, /async function ensureUsableAccount\(accountId\) \{\s*ensureAccountStored\(accountId\);\s*return ensureFreshAccount\(accountId\);/);
  assert.match(source, /async function refreshAccountForWriteRetry\(accountId\) \{\s*ensureAccountStored\(accountId\);\s*return ensureFreshAccount\(accountId, \{ force: true \}\);/);
  assert.match(source, /const client = new MercadoLibreClient\(\);\s*const token = await client\.refreshToken/);
  const automaticAuth = source.slice(
    source.indexOf('async function refreshAccountForWriteRetry'),
    source.indexOf('function serveStatic'),
  );
  assert.doesNotMatch(automaticAuth, /refreshStandaloneToken|ensureStandaloneUsable|refresh_now\.ps1/);
  assert.equal(accountAccessErrorKind(apiError(403, 'FORBIDDEN')), 'forbidden');
});
