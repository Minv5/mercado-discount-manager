import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';

const ROOT = path.resolve(import.meta.dirname, '..');

function runIsolated(source) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-oauth-daily-snapshot-'));
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

test('legacy OAuth state schema migrates without losing state or inventing route identity', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-oauth-legacy-migration-'));
  const dbPath = path.join(dataDir, 'legacy.sqlite');
  try {
    const legacy = new DatabaseSync(dbPath);
    legacy.exec(`
      CREATE TABLE oauth_states (
        state TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        client_secret_cipher TEXT NOT NULL,
        redirect_uri TEXT NOT NULL,
        auth_domain TEXT NOT NULL,
        code_verifier TEXT NOT NULL,
        code_challenge TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      INSERT INTO oauth_states VALUES (
        'legacy-state', 'client', 'cipher', 'https://example.test/callback',
        'https://auth.example.test', 'verifier', 'challenge', '2026-07-26T02:59:00.000Z'
      );
    `);
    legacy.close();

    const payload = JSON.parse(execFileSync(
      process.execPath,
      ['--input-type=module', '--eval', `
        const db = await import('./src/db.js');
        const database = db.getDb();
        const oauth = database.prepare(
          "SELECT state, processing_state, attempt_count FROM oauth_states WHERE state = 'legacy-state'"
        ).get();
        const snapshotColumns = database.prepare(
          "PRAGMA table_info(daily_item_identity_snapshots)"
        ).all().map((row) => row.name);
        console.log(JSON.stringify({ oauth, snapshotColumns }));
        db.closeDb();
      `],
      {
        cwd: ROOT,
        env: { ...process.env, MDM_DATA_DIR: dataDir, MDM_DB_PATH: dbPath },
        encoding: 'utf8',
        timeout: 30_000,
      },
    ));

    assert.deepEqual(payload.oauth, {
      state: 'legacy-state',
      processing_state: 'pending',
      attempt_count: 0,
    });
    assert.ok(payload.snapshotColumns.includes('child_user_id'));
    assert.ok(payload.snapshotColumns.includes('item_ids_hash'));
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('OAuth state claim enforces TTL, exclusive lease, retry, and consume-after-success', () => {
  const payload = runIsolated(`
    const repo = await import('./src/repository.js');
    const db = await import('./src/db.js');
    const base = new Date('2026-07-26T03:00:00.000Z');
    const stateRecord = (state, createdAt) => ({
      state,
      clientId: 'client-id',
      clientSecret: 'client-secret',
      redirectUri: 'https://example.test/callback',
      authDomain: 'https://auth.example.test',
      codeVerifier: 'verifier',
      codeChallenge: 'challenge',
      createdAt,
    });

    repo.saveOAuthState(stateRecord('expired', '2026-07-26T02:44:59.000Z'));
    const expired = repo.claimOAuthState('expired', { now: base, maxAgeMs: 15 * 60 * 1000 });

    repo.saveOAuthState(stateRecord('retryable', '2026-07-26T02:59:00.000Z'));
    const first = repo.claimOAuthState('retryable', { now: base, leaseMs: 60_000 });
    const concurrent = repo.claimOAuthState('retryable', { now: base, leaseMs: 60_000 });
    const reclaimed = repo.claimOAuthState('retryable', {
      now: new Date('2026-07-26T03:01:01.000Z'),
      leaseMs: 60_000,
    });
    const consumed = repo.consumeClaimedOAuthState({
      state: 'retryable',
      claimToken: reclaimed.claim_token,
      now: new Date('2026-07-26T03:01:02.000Z'),
    });
    const afterSuccess = repo.claimOAuthState('retryable', {
      now: new Date('2026-07-26T03:01:03.000Z'),
    });

    repo.saveOAuthState(stateRecord('released', '2026-07-26T02:59:30.000Z'));
    const releaseClaim = repo.claimOAuthState('released', { now: base, leaseMs: 60_000 });
    const released = repo.releaseOAuthStateClaim({
      state: 'released',
      claimToken: releaseClaim.claim_token,
      errorCode: 'TOKEN_EXCHANGE_FAILED',
      now: new Date('2026-07-26T03:00:01.000Z'),
    });
    const immediateRetry = repo.claimOAuthState('released', {
      now: new Date('2026-07-26T03:00:02.000Z'),
    });

    repo.saveOAuthState(stateRecord('renewable', '2026-07-26T03:00:00.000Z'));
    const defaultLease = repo.claimOAuthState('renewable', {
      now: new Date('2026-07-26T03:01:00.000Z'),
    });
    const renewed = repo.renewOAuthStateClaim({
      state: 'renewable',
      claimToken: defaultLease.claim_token,
      now: new Date('2026-07-26T03:05:00.000Z'),
    });
    const replacement = repo.claimOAuthState('renewable', {
      now: new Date('2026-07-26T03:11:00.000Z'),
    });
    const staleRenew = repo.renewOAuthStateClaim({
      state: 'renewable',
      claimToken: defaultLease.claim_token,
      now: new Date('2026-07-26T03:11:01.000Z'),
    });
    const staleCommit = repo.saveTokenAccountAndConsumeOAuthState({
      state: 'renewable',
      claimToken: defaultLease.claim_token,
      token: {
        user_id: 'ACCOUNT-STALE-CLAIM',
        access_token: 'must-not-save',
        refresh_token: 'must-not-save',
      },
      profile: { id: 'ACCOUNT-STALE-CLAIM', nickname: 'Stale Claim' },
      clientId: 'client-id',
      clientSecret: 'client-secret',
      redirectUri: 'https://example.test/callback',
      authDomain: 'https://auth.example.test',
      now: new Date('2026-07-26T03:11:02.000Z'),
    });
    const staleTokenCount = db.getDb().prepare(
      "SELECT COUNT(*) AS count FROM oauth_tokens WHERE account_id = 'ACCOUNT-STALE-CLAIM'"
    ).get().count;

    console.log(JSON.stringify({
      expired,
      first,
      concurrent,
      reclaimed,
      consumed,
      afterSuccess,
      released,
      immediateRetry,
      defaultLease,
      renewed,
      replacement,
      staleRenew,
      staleCommit,
      staleTokenCount,
    }));
    db.closeDb();
  `);

  assert.equal(payload.expired.status, 'expired');
  assert.equal(payload.expired.claim_token, null);
  assert.equal(payload.first.status, 'claimed');
  assert.equal(payload.concurrent.status, 'in_progress');
  assert.equal(payload.reclaimed.status, 'claimed');
  assert.notEqual(payload.reclaimed.claim_token, payload.first.claim_token);
  assert.equal(payload.consumed.status, 'consumed');
  assert.equal(payload.consumed.record.clientSecret, 'client-secret');
  assert.equal(payload.afterSuccess.status, 'consumed');
  assert.equal(payload.released.status, 'released');
  assert.equal(payload.immediateRetry.status, 'claimed');
  assert.equal(
    Date.parse(payload.defaultLease.claim_expires_at) - Date.parse('2026-07-26T03:01:00.000Z'),
    5 * 60 * 1000,
  );
  assert.equal(payload.renewed.status, 'renewed');
  assert.equal(payload.renewed.claim_token, payload.defaultLease.claim_token);
  assert.equal(payload.renewed.claim_expires_at, '2026-07-26T03:10:00.000Z');
  assert.equal(payload.replacement.status, 'claimed');
  assert.notEqual(payload.replacement.claim_token, payload.defaultLease.claim_token);
  assert.equal(payload.staleRenew.status, 'claim_mismatch');
  assert.equal(payload.staleCommit.status, 'claim_mismatch');
  assert.equal(payload.staleTokenCount, 0);
});

test('OAuth token upsert and claimed-state consume commit or roll back together', () => {
  const payload = runIsolated(`
    const repo = await import('./src/repository.js');
    const db = await import('./src/db.js');
    repo.saveOAuthState({
      state: 'atomic-state',
      clientId: 'client-id',
      clientSecret: 'client-secret',
      redirectUri: 'https://example.test/callback',
      authDomain: 'https://auth.example.test',
      codeVerifier: 'verifier',
      codeChallenge: 'challenge',
      createdAt: '2026-07-26T02:59:00.000Z',
    });
    const claim = repo.claimOAuthState('atomic-state', {
      now: new Date('2026-07-26T03:00:00.000Z'),
    });
    const database = db.getDb();
    database.exec(\`
      CREATE TRIGGER fail_oauth_consume
      BEFORE UPDATE OF processing_state ON oauth_states
      WHEN NEW.processing_state = 'consumed'
      BEGIN
        SELECT RAISE(ABORT, 'simulated consume crash');
      END;
    \`);
    let failed = '';
    try {
      repo.saveTokenAccountAndConsumeOAuthState({
        state: 'atomic-state',
        claimToken: claim.claim_token,
        token: {
          user_id: 'ACCOUNT-1',
          access_token: 'access-token',
          refresh_token: 'refresh-token',
          token_type: 'Bearer',
          expires_in: 21600,
        },
        profile: { id: 'ACCOUNT-1', nickname: 'Account One', site_id: 'CBT' },
        clientId: 'client-id',
        clientSecret: 'client-secret',
        redirectUri: 'https://example.test/callback',
        authDomain: 'https://auth.example.test',
        now: new Date('2026-07-26T03:00:01.000Z'),
      });
    } catch (error) {
      failed = error.message;
    }
    const afterFailure = {
      token_count: database.prepare('SELECT COUNT(*) AS count FROM oauth_tokens').get().count,
      state: database.prepare(
        "SELECT processing_state, claim_token FROM oauth_states WHERE state = 'atomic-state'"
      ).get(),
    };
    database.exec('DROP TRIGGER fail_oauth_consume');
    const committed = repo.saveTokenAccountAndConsumeOAuthState({
      state: 'atomic-state',
      claimToken: claim.claim_token,
      token: {
        user_id: 'ACCOUNT-1',
        access_token: 'access-token',
        refresh_token: 'refresh-token',
        token_type: 'Bearer',
        expires_in: 21600,
      },
      profile: { id: 'ACCOUNT-1', nickname: 'Account One', site_id: 'CBT' },
      clientId: 'client-id',
      clientSecret: 'client-secret',
      redirectUri: 'https://example.test/callback',
      authDomain: 'https://auth.example.test',
      now: new Date('2026-07-26T03:00:02.000Z'),
    });
    const afterSuccess = {
      token_count: database.prepare('SELECT COUNT(*) AS count FROM oauth_tokens').get().count,
      state: database.prepare(
        "SELECT processing_state, claim_token FROM oauth_states WHERE state = 'atomic-state'"
      ).get(),
    };
    const duplicate = repo.saveTokenAccountAndConsumeOAuthState({
      state: 'atomic-state',
      claimToken: claim.claim_token,
      token: {
        user_id: 'ACCOUNT-1',
        access_token: 'must-not-overwrite',
        refresh_token: 'must-not-overwrite',
      },
      profile: { id: 'ACCOUNT-1', nickname: 'Tampered' },
      clientId: 'client-id',
      clientSecret: 'client-secret',
      redirectUri: 'https://example.test/callback',
      authDomain: 'https://auth.example.test',
      now: new Date('2026-07-26T03:00:03.000Z'),
    });

    repo.saveOAuthState({
      state: 'expired-during-exchange',
      clientId: 'client-id',
      clientSecret: 'client-secret',
      redirectUri: 'https://example.test/callback',
      authDomain: 'https://auth.example.test',
      codeVerifier: 'verifier',
      codeChallenge: 'challenge',
      createdAt: '2026-07-26T03:04:00.000Z',
    });
    const expiringClaim = repo.claimOAuthState('expired-during-exchange', {
      now: new Date('2026-07-26T03:05:00.000Z'),
      leaseMs: 30 * 60 * 1000,
    });
    const expiredCommit = repo.saveTokenAccountAndConsumeOAuthState({
      state: 'expired-during-exchange',
      claimToken: expiringClaim.claim_token,
      token: {
        user_id: 'ACCOUNT-EXPIRED',
        access_token: 'must-not-save',
        refresh_token: 'must-not-save',
      },
      profile: { id: 'ACCOUNT-EXPIRED', nickname: 'Expired' },
      clientId: 'client-id',
      clientSecret: 'client-secret',
      redirectUri: 'https://example.test/callback',
      authDomain: 'https://auth.example.test',
      now: new Date('2026-07-26T03:20:01.000Z'),
    });
    const expiredTokenCount = database.prepare(
      "SELECT COUNT(*) AS count FROM oauth_tokens WHERE account_id = 'ACCOUNT-EXPIRED'"
    ).get().count;

    repo.saveOAuthState({
      state: 'lease-expired',
      clientId: 'client-id',
      clientSecret: 'client-secret',
      redirectUri: 'https://example.test/callback',
      authDomain: 'https://auth.example.test',
      codeVerifier: 'verifier',
      codeChallenge: 'challenge',
      createdAt: '2026-07-26T03:29:00.000Z',
    });
    const shortClaim = repo.claimOAuthState('lease-expired', {
      now: new Date('2026-07-26T03:30:00.000Z'),
      leaseMs: 1000,
    });
    const leaseExpiredCommit = repo.saveTokenAccountAndConsumeOAuthState({
      state: 'lease-expired',
      claimToken: shortClaim.claim_token,
      token: {
        user_id: 'ACCOUNT-LEASE-EXPIRED',
        access_token: 'must-not-save',
        refresh_token: 'must-not-save',
      },
      profile: { id: 'ACCOUNT-LEASE-EXPIRED', nickname: 'Lease Expired' },
      clientId: 'client-id',
      clientSecret: 'client-secret',
      redirectUri: 'https://example.test/callback',
      authDomain: 'https://auth.example.test',
      now: new Date('2026-07-26T03:30:02.000Z'),
    });
    const leaseExpiredTokenCount = database.prepare(
      "SELECT COUNT(*) AS count FROM oauth_tokens WHERE account_id = 'ACCOUNT-LEASE-EXPIRED'"
    ).get().count;
    const leaseExpiredState = database.prepare(
      "SELECT processing_state, claim_token, last_error_code FROM oauth_states WHERE state = 'lease-expired'"
    ).get();

    console.log(JSON.stringify({
      failed,
      afterFailure,
      committed,
      afterSuccess,
      duplicate,
      expiredCommit,
      expiredTokenCount,
      leaseExpiredCommit,
      leaseExpiredTokenCount,
      leaseExpiredState,
    }));
    db.closeDb();
  `);

  assert.match(payload.failed, /simulated consume crash/);
  assert.equal(payload.afterFailure.token_count, 0);
  assert.equal(payload.afterFailure.state.processing_state, 'processing');
  assert.ok(payload.afterFailure.state.claim_token);
  assert.equal(payload.committed.status, 'consumed');
  assert.equal(payload.committed.account.account_id, 'ACCOUNT-1');
  assert.equal(payload.afterSuccess.token_count, 1);
  assert.equal(payload.afterSuccess.state.processing_state, 'consumed');
  assert.equal(payload.afterSuccess.state.claim_token, null);
  assert.equal(payload.duplicate.status, 'consumed');
  assert.equal(payload.duplicate.account.display_name, 'Account One');
  assert.equal(payload.expiredCommit.status, 'expired');
  assert.equal(payload.expiredTokenCount, 0);
  assert.equal(payload.leaseExpiredCommit.status, 'claim_expired');
  assert.equal(payload.leaseExpiredTokenCount, 0);
  assert.deepEqual(payload.leaseExpiredState, {
    processing_state: 'pending',
    claim_token: null,
    last_error_code: 'OAUTH_STATE_CLAIM_EXPIRED',
  });
});

test('daily item identity snapshots isolate child routes and reject partial removal inference', () => {
  const payload = runIsolated(`
    const repo = await import('./src/repository.js');
    const db = await import('./src/db.js');
    const routeA = { accountId: 'A', childUserId: 'CH-1', siteId: 'MLM' };
    const routeB = { accountId: 'A', childUserId: 'CH-2', siteId: 'MLM' };

    repo.saveDailyItemIdentitySnapshot({
      businessDate: '2026-07-25',
      ...routeA,
      itemIds: ['I-2', 'I-1', 'I-2'],
      complete: true,
    });
    repo.saveDailyItemIdentitySnapshot({
      businessDate: '2026-07-25',
      ...routeB,
      itemIds: ['OTHER'],
      complete: true,
    });
    repo.saveDailyItemIdentitySnapshot({
      businessDate: '2026-07-26',
      ...routeA,
      itemIds: ['I-2'],
      complete: false,
    });

    const routeAOld = repo.getDailyItemIdentitySnapshot({
      businessDate: '2026-07-25',
      ...routeA,
    });
    const routeBOld = repo.getDailyItemIdentitySnapshot({
      businessDate: '2026-07-25',
      ...routeB,
    });
    const partialDelta = repo.getDailyItemIdentityDelta({
      businessDate: '2026-07-26',
      ...routeA,
    });

    console.log(JSON.stringify({ routeAOld, routeBOld, partialDelta }));
    db.closeDb();
  `);

  assert.equal(payload.routeAOld.item_count, 2);
  assert.deepEqual(payload.routeAOld.item_ids, ['I-1', 'I-2']);
  assert.deepEqual(payload.routeBOld.item_ids, ['OTHER']);
  assert.equal(payload.partialDelta.status, 'insufficient');
  assert.equal(payload.partialDelta.reason, 'current_snapshot_incomplete');
  assert.equal(payload.partialDelta.added_count, null);
  assert.equal(payload.partialDelta.removed_count, null);
});

test('adjacent complete daily snapshots produce exact added and removed identities', () => {
  const payload = runIsolated(`
    const repo = await import('./src/repository.js');
    const db = await import('./src/db.js');
    const route = { accountId: 'A', childUserId: 'CH-1', siteId: 'MLB' };
    repo.saveDailyItemIdentitySnapshot({
      businessDate: '2026-07-25',
      ...route,
      itemIds: ['I-1', 'I-2'],
      complete: true,
    });
    repo.saveDailyItemIdentitySnapshot({
      businessDate: '2026-07-26',
      ...route,
      itemIds: ['I-2', 'I-3'],
      complete: true,
    });
    const delta = repo.getDailyItemIdentityDelta({
      businessDate: '2026-07-26',
      ...route,
    });
    const aggregate = repo.summarizeDailyItemIdentityDeltas({
      businessDate: '2026-07-26',
      routes: [route],
    });
    console.log(JSON.stringify({ delta, aggregate }));
    db.closeDb();
  `);

  assert.equal(payload.delta.status, 'ready');
  assert.equal(payload.delta.baseline_date, '2026-07-25');
  assert.equal(payload.delta.current_date, '2026-07-26');
  assert.deepEqual(payload.delta.added_item_ids, ['I-3']);
  assert.deepEqual(payload.delta.removed_item_ids, ['I-1']);
  assert.equal(payload.delta.added_count, 1);
  assert.equal(payload.delta.removed_count, 1);
  assert.deepEqual(payload.aggregate, {
    status: 'ready',
    baseline_date: '2026-07-25',
    current_date: '2026-07-26',
    added_count: 1,
    removed_count: 1,
    route_count: 1,
    ready_route_count: 1,
    insufficient_route_count: 0,
    reason: '',
  });
});

test('daily snapshots retain the latest 90 Shanghai business dates without breaking adjacent delta', () => {
  const payload = runIsolated(`
    const repo = await import('./src/repository.js');
    const db = await import('./src/db.js');
    const route = { accountId: 'A', childUserId: 'CH-1', siteId: 'MLM' };
    const start = new Date('2026-04-27T00:00:00.000Z');
    for (let index = 0; index < 91; index += 1) {
      const date = new Date(start.getTime());
      date.setUTCDate(start.getUTCDate() + index);
      repo.saveDailyItemIdentitySnapshot({
        businessDate: date.toISOString().slice(0, 10),
        ...route,
        itemIds: index === 89 ? ['OLD', 'KEEP'] : index === 90 ? ['KEEP', 'NEW'] : [\`ITEM-\${index}\`],
        complete: true,
      });
    }
    const database = db.getDb();
    const retained = database.prepare(
      'SELECT COUNT(*) AS count, MIN(business_date) AS min_date, MAX(business_date) AS max_date FROM daily_item_identity_snapshots'
    ).get();
    const delta = repo.getDailyItemIdentityDelta({
      businessDate: '2026-07-26',
      ...route,
    });
    console.log(JSON.stringify({ retained, delta }));
    db.closeDb();
  `);

  assert.deepEqual(payload.retained, {
    count: 90,
    min_date: '2026-04-28',
    max_date: '2026-07-26',
  });
  assert.equal(payload.delta.status, 'ready');
  assert.deepEqual(payload.delta.added_item_ids, ['NEW']);
  assert.deepEqual(payload.delta.removed_item_ids, ['OLD']);
});
