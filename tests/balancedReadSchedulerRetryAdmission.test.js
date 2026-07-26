import assert from 'node:assert/strict';
import test from 'node:test';

import { BalancedReadScheduler } from '../src/balancedReadScheduler.js';

const tick = () => new Promise((resolve) => setImmediate(resolve));

async function waitUntil(predicate, message) {
  for (let index = 0; index < 200; index += 1) {
    if (predicate()) return;
    await tick();
  }
  assert.fail(message);
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test('simultaneous transient failures re-enter admission at the reduced limit and stay account-fair', async () => {
  const scheduler = new BalancedReadScheduler({
    initialLimit: 6,
    maxLimit: 6,
    perAccountLimit: 6,
    detailLimit: 6,
    detailPerAccountLimit: 6,
    minLimit: 2,
    rateLimitDecreaseStep: 2,
    successesPerIncrease: 1_000,
    sleep: async () => {},
    random: () => 0,
  });
  const firstWave = deferred();
  const retryWave = deferred();
  let firstStarted = 0;
  let retryActive = 0;
  let retryPeak = 0;
  const retryStarts = [];

  const jobs = ['A', 'B', 'C'].flatMap((accountId) => Array.from({ length: 2 }, (_, index) => (
    scheduler.schedule({
      accountId,
      key: `${accountId}-${index}`,
      kind: 'detail',
    }, async ({ attempt }) => {
      if (attempt === 1) {
        firstStarted += 1;
        await firstWave.promise;
        throw Object.assign(new Error('shared connection overload'), { code: 'ECONNRESET' });
      }
      retryStarts.push(accountId);
      retryActive += 1;
      retryPeak = Math.max(retryPeak, retryActive);
      await retryWave.promise;
      retryActive -= 1;
      return true;
    })
  )));

  await waitUntil(() => firstStarted === 6, 'initial wave did not fill the configured limit');
  firstWave.resolve();
  await waitUntil(() => retryStarts.length >= 4, 'retry wave did not fill the reduced limit');

  assert.equal(scheduler.snapshot().dynamicLimit, 4);
  assert.equal(retryPeak, 4);
  assert.deepEqual(new Set(retryStarts.slice(0, 4)), new Set(['A', 'B', 'C']));

  retryWave.resolve();
  await Promise.all(jobs);
  assert.equal(retryStarts.length, 6);
  assert.equal(scheduler.snapshot().mercado_outbound_retry_count, 6);
});

test('successes from the overloaded generation cannot restore capacity before new-generation stability', async () => {
  const retryCooldown = deferred();
  const oldSuccessWave = deferred();
  let started = 0;
  let failingAttempt = 0;
  const scheduler = new BalancedReadScheduler({
    initialLimit: 6,
    maxLimit: 6,
    perAccountLimit: 6,
    minLimit: 2,
    rateLimitDecreaseStep: 2,
    successesPerIncrease: 2,
    random: () => 0,
    sleep: async () => retryCooldown.promise,
  });

  const overloaded = scheduler.schedule({ accountId: 'A', key: 'overloaded' }, async ({ attempt }) => {
    started += 1;
    failingAttempt = attempt;
    if (attempt === 1) {
      throw Object.assign(new Error('connection overload'), { code: 'ECONNRESET' });
    }
    return true;
  });
  const oldGenerationSuccesses = ['B', 'C', 'D', 'E', 'F'].map((accountId) => (
    scheduler.schedule({ accountId, key: `old-success-${accountId}` }, async () => {
      started += 1;
      await oldSuccessWave.promise;
      return true;
    })
  ));

  await waitUntil(() => started === 6, 'old generation did not fill the initial window');
  await waitUntil(() => scheduler.snapshot().dynamicLimit === 4, 'overload did not lower one tier');
  oldSuccessWave.resolve();
  await Promise.all(oldGenerationSuccesses);

  assert.equal(scheduler.snapshot().dynamicLimit, 4);

  const oneNewGenerationSuccess = scheduler.schedule({ accountId: 'B', key: 'new-success' }, async () => true);
  await oneNewGenerationSuccess;
  assert.equal(scheduler.snapshot().dynamicLimit, 4);

  retryCooldown.resolve();
  await overloaded;
  assert.equal(failingAttempt, 2);
  assert.equal(scheduler.snapshot().dynamicLimit, 5);
});

test('Retry-After blocks every account until the global cooldown admission opens', async () => {
  let now = 0;
  const sleepers = [];
  const scheduler = new BalancedReadScheduler({
    initialLimit: 1,
    maxLimit: 1,
    perAccountLimit: 1,
    minLimit: 1,
    successesPerIncrease: 1_000,
    now: () => now,
    random: () => 0,
    sleep: (ms) => new Promise((resolve) => sleepers.push({
      ms,
      resolve: () => {
        now += ms;
        resolve();
      },
    })),
  });
  const starts = [];
  const limited = scheduler.schedule({ accountId: 'A', key: 'limited' }, async ({ attempt }) => {
    starts.push(`A${attempt}`);
    if (attempt === 1) {
      throw Object.assign(new Error('limited'), { status: 429, retryAfterMs: 3_000 });
    }
    return true;
  });
  const other = scheduler.schedule({ accountId: 'B', key: 'other' }, async () => {
    starts.push('B1');
    return true;
  });

  await waitUntil(() => sleepers.length > 0, 'global cooldown was not scheduled');
  assert.deepEqual(starts, ['A1']);
  assert.ok(sleepers.every(({ ms }) => ms >= 3_000));

  for (const sleeper of sleepers.splice(0)) sleeper.resolve();
  await Promise.all([limited, other]);
  assert.ok(starts.indexOf('B1') >= 1);
  assert.ok(starts.indexOf('A2') >= 1);
});

test('aborting a retry queued behind cooldown prevents every further attempt', async () => {
  const controller = new AbortController();
  const cooldown = deferred();
  let attempts = 0;
  let retryQueued = false;
  const scheduler = new BalancedReadScheduler({
    initialLimit: 1,
    maxLimit: 1,
    minLimit: 1,
    successesPerIncrease: 1_000,
    random: () => 0,
    sleep: async () => {
      retryQueued = true;
      await cooldown.promise;
    },
  });

  const run = scheduler.schedule({
    accountId: 'A',
    key: 'abort-after-overload',
    signal: controller.signal,
  }, async () => {
    attempts += 1;
    throw Object.assign(new Error('network'), { code: 'ECONNRESET' });
  });
  const rejection = assert.rejects(run, (error) => error?.name === 'AbortError');

  await waitUntil(() => retryQueued, 'retry did not enter cooldown');
  controller.abort();
  cooldown.resolve();
  await rejection;
  assert.equal(attempts, 1);
  assert.equal(scheduler.snapshot().queued, 0);
});
