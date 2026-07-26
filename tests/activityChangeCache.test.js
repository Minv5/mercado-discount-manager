import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import {
  ACTIVITY_CATALOG_TTL_MS,
  ACTIVITY_ITEMS_TTL_MS,
  ACTIVITY_PARTIAL_ITEMS_TTL_MS,
  activityCatalogDecision,
  activityItemsDecision,
  applyActivityChangeEvent,
  buildItemIdentitySummary,
  candidatePreparationReadDecision,
  candidateTotalProbeDecision,
  isActivityExpired,
  inventoryTotalProbeDecision,
  itemIdentityDelta,
  nextNonPeakCalibrationAt,
  planActivityCatalogRoutes,
  shouldProbeFreshPreparationItems,
} from '../src/activityChangeCache.js';

const NOW = new Date('2026-07-15T06:00:00.000Z');

test('activity catalog uses a daily calibration and dirty or gap forces only the target scope', () => {
  const fresh = { catalog_checked_at: new Date(NOW.getTime() - 60_000).toISOString(), dirty: 0, continuity: 'continuous' };
  assert.equal(activityCatalogDecision(fresh, NOW).refresh, false);
  assert.equal(activityCatalogDecision(fresh, NOW).reason, 'same_day_cache');
  assert.equal(activityCatalogDecision({ ...fresh, dirty: 1 }, NOW).reason, 'dirty');
  assert.equal(activityCatalogDecision({ ...fresh, continuity: 'gap' }, NOW).reason, 'event_gap');
  assert.equal(activityCatalogDecision({ ...fresh, catalog_checked_at: '2026-07-14T06:00:00.000Z' }, NOW).reason, 'daily_due');
  assert.deepEqual(activityCatalogDecision({
    ...fresh,
    dirty: 1,
    continuity: 'gap',
    last_error: 'network',
    updated_at: new Date(NOW.getTime() - 60_000).toISOString(),
  }, NOW), { refresh: false, blocked: true, reason: 'same_day_failed' });

  const states = new Map([
    ['A|MLM|P-1|DEAL', { dirty: 0, continuity: 'continuous' }],
    ['A|MLM|P-2|DEAL', { dirty: 0, continuity: 'continuous' }],
  ]);
  applyActivityChangeEvent(states, { account_id: 'A', site_id: 'MLM', promotion_id: 'P-2', promotion_type: 'DEAL', cursor: '18' });
  assert.equal(states.get('A|MLM|P-1|DEAL').dirty, 0);
  assert.equal(states.get('A|MLM|P-2|DEAL').dirty, 1);
  assert.equal(states.get('A|MLM|P-2|DEAL').event_cursor, '18');
});

test('catalog route plan performs zero external reads for clean routes and targets only dirty routes', () => {
  const routes = [
    { account_id: 'A', child_user_id: 'C1', site_id: 'MLM' },
    { account_id: 'A', child_user_id: 'C2', site_id: 'MLB' },
  ];
  const checkedAt = new Date(NOW.getTime() - 1_000).toISOString();
  const states = new Map([
    ['MLM', { catalog_checked_at: checkedAt, dirty: 0, continuity: 'continuous' }],
    ['MLB', { catalog_checked_at: checkedAt, dirty: 1, continuity: 'continuous' }],
  ]);
  const plan = planActivityCatalogRoutes(routes, (route) => states.get(route.site_id), NOW);
  assert.deepEqual(plan.cached.map((route) => route.site_id), ['MLM']);
  assert.deepEqual(plan.refresh.map((route) => route.site_id), ['MLB']);
  assert.equal(plan.reasons['A|C2|MLB'], 'dirty');
});

test('catalog route plan blocks a failed route for the rest of the Shanghai day', () => {
  const routes = [{ account_id: 'A', child_user_id: 'C1', site_id: 'MLM' }];
  const plan = planActivityCatalogRoutes(routes, () => ({
    dirty: 1,
    continuity: 'gap',
    last_error: 'network',
    updated_at: new Date(NOW.getTime() - 1_000).toISOString(),
  }), NOW);
  assert.equal(plan.refresh.length, 0);
  assert.equal(plan.cached.length, 0);
  assert.deepEqual(plan.blocked, routes);
  assert.equal(plan.reasons['A|C1|MLM'], 'same_day_failed');
});

test('activity items reuse verified full cache for three days and never reuse dirty gap error or partial cache', () => {
  const promotion = { finish_date: '2026-07-31' };
  const cacheState = { items_full_checked_at: new Date(NOW.getTime() - ACTIVITY_ITEMS_TTL_MS + 1_000).toISOString(), dirty: 0, continuity: 'continuous' };
  const fetchState = { detail_status: 'ok', saved_count: 12, platform_total: 12, updated_at: cacheState.items_full_checked_at };
  assert.equal(activityItemsDecision({ promotion, cacheState, fetchState, now: NOW }).refresh, false);
  const sameDayCache = {
    ...cacheState,
    items_full_checked_at: new Date(NOW.getTime() - 2 * 60 * 60 * 1000).toISOString(),
  };
  assert.equal(activityItemsDecision({ promotion, cacheState: sameDayCache, fetchState, now: NOW }).reason, 'same_day_cache');
  assert.equal(activityItemsDecision({ promotion, cacheState: { ...cacheState, dirty: 1 }, fetchState, now: NOW }).reason, 'dirty');
  assert.equal(activityItemsDecision({ promotion, cacheState: { ...cacheState, continuity: 'gap' }, fetchState, now: NOW }).reason, 'event_gap');
  assert.equal(activityItemsDecision({ promotion, cacheState, fetchState: { ...fetchState, detail_status: 'error' }, now: NOW }).reason, 'unreadable');
  assert.equal(activityItemsDecision({ promotion, cacheState, fetchState: { ...fetchState, detail_status: 'partial' }, now: NOW }).reason, 'not_full');
  assert.equal(activityItemsDecision({ promotion, cacheState: { ...cacheState, items_full_checked_at: new Date(NOW.getTime() - ACTIVITY_ITEMS_TTL_MS - 1).toISOString() }, fetchState, now: NOW }).reason, 'three_day_due');
});

test('same-day failed or incomplete item reads do not repeat unless a newer event marks the activity dirty', () => {
  const attemptedAt = new Date(NOW.getTime() - 60_000).toISOString();
  const base = {
    promotion: { finish_date: '2026-07-31' },
    cacheState: { dirty: 1, continuity: 'gap', updated_at: new Date(NOW.getTime() - 120_000).toISOString() },
    now: NOW,
  };
  assert.deepEqual(activityItemsDecision({
    ...base,
    fetchState: { detail_status: 'error', updated_at: attemptedAt },
  }), { refresh: false, blocked: true, reason: 'same_day_failed' });
  assert.deepEqual(activityItemsDecision({
    ...base,
    fetchState: { detail_status: 'api_incomplete_marketplace_candidate', updated_at: attemptedAt },
  }), { refresh: false, blocked: true, reason: 'same_day_incomplete' });

  const newerEvent = activityItemsDecision({
    ...base,
    cacheState: { ...base.cacheState, updated_at: new Date(NOW.getTime() - 1_000).toISOString() },
    fetchState: { detail_status: 'error', updated_at: attemptedAt },
  });
  assert.equal(newerEvent.refresh, true);
  assert.equal(newerEvent.reason, 'dirty');
});

test('partial candidate plus fresh inventory fallback is a reusable complete verification state', () => {
  const checkedAt = new Date(NOW.getTime() - 60_000).toISOString();
  const decision = activityItemsDecision({
    promotion: { finish_date: '2026-07-31' },
    cacheState: { items_full_checked_at: checkedAt, dirty: 0, continuity: 'continuous' },
    fetchState: { detail_status: 'partial', saved_count: 20, platform_total: 80, updated_at: checkedAt },
    fallbackState: { detail_status: 'candidate_inventory_fallback', saved_count: 60, platform_total: 60, updated_at: checkedAt },
    now: NOW,
  });
  assert.equal(decision.refresh, false);
  assert.equal(decision.reason, 'verified_composite_cache');
  assert.equal(decision.effective_state, 'candidate_plus_inventory_fallback');

  const staleFallback = activityItemsDecision({
    promotion: { finish_date: '2026-07-31' },
    cacheState: { items_full_checked_at: checkedAt, dirty: 0, continuity: 'continuous' },
    fetchState: { detail_status: 'partial', saved_count: 20, platform_total: 80, updated_at: checkedAt },
    fallbackState: { detail_status: 'candidate_inventory_fallback', saved_count: 60, platform_total: 60, updated_at: new Date(NOW.getTime() - ACTIVITY_ITEMS_TTL_MS - 1).toISOString() },
    now: NOW,
  });
  assert.equal(staleFallback.refresh, false);
  assert.equal(staleFallback.blocked, true);
  assert.equal(staleFallback.reason, 'same_day_incomplete');
});

test('audited sparse candidate responses use a bounded low-frequency window instead of refreshing every prepare', () => {
  const checkedAt = new Date(NOW.getTime() - ACTIVITY_PARTIAL_ITEMS_TTL_MS + 1).toISOString();
  const reusable = activityItemsDecision({
    promotion: { finish_date: '2026-07-31' },
    cacheState: { dirty: 0, continuity: 'continuous' },
    fetchState: {
      detail_status: 'partial_api_sparse_marketplace_candidate',
      saved_count: 20,
      platform_total: 80,
      updated_at: checkedAt,
    },
    now: NOW,
  });
  assert.deepEqual(reusable, {
    refresh: false,
    reason: 'verified_sparse_window',
    effective_state: 'partial_api_sparse_marketplace_candidate',
  });
  const stale = activityItemsDecision({
    promotion: { finish_date: '2026-07-31' },
    cacheState: { dirty: 0, continuity: 'continuous' },
    fetchState: {
      detail_status: 'partial_api_sparse_marketplace_candidate',
      saved_count: 20,
      platform_total: 80,
      updated_at: new Date(NOW.getTime() - ACTIVITY_PARTIAL_ITEMS_TTL_MS - 1).toISOString(),
    },
    now: NOW,
  });
  assert.equal(stale.refresh, true);
  assert.equal(stale.reason, 'not_full');
});

test('started activities never reuse sparse candidate window', () => {
  const checkedAt = new Date(NOW.getTime() - ACTIVITY_PARTIAL_ITEMS_TTL_MS + 1).toISOString();
  const startedDecision = activityItemsDecision({
    promotion: { finish_date: '2026-07-31' },
    cacheState: { dirty: 0, continuity: 'continuous' },
    fetchState: {
      detail_status: 'partial_api_sparse_marketplace_candidate',
      saved_count: 20,
      platform_total: 80,
      updated_at: checkedAt,
    },
    itemStatus: 'started',
    now: NOW,
  });
  assert.equal(startedDecision.refresh, true);
  assert.equal(startedDecision.reason, 'not_full');
});

test('only a new enrollment probes candidate totals while fixed update and cancel scopes reuse cache', () => {
  assert.equal(shouldProbeFreshPreparationItems({ action: 'enroll', itemStatus: 'candidate' }), true);
  assert.equal(shouldProbeFreshPreparationItems({ action: 'enroll', itemStatus: 'started' }), false);
  assert.equal(shouldProbeFreshPreparationItems({ action: 'update', itemStatus: 'started' }), false);
  assert.equal(shouldProbeFreshPreparationItems({ action: 'cancel', itemStatus: 'started' }), false);
  assert.equal(shouldProbeFreshPreparationItems({
    action: 'enroll',
    itemStatus: 'candidate',
    finalRevalidation: true,
  }), false);
});

test('same-day clean candidate preparation performs zero probe while a new business day may probe', () => {
  const sameDayFetchState = {
    detail_status: 'ok',
    updated_at: new Date(NOW.getTime() - 60_000).toISOString(),
  };
  for (const cacheDecision of [
    { refresh: false, reason: 'same_day_cache' },
    { refresh: false, reason: 'same_day_partial_cache', effective_state: 'partial_api_sparse_marketplace_candidate' },
    { refresh: false, reason: 'verified_composite_cache', effective_state: 'candidate_plus_inventory_fallback' },
  ]) {
    assert.deepEqual(candidatePreparationReadDecision({
      action: 'enroll',
      itemStatus: 'candidate',
      cacheDecision,
      fetchState: sameDayFetchState,
      now: NOW,
    }), {
      probe: false,
      reason: 'same_day_candidate_cache',
    });
  }

  assert.deepEqual(candidatePreparationReadDecision({
    action: 'enroll',
    itemStatus: 'candidate',
    cacheDecision: { refresh: false, reason: 'verified_cache' },
    fetchState: { ...sameDayFetchState, updated_at: '2026-07-14T06:00:00.000Z' },
    now: NOW,
  }), {
    probe: true,
    reason: 'candidate_daily_probe_due',
  });
});

test('item identity summary is stable across duplicates, row shapes and ordering', () => {
  const expected = buildItemIdentitySummary(['I-2', 'I-1', 'I-2'], { complete: true });
  assert.deepEqual(
    buildItemIdentitySummary([{ id: 'I-1' }, { item_id: 'I-2' }], { complete: true }),
    expected,
  );
  assert.equal(expected.algorithm, 'sha256-sorted-item-ids-v1');
  assert.equal(expected.item_count, 2);
  assert.equal(expected.complete, true);
});

test('candidate total probe refreshes only changed, unverified, first-page drift or new-day activities', () => {
  const sameDayState = {
    platform_total: 80,
    detail_status: 'ok',
    updated_at: new Date(NOW.getTime() - 60_000).toISOString(),
    raw_json: JSON.stringify({ first_page_item_ids: ['I-1', 'I-2'] }),
  };
  assert.deepEqual(candidateTotalProbeDecision({
    fetchState: sameDayState,
    probe: { platform_total: 80, first_page_item_ids: ['I-2', 'I-1'] },
    now: NOW,
  }), { refresh: false, reason: 'candidate_total_unchanged' });
  assert.equal(candidateTotalProbeDecision({
    fetchState: sameDayState,
    probe: { platform_total: 81, first_page_item_ids: ['I-1', 'I-2'] },
    now: NOW,
  }).reason, 'candidate_total_changed');
  assert.equal(candidateTotalProbeDecision({
    fetchState: sameDayState,
    probe: { platform_total: 80, first_page_item_ids: ['I-1', 'I-3'] },
    now: NOW,
  }).reason, 'candidate_first_page_changed');
  assert.equal(candidateTotalProbeDecision({
    fetchState: { ...sameDayState, updated_at: '2026-07-14T06:00:00.000Z' },
    probe: { platform_total: 80, first_page_item_ids: ['I-1', 'I-2'] },
    now: NOW,
  }).reason, 'candidate_daily_identity_due');
  assert.equal(candidateTotalProbeDecision({
    fetchState: null,
    probe: { platform_total: 80, first_page_item_ids: ['I-1'] },
    now: NOW,
  }).reason, 'candidate_baseline_missing');
  assert.equal(candidateTotalProbeDecision({
    fetchState: sameDayState,
    probe: { platform_total: null, first_page_item_ids: [] },
    now: NOW,
  }).reason, 'candidate_total_unavailable');
  assert.equal(candidateTotalProbeDecision({
    fetchState: { ...sameDayState, raw_json: '{}' },
    probe: { platform_total: 80, first_page_item_ids: ['I-1'] },
    now: NOW,
  }).reason, 'candidate_identity_baseline_missing');
});

test('seller inventory probe detects route inventory changes and requires a migration identity baseline', () => {
  const fallbackState = {
    updated_at: new Date(NOW.getTime() - 60_000).toISOString(),
    raw_json: JSON.stringify({
      scan_total: 100,
      inventory_first_page_item_ids: ['I-1', 'I-2'],
    }),
  };
  assert.deepEqual(inventoryTotalProbeDecision({
    fallbackState,
    probe: { platform_total: 100, first_page_item_ids: ['I-2', 'I-1'] },
    now: NOW,
  }), { refresh: false, reason: 'inventory_total_unchanged' });
  assert.equal(inventoryTotalProbeDecision({
    fallbackState,
    probe: { platform_total: 101, first_page_item_ids: ['I-1', 'I-2'] },
    now: NOW,
  }).reason, 'inventory_total_changed');
  assert.equal(inventoryTotalProbeDecision({
    fallbackState: { ...fallbackState, raw_json: JSON.stringify({ scan_total: 100 }) },
    probe: { platform_total: 100, first_page_item_ids: ['I-1'] },
    now: NOW,
  }).reason, 'inventory_identity_baseline_missing');
  assert.equal(inventoryTotalProbeDecision({
    fallbackState: {
      ...fallbackState,
      raw_json: JSON.stringify({
        scan_total: 100,
        scan: { inventory_first_page_item_ids: ['I-1', 'I-2'] },
      }),
    },
    probe: { platform_total: 100, first_page_item_ids: ['I-1', 'I-2'] },
    now: NOW,
  }).reason, 'inventory_total_unchanged');
});

test('item identity delta only confirms removals after a complete read', () => {
  assert.deepEqual(itemIdentityDelta(
    [{ item_id: 'I-1' }, { item_id: 'I-2' }],
    [{ item_id: 'I-2' }, { item_id: 'I-3' }],
    { complete: true },
  ), {
    previous_count: 2,
    current_count: 2,
    added_count: 1,
    removed_count: 1,
    unreturned_count: 0,
  });
  assert.deepEqual(itemIdentityDelta(
    [{ item_id: 'I-1' }, { item_id: 'I-2' }],
    [{ item_id: 'I-2' }, { item_id: 'I-3' }],
    { complete: false },
  ), {
    previous_count: 2,
    current_count: 2,
    added_count: 1,
    removed_count: 0,
    unreturned_count: 1,
  });
});

test('finish date expires locally after the Shanghai business day without a network read', () => {
  assert.equal(isActivityExpired({ finish_date: '2026-07-14' }, NOW), true);
  assert.equal(isActivityExpired({ finish_date: '2026-07-15' }, NOW), false);
  assert.equal(isActivityExpired({ finish_date: '2026-07-16' }, NOW), false);
});

test('low frequency calibration schedules once at the next non-peak window', () => {
  const before = nextNonPeakCalibrationAt(new Date('2026-07-15T01:00:00+08:00'));
  assert.equal(before.toISOString(), '2026-07-14T18:30:00.000Z');
  const after = nextNonPeakCalibrationAt(new Date('2026-07-15T03:00:00+08:00'));
  assert.equal(after.toISOString(), '2026-07-15T18:30:00.000Z');
});

test('prepare source uses hybrid catalog and item cache gates without weakening blocked live reads', () => {
  const server = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  assert.match(server, /refreshActivityCatalogForPrepare\(/);
  assert.match(server, /activityItemsDecision\(/);
  assert.match(server, /probeCandidateTotals:\s*candidateProbeEnabled/);
  assert.match(server, /candidatePreparationReadDecision\(\{/);
  assert.match(server, /candidateTotalProbeDecision\(\{\s*fetchState,\s*probe\s*\}\)/);
  assert.match(server, /probeInventoryItemsForCampaign\(/);
  assert.match(server, /inventoryTotalProbeDecision\(\{\s*fallbackState,\s*probe:\s*inventoryProbe\s*\}\)/);
  assert.match(server, /initialPage:\s*probe\.page/);
  assert.match(server, /!String\(row\.probe_reason \|\| ''\)\.endsWith\('_probe_failed'\)/);
  assert.doesNotMatch(server, /for\s*\(const promotion of selectedForLiveRead\)\s*\{\s*keys\.add\(operationActivityReadKey/);
  assert.match(server, /cache_reused:\s*true/);
  assert.match(server, /scheduleLowFrequencyActivityCalibration\(\)/);
  assert.match(server, /detail_status:\s*'error'[\s\S]*blocked:\s*true/);
  assert.match(server, /正在实时核对已报名商品/);
  assert.match(server, /正在实时核对可报名商品/);
  assert.doesNotMatch(server, /正在读取需更新的(?:已报名|可报名)商品缓存/);
  assert.doesNotMatch(server, /buildExecutionSubmissionSnapshot[\s\S]{0,1800}await fetchAndSavePromotions\(account/);
});
