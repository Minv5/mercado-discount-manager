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

test('balanced scheduler applies Retry-After, halves globally and uses account backoff for transient failures', async () => {
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
  assert.ok(scheduler.snapshot().dynamicLimit <= 4);

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

test('high adaptive prepare profile raises only Mercado GET ceilings and keeps fallback serialized', async () => {
  assert.deepEqual(BALANCED_READ_PROFILES.balanced, {
    initialLimit: 8,
    maxLimit: 12,
    perAccountLimit: 4,
    detailLimit: 6,
    fallbackPerAccount: 1,
  });
  assert.deepEqual(BALANCED_READ_PROFILES.prepare, {
    initialLimit: 10,
    maxLimit: 18,
    perAccountLimit: 6,
    detailLimit: 8,
    fallbackPerAccount: 1,
  });
  const scheduler = createBalancedReadScheduler(BALANCED_READ_PROFILES.prepare);
  assert.equal(scheduler.snapshot().dynamicLimit, 10);

  const baseline = await runRecordedReadBatch(BALANCED_READ_PROFILES.compatibility);
  const balanced = await runRecordedReadBatch(BALANCED_READ_PROFILES.balanced);
  const high = await runRecordedReadBatch(BALANCED_READ_PROFILES.prepare);
  assert.equal(baseline.snapshot.peakDetail, 4);
  assert.equal(balanced.snapshot.peakDetail, 6);
  assert.equal(high.snapshot.peakDetail, 8);
  assert.ok(high.snapshot.peakInflight <= 18);
  assert.ok(high.snapshot.peakDetail <= 8);
  const baselineWaves = Math.ceil(36 / baseline.snapshot.peakDetail);
  const balancedWaves = Math.ceil(36 / balanced.snapshot.peakDetail);
  const highWaves = Math.ceil(36 / high.snapshot.peakDetail);
  assert.ok(balancedWaves <= baselineWaves * 0.7, `baselineWaves=${baselineWaves} balancedWaves=${balancedWaves}`);
  assert.ok(highWaves <= baselineWaves * 0.6, `baselineWaves=${baselineWaves} highWaves=${highWaves}`);
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
  assert.equal(report.mercado_outbound_inflight, 0);
  assert.equal(report.mercado_outbound_peak, 1);
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

test('balanced prepare profile still halves immediately after a 429 response', async () => {
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
  assert.ok(scheduler.snapshot().dynamicLimit <= 5);
});

test('prepare integration shares the scheduler and batch persistence avoids per-row transactions', () => {
  const server = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  const repository = fs.readFileSync(new URL('../src/repository.js', import.meta.url), 'utf8');
  const client = fs.readFileSync(new URL('../src/mlClient.js', import.meta.url), 'utf8');
  assert.match(server, /createBalancedReadScheduler\(BALANCED_READ_PROFILES\.prepare\)/);
  assert.match(server, /listPreparationReadStates\(accountIds\)/);
  assert.match(server, /read_concurrency:\s*buildReadConcurrencyReport/);
  assert.match(server, /readScheduler[\s\S]*operationReadCache/);
  assert.match(server, /await Promise\.all\(accountIds\.map/);
  assert.match(server, /markActivityCacheDirty\([\s\S]*promotionResult\.promotion_id/);
  assert.match(client, /this\.readScheduler\.schedule/);
  assert.match(client, /retryAfterMs/);
  assert.match(repository, /saveCampaigns[\s\S]*transaction\(\(database\)[\s\S]*const statement = database\.prepare/);
  assert.match(repository, /saveItems[\s\S]*transaction\(\(database\)[\s\S]*const statement = database\.prepare/);
});
