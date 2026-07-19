import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { createAdaptiveWriteScheduler } from '../src/adaptiveWriteScheduler.js';
import { executePlannedRowsWithConcurrency } from '../src/executor.js';
import { createPendingWriteQueue, pendingRelationKey } from '../src/pendingWriteQueue.js';

test('adaptive write scheduler is fair across routes and never uses the requested 350 peak', async () => {
  const scheduler = createAdaptiveWriteScheduler({
    profile: { initialGlobal: 4, maxGlobal: 6, perRoute: 2, minGlobal: 1, successWindow: 100 },
  });
  let active = 0;
  let peak = 0;
  const routeActive = new Map();
  const routePeak = new Map();
  const work = [];
  for (const accountId of ['A', 'B', 'C']) {
    for (let index = 0; index < 8; index += 1) {
      work.push(scheduler.run(async () => {
        active += 1;
        peak = Math.max(peak, active);
        routeActive.set(accountId, Number(routeActive.get(accountId) || 0) + 1);
        routePeak.set(accountId, Math.max(Number(routePeak.get(accountId) || 0), routeActive.get(accountId)));
        await new Promise((resolve) => setTimeout(resolve, 2));
        routeActive.set(accountId, routeActive.get(accountId) - 1);
        active -= 1;
      }, { accountId, siteId: 'MLM' }));
    }
  }
  await Promise.all(work);
  assert.ok(peak <= 4, `peak=${peak}`);
  assert.ok([...routePeak.values()].every((value) => value <= 2), JSON.stringify(Object.fromEntries(routePeak)));
});

test('429 honors Retry-After, halves the global window, and does not lose queued relations', async () => {
  let clock = 0;
  const sleeps = [];
  const scheduler = createAdaptiveWriteScheduler({
    profile: { initialGlobal: 8, maxGlobal: 12, perRoute: 4, minGlobal: 2, successWindow: 100 },
    now: () => clock,
    sleep: async (ms) => { sleeps.push(ms); clock += ms; },
  });
  const error = new Error('rate limited');
  error.status = 429;
  error.retryAfterMs = 2_000;
  await assert.rejects(scheduler.run(async () => { throw error; }, { accountId: 'A', siteId: 'MLM' }));
  await scheduler.run(async () => 'ok', { accountId: 'B', siteId: 'MLM' });
  assert.equal(scheduler.limit, 4);
  assert.ok(sleeps.some((value) => value >= 2_000));
});

test('persistent pending queue survives recreation and resolves each relation exactly once', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-pending-write-'));
  try {
    const key = pendingRelationKey({ accountId: 'A', siteId: 'MLM', promotionId: 'P-1', promotionType: 'DEAL', itemId: 'I-1', action: 'enroll' });
    createPendingWriteQueue({ stateDir: root }).enqueue('J-1', { relation_key: key, row: { item: { item_id: 'I-1' } }, attempt_count: 4 });
    const restarted = createPendingWriteQueue({ stateDir: root });
    assert.equal(restarted.pending('J-1').length, 1);
    restarted.resolve('J-1', key, 'success');
    assert.equal(restarted.pending('J-1').length, 0);
    assert.equal(restarted.load('J-1').records[key].state, 'success');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('recorded rate-limit shape accounts every frozen relation without stopping the account', async () => {
  const saved = [];
  const pending = [];
  let stopRequests = 0;
  const plan = {
    rows: Array.from({ length: 24 }, (_, index) => ({
      status: 'planned',
      item: { item_id: `RECORDED-${index + 1}` },
      deal_price: 90,
    })),
  };
  const result = await executePlannedRowsWithConcurrency({
    plan,
    action: 'enroll',
    promotionId: 'P-RECORDED',
    promotionType: 'DEAL',
    accountId: 'A',
    taskId: 99,
    writeConcurrency: 6,
    executeOne: async ({ itemId }) => {
      if (['RECORDED-7', 'RECORDED-13'].includes(itemId)) {
        const error = new Error('rate limited');
        error.status = 429;
        error.retryAfterMs = 1;
        throw error;
      }
      return { ok: true };
    },
    saveResult: (row) => saved.push(row),
    classifyError: (error) => ({ interfaceFailure: true, rateLimited: error.status === 429, category: 'rate_limited' }),
    onPending: (row) => pending.push(row),
    onStopRequested: () => { stopRequests += 1; },
    retryOptions: { retryBackoffMs: [0, 0, 0], deferredConcurrency: 2 },
  });
  assert.equal(stopRequests, 0);
  assert.equal(result.counts.success, 22);
  assert.equal(result.counts.pending, 2);
  assert.equal(result.counts.success + result.counts.failed + result.counts.skipped + result.counts.pending, 24);
  assert.equal(pending.length, 2);
  assert.equal(saved.filter((row) => row.status === 'pending').length, 2);
});
