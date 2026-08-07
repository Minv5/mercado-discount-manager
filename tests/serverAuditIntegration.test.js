import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { MercadoLibreClient } from '../src/mlClient.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SERVER_PATH = path.join(ROOT, 'src', 'server.js');
const SERVER_SOURCE = fs.readFileSync(SERVER_PATH, 'utf8');

function functionSource(name, nextName) {
  const start = SERVER_SOURCE.indexOf(`function ${name}`);
  assert.notEqual(start, -1, `missing function ${name}`);
  const end = nextName ? SERVER_SOURCE.indexOf(`function ${nextName}`, start + 1) : SERVER_SOURCE.length;
  assert.notEqual(end, -1, `missing next function ${nextName}`);
  return SERVER_SOURCE.slice(start, end);
}

function spawnServer({ dataDir, port }) {
  return spawn(process.execPath, ['src/server.js'], {
    cwd: ROOT,
    env: {
      ...process.env,
      MDM_DATA_DIR: dataDir,
      MDM_DB_PATH: path.join(dataDir, 'test.sqlite'),
      MDM_KEY_PATH: path.join(dataDir, 'local.key'),
      MDM_PORT: String(port),
      MDM_ACTIVITY_CALLBACK_ENABLED: '0',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
}

function runIsolated(source) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-server-audit-isolated-'));
  try {
    return JSON.parse(execFileSync(
      process.execPath,
      ['--input-type=module', '--eval', source],
      {
        cwd: ROOT,
        env: {
          ...process.env,
          MDM_DATA_DIR: dataDir,
          MDM_DB_PATH: path.join(dataDir, 'test.sqlite'),
        },
        encoding: 'utf8',
        timeout: 30_000,
      },
    ));
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

async function waitForHealth(baseUrl) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) return response.json();
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error('isolated server did not become ready');
}

async function stopChild(child) {
  if (!child || child.exitCode !== null) return;
  child.kill();
  await Promise.race([
    new Promise((resolve) => child.once('exit', resolve)),
    new Promise((resolve) => setTimeout(resolve, 5_000)),
  ]);
  if (child.exitCode === null) child.kill('SIGKILL');
}

test('port conflict happens before persisted group recovery or mutation', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-server-start-order-'));
  const port = 33000 + Math.floor(Math.random() * 1000);
  const first = spawnServer({ dataDir, port });
  let second = null;
  try {
    await waitForHealth(`http://127.0.0.1:${port}`);
    const groupDir = path.join(dataDir, 'execution-group-states');
    fs.mkdirSync(groupDir, { recursive: true });
    const groupPath = path.join(groupDir, 'must-not-recover.json');
    const original = JSON.stringify({
      id: 'must-not-recover',
      client_submission_id: 'must-not-recover-submission',
      status: 'running',
      process_pid: 999999,
      children: [],
      created_at: '2026-07-26T00:00:00.000Z',
      updated_at: '2026-07-26T00:00:00.000Z',
    });
    fs.writeFileSync(groupPath, original, 'utf8');

    second = spawnServer({ dataDir, port });
    await Promise.race([
      new Promise((resolve) => second.once('exit', resolve)),
      new Promise((_, reject) => setTimeout(() => reject(new Error('second server did not exit')), 5_000)),
    ]);
    assert.notEqual(second.exitCode, 0);
    assert.equal(fs.readFileSync(groupPath, 'utf8'), original);
  } finally {
    await stopChild(second);
    await stopChild(first);
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('HTTP JSON body limit returns 413 without echoing payload', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-server-body-limit-'));
  const port = 34000 + Math.floor(Math.random() * 1000);
  const child = spawnServer({ dataDir, port });
  try {
    await waitForHealth(`http://127.0.0.1:${port}`);
    const secretMarker = `secret-${'x'.repeat(1_100_000)}`;
    const response = await fetch(`http://127.0.0.1:${port}/api/settings`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ marker: secretMarker }),
    });
    const text = await response.text();
    assert.equal(response.status, 413);
    assert.doesNotMatch(text, /secret-/);
    const payload = JSON.parse(text);
    assert.equal(payload.details.code, 'REQUEST_BODY_TOO_LARGE');
  } finally {
    await stopChild(child);
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('server startup, webhook, OAuth and safe HTTP helpers are integrated', () => {
  assert.match(SERVER_SOURCE, /acquireProcessInstanceLock/);
  assert.match(SERVER_SOURCE, /createExecutionGroupPersistence\(\{[\s\S]*loadOnCreate:\s*false/);
  assert.match(SERVER_SOURCE, /server\.listen\([\s\S]*initializeServerState/);
  assert.match(SERVER_SOURCE, /claimActivityCallbackEvent/);
  assert.match(SERVER_SOURCE, /finalizeActivityCallbackEvent/);
  assert.match(SERVER_SOURCE, /createResourceClient:[\s\S]*readScheduler:\s*sharedReadScheduler/);
  assert.match(SERVER_SOURCE, /claimOAuthState/);
  assert.match(SERVER_SOURCE, /releaseOAuthStateClaim/);
  assert.match(SERVER_SOURCE, /readJsonBody/);
  assert.doesNotMatch(SERVER_SOURCE, /async function readJson\(/);
  assert.match(SERVER_SOURCE, /safeErrorDetails/);
  assert.doesNotMatch(SERVER_SOURCE, /function safeDetails\(/);
});

test('OAuth completion uses one atomic persistence operation', async () => {
  const previous = process.env.MDM_SERVER_LIBRARY_MODE;
  process.env.MDM_SERVER_LIBRARY_MODE = '1';
  try {
    const { commitOAuthAuthorizationState } = await import(`../src/server.js?oauth-atomic=${Date.now()}`);
    const input = {
      targetAccountId: 'A-1',
      state: 'state-1',
      claimToken: 'claim-1',
      token: { user_id: 'A-1' },
      profile: { id: 'A-1' },
    };
    const calls = [];
    const result = commitOAuthAuthorizationState(input, {
      commit: (value) => {
        calls.push(value);
        return { status: 'consumed', account: { account_id: 'A-1' } };
      },
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0], input);
    assert.deepEqual(result, {
      status: 'consumed',
      account: { account_id: 'A-1' },
    });
    assert.throws(
      () => commitOAuthAuthorizationState(input, {
        commit: () => {
          throw new Error('atomic transaction rolled back');
        },
      }),
      /atomic transaction rolled back/,
    );
    assert.throws(
      () => commitOAuthAuthorizationState({
        ...input,
        targetAccountId: 'different-account',
      }, {
        commit: () => {
          throw new Error('should not reach commit');
        },
      }),
      (error) => error?.code === 'ACCOUNT_IDENTITY_MISMATCH' && error?.status === 422,
    );
  } finally {
    if (previous === undefined) delete process.env.MDM_SERVER_LIBRARY_MODE;
    else process.env.MDM_SERVER_LIBRARY_MODE = previous;
  }
});

test('OAuth external stages renew the same claim before and after each request', async () => {
  const previous = process.env.MDM_SERVER_LIBRARY_MODE;
  process.env.MDM_SERVER_LIBRARY_MODE = '1';
  try {
    const { runOAuthExternalStagesWithLease } = await import(`../src/server.js?oauth-lease=${Date.now()}`);
    const sequence = [];
    const result = await runOAuthExternalStagesWithLease({
      state: 'state-1',
      claimToken: 'claim-1',
      code: 'code-1',
      record: {
        clientId: 'client',
        clientSecret: 'secret',
        redirectUri: 'https://example.test/callback',
        codeVerifier: 'verifier',
      },
    }, {
      renew: ({ state, claimToken }) => {
        sequence.push(`renew:${state}:${claimToken}`);
        return { status: 'renewed', claim_token: claimToken };
      },
      exchangeCode: async () => {
        sequence.push('exchange');
        return { access_token: 'token', user_id: 'A-1' };
      },
      getProfile: async () => {
        sequence.push('profile');
        return { id: 'A-1' };
      },
    });
    assert.deepEqual(sequence, [
      'renew:state-1:claim-1',
      'exchange',
      'renew:state-1:claim-1',
      'renew:state-1:claim-1',
      'profile',
      'renew:state-1:claim-1',
    ]);
    assert.equal(result.token.user_id, 'A-1');
    assert.equal(result.profile.id, 'A-1');
    await assert.rejects(
      runOAuthExternalStagesWithLease({
        state: 'state-1',
        claimToken: 'stale-claim',
        code: 'code-1',
        record: {},
      }, {
        renew: () => ({ status: 'claim_mismatch' }),
        exchangeCode: async () => {
          throw new Error('must not exchange');
        },
        getProfile: async () => {
          throw new Error('must not read profile');
        },
      }),
      (error) => error?.code === 'OAUTH_STATE_CLAIM_MISMATCH',
    );
  } finally {
    if (previous === undefined) delete process.env.MDM_SERVER_LIBRARY_MODE;
    else process.env.MDM_SERVER_LIBRARY_MODE = previous;
  }
});

test('OAuth release failure cannot replace the original callback error or leak details', async () => {
  const previous = process.env.MDM_SERVER_LIBRARY_MODE;
  process.env.MDM_SERVER_LIBRARY_MODE = '1';
  try {
    const { releaseOAuthClaimAfterFailure } = await import(`../src/server.js?oauth-release=${Date.now()}`);
    const original = Object.assign(new Error('profile request failed'), {
      code: 'PROFILE_FAILED',
    });
    let audit = null;
    assert.throws(
      () => releaseOAuthClaimAfterFailure({
        state: 'state-secret',
        claimToken: 'claim-secret',
        originalError: original,
      }, {
        release: () => {
          const error = new Error('release failed Bearer secret-token');
          error.code = 'SQLITE_BUSY';
          error.body = { secret: 'must-not-log' };
          throw error;
        },
        log: (entry) => {
          audit = entry;
        },
      }),
      (error) => error === original,
    );
    assert.deepEqual(audit, {
      event: 'oauth_state_release_failed',
      error_code: 'SQLITE_BUSY',
      error_name: 'Error',
    });
    assert.doesNotMatch(JSON.stringify(audit), /state-secret|claim-secret|Bearer|must-not-log/);
  } finally {
    if (previous === undefined) delete process.env.MDM_SERVER_LIBRARY_MODE;
    else process.env.MDM_SERVER_LIBRARY_MODE = previous;
  }
});

test('final reads are complete and unverifiable results remain paused', () => {
  const recheck = functionSource('recheckAndCancelRemainingStarted', 'readAppliedWriteRows');
  const applied = functionSource('readAppliedWriteRows', 'waitForAppliedWriteRows');
  const recovery = functionSource('recoverPendingVerificationRecords', 'finalizeExecutionJob');
  assert.doesNotMatch(recheck, /maxItems:\s*5000/);
  assert.match(recheck, /maxItems:\s*'all'/);
  assert.match(recheck, /isFullFetch|is_full_fetch/);
  assert.doesNotMatch(applied, /maxItems:\s*5000/);
  assert.match(applied, /maxItems:\s*'all'/);
  assert.match(applied, /unverifiable/);
  assert.doesNotMatch(recovery, /exhausted\s*\?\s*'failed'/);
  assert.match(recovery, /verification_polling_exhausted/);
  assert.match(recovery, /unresolved/);

  const smartReadback = functionSource('fetchAndSyncSmartStarted', 'safeSmartDetailSummary');
  assert.match(smartReadback, /maxItems:\s*'all'/);
  assert.match(smartReadback, /isCompletePromotionRead\(started\)/);
  assert.match(smartReadback, /target_item_remaining:\s*null/);
  assert.doesNotMatch(smartReadback, /maxItems:\s*5000/);
});

test('complete promotion readback accepts more than 5000 relations', async () => {
  const total = 5001;
  const client = new MercadoLibreClient({ accessToken: 'fake-read-only-token' });
  client.getPromotionItems = async ({ offset = 0, limit = 50 }) => ({
    paging: { total, limit, offset },
    results: Array.from(
      { length: Math.max(0, Math.min(limit, total - offset)) },
      (_, index) => ({ id: `ITEM-${offset + index + 1}` }),
    ),
  });

  const result = await client.fetchAllPromotionItems({
    promotionId: 'P-5001',
    promotionType: 'DEAL',
    status: 'started',
    maxItems: 'all',
  });

  assert.equal(result.results.length, total);
  assert.equal(result.isFullFetch, true);
  assert.equal(result.sampleOnly, false);
});

test('job and group completion require closed accounting', () => {
  const finalize = functionSource('finalizeExecutionJob', 'displayProgressTotal');
  const group = functionSource('runExecutionGroup', 'failExecutionGroup');
  const failure = functionSource('failExecutionJob', 'runExecutionJob');
  assert.match(finalize, /terminal_counts/);
  assert.match(finalize, /accounting_complete/);
  assert.match(finalize, /activity_failure_count/);
  assert.match(finalize, /unresolved/);
  assert.match(group, /accounting_complete/);
  assert.doesNotMatch(group, /\.filter\(\(job\)\s*=>\s*job/);
  assert.match(failure, /job\.result\s*=\s*\{/);
  assert.match(failure, /accounting_complete:\s*false/);
});

test('cache identity, final live routes, calibration and daily deltas are wired', () => {
  assert.doesNotMatch(SERVER_SOURCE, /getActivityCacheState\(account\.account_id,\s*campaign\.site_id/);
  assert.match(SERVER_SOURCE, /getActivityCacheState\(\{[\s\S]*childUserId:/);
  assert.match(SERVER_SOURCE, /required_live_route_keys/);
  assert.match(SERVER_SOURCE, /prepared_route_snapshot/);
  assert.match(SERVER_SOURCE, /platform_read_required/);
  assert.match(SERVER_SOURCE, /identity_summary/);
  assert.doesNotMatch(SERVER_SOURCE, /candidate_identity_summary/);

  const calibration = functionSource('runLowFrequencyActivityCalibration', 'resumePersistedExecutionSubmissions');
  assert.match(calibration, /createBalancedReadScheduler|sharedReadScheduler/);
  assert.match(SERVER_SOURCE, /summarizeDailyItemIdentityDeltas/);
  assert.match(SERVER_SOURCE, /daily_item_delta/);
});

test('daily calibration aborts an in-flight scan and never saves after execution becomes active', async () => {
  const previous = process.env.MDM_SERVER_LIBRARY_MODE;
  process.env.MDM_SERVER_LIBRARY_MODE = '1';
  try {
    const {
      createActiveExecutionAbortGuard,
      scanDailyItemIdentityRoute,
    } = await import(`../src/server.js?daily-active-gate=${Date.now()}`);
    let active = false;
    let saveCount = 0;
    const guard = createActiveExecutionAbortGuard({
      isActive: () => active,
      intervalMs: 5,
    });
    const scanStarted = new Promise((resolve) => {
      guard.scanStarted = resolve;
    });
    const resultPromise = scanDailyItemIdentityRoute({
      route: { account_id: 'A', child_user_id: 'CH-1', site_id: 'MLM' },
      businessDate: '2026-07-26',
      guard,
      scan: ({ signal }) => new Promise((resolve, reject) => {
        guard.scanStarted();
        signal.addEventListener('abort', () => reject(
          Object.assign(new Error('aborted by active execution'), { name: 'AbortError' }),
        ), { once: true });
      }),
      saveSnapshot: () => {
        saveCount += 1;
        return { saved: true };
      },
    });
    await scanStarted;
    active = true;
    const result = await resultPromise;
    guard.close();
    assert.equal(guard.signal.aborted, true);
    assert.equal(result.skipped, true);
    assert.equal(result.reason, 'active_execution');
    assert.equal(saveCount, 0);
  } finally {
    if (previous === undefined) delete process.env.MDM_SERVER_LIBRARY_MODE;
    else process.env.MDM_SERVER_LIBRARY_MODE = previous;
  }
});

test('daily calibration checks active state again after scan and before snapshot save', async () => {
  const previous = process.env.MDM_SERVER_LIBRARY_MODE;
  process.env.MDM_SERVER_LIBRARY_MODE = '1';
  try {
    const { scanDailyItemIdentityRoute } = await import(`../src/server.js?daily-save-gate=${Date.now()}`);
    let checks = 0;
    let saveCount = 0;
    const guard = {
      signal: new AbortController().signal,
      check: () => {
        checks += 1;
        return checks >= 2;
      },
    };
    const result = await scanDailyItemIdentityRoute({
      route: { account_id: 'A', child_user_id: 'CH-1', site_id: 'MLM' },
      businessDate: '2026-07-26',
      guard,
      scan: async () => ({ ids: ['ITEM-1'], isFullFetch: true }),
      saveSnapshot: () => {
        saveCount += 1;
        return { saved: true };
      },
    });
    assert.equal(checks, 2);
    assert.equal(result.skipped, true);
    assert.equal(result.reason, 'active_execution');
    assert.equal(saveCount, 0);
  } finally {
    if (previous === undefined) delete process.env.MDM_SERVER_LIBRARY_MODE;
    else process.env.MDM_SERVER_LIBRARY_MODE = previous;
  }
});

test('daily snapshot retention keeps 90 business dates and preserves adjacent delta', () => {
  const payload = runIsolated(`
    const repo = await import('./src/repository.js');
    const db = await import('./src/db.js');
    const route = { accountId: 'A', childUserId: 'CH-1', siteId: 'MLM' };
    const start = new Date('2026-04-27T00:00:00.000Z');
    for (let index = 0; index < 91; index += 1) {
      const date = new Date(start.getTime() + index * 86400000).toISOString().slice(0, 10);
      repo.saveDailyItemIdentitySnapshot({
        businessDate: date,
        ...route,
        itemIds: index === 90 ? ['KEEP', 'NEW'] : ['KEEP', 'OLD'],
        complete: true,
      });
    }
    const database = db.getDb();
    const bounds = database.prepare(
      'SELECT COUNT(*) AS count, MIN(business_date) AS min_date, MAX(business_date) AS max_date FROM daily_item_identity_snapshots'
    ).get();
    const delta = repo.getDailyItemIdentityDelta({
      businessDate: '2026-07-26',
      ...route,
    });
    console.log(JSON.stringify({ bounds, delta }));
    db.closeDb();
  `);
  assert.deepEqual(payload.bounds, {
    count: 90,
    min_date: '2026-04-28',
    max_date: '2026-07-26',
  });
  assert.equal(payload.delta.status, 'ready');
  assert.deepEqual(payload.delta.added_item_ids, ['NEW']);
  assert.deepEqual(payload.delta.removed_item_ids, ['OLD']);
});

test('SELLER calibrated routes, cancel dimensions, log ids and safe external errors are preserved', () => {
  const sellerStatus = functionSource('activityCatalogSellerStatus', 'buildExecutionSubmissionSnapshot');
  assert.match(sellerStatus, /cached_route_keys/);
  assert.match(sellerStatus, /visibility_unknown/);

  const cancelContract = SERVER_SOURCE.slice(
    SERVER_SOURCE.indexOf('resultContract = buildCancelResultContract'),
    SERVER_SOURCE.indexOf('const completed = counts.failed', SERVER_SOURCE.indexOf('resultContract = buildCancelResultContract')),
  );
  assert.match(cancelContract, /live_read_classification_by_item/);
  assert.match(cancelContract, /request_failure_reason/);
  assert.match(cancelContract, /live_status/);

  const jobLog = functionSource('appendExecutionJobLog', 'appendExecutionUserLog');
  const userLog = functionSource('appendExecutionUserLog', 'ensureExecutionJobEventDir');
  assert.match(jobLog, /event_id/);
  assert.match(userLog, /event_id/);
  assert.match(SERVER_SOURCE, /sanitizeExternalErrorForPersistence|safeErrorDetails/);
  assert.doesNotMatch(SERVER_SOURCE, /raw:\s*\{[\s\S]*message:\s*error\?\.message/);

  const externalError = functionSource('sanitizeExternalErrorForPersistence', 'sanitizeAuditEventPayload');
  assert.match(externalError, /reason_cn:\s*toChineseError\(error\)/);
  assert.match(externalError, /error\?\.body\?\.(?:code|error|message_code)/);
  const benchmarkError = functionSource('writeBenchmarkError', 'sanitizeMercadoMessage');
  assert.doesNotMatch(benchmarkError, /error\?\.message|String\(error\)|body\?\.message/);
  const response = functionSource('responseSummary', 'summarizeWriteBenchmarkErrors');
  assert.doesNotMatch(response, /headers:/);
  assert.doesNotMatch(response, /body:\s*sanitize/);
  assert.match(SERVER_SOURCE, /raw_error_summary:\s*executionSafeErrorSummary\(error\)/);
});
