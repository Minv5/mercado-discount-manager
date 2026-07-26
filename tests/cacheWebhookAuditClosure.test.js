import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';

import {
  buildItemIdentitySummary,
  candidateTotalProbeDecision,
} from '../src/activityChangeCache.js';
import {
  activityCallbackConfig,
  createActivityCallbackAdapter,
  signActivityCallbackEvent,
} from '../src/activityCallbackAdapter.js';
import {
  assessFinalVerificationEvidence,
  buildFinalRevalidationPlan,
} from '../src/finalRevalidationPlan.js';
import { createActivityWebhookConsumer } from '../src/activityWebhookConsumer.js';
import { createConfirmedExecutionScope } from '../src/submissionScopeFreeze.js';

function runIsolated(source, dataDir) {
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      MDM_DATA_DIR: dataDir,
      MDM_DB_PATH: path.join(dataDir, 'audit.sqlite'),
    },
    encoding: 'utf8',
    timeout: 30_000,
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return JSON.parse(String(result.stdout || '').trim());
}

function activity(childUserId, promotionId = 'P-1') {
  return {
    account_id: 'A-1',
    child_user_id: childUserId,
    site_id: 'MLM',
    promotion_id: promotionId,
    promotion_type: 'DEAL',
    status: 'started',
    finish_date: '2026-08-31T23:59:59.000Z',
  };
}

test('activity cache migration quarantines legacy rows and isolates equal parent/site child routes', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-cache-child-migration-'));
  try {
    const dbPath = path.join(dataDir, 'audit.sqlite');
    const legacy = new DatabaseSync(dbPath);
    legacy.exec(`
      CREATE TABLE activity_cache_states (
        account_id TEXT NOT NULL,
        site_id TEXT NOT NULL DEFAULT '',
        promotion_id TEXT NOT NULL DEFAULT '',
        promotion_type TEXT NOT NULL DEFAULT '',
        catalog_checked_at TEXT,
        items_full_checked_at TEXT,
        dirty INTEGER NOT NULL DEFAULT 0,
        expired INTEGER NOT NULL DEFAULT 0,
        continuity TEXT NOT NULL DEFAULT 'continuous',
        event_cursor TEXT,
        last_error TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (account_id, site_id, promotion_id, promotion_type)
      );
      INSERT INTO activity_cache_states
        (account_id, site_id, promotion_id, promotion_type, dirty, continuity, updated_at)
      VALUES ('A-1', 'MLM', 'P-1', 'DEAL', 1, 'gap', '2026-07-20T00:00:00.000Z');
    `);
    legacy.close();

    const result = runIsolated(`
      const repo = await import('./src/repository.js');
      const db = await import('./src/db.js');
      const legacy = repo.getActivityCacheState({
        accountId: 'A-1', childUserId: '', siteId: 'MLM', promotionId: 'P-1', promotionType: 'DEAL',
      });
      const beforeA = repo.getActivityCacheState({
        accountId: 'A-1', childUserId: 'CH-A', siteId: 'MLM', promotionId: 'P-1', promotionType: 'DEAL',
      });
      repo.saveActivityCacheState({
        accountId: 'A-1', childUserId: 'CH-A', siteId: 'MLM', promotionId: 'P-1', promotionType: 'DEAL',
        dirty: true, continuity: 'gap',
      });
      repo.saveActivityCacheState({
        accountId: 'A-1', childUserId: 'CH-B', siteId: 'MLM', promotionId: 'P-1', promotionType: 'DEAL',
        dirty: false, continuity: 'continuous',
      });
      const childA = repo.getActivityCacheState({
        accountId: 'A-1', childUserId: 'CH-A', siteId: 'MLM', promotionId: 'P-1', promotionType: 'DEAL',
      });
      const childB = repo.getActivityCacheState({
        accountId: 'A-1', childUserId: 'CH-B', siteId: 'MLM', promotionId: 'P-1', promotionType: 'DEAL',
      });
      const pk = db.all('PRAGMA table_info(activity_cache_states)')
        .filter((row) => Number(row.pk) > 0)
        .sort((left, right) => Number(left.pk) - Number(right.pk))
        .map((row) => row.name);
      console.log(JSON.stringify({ legacy, beforeA, childA, childB, pk }));
    `, dataDir);

    assert.equal(result.legacy.child_user_id, '');
    assert.equal(result.beforeA ?? null, null);
    assert.equal(result.childA.dirty, 1);
    assert.equal(result.childA.continuity, 'gap');
    assert.equal(result.childB.dirty, 0);
    assert.equal(result.childB.continuity, 'continuous');
    assert.deepEqual(result.pk, ['account_id', 'child_user_id', 'site_id', 'promotion_id', 'promotion_type']);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('legacy webhook v1 is rejected before claim or dirty because it has no child route identity', async () => {
  const secret = 'fixture-secret';
  const event = {
    schema_version: '1',
    event_id: 'evt-v1-no-child',
    account_id: 'A-1',
    site_id: 'MLM',
    promotion_id: 'P-1',
    promotion_type: 'DEAL',
    cursor: '11',
    previous_cursor: '10',
  };
  let claimCalls = 0;
  let dirtyCalls = 0;
  const adapter = createActivityCallbackAdapter({
    config: activityCallbackConfig({
      MDM_ACTIVITY_CALLBACK_ENABLED: '1',
      MDM_ACTIVITY_CALLBACK_SECRET: secret,
    }),
    claimEvent: () => {
      claimCalls += 1;
      return { status: 'claimed', claim_token: 'must-not-be-used' };
    },
    finalizeEvent: () => assert.fail('unsupported v1 must not be finalized'),
    getCacheState: () => assert.fail('unsupported v1 must not read cache state'),
    markDirty: () => {
      dirtyCalls += 1;
    },
  });

  await assert.rejects(
    () => adapter.accept(event, signActivityCallbackEvent(event, secret)),
    (error) => error?.status === 400
      && error?.code === 'ACTIVITY_CALLBACK_UNSUPPORTED_VERSION'
      && error?.audit_reason === 'unsupported_version',
  );
  assert.equal(claimCalls, 0);
  assert.equal(dirtyCalls, 0);
});

test('webhook dirty calls carry the resolved child route separately from mutable cache fields', async () => {
  const calls = [];
  const consumer = createActivityWebhookConsumer({
    listMarketplaceSites: () => [
      { account_id: 'A-1', child_user_id: '1001', site_id: 'MLM' },
      { account_id: 'A-1', child_user_id: '1002', site_id: 'MLM' },
    ],
    listAccounts: () => [],
    createResourceClient: async () => ({
      getNotificationResource: async () => ({
        item_id: 'MLM123',
        promotion_id: 'P-1',
        promotion_type: 'DEAL',
      }),
    }),
    markDirty: (...args) => calls.push(args),
    invalidateCatalog: () => assert.fail('exact activity must not invalidate the route catalog'),
  });
  await consumer({
    schema_version: '2',
    event_id: 'evt-child-b',
    topic: 'public_offers',
    resource: '/seller-promotions/promotions/offer/O-1/1002',
    remote_user_id: '1002',
    application_id: 'APP-1',
    received_at: '2026-07-26T02:00:00.000Z',
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0].promotionId, 'P-1');
  assert.deepEqual(calls[0][1], {
    accountId: 'A-1',
    childUserId: '1002',
    siteId: 'MLM',
  });
});

test('exact webhook activity dirty also invalidates only its own route evidence', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-exact-webhook-route-dirty-'));
  try {
    const result = runIsolated(`
      const repo = await import('./src/repository.js');
      const clean = (childUserId) => repo.saveActivityCacheState({
        accountId: 'A-1',
        childUserId,
        siteId: 'MLM',
        promotionId: '',
        promotionType: '',
        catalogCheckedAt: '2026-07-26T01:00:00.000Z',
        dirty: false,
        continuity: 'continuous',
      });
      clean('CH-A');
      clean('CH-B');
      repo.markActivityCacheDirty({
        accountId: 'A-1',
        childUserId: 'CH-A',
        siteId: 'MLM',
        promotionId: 'P-1',
        promotionType: 'DEAL',
        eventCursor: 'evt-11',
      });
      const routeA = repo.getActivityCacheState({
        accountId: 'A-1', childUserId: 'CH-A', siteId: 'MLM',
        promotionId: '', promotionType: '',
      });
      const activityA = repo.getActivityCacheState({
        accountId: 'A-1', childUserId: 'CH-A', siteId: 'MLM',
        promotionId: 'P-1', promotionType: 'DEAL',
      });
      const routeB = repo.getActivityCacheState({
        accountId: 'A-1', childUserId: 'CH-B', siteId: 'MLM',
        promotionId: '', promotionType: '',
      });
      console.log(JSON.stringify({ routeA, activityA, routeB }));
    `, dataDir);
    assert.equal(result.routeA.dirty, 1);
    assert.equal(result.routeA.event_cursor, 'evt-11');
    assert.equal(result.activityA.dirty, 1);
    assert.equal(result.activityA.event_cursor, 'evt-11');
    assert.equal(result.routeB.dirty, 0);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('same-day candidate cache never upgrades a first-page probe to full pagination', () => {
  const baselineIds = ['I-1', 'I-2', 'I-3', 'I-4'];
  const swappedIds = ['I-1', 'I-2', 'I-3', 'I-5'];
  const fetchState = {
    platform_total: 4,
    updated_at: '2026-07-26T01:00:00.000Z',
    raw_json: JSON.stringify({
      first_page_item_ids: ['I-1', 'I-2'],
      identity_summary: buildItemIdentitySummary(baselineIds, { complete: true }),
    }),
  };

  const insufficient = candidateTotalProbeDecision({
    fetchState,
    probe: { platform_total: 4, first_page_item_ids: ['I-1', 'I-2'] },
    now: new Date('2026-07-26T02:00:00.000Z'),
  });
  assert.deepEqual(insufficient, {
    refresh: false,
    reason: 'same_day_candidate_cache',
    verification_source: 'same_day_cached_identity',
  });

  const nextDayInsufficient = candidateTotalProbeDecision({
    fetchState,
    probe: { platform_total: 4, first_page_item_ids: ['I-1', 'I-2'] },
    now: new Date('2026-07-27T02:00:00.000Z'),
  });
  assert.equal(nextDayInsufficient.refresh, true);
  assert.equal(nextDayInsufficient.reason, 'candidate_probe_not_authoritative');

  const changed = candidateTotalProbeDecision({
    fetchState,
    probe: {
      platform_total: 4,
      first_page_item_ids: ['I-1', 'I-2'],
      identity_summary: buildItemIdentitySummary(swappedIds, { complete: true }),
    },
    now: new Date('2026-07-26T02:00:00.000Z'),
  });
  assert.equal(changed.refresh, true);
  assert.equal(changed.reason, 'candidate_identity_changed');

  const unchanged = candidateTotalProbeDecision({
    fetchState,
    probe: {
      platform_total: 4,
      first_page_item_ids: ['I-1', 'I-2'],
      identity_summary: buildItemIdentitySummary([...baselineIds].reverse(), { complete: true }),
    },
    now: new Date('2026-07-26T02:00:00.000Z'),
  });
  assert.deepEqual(unchanged, {
    refresh: false,
    reason: 'candidate_identity_verified',
    verification_source: 'complete_identity_digest',
  });
});

test('final revalidation never treats webhook delivery as global catalog continuity', () => {
  const row = activity('CH-A');
  const confirmed = createConfirmedExecutionScope({ action: 'cancel', activities: [row] });
  const cacheState = {
    dirty: 0,
    continuity: 'continuous',
    items_full_checked_at: '2026-07-26T01:30:00.000Z',
  };
  const fetchState = {
    detail_status: 'ok',
    saved_count: 2,
    platform_total: 2,
    updated_at: '2026-07-26T01:30:00.000Z',
  };
  const withoutEvidence = buildFinalRevalidationPlan({
    confirmedScope: confirmed,
    currentPromotions: [row],
    action: 'cancel',
    preparedAt: '2026-07-26T01:00:00.000Z',
    now: new Date('2026-07-26T02:00:00.000Z'),
    verificationEvidence: { source: 'local_cache' },
    getCacheState: () => cacheState,
    getFetchState: () => fetchState,
  });
  assert.equal(withoutEvidence.platform_read_required, true);
  assert.deepEqual([...withoutEvidence.required_live_route_keys], ['A-1|CH-A|MLM']);
  assert.equal(withoutEvidence.verification_contract.reason, 'authoritative_evidence_missing');

  const webhookEvidence = {
    source: 'official_webhook',
    production: true,
    signature_verified: true,
    continuity: 'continuous',
    coverage_scope: 'all_configured_routes',
    coverage_started_at: '2026-07-26T00:00:00.000Z',
    verified_at: '2026-07-26T02:00:00.000Z',
    evidence_id: 'evt-production-1',
  };
  const assessedWebhook = assessFinalVerificationEvidence({
    evidence: webhookEvidence,
    preparedAt: '2026-07-26T01:00:00.000Z',
    now: new Date('2026-07-26T02:00:00.000Z'),
  });
  assert.equal(assessedWebhook.allows_local_zero_read, false);
  assert.equal(assessedWebhook.reason, 'authoritative_evidence_missing');
  const withEvidence = buildFinalRevalidationPlan({
    confirmedScope: confirmed,
    currentPromotions: [row],
    action: 'cancel',
    preparedAt: '2026-07-26T01:00:00.000Z',
    now: new Date('2026-07-26T02:00:00.000Z'),
    verificationEvidence: webhookEvidence,
    getCacheState: () => cacheState,
    getFetchState: () => fetchState,
  });
  assert.equal(withEvidence.platform_read_required, true);
  assert.deepEqual([...withEvidence.required_live_route_keys], ['A-1|CH-A|MLM']);
});

test('SQLite callback claim permits one concurrent dirty side effect and retries failed consumption', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-callback-claim-'));
  try {
    const result = runIsolated(`
      const repo = await import('./src/repository.js');
      const { activityCallbackConfig, createActivityCallbackAdapter, signActivityCallbackEvent } =
        await import('./src/activityCallbackAdapter.js');
      const secret = 'fixture-secret';
      const base = {
        schema_version: '2',
        event_id: 'evt-concurrent',
        topic: 'public_offers',
        resource: '/seller-promotions/promotions/offer/O-1/CH-A',
        remote_user_id: 'CH-A',
        application_id: 'APP-1',
        received_at: '2026-07-26T02:00:00.000Z',
      };
      let consumeCalls = 0;
      const adapter = createActivityCallbackAdapter({
        config: activityCallbackConfig({
          MDM_ACTIVITY_CALLBACK_ENABLED: '1',
          MDM_ACTIVITY_CALLBACK_SECRET: secret,
          MDM_ACTIVITY_CALLBACK_APPLICATION_ID: 'APP-1',
        }),
        claimEvent: repo.claimActivityCallbackEvent,
        finalizeEvent: repo.finalizeActivityCallbackEvent,
        consumeEvent: async () => {
          consumeCalls += 1;
          await new Promise((resolve) => setTimeout(resolve, 30));
          return {
            account_id: 'A-1', child_user_id: 'CH-A', site_id: 'MLM',
            promotion_id: 'P-1', promotion_type: 'DEAL', outcome: 'activity_dirty',
          };
        },
      });
      const signature = signActivityCallbackEvent(base, secret);
      const concurrent = await Promise.all([
        adapter.accept(base, signature),
        adapter.accept(base, signature),
      ]);

      let retryCalls = 0;
      const retryEvent = { ...base, event_id: 'evt-retry' };
      const retryAdapter = createActivityCallbackAdapter({
        config: activityCallbackConfig({
          MDM_ACTIVITY_CALLBACK_ENABLED: '1',
          MDM_ACTIVITY_CALLBACK_SECRET: secret,
          MDM_ACTIVITY_CALLBACK_APPLICATION_ID: 'APP-1',
        }),
        claimEvent: repo.claimActivityCallbackEvent,
        finalizeEvent: repo.finalizeActivityCallbackEvent,
        consumeEvent: async () => {
          retryCalls += 1;
          if (retryCalls === 1) throw new Error('temporary resource failure');
          return {
            account_id: 'A-1', child_user_id: 'CH-A', site_id: 'MLM',
            promotion_id: 'P-1', promotion_type: 'DEAL', outcome: 'activity_dirty',
          };
        },
      });
      const retrySignature = signActivityCallbackEvent(retryEvent, secret);
      let firstRetryError = '';
      try {
        await retryAdapter.accept(retryEvent, retrySignature);
      } catch (error) {
        firstRetryError = error.message;
      }
      const recovered = await retryAdapter.accept(retryEvent, retrySignature);
      console.log(JSON.stringify({
        concurrent,
        consumeCalls,
        completed: repo.getActivityCallbackEvent('evt-concurrent'),
        firstRetryError,
        recovered,
        retryCalls,
        retried: repo.getActivityCallbackEvent('evt-retry'),
      }));
    `, dataDir);

    assert.equal(result.consumeCalls, 1);
    assert.deepEqual(result.concurrent.map((row) => row.status).sort(), ['accepted', 'in_progress']);
    assert.equal(result.completed.processing_state, 'completed');
    assert.match(result.firstRetryError, /temporary resource failure/);
    assert.equal(result.recovered.status, 'accepted');
    assert.equal(result.retryCalls, 2);
    assert.equal(result.retried.processing_state, 'completed');
    assert.equal(result.retried.attempt_count, 2);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
