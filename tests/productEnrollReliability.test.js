import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

import {
  ADAPTIVE_WRITE_ACTION_PROFILES,
  adaptiveWriteProfileForAction,
  adaptiveWriteProfileForLimit,
  allocateWriteLimitForStore,
  createAdaptiveWriteScheduler,
} from '../src/adaptiveWriteScheduler.js';
import { executePlannedRowsWithConcurrency } from '../src/executor.js';
import { createPendingWriteQueue, pendingRelationKey } from '../src/pendingWriteQueue.js';
import { buildPlan } from '../src/planner.js';
import { isTransientOfferLockError, shouldInvalidateWriteCache } from '../src/writeFailurePolicy.js';
import { applyWriteRepeatGuards, shanghaiDayStartIso } from '../src/writeRepeatGuard.js';

test('offer lock failures are retryable while permanent business rejections are not', () => {
  assert.equal(isTransientOfferLockError({ status: 423, message: 'Locked' }), true);
  assert.equal(isTransientOfferLockError({ status: 400, message: 'LockedEntityException: Offer Locked [MLM1]' }), true);
  assert.equal(isTransientOfferLockError({ status: 400, message: 'Item status is not allowed (under_review)' }), false);
  assert.equal(isTransientOfferLockError({ status: 400, message: 'The discounted price is not credible' }), false);
});

test('normal enroll/update success is terminal while uncertain responses remain platform pending', () => {
  const source = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  const acknowledged = source.slice(
    source.indexOf("if (action !== 'cancel')"),
    source.indexOf('if (Number(execution?.counts?.success', source.indexOf("if (action !== 'cancel')")),
  );
  assert.match(acknowledged, /write_request_acknowledged/);
  assert.match(acknowledged, /execution\.counts\.platform_pending_count = uncertainCount/);
  assert.doesNotMatch(acknowledged, /readIncompleteRows|pendingWriteQueue\.enqueue|confirmAppliedWrites/);
  assert.match(source, /retry_category: 'read_incomplete'/);
  assert.match(source, /'read_incomplete',[\s\S]*?const startsWithReadOnlyVerification/);
  assert.match(source, /retry_category: classifiedError\?\.ambiguousWrite[\s\S]*?'ambiguous_write_readback'/);
  assert.match(source, /'ambiguous_write_readback',[\s\S]*?const startsWithReadOnlyVerification/);
  assert.match(source, /finalCancelRead \|\| action !== 'cancel'[\s\S]*?\? \[\]/);
  assert.match(source, /hasBusinessFailure[\s\S]*?partial_or_failed/);
});

test('only successful or uncertain writes invalidate activity item caches', () => {
  assert.equal(shouldInvalidateWriteCache({ ok: true }), true);
  assert.equal(shouldInvalidateWriteCache({ retryable_failure: true }), true);
  assert.equal(shouldInvalidateWriteCache({ interface_failure: true }), true);
  assert.equal(shouldInvalidateWriteCache({ ok: false, retryable_failure: false, interface_failure: false }), false);
  assert.equal(shouldInvalidateWriteCache({ cancelled: true, ok: true }), false);
});

test('same-day repeat guard skips pending writes and permanent cancel leftovers without resending', () => {
  const basePlan = {
    promotionId: 'P-1',
    promotionType: 'DEAL',
    total: 3,
    planned: 3,
    skipped: 0,
    rows: [
      { status: 'planned', deal_price: 88, item: { item_id: 'I-1' } },
      { status: 'planned', deal_price: 88, item: { item_id: 'I-2' } },
      { status: 'planned', deal_price: 77, item: { item_id: 'I-3' } },
    ],
  };
  const guarded = applyWriteRepeatGuards(basePlan, [
    { promotion_id: 'P-1', promotion_type: 'DEAL', item_id: 'I-1', status: 'pending_verification', deal_price: 88 },
    { promotion_id: 'P-1', promotion_type: 'DEAL', item_id: 'I-2', status: 'failed', deal_price: 88 },
    { promotion_id: 'P-1', promotion_type: 'DEAL', item_id: 'I-3', status: 'failed', deal_price: 66 },
  ], 'update');
  assert.equal(guarded.repeat_guard_skipped, 2);
  assert.deepEqual(guarded.rows.map((row) => row.status), ['skipped', 'skipped', 'planned']);

  const cancel = applyWriteRepeatGuards({
    ...basePlan,
    rows: [{ status: 'planned', item: { item_id: 'I-2' } }],
  }, [{ promotion_id: 'P-1', promotion_type: 'DEAL', item_id: 'I-2', status: 'live_still_started' }], 'cancel');
  assert.equal(cancel.rows[0].status, 'skipped');
  assert.match(cancel.rows[0].reason, /不重复提交/);
  assert.equal(shanghaiDayStartIso(new Date('2026-07-22T03:00:00.000Z')), '2026-07-21T16:00:00.000Z');
});

test('repeat guard keys off the real buildPlan output shape, not synthetic top-level fields', () => {
  // buildPlan() returns promotion nested under plan.promotion; the guard must
  // resolve promotion_id/promotion_type from there to protect real executions.
  const plan = buildPlan({
    action: 'update',
    promotion: { promotion_id: 'P-REAL', promotion_type: 'DEAL', account_id: 'acc-1' },
    items: [
      { item_id: 'I-1', status: 'started', price: 100, original_price: 100, currency_id: 'BRL' },
      { item_id: 'I-2', status: 'started', price: 100, original_price: 100, currency_id: 'BRL' },
    ],
    priceMode: 'discount',
    discountPercent: 5,
  });
  assert.ok(plan.promotion, 'buildPlan output must carry promotion object');
  assert.equal(plan.promotionId, undefined, 'buildPlan must not add synthetic top-level promotionId');
  const guarded = applyWriteRepeatGuards(plan, [
    { promotion_id: 'P-REAL', promotion_type: 'DEAL', item_id: 'I-1', status: 'pending_verification', deal_price: 95 },
  ], 'update');
  assert.equal(guarded.repeat_guard_skipped, 1, 'real plan rows must be matched by promotion nested identity');
  assert.equal(guarded.rows[0].status, 'skipped');
  assert.equal(guarded.rows[1].status, 'planned');
});

test('successful platform write whose result persistence fails stays success and never retries', async () => {
  const plan = buildPlan({
    action: 'update',
    promotion: { promotion_id: 'P-STORAGE', promotion_type: 'DEAL', account_id: 'acc-1' },
    items: [
      { item_id: 'I-1', status: 'started', price: 100, original_price: 100, currency_id: 'BRL' },
      { item_id: 'I-2', status: 'started', price: 100, original_price: 100, currency_id: 'BRL' },
    ],
    priceMode: 'discount',
    discountPercent: 5,
  });
  const writeCalls = [];
  let saveCalls = 0;
  const events = [];
  const execution = await executePlannedRowsWithConcurrency({
    plan,
    action: 'update',
    promotionId: 'P-STORAGE',
    promotionType: 'DEAL',
    accountId: 'acc-1',
    taskId: 'task-1',
    mode: 'real',
    writeConcurrency: 2,
    executeOne: async () => {
      writeCalls.push('write');
      return { ok: true };
    },
    saveResult: async () => {
      saveCalls += 1;
      if (saveCalls === 1) throw new Error('disk full');
      return undefined;
    },
    onItemEvent: (event) => events.push(event),
  });
  // Both rows were written to the platform exactly once and reported success.
  assert.equal(writeCalls.length, 2);
  assert.equal(execution.counts.success, 2);
  assert.equal(execution.counts.failed, 0);
  // The failed persistence is surfaced as a distinct storage event, not a business failure.
  assert.ok(events.some((event) => event.type === 'item_storage_error' && event.status === 'success'));
  // No second write happens for the row whose persistence failed.
  assert.equal(writeCalls.filter(() => true).length, 2);
});

test('server routes transient offer locks to pending recovery in normal and benchmark writes', () => {
  const source = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  const writePolicy = fs.readFileSync(new URL('../src/writeFailurePolicy.js', import.meta.url), 'utf8');
  assert.match(writePolicy, /transientOfferLock = isTransientOfferLockError\(error\)/);
  assert.match(writePolicy, /transientOfferLock \? 'transient_offer_lock'/);
  assert.match(source, /return classifyWriteFailure\(error, \{ toErrorText: toChineseError \}\)/);
  assert.match(source, /is_retryable_failure: isRetryableFailure/);
  assert.match(source, /is_business_failure: !isInterfaceFailure && !isRetryableFailure/);
  assert.match(source, /shouldInvalidateWriteCache\(result\)/);
  assert.match(source, /listLatestWriteRepeatGuards/);
  assert.match(source, /applyWriteRepeatGuards/);
  assert.match(source, /recheckAndCancelRemainingStarted\([\s\S]*schedule: \(runWrite\) => globalWriteLimiter\.run/);
  assert.match(source, /startsWithReadOnlyVerification[\s\S]*跳过普通活动目录和商品准备/);
  assert.match(source, /execution\.retryable_pending_count \+= unresolvedPendingCount \+ retryCount/);
  assert.match(source, /MAX_PENDING_VERIFICATION_READ_ATTEMPTS = 3/);
  assert.match(source, /verification_unknown_after_retries/);
});

test('successful enrollment writes through item status and fetch counts in one activity transaction', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-enroll-cache-write-through-'));
  try {
    const script = `
      const repo = await import('./src/repository.js');
      const identity = { accountId: 'A', promotionId: 'P-1', promotionType: 'DEAL' };
      repo.saveItems('A', 'P-1', 'DEAL', [
        { item_id: 'I-1', status: 'candidate', price: 100, raw: {} },
        { item_id: 'I-2', status: 'candidate', price: 200, raw: {} },
        { item_id: 'I-3', status: 'started', price: 90, raw: {} },
      ]);
      repo.saveItemFetchState({ ...identity, itemStatus: 'candidate', platformTotal: 2, savedCount: 2, detailStatus: 'full' });
      repo.saveItemFetchState({ ...identity, itemStatus: 'started', platformTotal: 1, savedCount: 1, detailStatus: 'full' });
      repo.applySuccessfulPromotionItemWrites({
        ...identity,
        action: 'enroll',
        items: [{ itemId: 'I-1', dealPrice: 88 }],
      });
      repo.reconcilePromotionItemFetchCounts(identity);
      console.log(JSON.stringify({
        candidate: repo.listItems('A', 'P-1', 'DEAL', 'candidate').map((row) => row.item_id),
        started: repo.listItems('A', 'P-1', 'DEAL', 'started').map((row) => [row.item_id, row.price]),
        candidateState: repo.getItemFetchState('A', 'P-1', 'DEAL', 'candidate') ?? null,
        startedState: repo.getItemFetchState('A', 'P-1', 'DEAL', 'started') ?? null,
      }));
    `;
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
      cwd: process.cwd(),
      env: { ...process.env, MDM_DATA_DIR: dataDir },
      encoding: 'utf8',
      timeout: 30_000,
    });
    assert.equal(child.status, 0, child.stderr);
    const result = JSON.parse(child.stdout.trim().split(/\r?\n/).at(-1));
    assert.deepEqual(result.candidate, ['I-2']);
    assert.deepEqual(result.started, [['I-1', 88], ['I-3', 90]]);
    assert.equal(result.candidateState.saved_count, 1);
    assert.equal(result.candidateState.platform_total, 1);
    assert.equal(result.startedState.saved_count, 2);
    assert.equal(result.startedState.platform_total, 2);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('a platform write invalidates both candidate and started fetch states without deleting cached rows', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-write-cross-status-invalidation-'));
  try {
    const script = `
      const repo = await import('./src/repository.js');
      const identity = { accountId: 'A', promotionId: 'P-1', promotionType: 'DEAL' };
      repo.saveItems('A', 'P-1', 'DEAL', [
        { item_id: 'I-1', status: 'candidate', price: 100, raw: {} },
        { item_id: 'I-2', status: 'started', price: 90, raw: {} },
      ]);
      repo.saveItemFetchState({ ...identity, itemStatus: 'candidate', platformTotal: 1, savedCount: 1, detailStatus: 'full' });
      repo.saveItemFetchState({ ...identity, itemStatus: 'started', platformTotal: 1, savedCount: 1, detailStatus: 'full' });
      repo.invalidatePromotionItemFetchStates(identity);
      console.log(JSON.stringify({
        candidateState: repo.getItemFetchState('A', 'P-1', 'DEAL', 'candidate') ?? null,
        startedState: repo.getItemFetchState('A', 'P-1', 'DEAL', 'started') ?? null,
        candidateItems: repo.listItems('A', 'P-1', 'DEAL', 'candidate').map((row) => row.item_id),
        startedItems: repo.listItems('A', 'P-1', 'DEAL', 'started').map((row) => row.item_id),
      }));
    `;
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
      cwd: process.cwd(),
      env: { ...process.env, MDM_DATA_DIR: dataDir },
      encoding: 'utf8',
      timeout: 30_000,
    });
    assert.equal(child.status, 0, child.stderr);
    const result = JSON.parse(child.stdout.trim().split(/\r?\n/).at(-1));
    assert.equal(result.candidateState, null);
    assert.equal(result.startedState, null);
    assert.deepEqual(result.candidateItems, ['I-1']);
    assert.deepEqual(result.startedItems, ['I-2']);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

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

test('execution-group write budget is partitioned across stores without exceeding the group ceiling', () => {
  const allocations = [0, 1, 2].map((index) => allocateWriteLimitForStore(160, 3, index));
  assert.deepEqual(allocations, [54, 53, 53]);
  assert.equal(allocations.reduce((sum, value) => sum + value, 0), 160);
  assert.equal(allocateWriteLimitForStore(128, 3, 0), 43);
  assert.equal(allocateWriteLimitForStore(128, 3, 2), 42);
});

test('one store overload does not lower or cool down another store scheduler', async () => {
  let firstClock = 0;
  let secondClock = 0;
  const profile = {
    initialGlobal: 4,
    maxGlobal: 4,
    perRoute: 4,
    minGlobal: 1,
    successWindow: 100,
    overloadDecreaseStep: 2,
    defaultTransientCooldownMs: 1,
  };
  const firstStore = createAdaptiveWriteScheduler({
    profile,
    now: () => firstClock,
    sleep: async (ms) => { firstClock += ms; },
  });
  const secondStore = createAdaptiveWriteScheduler({
    profile,
    now: () => secondClock,
    sleep: async (ms) => { secondClock += ms; },
  });
  await assert.rejects(firstStore.run(async () => {
    throw Object.assign(new Error('service unavailable'), { status: 503 });
  }, { accountId: 'A', siteId: 'MLB' }));
  assert.equal(firstStore.limit, 2);
  assert.equal(secondStore.limit, 4);
  await secondStore.run(async () => 'ok', { accountId: 'B', siteId: 'MLM' });
  assert.equal(secondStore.snapshot().overload_count, 0);
});

test('execution groups wire one adaptive scheduler per account behind one non-adaptive group guard', () => {
  const serverSource = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  assert.match(serverSource, /getSharedWriteLimiter\(request\.executionGroupId, account\.account_id, action/);
  assert.match(serverSource, /const key = `\$\{groupKey\}\|\$\{normalizedAccountId\}`/);
  assert.match(serverSource, /storeLimiter\.run\(\(\) => groupGuard\.run\(fn\), meta\)/);
  assert.match(serverSource, /deleteSharedWriteLimitersForGroup\(current\.id\)/);
});

test('real full-volume evidence defines independent action write ceilings', () => {
  assert.deepEqual(
    Object.fromEntries(Object.entries(ADAPTIVE_WRITE_ACTION_PROFILES).map(([action, profile]) => [
      action,
      { initialGlobal: profile.initialGlobal, maxGlobal: profile.maxGlobal, perRoute: profile.perRoute },
    ])),
    {
      cancel: { initialGlobal: 160, maxGlobal: 160, perRoute: 54 },
      enroll: { initialGlobal: 160, maxGlobal: 160, perRoute: 28 },
      update: { initialGlobal: 128, maxGlobal: 128, perRoute: 28 },
    },
  );
  assert.equal(adaptiveWriteProfileForAction('cancel', 192).maxGlobal, 160);
  assert.equal(adaptiveWriteProfileForAction('enroll', 192).maxGlobal, 160);
  assert.equal(ADAPTIVE_WRITE_ACTION_PROFILES.enroll.successWindow, 1000);
  assert.equal(ADAPTIVE_WRITE_ACTION_PROFILES.enroll.defaultRateLimitCooldownMs, 15_000);
  assert.equal(adaptiveWriteProfileForAction('update', 192).maxGlobal, 128);
  assert.equal(adaptiveWriteProfileForAction('enroll', 100).initialGlobal, 100);
  assert.equal(adaptiveWriteProfileForAction('update', 20).perRoute, 20);
});

test('saved write setting remains a compatibility ceiling', () => {
  assert.equal(adaptiveWriteProfileForLimit(100).maxGlobal, 100);
  assert.equal(adaptiveWriteProfileForLimit(100).initialGlobal, 100);
  assert.equal(adaptiveWriteProfileForLimit(5).maxGlobal, 5);
  assert.equal(adaptiveWriteProfileForLimit(1).initialGlobal, 1);
  const serverSource = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  assert.match(serverSource, /adaptiveWriteProfileForAction\(action, requestedGlobalWriteConcurrency\)/);
});

test('429 honors Retry-After, lowers one write tier, and does not lose queued relations', async () => {
  let clock = 0;
  const sleeps = [];
  const scheduler = createAdaptiveWriteScheduler({
    profile: { initialGlobal: 8, maxGlobal: 12, perRoute: 4, minGlobal: 2, successWindow: 100, overloadDecreaseStep: 4 },
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

test('network service and timeout overloads each lower one write tier without account-wide stop', async () => {
  for (const error of [
    Object.assign(new Error('fetch failed'), { cause: { code: 'UND_ERR_CONNECT_TIMEOUT' } }),
    Object.assign(new Error('service unavailable'), { status: 503 }),
    Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' }),
  ]) {
    let clock = 0;
    const scheduler = createAdaptiveWriteScheduler({
      profile: {
        initialGlobal: 160,
        maxGlobal: 160,
        perRoute: 54,
        minGlobal: 16,
        successWindow: 100,
        overloadDecreaseStep: 16,
        defaultTransientCooldownMs: 1,
      },
      now: () => clock,
      sleep: async (ms) => { clock += ms; },
    });
    await assert.rejects(scheduler.run(async () => { throw error; }, { accountId: 'A', siteId: 'MLM' }));
    assert.equal(scheduler.limit, 144);
    assert.equal(scheduler.snapshot().overload_count, 1);
  }
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

test('successful writes are classified before any bounded semantic retry', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  assert.match(serverSource, /retry_category:\s*'pending_verification'/);
  assert.match(serverSource, /recoverPendingVerificationRecords\(/);
  const recoveryBody = serverSource.split('async function recoverPendingVerificationRecords')[1]
    .split('function finalizeExecutionJob')[0];
  assert.match(recoveryBody, /confirmAppliedWrites/);
  assert.match(serverSource, /status:\s*'candidate'/);
  assert.match(serverSource, /status:\s*'pending'/);
  assert.match(serverSource, /platform_pending/);
  assert.match(serverSource, /平台已明确返回 pending/);
  assert.match(serverSource, /started_price_mismatch/);
  assert.match(serverSource, /confirmed_candidate_after_write/);
  assert.match(serverSource, /previousAttempts < MAX_CONFIRMED_CANDIDATE_WRITE_ATTEMPTS/);
  assert.doesNotMatch(recoveryBody, /executeOnePlannedWithTokenRefresh|client\.enrollItem|client\.updateItem|client\.cancelItem/);
  assert.match(recoveryBody, /repeated_write_requests:\s*0/);
});

test('readback helper remains bounded for explicit recovery while normal writes do not call it', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  // The unified confirmAppliedWrites entry point exists and gates on complete reads.
  assert.match(serverSource, /async function confirmAppliedWrites/);
  assert.match(serverSource, /const completeRead = Boolean\(startedComplete && candidateComplete && pendingComplete\)/);
  // Incomplete reads return read_incomplete rows and clear every verdict category.
  assert.match(serverSource, /verdict: 'read_incomplete'/);
  assert.match(serverSource, /read_incomplete: rows \|\| \[\]/);
  assert.match(serverSource, /verified: \[\],\s*started_price_mismatch: \[\],\s*confirmed_pending: \[\],\s*confirmed_candidate: \[\],\s*unresolved: \[\]/);
  // Explicit recovery/cancellation may still use this helper, but the normal
  // enroll/update acknowledgement path must not.
  const confirmCalls = (serverSource.match(/confirmAppliedWrites\(\{/g) || []).length;
  assert.ok(confirmCalls >= 1, `expected recovery confirmAppliedWrites call sites, got ${confirmCalls}`);
  const acknowledged = serverSource.slice(
    serverSource.indexOf("if (action !== 'cancel')"),
    serverSource.indexOf('if (Number(execution?.counts?.success', serverSource.indexOf("if (action !== 'cancel')")),
  );
  assert.doesNotMatch(acknowledged, /confirmAppliedWrites|pendingWriteQueue\.enqueue/);
  // Legacy read-incomplete recovery remains GET-only and never repeats writes.
  assert.match(serverSource, /retry_category: 'read_incomplete'/);
  assert.match(serverSource, /平台回读不完整[\s\S]*?不会重复写入/);
  // The sync read-back no longer has a 200-row skip.
  assert.doesNotMatch(serverSource, /smallEnoughToReadBack/);
  assert.doesNotMatch(serverSource, /write_response_trusted/);
  assert.match(serverSource, /const pendingComplete = completeness\.pending === true/);
});

test('clean cached catalog absence can create seller activity and enrollment preserves real started counts', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  assert.match(serverSource, /confirmedAbsent = Boolean\(\(calibratedFromLive \|\| calibratedFromCache\) && sellerCount === 0\)/);
  assert.match(serverSource, /Webhook 本地活动缓存完整且无变更/);
  assert.match(serverSource, /const startedItems = listItems\([\s\S]*?platformTotal: startedItems\.length,[\s\S]*?savedCount: startedItems\.length/);
  assert.doesNotMatch(serverSource, /报名执行后本地 started 状态已更新[\s\S]*?platformTotal: null/);
});

test('a transient item read error preserves the previous complete fetch baseline', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  const fetchSource = serverSource.slice(
    serverSource.indexOf('async function fetchAndSavePromotionItemsForCampaign'),
    serverSource.indexOf('function shouldPreserveExistingFetchState'),
  );
  assert.match(fetchSource, /const preservedCompleteState = isStateFull\(existingState\)/);
  assert.match(fetchSource, /if \(!preservedCompleteState\) \{[\s\S]*?saveItemFetchState/);
  assert.match(fetchSource, /已保留此前完整本地基线/);
});

test('pending recovery closes empty queues idempotently and preserves terminalized accounting', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  const recoverySource = fs.readFileSync(path.join(process.cwd(), 'scripts/recover-pending-verification-group.mjs'), 'utf8');

  assert.match(serverSource, /request\.resumePendingOnly && startupPendingRecords\.length === 0/);
  assert.match(serverSource, /recovery_queue_mismatch:\s*true/);
  assert.match(serverSource, /未回落普通执行/);
  assert.match(serverSource, /执行结果已幂等收口，未重复提交商品/);
  assert.match(recoverySource, /isVerificationExhausted/);
  assert.match(recoverySource, /failed_terminalized_by_job/);
  assert.match(recoverySource, /group\.result = summarizeExecutionGroup\(group\)/);
  assert.match(recoverySource, /job\.status = pendingCount > 0 \? 'paused' : 'completed'/);
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

test('definite business failure is not retried even when a later call would recover', async () => {
  let calls = 0;
  const saved = [];
  const events = [];
  const result = await executePlannedRowsWithConcurrency({
    plan: { rows: [{ status: 'planned', item: { item_id: 'BUSINESS-RECOVER' }, deal_price: null }] },
    action: 'cancel', promotionId: 'P', promotionType: 'DEAL', accountId: 'A', taskId: 1, mode: 'real',
    executeOne: async () => {
      calls += 1;
      if (calls < 4) {
        const error = new Error('definite business rejection');
        error.status = 400;
        throw error;
      }
      return { ok: true };
    },
    saveResult: (row) => saved.push(row),
    classifyError: () => ({ interfaceFailure: false, category: 'business_failure' }),
    onItemEvent: (event) => events.push(event),
    retryOptions: {
      maxImmediateRetries: 3,
      maxFinalFailureRetries: 3,
      finalFailureBackoffMs: [0, 0, 0],
      finalFailureConcurrency: 1,
    },
  });
  assert.equal(calls, 1);
  assert.equal(result.counts.success, 0);
  assert.equal(result.counts.failed, 1);
  assert.equal(result.counts.skipped, 0);
  assert.equal(result.retrySummary.final_failure_retry_attempts, 0);
  assert.equal(result.retrySummary.final_failure_retry_success, 0);
  assert.equal(saved.at(-1).status, 'failed');
  assert.deepEqual(events.filter((event) => event.type === 'final_failure_retry_start'), []);
});

test('definite business failure is recorded once without batch-end retries', async () => {
  let calls = 0;
  const saved = [];
  const result = await executePlannedRowsWithConcurrency({
    plan: { rows: [{ status: 'planned', item: { item_id: 'BUSINESS-EXHAUSTED' }, deal_price: null }] },
    action: 'cancel', promotionId: 'P', promotionType: 'DEAL', accountId: 'A', taskId: 1, mode: 'real',
    executeOne: async () => {
      calls += 1;
      const error = new Error('definite business rejection');
      error.status = 400;
      throw error;
    },
    saveResult: (row) => saved.push(row),
    classifyError: () => ({ interfaceFailure: false, category: 'business_failure' }),
    retryOptions: {
      maxImmediateRetries: 3,
      maxFinalFailureRetries: 3,
      finalFailureBackoffMs: [0, 0, 0],
      finalFailureConcurrency: 1,
    },
  });
  assert.equal(calls, 1);
  assert.equal(result.counts.success, 0);
  assert.equal(result.counts.failed, 1);
  assert.equal(result.counts.skipped, 0);
  assert.equal(result.retrySummary.final_failure_retry_attempts, 0);
  assert.equal(result.retrySummary.final_failure_retry_failed, 0);
  assert.equal(saved.at(-1).status, 'failed');
  assert.equal(saved.at(-1).errorCn, 'definite business rejection');
});
