import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import {
  ACTIVITY_CATALOG_TTL_MS,
  ACTIVITY_ITEMS_TTL_MS,
  activityCatalogDecision,
  activityItemsDecision,
  applyActivityChangeEvent,
  isActivityExpired,
  nextNonPeakCalibrationAt,
} from '../src/activityChangeCache.js';

const NOW = new Date('2026-07-15T06:00:00.000Z');

test('activity catalog uses a daily calibration and dirty or gap forces only the target scope', () => {
  const fresh = { catalog_checked_at: new Date(NOW.getTime() - ACTIVITY_CATALOG_TTL_MS + 1_000).toISOString(), dirty: 0, continuity: 'continuous' };
  assert.equal(activityCatalogDecision(fresh, NOW).refresh, false);
  assert.equal(activityCatalogDecision({ ...fresh, dirty: 1 }, NOW).reason, 'dirty');
  assert.equal(activityCatalogDecision({ ...fresh, continuity: 'gap' }, NOW).reason, 'event_gap');
  assert.equal(activityCatalogDecision({ ...fresh, catalog_checked_at: new Date(NOW.getTime() - ACTIVITY_CATALOG_TTL_MS - 1).toISOString() }, NOW).reason, 'daily_due');

  const states = new Map([
    ['A|MLM|P-1|DEAL', { dirty: 0, continuity: 'continuous' }],
    ['A|MLM|P-2|DEAL', { dirty: 0, continuity: 'continuous' }],
  ]);
  applyActivityChangeEvent(states, { account_id: 'A', site_id: 'MLM', promotion_id: 'P-2', promotion_type: 'DEAL', cursor: '18' });
  assert.equal(states.get('A|MLM|P-1|DEAL').dirty, 0);
  assert.equal(states.get('A|MLM|P-2|DEAL').dirty, 1);
  assert.equal(states.get('A|MLM|P-2|DEAL').event_cursor, '18');
});

test('activity items reuse verified full cache for three days and never reuse dirty gap error or partial cache', () => {
  const promotion = { finish_date: '2026-07-31' };
  const cacheState = { items_full_checked_at: new Date(NOW.getTime() - ACTIVITY_ITEMS_TTL_MS + 1_000).toISOString(), dirty: 0, continuity: 'continuous' };
  const fetchState = { detail_status: 'ok', saved_count: 12, platform_total: 12, updated_at: cacheState.items_full_checked_at };
  assert.equal(activityItemsDecision({ promotion, cacheState, fetchState, now: NOW }).refresh, false);
  assert.equal(activityItemsDecision({ promotion, cacheState: { ...cacheState, dirty: 1 }, fetchState, now: NOW }).reason, 'dirty');
  assert.equal(activityItemsDecision({ promotion, cacheState: { ...cacheState, continuity: 'gap' }, fetchState, now: NOW }).reason, 'event_gap');
  assert.equal(activityItemsDecision({ promotion, cacheState, fetchState: { ...fetchState, detail_status: 'error' }, now: NOW }).reason, 'unreadable');
  assert.equal(activityItemsDecision({ promotion, cacheState, fetchState: { ...fetchState, detail_status: 'partial' }, now: NOW }).reason, 'not_full');
  assert.equal(activityItemsDecision({ promotion, cacheState: { ...cacheState, items_full_checked_at: new Date(NOW.getTime() - ACTIVITY_ITEMS_TTL_MS - 1).toISOString() }, fetchState, now: NOW }).reason, 'three_day_due');
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
  assert.equal(staleFallback.refresh, true);
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
  assert.match(server, /cache_reused:\s*true/);
  assert.match(server, /scheduleLowFrequencyActivityCalibration\(\)/);
  assert.match(server, /detail_status:\s*'error'[\s\S]*blocked:\s*true/);
  assert.doesNotMatch(server, /buildExecutionSubmissionSnapshot[\s\S]{0,1800}await fetchAndSavePromotions\(account/);
});
