import assert from 'node:assert/strict';
import test from 'node:test';

import { classifyCbtUnresolved, isEligibleCbtContinuation } from '../src/cbtUnresolvedClassification.js';
import { runCbtReplayWaves } from '../src/cbtReplayContinuation.js';

test('38 unresolved fixture classifies missing-site rows and terminal unusable rows separately', () => {
  const fixture = [
    ...Array.from({ length: 14 }, (_, index) => ({ code: 'OFFICIAL_SITE_MISSING', account: '2651442567', resource: `A-${index}` })),
    ...Array.from({ length: 9 }, (_, index) => ({ code: 'OFFICIAL_SITE_MISSING', account: '3332096437', resource: `B-${index}` })),
    ...Array.from({ length: 15 }, (_, index) => ({ code: 'OFFICIAL_SITE_MISSING', account: '3408885754', resource: `C-${index}` })),
    { code: 'RESOURCE_STATUS_UNUSABLE', account: '3332096437', resource: 'D-1' },
    { code: 'RESOURCE_STATUS_UNUSABLE', account: '3408885754', resource: 'D-2' },
  ];
  const categories = fixture.map((row) => classifyCbtUnresolved({ code: row.code }));
  assert.equal(categories.filter((row) => row.category === 'quarantined_unknown').length, 38);
  assert.equal(categories.filter((row) => row.category === 'terminal_irrelevant').length, 2);
  assert.equal(fixture.filter((row) => row.code === 'OFFICIAL_SITE_MISSING').length, 38);
  assert.equal(fixture.filter((row) => row.code === 'RESOURCE_STATUS_UNUSABLE').length, 2);
  assert.equal(isEligibleCbtContinuation({ code: 'OFFICIAL_SITE_MISSING' }), false);
  assert.equal(isEligibleCbtContinuation({ code: 'CBT_FANOUT_PARTIAL' }), true);
  assert.equal(classifyCbtUnresolved({ code: 'ROUTE_NOT_OWNED', signal_presence: { owned_identity_proven: true } }).category, 'route_catalog_gap');
  assert.equal(classifyCbtUnresolved({ code: 'ROUTE_NOT_OWNED', signal_presence: { foreign_route_candidate_count: 1 } }).category, 'terminal_foreign');
  assert.equal(classifyCbtUnresolved({ code: 'ROUTE_AMBIGUOUS' }).category, 'quarantined_unknown');
});

test('replay continuation runs bounded waves with cooldown and separates totals', async () => {
  let calls = 0;
  const progress = [];
  const sleeps = [];
  const result = await runCbtReplayWaves({
    wavePhysicalBudget: 1000,
    maxWaves: 4,
    cooldownMs: 7,
    sleep: async (ms) => sleeps.push(ms),
    replay: async ({ onProgress }) => {
      calls += 1;
      await onProgress({ pages: 1, physical_get_used: 1000, unique_resource_succeeded: calls === 1 ? 2 : 1, cache_updated_count: 0, remaining_eligible_events: calls < 3 ? 10 : 0 });
      return {
        pages: 1, physical_get_used: 1000, physical_budget: 1000,
        unique_resource_succeeded: calls === 1 ? 2 : 1,
        cache_updated_count: 0, remaining_eligible_events: calls < 3 ? 10 : 0,
        remaining_eligible_resources: calls < 3 ? 3 : 0,
        physical_budget_exhausted: calls < 3,
      };
    },
    onProgress: (value) => progress.push(value),
  });
  assert.equal(calls, 3);
  assert.equal(result.continuation_status, 'complete');
  assert.equal(result.wave_count, 3);
  assert.equal(result.cooldown_count, 2);
  assert.deepEqual(sleeps, [7, 7]);
  assert.equal(result.physical_get_used, 3000);
  assert.ok(progress.some((row) => row.continuation_status === 'cooldown'));
});

test('continuation opens a no-progress circuit after three zero-progress waves', async () => {
  let calls = 0;
  const result = await runCbtReplayWaves({
    maxWaves: 10,
    cooldownMs: 0,
    replay: async () => {
      calls += 1;
      return { remaining_eligible_events: 5, remaining_eligible_resources: 5, unique_resource_succeeded: 0, cache_updated_count: 0 };
    },
  });
  assert.equal(calls, 3);
  assert.equal(result.continuation_status, 'no_progress_circuit_open');
  assert.equal(result.no_progress_waves, 3);
});

test('continuation supports safe stop and checkpoint resume without rereading terminal scope', async () => {
  const saved = { next_wave: 2 };
  const progress = [];
  let calls = 0;
  const result = await runCbtReplayWaves({
    maxWaves: 4,
    cooldownMs: 0,
    checkpoint: {
      load: async () => saved,
      save: async (value) => Object.assign(saved, value),
    },
    shouldStop: () => calls >= 1,
    replay: async () => {
      calls += 1;
      return { remaining_eligible_events: 5, remaining_eligible_resources: 5, unique_resource_succeeded: 1, cache_updated_count: 0 };
    },
    onProgress: (value) => progress.push(value),
  });
  assert.equal(result.resumed_from_wave, 2);
  assert.equal(result.continuation_status, 'stopped');
  assert.equal(result.wave_count, 2);
  assert.equal(saved.next_wave, 3);
  assert.ok(progress.length >= 0);
});

test('continuation carries classification snapshot and retries one partial resource without recapturing terminal rows', async () => {
  let calls = 0;
  const result = await runCbtReplayWaves({
    wavePhysicalBudget: 1000,
    maxWaves: 3,
    cooldownMs: 0,
    replay: async () => {
      calls += 1;
      if (calls === 1) {
        return {
          physical_get_used: 367,
          unique_resource_succeeded: 51,
          cache_updated_count: 236,
          remaining_eligible_events: 1,
          remaining_eligible_resources: 1,
          classification_counts: { quarantined_unknown: 59, terminal_irrelevant: 6, eligible_partial: 1 },
          classification_item_counts: { quarantined_unknown: 52, terminal_irrelevant: 6, eligible_partial: 1 },
        };
      }
      return {
        physical_get_used: 12,
        unique_resource_succeeded: 1,
        cache_updated_count: 2,
        remaining_eligible_events: 0,
        remaining_eligible_resources: 0,
        classification_counts: { quarantined_unknown: 52 },
        classification_item_counts: { quarantined_unknown: 52 },
      };
    },
  });
  assert.equal(calls, 2);
  assert.equal(result.continuation_status, 'complete');
  assert.equal(result.classification_item_counts.quarantined_unknown, 52);
  assert.equal(result.classification_item_counts.terminal_irrelevant, undefined);
  assert.equal(result.unique_resource_retained_failure, 52);
});
