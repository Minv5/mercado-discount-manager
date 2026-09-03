import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { executePlannedRowsWithConcurrency } from '../src/executor.js';
import {
  itemResourceRevisionIsBehindEvent,
  normalizeItemSnapshot,
  readItemResourceAfterEvent,
  revalidatePlannedRow,
} from '../src/itemSnapshot.js';
import { calculateDealPrice, validateDealPrice } from '../src/planner.js';

function runIsolated(source) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-item-snapshot-'));
  try {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: process.cwd(),
      env: { ...process.env, MDM_DATA_DIR: dataDir },
      encoding: 'utf8',
      timeout: 30_000,
    });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    return JSON.parse(String(result.stdout || '').trim());
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

test('frontend webhook summary counts refreshed items and current field changes without raw payloads', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    const base = { price: 100, original_price: 100, currency_id: 'USD', status: 'active' };
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1',
      observedAt: '2026-09-02T01:00:00.000Z', resource: { id: 'MLB1', ...base }
    });
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB2',
      observedAt: '2026-09-02T01:01:00.000Z', resource: { id: 'MLB2', ...base }
    });
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB2',
      observedAt: '2026-09-02T01:02:00.000Z', resource: { id: 'MLB2', ...base }
    });
    console.log(JSON.stringify(repo.summarizeObservedItemSnapshotChanges({ since: '2026-09-02T00:00:00.000Z' })));
  `);
  assert.deepEqual(result, { refreshed_item_count: 2, changed_item_count: 1 });
});

test('webhook snapshot stores field-level changes and rejects an older resource revision', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    const promotion = { account_id: 'A', child_user_id: 'C', site_id: 'MLB', promotion_id: 'P', promotion_type: 'DEAL' };
    repo.saveItems('A', 'P', 'DEAL', [{ id: 'MLB1', status: 'started', original_price: 100, price: 82 }], {
      childUserId: 'C', siteId: 'MLB', itemStatus: 'started'
    });
    const first = repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1', observedAt: '2099-01-01T00:00:00Z',
      resource: {
        id: 'MLB1', price: 120, original_price: 120, currency_id: 'BRL', available_quantity: 7,
        status: 'active', last_updated: '2026-08-28T00:59:00Z',
        shipping: { dimensions: '10x20x30,500' },
        attributes: [{ id: 'PACKAGE_WEIGHT', value_struct: { number: 500, unit: 'g' } }]
      }
    });
    const older = repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1', observedAt: '2099-01-01T00:05:00Z',
      resource: { id: 'MLB1', price: 90, original_price: 90, status: 'active', last_updated: '2026-08-27T23:00:00Z' }
    });
    const snapshot = repo.getConfirmedItemSnapshot({ accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1' });
    const item = repo.listItems('A', 'P', 'DEAL', 'started', promotion)[0];
    const pending = Object.fromEntries(repo.listPendingActivityPriceRecalcCountsForPromotions('A', [promotion]));
    console.log(JSON.stringify({ first, older, snapshot, item, pending }));
  `);
  assert.equal(result.first.updated, 1);
  assert.ok(result.first.changed_fields.includes('price'));
  assert.ok(result.first.changed_fields.includes('available_quantity'));
  assert.ok(result.first.changed_fields.includes('dimensions'));
  assert.ok(result.first.changed_fields.includes('weight'));
  assert.equal(result.first.reprice_queued, 1);
  assert.equal(result.older.ignored, true, JSON.stringify(result));
  assert.equal(result.older.reason, 'out_of_order_snapshot');
  assert.equal(result.snapshot.price, 120);
  assert.equal(result.snapshot.available_quantity, 7);
  assert.equal(result.item.original_price, 120);
  assert.ok(result.item.snapshot_hash);
  assert.equal(Object.values(result.pending)[0], 1);
});

test('resource revision older than its webhook is retried as a hint and the newest successful GET is accepted', async () => {
  assert.equal(itemResourceRevisionIsBehindEvent(
    { last_updated: '2026-08-28T00:59:59Z' },
    '2026-08-28T01:00:00Z',
  ), true);
  const responses = [
    { id: 'MLB1', original_price: 100, last_updated: '2026-08-28T00:59:59Z' },
    { id: 'MLB1', original_price: 100, last_updated: '2026-08-28T00:59:59Z' },
    { id: 'MLB1', original_price: 100, last_updated: '2026-08-28T00:59:59Z' },
    { id: 'MLB1', original_price: 120, last_updated: '2026-08-28T01:00:01Z' },
  ];
  const result = await readItemResourceAfterEvent({
    read: async () => responses.shift(),
    eventReceivedAt: '2026-08-28T01:00:00Z',
    retryDelaysMs: [0, 0, 0],
    sleepFn: async () => {},
  });
  assert.equal(result.attempts, 4);
  assert.equal(result.fresh, true);
  assert.equal(result.resource.original_price, 120);
});

test('bounded reread keeps the newest successful resource when a later retry fails', async () => {
  const responses = [
    { id: 'MLB1', price: 100, last_updated: '2026-08-28T00:59:58Z' },
    { id: 'MLB1', price: 120, last_updated: '2026-08-28T00:59:59Z' },
  ];
  let calls = 0;
  const result = await readItemResourceAfterEvent({
    read: async () => {
      calls += 1;
      if (responses.length) return responses.shift();
      const error = new Error('fetch failed');
      error.code = 'NETWORK';
      throw error;
    },
    eventReceivedAt: '2026-08-28T01:00:00Z',
    retryDelaysMs: [0, 0, 0],
    sleepFn: async () => {},
  });
  assert.equal(calls, 3);
  assert.equal(result.fresh, true);
  assert.equal(result.resource.price, 120);
  assert.equal(result.retry_error, 'NETWORK');
});

test('unconfirmed refresh preserves the last snapshot and candidate planning defers only that item', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    const planner = await import('./src/planner.js');
    const promotion = { account_id: 'A', child_user_id: 'C', site_id: 'MLB', promotion_id: 'P', promotion_type: 'DEAL' };
    repo.saveItems('A', 'P', 'DEAL', [{ id: 'MLB1', status: 'candidate', original_price: 100, price: 100 }], {
      childUserId: 'C', siteId: 'MLB', itemStatus: 'candidate'
    });
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1',
      resource: { id: 'MLB1', price: 150, original_price: null, status: 'active', last_updated: '2026-08-28T02:00:00Z' },
      observedAt: '2099-01-01T00:00:00Z', confirmed: true,
    });
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1',
      resource: { id: 'MLB1', price: 90, original_price: 90, status: 'active', last_updated: '2026-08-28T01:00:00Z' },
      observedAt: '2099-01-01T00:00:01Z', confirmed: false,
    });
    const snapshot = repo.getConfirmedItemSnapshot({ accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1' });
    const item = repo.listItems('A', 'P', 'DEAL', 'candidate', promotion)[0];
    const plan = planner.buildPlan({ action: 'enroll', promotion, items: [item], discountPercent: 18 });
    console.log(JSON.stringify({ snapshot, item, plan }));
  `);
  assert.equal(result.snapshot.price, 150);
  assert.equal(result.snapshot.original_price, null);
  assert.equal(result.snapshot.confirmed, false);
  assert.equal(result.item.original_price, 100);
  assert.equal(result.item.snapshot_confirmed, false);
  assert.equal(result.plan.planned, 0);
  assert.equal(result.plan.skipped, 1);
  assert.equal(result.plan.rows[0].reason, '商品变动数据仍在定向同步，暂缓本商品');
});

test('confirmed cache with null original price falls back to its positive listing price instead of zero', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    const promotion = { account_id: 'A', child_user_id: 'C', site_id: 'MLB', promotion_id: 'P', promotion_type: 'DEAL' };
    repo.saveItems('A', 'P', 'DEAL', [{ id: 'MLB1', status: 'candidate', original_price: 100, price: 100 }], {
      childUserId: 'C', siteId: 'MLB', itemStatus: 'candidate'
    });
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1',
      resource: { id: 'MLB1', price: 150, original_price: null, status: 'active', last_updated: '2026-08-28T02:00:00Z' },
      observedAt: '2099-01-01T00:00:00Z', confirmed: true,
    });
    console.log(JSON.stringify(repo.listItems('A', 'P', 'DEAL', 'candidate', promotion)[0]));
  `);
  assert.equal(result.original_price, 150);
  assert.notEqual(result.original_price, 0);
  assert.equal(result.snapshot_confirmed, true);
});

test('a later promotion relation price is not overwritten by an older item snapshot', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    const promotion = { account_id: 'A', child_user_id: 'C', site_id: 'MLB', promotion_id: 'P', promotion_type: 'DEAL' };
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1',
      resource: { id: 'MLB1', price: 80, original_price: 100, status: 'active', last_updated: '2026-01-01T00:00:00Z' },
      observedAt: '2026-01-01T00:00:01Z', confirmed: true,
    });
    repo.saveItems('A', 'P', 'DEAL', [{ id: 'MLB1', status: 'candidate', original_price: 150, price: 150 }], {
      childUserId: 'C', siteId: 'MLB', itemStatus: 'candidate'
    });
    console.log(JSON.stringify(repo.listItems('A', 'P', 'DEAL', 'candidate', promotion)[0]));
  `);
  assert.equal(result.original_price, 150);
  assert.equal(result.price, 150);
  assert.equal(result.snapshot_confirmed, true);
  assert.ok(result.snapshot_hash);
});

test('stale event resource is persisted unconfirmed and startup query sees only that item', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1',
      resource: { id: 'MLB1', price: 100, original_price: 100, last_updated: '2026-08-28T00:59:59Z' },
      observedAt: '2026-08-28T01:00:00Z', confirmed: false,
    });
    const before = repo.listUnconfirmedItemSnapshots();
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1',
      resource: { id: 'MLB1', price: 120, original_price: 120, last_updated: '2026-08-28T01:00:01Z' },
      observedAt: '2026-08-28T01:00:00Z', confirmed: true,
    });
    const after = repo.listUnconfirmedItemSnapshots();
    console.log(JSON.stringify({ before, after }));
  `);
  assert.equal(result.before.length, 1);
  assert.equal(result.before[0].item_id, 'MLB1');
  assert.equal(result.before[0].observed_at, '2026-08-28T01:00:00Z');
  assert.deepEqual(result.after, []);
});

test('startup compatibility migration releases only the old notification-time false marker', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    for (const itemId of ['MLB1', 'MLB2']) {
      repo.applyItemSnapshotFromWebhook({
        accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId,
        resource: { id: itemId, price: 120, last_updated: '2026-08-28T02:00:00Z' },
        confirmed: true,
      });
    }
    repo.markItemSnapshotUnconfirmed({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1',
      reason: 'resource_revision_behind_event',
    });
    repo.markItemSnapshotUnconfirmed({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB2',
      reason: 'timeout',
    });
    const migrated = repo.confirmLegacyTimestampComparedItemSnapshots();
    const remaining = repo.listUnconfirmedItemSnapshots();
    console.log(JSON.stringify({ migrated, remaining }));
  `);
  assert.equal(result.migrated.updated, 1);
  assert.deepEqual(result.remaining.map((row) => row.item_id), ['MLB2']);
});

test('legacy baseline marks every cache row without provenance and prioritizes executable relations', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    repo.upsertItemPriceCache({ accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'LEGACY', price: 100 });
    const { getDb } = await import('./src/db.js');
    getDb().prepare('UPDATE item_price_cache SET source_revision = NULL, observed_at = NULL WHERE item_id = ?').run('LEGACY');
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'REVISIONED',
      resource: { id: 'REVISIONED', price: 110, last_updated: '2026-08-31T01:00:00Z' },
      observedAt: '2026-08-31T01:00:01Z', confirmed: true,
    });
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'OBSERVED',
      resource: { id: 'OBSERVED', price: 120 },
      observedAt: '2026-08-31T01:00:02Z', confirmed: true,
    });
    repo.saveItems('A', 'P', 'DEAL', [
      { id: 'LEGACY', status: 'candidate', original_price: 100, price: 100 },
      { id: 'MISSING', status: 'candidate', original_price: 90, price: 90 },
    ], {
      childUserId: 'C', siteId: 'MLB', itemStatus: 'candidate'
    });
    const marked = repo.markLegacyUnverifiedItemSnapshots();
    const before = repo.countUnconfirmedItemSnapshots();
    const queued = repo.listUnconfirmedItemSnapshots({ limit: 10000 });
    const incrementalBefore = repo.countUnconfirmedItemSnapshots({ includeMissingRelations: false });
    const incrementalQueued = repo.listUnconfirmedItemSnapshots({ limit: 10000, includeMissingRelations: false });
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'LEGACY',
      resource: { id: 'LEGACY', price: 130, last_updated: '2026-08-31T02:00:00Z' },
      observedAt: '2026-08-31T02:00:01Z', confirmed: true,
    });
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MISSING',
      resource: { id: 'MISSING', price: 140, last_updated: '2026-08-31T02:00:00Z' },
      observedAt: '2026-08-31T02:00:01Z', confirmed: true,
    });
    const after = repo.countUnconfirmedItemSnapshots();
    console.log(JSON.stringify({ marked, before, queued, incrementalBefore, incrementalQueued, after }));
  `);
  assert.equal(result.marked.updated, 1);
  assert.equal(result.before.total, 2);
  assert.equal(result.before.execution_priority, 2);
  assert.equal(result.before.missing_snapshot, 1);
  assert.deepEqual(new Set(result.queued.map((row) => row.item_id)), new Set(['LEGACY', 'MISSING']));
  assert.equal(result.queued.find((row) => row.item_id === 'MISSING').snapshot_missing, 1);
  assert.ok(result.queued.every((row) => row.execution_priority === 1));
  assert.equal(result.incrementalBefore.total, 1);
  assert.deepEqual(result.incrementalQueued.map((row) => row.item_id), ['LEGACY']);
  assert.equal(result.after.total, 0);
});

test('write-time revalidation recomputes the deal price from the latest confirmed snapshot', () => {
  const latestSnapshot = normalizeItemSnapshot({
    id: 'MLB1', price: 120, original_price: 120, currency_id: 'BRL', status: 'active',
    last_updated: '2026-08-28T02:00:00Z',
  }, { observedAt: '2026-08-28T02:00:01Z' });
  const result = revalidatePlannedRow({
    row: { status: 'planned', item: { item_id: 'MLB1', original_price: 100, price: 100, currency_id: 'BRL', min_discounted_price: 70, max_discounted_price: 90, snapshot_hash: 'old' }, deal_price: 82 },
    latestSnapshot,
    action: 'enroll', priceMode: 'discount', discountPercent: 18, directPrice: null, promotionType: 'DEAL',
    calculateDealPrice, validateDealPrice,
  });
  assert.equal(result.changed, true);
  assert.equal(result.row.item.original_price, 120);
  assert.equal(result.row.deal_price, 98.4);
  assert.equal(result.row.reason, '写入前已按最新商品快照重算');
});

test('failed official resource refresh preserves the snapshot but blocks stale writes as unconfirmed', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    repo.applyItemSnapshotFromWebhook({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1',
      resource: { id: 'MLB1', price: 120, original_price: 120, status: 'active', last_updated: '2026-08-28T02:00:00Z' }
    });
    const marked = repo.markItemSnapshotUnconfirmed({ accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1', reason: 'timeout' });
    const snapshot = repo.getConfirmedItemSnapshot({ accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1' });
    console.log(JSON.stringify({ marked, snapshot }));
  `);
  assert.equal(result.marked.updated, 1);
  assert.equal(result.snapshot.price, 120);
  assert.equal(result.snapshot.confirmed, false);
});

test('executor uses bounded concurrent chunks, persists checkpoints, and writes revalidated rows', async () => {
  const written = [];
  const checkpoints = [];
  const events = [];
  const rows = Array.from({ length: 5 }, (_, index) => ({
    status: 'planned', item: { item_id: `MLB${index + 1}` }, deal_price: 80,
  }));
  const result = await executePlannedRowsWithConcurrency({
    plan: { rows }, action: 'enroll', promotionId: 'P', promotionType: 'DEAL', accountId: 'A', taskId: 1, mode: 'real',
    writeConcurrency: 3, chunkSize: 2,
    beforeExecuteRow: async (row) => ({ changed: true, changed_fields: ['price'], row: { ...row, deal_price: 99 } }),
    executeOne: async ({ itemId, dealPrice }) => { written.push({ itemId, dealPrice }); return { ok: true }; },
    saveResult: async () => {},
    onCheckpoint: async (value) => checkpoints.push(value),
    onItemEvent: async (value) => events.push(value),
  });
  assert.equal(result.counts.success, 5);
  assert.deepEqual(written.map((row) => row.dealPrice), [99, 99, 99, 99, 99]);
  assert.equal(checkpoints.filter((value) => value.type === 'item').length, 5);
  assert.equal(checkpoints.filter((value) => value.type === 'chunk').length, 3);
  assert.equal(events.filter((value) => value.type === 'chunk_done').length, 3);
  assert.equal(events.filter((value) => value.type === 'item_revalidated').length, 5);
});

test('large execution treats enroll/update request acknowledgement as terminal without background readback', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const acknowledged = source.slice(
    source.indexOf("if (action !== 'cancel')"),
    source.indexOf('if (Number(execution?.counts?.success', source.indexOf("if (action !== 'cancel')")),
  );
  assert.match(acknowledged, /write_request_acknowledged/);
  assert.match(acknowledged, /deferred_to_webhook_or_incremental_refresh/);
  assert.match(acknowledged, /request_success_count/);
  assert.doesNotMatch(acknowledged, /confirmAppliedWrites|pendingWriteQueue\.enqueue|background_get_only_queue/);
  assert.match(source, /write_checkpoint/);
  assert.match(source, /async function refreshStartupUnconfirmedItemSnapshots/);
  assert.match(source, /listUnconfirmedItemSnapshots/);
  assert.match(source, /retryDelaysMs: \[\]/);
  assert.match(source, /个变价商品仍待平台同步，涉及商品本批将暂缓/);
  assert.match(source, /markLegacyUnverifiedItemSnapshots/);
  assert.match(source, /limit = 100_000/);
  assert.match(source, /全量商品基线：已核对/);
  assert.match(source, /Math\.min\(24, rows\.length\)/);
  assert.match(source, /readItemSnapshotBaselineCheckpoint/);
  assert.match(source, /includeMissingRelations/);
  assert.match(source, /正在核对商品增量快照/);
  const batchExecutionStart = source.indexOf('execution = await executePlannedRowsWithConcurrency({', source.indexOf('async function executeBatchPlans'));
  const batchExecutionEnd = source.indexOf('saveResult: persistExecutionOutcome', batchExecutionStart);
  const batchExecution = source.slice(batchExecutionStart, batchExecutionEnd);
  assert.match(batchExecution, /beforeExecuteRow: buildWriteTimeItemRevalidator/);
  assert.match(batchExecution, /readItem: \(itemId\) => readWriteTimeItemWithTokenRefresh/);
  assert.match(source, /createWriteTimeItemRevalidator/);
  assert.doesNotMatch(source, /if \(!latestSnapshot\) return \{ row, changed: false, changed_fields: \[\] \}/);
});
