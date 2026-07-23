import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';

import {
  BALANCED_READ_PROFILES,
  BalancedReadScheduler,
  buildReadConcurrencyReport,
  createBalancedReadScheduler,
  selectReadConcurrencyProfile,
} from '../src/balancedReadScheduler.js';

const wait = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

test('balanced scheduler caps global, account, detail and fallback concurrency while serving accounts fairly', async () => {
  const scheduler = new BalancedReadScheduler({
    initialLimit: 6,
    maxLimit: 10,
    perAccountLimit: 4,
    detailLimit: 4,
    fallbackPerAccount: 1,
    successesPerIncrease: 1_000,
  });
  const activeByAccount = new Map();
  const peakByAccount = new Map();
  let active = 0;
  let peak = 0;
  const starts = [];

  const jobs = ['A', 'B', 'C'].flatMap((accountId) => Array.from({ length: 12 }, (_, index) => (
    scheduler.schedule({ accountId, key: `${accountId}-${index}`, kind: index % 2 ? 'detail' : 'read' }, async () => {
      starts.push(accountId);
      active += 1;
      peak = Math.max(peak, active);
      const next = Number(activeByAccount.get(accountId) || 0) + 1;
      activeByAccount.set(accountId, next);
      peakByAccount.set(accountId, Math.max(Number(peakByAccount.get(accountId) || 0), next));
      await wait(4);
      active -= 1;
      activeByAccount.set(accountId, next - 1);
      return `${accountId}-${index}`;
    })
  )));
  await Promise.all(jobs);

  assert.ok(peak <= 6, `initial global peak was ${peak}`);
  assert.ok([...peakByAccount.values()].every((value) => value <= 4));
  assert.ok(scheduler.snapshot().peakDetail <= 4);
  assert.deepEqual(new Set(starts.slice(0, 6)), new Set(['A', 'B', 'C']));

  let fallbackActive = 0;
  let fallbackPeak = 0;
  await Promise.all(Array.from({ length: 5 }, (_, index) => scheduler.withFallback({
    accountId: 'A', key: `fallback-${index}`,
  }, async () => {
    fallbackActive += 1;
    fallbackPeak = Math.max(fallbackPeak, fallbackActive);
    await scheduler.schedule({ accountId: 'A', key: `fallback-detail-${index}`, kind: 'detail' }, () => wait(2));
    fallbackActive -= 1;
  })));
  assert.equal(fallbackPeak, 1);
});

test('balanced scheduler deduplicates promise keys and aborts queued work', async () => {
  const scheduler = new BalancedReadScheduler({ initialLimit: 1, maxLimit: 1 });
  let calls = 0;
  let release;
  const blocker = scheduler.schedule({ accountId: 'A', key: 'blocker' }, async () => {
    await new Promise((resolve) => { release = resolve; });
  });
  const first = scheduler.schedule({ accountId: 'B', key: 'same' }, async () => { calls += 1; return 42; });
  const second = scheduler.schedule({ accountId: 'B', key: 'same' }, async () => { calls += 1; return 99; });
  const controller = new AbortController();
  const aborted = scheduler.schedule({ accountId: 'C', key: 'aborted', signal: controller.signal }, async () => 1);
  const abortedCheck = assert.rejects(aborted, (error) => error?.name === 'AbortError');
  controller.abort();
  await new Promise((resolve) => setImmediate(resolve));
  release();
  await blocker;
  assert.deepEqual(await Promise.all([first, second]), [42, 42]);
  assert.equal(calls, 1);
  await abortedCheck;
});

test('balanced scheduler applies Retry-After, lowers one level and uses account backoff for transient failures', async () => {
  const sleeps = [];
  const scheduler = new BalancedReadScheduler({
    initialLimit: 6,
    maxLimit: 10,
    sleep: async (ms) => { sleeps.push(ms); },
    random: () => 0,
    now: (() => { let value = 0; return () => value += 10_000; })(),
    successesPerIncrease: 2,
  });
  let rateAttempts = 0;
  const rateResult = await scheduler.schedule({ accountId: 'A', key: 'rate' }, async () => {
    rateAttempts += 1;
    if (rateAttempts === 1) {
      const error = new Error('limited');
      error.status = 429;
      error.retryAfterMs = 3_000;
      throw error;
    }
    return 'ok';
  });
  assert.equal(rateResult, 'ok');
  assert.equal(rateAttempts, 2);
  assert.ok(sleeps.includes(3_000));
  assert.equal(scheduler.snapshot().dynamicLimit, 5);

  let networkAttempts = 0;
  await scheduler.schedule({ accountId: 'B', key: 'network' }, async () => {
    networkAttempts += 1;
    if (networkAttempts < 4) {
      const error = new Error('timeout');
      error.code = 'ETIMEDOUT';
      throw error;
    }
    return true;
  });
  assert.equal(networkAttempts, 4);
  assert.ok(sleeps.includes(1_000));
  assert.ok(sleeps.includes(2_000));
  assert.ok(sleeps.includes(4_000));
});

async function runRecordedReadBatch(options) {
  const scheduler = createBalancedReadScheduler({ ...options, successesPerIncrease: 1_000 });
  const startedAt = Date.now();
  await Promise.all(['A', 'B', 'C'].flatMap((accountId) => Array.from({ length: 12 }, (_, index) => (
    scheduler.schedule({ accountId, key: `${accountId}-${index}`, kind: 'detail' }, () => wait(12))
  ))));
  return { elapsed_ms: Date.now() - startedAt, snapshot: scheduler.snapshot() };
}

test('high-throughput prepare profile raises only Mercado GET ceilings and bounds fallback per account', async () => {
  assert.deepEqual(BALANCED_READ_PROFILES.balanced, {
    initialLimit: 8,
    maxLimit: 12,
    perAccountLimit: 4,
    detailLimit: 6,
    fallbackPerAccount: 1,
  });
  assert.deepEqual(BALANCED_READ_PROFILES.prepare, {
    initialLimit: 192,
    maxLimit: 192,
    perAccountLimit: 64,
    detailLimit: 125,
    detailPerAccountLimit: 42,
    activityLimit: 192,
    activityPerAccountLimit: 64,
    fallbackPerAccount: 2,
    minLimit: 10,
    rateLimitDecreaseStep: 5,
  });
  const scheduler = createBalancedReadScheduler(BALANCED_READ_PROFILES.prepare);
  assert.equal(scheduler.snapshot().dynamicLimit, 192);

  const baseline = await runRecordedReadBatch(BALANCED_READ_PROFILES.compatibility);
  const balanced = await runRecordedReadBatch(BALANCED_READ_PROFILES.balanced);
  const high = await runRecordedReadBatch(BALANCED_READ_PROFILES.prepare);
  assert.equal(baseline.snapshot.peakDetail, 4);
  assert.equal(balanced.snapshot.peakDetail, 6);
  assert.equal(high.snapshot.peakDetail, 36);
  assert.ok(high.snapshot.peakInflight <= 192);
  assert.ok(high.snapshot.peakDetail <= 125);
  const baselineWaves = Math.ceil(36 / baseline.snapshot.peakDetail);
  const balancedWaves = Math.ceil(36 / balanced.snapshot.peakDetail);
  const highWaves = Math.ceil(36 / high.snapshot.peakDetail);
  assert.ok(balancedWaves <= baselineWaves * 0.7, `baselineWaves=${baselineWaves} balancedWaves=${balancedWaves}`);
  assert.ok(highWaves <= baselineWaves * 0.4, `baselineWaves=${baselineWaves} highWaves=${highWaves}`);

  let fallbackActive = 0;
  let fallbackPeak = 0;
  await Promise.all(Array.from({ length: 6 }, (_, index) => scheduler.withFallback({
    accountId: 'A', key: `prepare-fallback-${index}`,
  }, async () => {
    fallbackActive += 1;
    fallbackPeak = Math.max(fallbackPeak, fallbackActive);
    await wait(4);
    fallbackActive -= 1;
  })));
  assert.equal(fallbackPeak, 2);
});

test('prepare scheduler profile applies saved global and activity ceilings without exceeding verified limits', async () => {
  const { prepareReadSchedulerProfile } = await import('../src/balancedReadScheduler.js');
  assert.deepEqual(prepareReadSchedulerProfile({ readConcurrency: 200, previewConcurrency: 999 }), {
    ...BALANCED_READ_PROFILES.prepare,
    initialLimit: 192,
    maxLimit: 192,
    perAccountLimit: 64,
    detailLimit: 125,
    detailPerAccountLimit: 42,
    activityLimit: 192,
    activityPerAccountLimit: 64,
  });
  assert.deepEqual(prepareReadSchedulerProfile({ readConcurrency: 18, previewConcurrency: 3 }), {
    ...BALANCED_READ_PROFILES.prepare,
    initialLimit: 18,
    maxLimit: 18,
    perAccountLimit: 18,
    detailLimit: 18,
    detailPerAccountLimit: 18,
    activityLimit: 3,
    activityPerAccountLimit: 3,
  });
});

test('balanced scheduler separately caps activity-directory reads while detail reads share the global ceiling', async () => {
  const scheduler = createBalancedReadScheduler({ initialLimit: 8, maxLimit: 8, perAccountLimit: 8, detailLimit: 8, activityLimit: 2, successesPerIncrease: 1_000 });
  let activeActivity = 0;
  let peakActivity = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const activities = Array.from({ length: 6 }, (_, index) => scheduler.schedule({ accountId: `A${index}`, key: `activity-${index}`, kind: 'activity' }, async () => {
    activeActivity += 1;
    peakActivity = Math.max(peakActivity, activeActivity);
    await gate;
    activeActivity -= 1;
  }));
  await wait(10);
  assert.equal(peakActivity, 2);
  assert.equal(scheduler.snapshot().activityLimit, 2);
  release();
  await Promise.all(activities);
});

test('prepare scheduler can use activity 192 while detail remains capped at verified 125 and 42 per account', async () => {
  const scheduler = createBalancedReadScheduler({
    ...BALANCED_READ_PROFILES.prepare,
    successesPerIncrease: 1_000,
  });
  let releaseActivity;
  const activityGate = new Promise((resolve) => { releaseActivity = resolve; });
  const activityJobs = ['A', 'B', 'C'].flatMap((accountId) => Array.from({ length: 64 }, (_, index) => (
    scheduler.schedule({ accountId, key: `activity-max-${accountId}-${index}`, kind: 'activity' }, () => activityGate)
  )));
  await wait(20);
  const activitySnapshot = scheduler.snapshot();
  assert.equal(activitySnapshot.inflight, 192);
  assert.equal(activitySnapshot.activityInflight, 192);
  assert.deepEqual(Object.values(activitySnapshot.accountInflight).sort((a, b) => a - b), [64, 64, 64]);
  releaseActivity();
  await Promise.all(activityJobs);

  let releaseDetail;
  const detailGate = new Promise((resolve) => { releaseDetail = resolve; });
  const detailJobs = ['A', 'B', 'C'].flatMap((accountId) => Array.from({ length: 60 }, (_, index) => (
    scheduler.schedule({ accountId, key: `detail-max-${accountId}-${index}`, kind: 'detail' }, () => detailGate)
  )));
  await wait(20);
  const detailSnapshot = scheduler.snapshot();
  assert.equal(detailSnapshot.inflight, 125);
  assert.equal(detailSnapshot.detailInflight, 125);
  assert.deepEqual(Object.values(detailSnapshot.accountInflight).sort((a, b) => a - b), [41, 42, 42]);
  releaseDetail();
  await Promise.all(detailJobs);
});

test('high-throughput profile reaches its starting global window without breaking account fairness', async () => {
  const scheduler = createBalancedReadScheduler({
    ...BALANCED_READ_PROFILES.prepare,
    successesPerIncrease: 1_000,
  });
  const activeByAccount = new Map();
  const peakByAccount = new Map();
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const jobs = ['A', 'B', 'C'].flatMap((accountId) => Array.from({ length: 50 }, (_, index) => (
    scheduler.schedule({ accountId, key: `peak-${accountId}-${index}`, kind: 'detail' }, async () => {
      const active = Number(activeByAccount.get(accountId) || 0) + 1;
      activeByAccount.set(accountId, active);
      peakByAccount.set(accountId, Math.max(Number(peakByAccount.get(accountId) || 0), active));
      await gate;
      activeByAccount.set(accountId, active - 1);
    })
  )));
  await wait(10);
  const running = scheduler.snapshot();
  assert.equal(running.inflight, 125);
  assert.equal(running.detailInflight, 125);
  assert.deepEqual(new Set(Object.keys(running.accountInflight).filter((key) => running.accountInflight[key] > 0)), new Set(['A', 'B', 'C']));
  assert.deepEqual([...peakByAccount.values()].sort((left, right) => left - right), [41, 42, 42]);
  release();
  await Promise.all(jobs);
});

test('scheduler metrics name only Mercado outbound work and report p95, retry and throughput', async () => {
  const scheduler = new BalancedReadScheduler({
    initialLimit: 2,
    maxLimit: 2,
    successesPerIncrease: 100,
    sleep: async () => {},
    random: () => 0,
  });
  let attempts = 0;
  await scheduler.schedule({ accountId: 'A', key: 'outbound-get' }, async () => {
    attempts += 1;
    if (attempts === 1) {
      const error = new Error('rate limited');
      error.status = 429;
      error.retryAfterMs = 0;
      throw error;
    }
    await wait(2);
    return true;
  });
  const snapshot = scheduler.snapshot();
  assert.equal(snapshot.mercado_outbound_inflight, 0);
  assert.equal(snapshot.mercado_outbound_peak, 1);
  assert.equal(snapshot.mercado_outbound_request_count, 2);
  assert.equal(snapshot.mercado_outbound_retry_count, 1);
  assert.equal(snapshot.mercado_outbound_rate_limit_count, 1);
  assert.equal(snapshot.mercado_outbound_failure_count, 0);
  assert.ok(snapshot.mercado_outbound_p95_ms >= 0);
  assert.ok(snapshot.mercado_outbound_throughput_per_second >= 0);

  const report = buildReadConcurrencyReport({
    schedulerSnapshot: snapshot,
    localWorkConcurrency: 3,
    localDbBatchQueries: 2,
  });
  assert.equal(report.local_work_concurrency, 3);
  assert.equal(report.local_db_batch_queries, 2);
  assert.equal(report.mercado_outbound_dynamic_limit, 1);
  assert.equal(report.mercado_outbound_max_limit, 2);
  assert.equal(report.mercado_outbound_inflight, 0);
  assert.equal(report.mercado_outbound_peak, 1);
  assert.equal(report.mercado_outbound_rate_limit_count, 1);
  assert.equal(report.mercado_outbound_network_error_count, 0);
  assert.equal(report.mercado_outbound_service_error_count, 0);
  assert.equal(report.mercado_outbound_timeout_error_count, 0);
  assert.equal('read_concurrency' in report, false);
});

test('recorded benchmark selector adopts high profile only when elapsed improves without worse 429 or failures', () => {
  assert.equal(selectReadConcurrencyProfile({
    compatibility: { elapsed_ms: 1_000, rate_limit_count: 0, failure_count: 0 },
    balanced: { elapsed_ms: 760, rate_limit_count: 0, failure_count: 0 },
    high: { elapsed_ms: 560, rate_limit_count: 0, failure_count: 0 },
  }), 'high');
  assert.equal(selectReadConcurrencyProfile({
    compatibility: { elapsed_ms: 1_000, rate_limit_count: 0, failure_count: 0 },
    balanced: { elapsed_ms: 720, rate_limit_count: 0, failure_count: 0 },
    high: { elapsed_ms: 500, rate_limit_count: 1, failure_count: 0 },
  }), 'balanced');
  assert.equal(selectReadConcurrencyProfile({
    compatibility: { elapsed_ms: 1_000, rate_limit_count: 0, failure_count: 0 },
    balanced: { elapsed_ms: 930, rate_limit_count: 0, failure_count: 0 },
    high: { elapsed_ms: 850, rate_limit_count: 0, failure_count: 1 },
  }), 'compatibility');
});

test('high-throughput prepare profile lowers by five on 429 and keeps Retry-After cooldown', async () => {
  const sleeps = [];
  const scheduler = createBalancedReadScheduler({
    ...BALANCED_READ_PROFILES.prepare,
    sleep: async (ms) => { sleeps.push(ms); },
    random: () => 0,
    now: (() => { let value = 0; return () => value += 10_000; })(),
  });
  let attempts = 0;
  await scheduler.schedule({ accountId: 'A', key: 'candidate-page', kind: 'detail' }, async () => {
    attempts += 1;
    if (attempts === 1) {
      const error = new Error('limited');
      error.status = 429;
      error.retryAfterMs = 2_500;
      throw error;
    }
    return true;
  });
  assert.equal(attempts, 2);
  assert.ok(sleeps.includes(2_500));
  assert.equal(scheduler.snapshot().dynamicLimit, 187);
  assert.equal(scheduler.snapshot().globalCooldownRemainingMs, 0);
});

test('sustained 429 responses step down by five repeatedly without exceeding the tested ceiling', async () => {
  const scheduler = createBalancedReadScheduler({
    ...BALANCED_READ_PROFILES.prepare,
    sleep: async () => {},
    random: () => 0,
    now: (() => { let value = 0; return () => value += 10_000; })(),
    successesPerIncrease: 1_000,
  });
  let attempts = 0;
  await scheduler.schedule({ accountId: 'A', key: 'repeated-limit', kind: 'detail' }, async () => {
    attempts += 1;
    if (attempts <= 3) {
      const error = new Error('limited');
      error.status = 429;
      throw error;
    }
    return true;
  });
  assert.equal(attempts, 4);
  assert.equal(scheduler.snapshot().dynamicLimit, 177);
  assert.equal(scheduler.snapshot().mercado_outbound_rate_limit_count, 3);
  assert.equal(scheduler.snapshot().mercado_outbound_retry_count, 3);
});

for (const [kind, makeError, metric] of [
  ['network', () => Object.assign(new Error('fetch failed'), { code: 'ECONNRESET' }), 'mercado_outbound_network_error_count'],
  ['service', () => Object.assign(new Error('service unavailable'), { status: 503 }), 'mercado_outbound_service_error_count'],
  ['timeout', () => Object.assign(new Error('request timed out'), { code: 'ETIMEDOUT' }), 'mercado_outbound_timeout_error_count'],
]) {
  test(`prepare scheduler lowers by five on ${kind} overload and records its category`, async () => {
    const scheduler = createBalancedReadScheduler({
      ...BALANCED_READ_PROFILES.prepare,
      sleep: async () => {},
      random: () => 0,
      successesPerIncrease: 1_000,
    });
    let attempts = 0;
    await scheduler.schedule({ accountId: 'A', key: `${kind}-overload` }, async () => {
      attempts += 1;
      if (attempts === 1) throw makeError();
      return true;
    });
    const snapshot = scheduler.snapshot();
    assert.equal(snapshot.dynamicLimit, 187);
    assert.equal(snapshot[metric], 1);
    assert.equal(snapshot.mercado_outbound_retry_count, 1);
  });
}

test('stable successes recover one slot at a time and never exceed the combined 192 ceiling', async () => {
  const scheduler = createBalancedReadScheduler({
    ...BALANCED_READ_PROFILES.prepare,
    sleep: async () => {},
    random: () => 0,
    successesPerIncrease: 2,
  });
  let attempts = 0;
  await scheduler.schedule({ accountId: 'A', key: 'recover-after-network' }, async () => {
    attempts += 1;
    if (attempts === 1) throw Object.assign(new Error('network'), { code: 'ECONNRESET' });
    return true;
  });
  assert.equal(scheduler.snapshot().dynamicLimit, 187);
  await scheduler.schedule({ accountId: 'B', key: 'one-stable-success' }, async () => true);
  assert.equal(scheduler.snapshot().dynamicLimit, 188);
  await Promise.all(Array.from({ length: 20 }, (_, index) => (
    scheduler.schedule({ accountId: `A${index % 3}`, key: `cap-${index}` }, async () => true)
  )));
  assert.equal(scheduler.snapshot().dynamicLimit, 192);
});

test('fallback concurrency remains per-account bounded and consumes the same global outbound quota', async () => {
  const scheduler = createBalancedReadScheduler({
    ...BALANCED_READ_PROFILES.prepare,
    initialLimit: 3,
    maxLimit: 3,
    perAccountLimit: 3,
    detailLimit: 3,
    successesPerIncrease: 1_000,
  });
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const jobs = ['A', 'B', 'C'].flatMap((accountId) => Array.from({ length: 3 }, (_, index) => (
    scheduler.withFallback({ accountId, key: `fallback-${accountId}-${index}` }, () => (
      scheduler.schedule({ accountId, key: `fallback-read-${accountId}-${index}`, kind: 'detail' }, () => gate)
    ))
  )));
  await wait(10);
  const running = scheduler.snapshot();
  assert.equal(running.inflight, 3);
  assert.ok(Object.values(running.peakFallbackByAccount).every((value) => value <= 2));
  release();
  await Promise.all(jobs);
});

test('scheduler emits bounded observable snapshots with queue, cooldown and fallback activity', async () => {
  const snapshots = [];
  const scheduler = createBalancedReadScheduler({
    initialLimit: 1,
    maxLimit: 2,
    fallbackPerAccount: 2,
    snapshotThrottleMs: 0,
  });
  scheduler.subscribe((snapshot) => snapshots.push(snapshot));
  await scheduler.withFallback({ accountId: 'A', key: 'observable-fallback' }, async () => {
    await scheduler.schedule({ accountId: 'A', key: 'observable-read', kind: 'detail' }, async () => true);
  });
  assert.ok(snapshots.some((row) => row.fallbackActiveByAccount?.A === 1));
  assert.ok(snapshots.some((row) => row.mercado_outbound_inflight === 1));
  const finalSnapshot = snapshots.at(-1);
  assert.equal(finalSnapshot.dynamicLimit, 1);
  assert.equal(finalSnapshot.queued, 0);
  assert.equal(finalSnapshot.fallbackActiveByAccount.A, 0);
});

test('prepare integration shares the scheduler and only live-verified writes update cache before exact invalidation', () => {
  const server = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  const repository = fs.readFileSync(new URL('../src/repository.js', import.meta.url), 'utf8');
  const client = fs.readFileSync(new URL('../src/mlClient.js', import.meta.url), 'utf8');
  assert.match(server, /createBalancedReadScheduler\(prepareReadSchedulerProfile\(readSettings\(\)\)\)/);
  assert.match(server, /listPreparationReadStates\(accountIds\)/);
  assert.match(server, /read_concurrency:\s*buildReadConcurrencyReport/);
  assert.match(server, /readScheduler[\s\S]*operationReadCache/);
  assert.match(server, /await Promise\.all\(accountIds\.map/);
  assert.doesNotMatch(server, /for \(const promotionResult of promotionResults\)[\s\S]*markActivityCacheDirty/);
  assert.match(server, /applySuccessfulPromotionItemWrites\([\s\S]*items: verifiedRows/);
  assert.match(server, /invalidatePromotionItemFetchStates\([\s\S]*markActivityCacheDirty/);
  assert.match(repository, /applySuccessfulPromotionItemWrites[\s\S]*transaction\(\(database\)[\s\S]*for \(const row of rows\)/);
  assert.match(client, /this\.readScheduler\.schedule/);
  assert.match(client, /retryAfterMs/);
  assert.match(repository, /saveCampaigns[\s\S]*transaction\(\(database\)[\s\S]*const statement = database\.prepare/);
  assert.match(repository, /saveItems[\s\S]*transaction\(\(database\)[\s\S]*const statement = database\.prepare/);
});
