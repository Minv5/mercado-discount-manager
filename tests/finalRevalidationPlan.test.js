import assert from 'node:assert/strict';
import test from 'node:test';

import { buildFinalRevalidationPlan } from '../src/finalRevalidationPlan.js';
import { createConfirmedExecutionScope } from '../src/submissionScopeFreeze.js';

const NOW = new Date('2026-07-17T03:00:00.000Z');
const FRESH = '2026-07-17T02:30:00.000Z';

function activity(accountId, childUserId, promotionId, extra = {}) {
  return {
    account_id: accountId,
    child_user_id: childUserId,
    site_id: 'MLM',
    promotion_id: promotionId,
    promotion_type: 'DEAL',
    status: 'started',
    finish_date: '2026-08-31',
    item_status: 'candidate',
    item_ids: [`${promotionId}-I-1`, `${promotionId}-I-2`],
    ...extra,
  };
}

function freshState() {
  return {
    cache: {
      dirty: 0,
      continuity: 'continuous',
      items_full_checked_at: FRESH,
      updated_at: '2026-07-17T02:00:00.000Z',
    },
    candidate: { detail_status: 'ok', saved_count: 2, platform_total: 2, updated_at: FRESH },
    started: { detail_status: 'ok', saved_count: 0, platform_total: 0, updated_at: FRESH },
    fallback: null,
  };
}

function plan({ confirmed, current = confirmed.activities, states = new Map(), refreshes = [], explicit = [] }) {
  return buildFinalRevalidationPlan({
    confirmedScope: confirmed,
    currentPromotions: current,
    catalogRefreshes: refreshes,
    action: confirmed.action,
    explicitTargetKeys: explicit,
    now: NOW,
    getCacheState: (row) => states.get(row.promotion_id)?.cache || null,
    getFetchState: (row, status) => states.get(row.promotion_id)?.[status] || null,
    getFallbackState: (row) => states.get(row.promotion_id)?.fallback || null,
  });
}

test('unchanged prepared scope performs zero item reads during final revalidation', () => {
  const rows = [activity('A', 'CH-A', 'P-1'), activity('B', 'CH-B', 'P-2')];
  const confirmed = createConfirmedExecutionScope({ action: 'enroll', activities: rows });
  const states = new Map(rows.map((row) => [row.promotion_id, freshState()]));
  const result = plan({ confirmed, states });

  assert.equal(result.total_activity_count, 2);
  assert.equal(result.item_read_identity_keys.size, 0);
  assert.equal(result.scope_review_identity_keys.size, 0);
  assert.equal(result.blocked_identity_keys.size, 0);
  assert.equal(result.removed_identity_keys.size, 0);
});

test('unchanged scope with clean previous final revalidation skips all item reads', () => {
  const rows = [activity('A', 'CH-A', 'P-1'), activity('B', 'CH-B', 'P-2')];
  const confirmed = createConfirmedExecutionScope({
    action: 'enroll',
    activities: rows,
    sellerCreateTargetKeys: ['A|CH-A|MLM'],
  });
  const states = new Map(rows.map((row) => [row.promotion_id, freshState()]));
  const result = buildFinalRevalidationPlan({
    confirmedScope: confirmed,
    currentPromotions: rows,
    catalogRefreshes: [],
    action: confirmed.action,
    explicitTargetKeys: [],
    now: NOW,
    previousRevalidationRecord: {
      total_activity_count: 2,
      item_read_activity_count: 0,
      scope_review_activity_count: 0,
      revalidation_reasons: {},
    },
    previousConfirmedScope: confirmed,
    getCacheState: (row) => states.get(row.promotion_id)?.cache || null,
    getFetchState: (row, status) => states.get(row.promotion_id)?.[status] || null,
    getFallbackState: (row) => states.get(row.promotion_id)?.fallback || null,
  });

  assert.equal(result.total_activity_count, 2);
  assert.equal(result.item_read_identity_keys.size, 0);
  assert.equal(result.scope_review_identity_keys.size, 0);
  assert.equal(result.blocked_identity_keys.size, 0);
  assert.equal(result.removed_identity_keys.size, 0);
  assert.equal(result.excluded_new_activity_count, 0);
});

test('same scope with historical reasons does not short-circuit final revalidation', () => {
  const rows = [activity('A', 'CH-A', 'P-1'), activity('B', 'CH-B', 'P-2')];
  const confirmed = createConfirmedExecutionScope({ action: 'enroll', activities: rows });
  const states = new Map(rows.map((row) => [row.promotion_id, freshState()]));
  const result = buildFinalRevalidationPlan({
    confirmedScope: confirmed,
    currentPromotions: rows,
    catalogRefreshes: [],
    action: confirmed.action,
    explicitTargetKeys: [],
    now: NOW,
    previousRevalidationRecord: {
      total_activity_count: 2,
      item_read_activity_count: 0,
      scope_review_activity_count: 0,
      revalidation_reasons: {
        'A|CH-A|MLM|P-1|DEAL': ['candidate:dirty'],
      },
    },
    previousConfirmedScope: confirmed,
    getCacheState: (row) => states.get(row.promotion_id)?.cache || null,
    getFetchState: (row, status) => states.get(row.promotion_id)?.[status] || null,
    getFallbackState: (row) => states.get(row.promotion_id)?.fallback || null,
  });

  assert.equal(result.item_read_identity_keys.size, 0);
  assert.equal(result.scope_review_identity_keys.size, 0);
  assert.equal(result.blocked_identity_keys.size, 0);
  assert.equal(result.removed_identity_keys.size, 0);
});

test('explicit revalidate keys force final item read even with clean previous context', () => {
  const rows = [activity('A', 'CH-A', 'P-1'), activity('B', 'CH-B', 'P-2')];
  const confirmed = createConfirmedExecutionScope({ action: 'enroll', activities: rows });
  const states = new Map(rows.map((row) => [row.promotion_id, freshState()]));
  const result = buildFinalRevalidationPlan({
    confirmedScope: confirmed,
    currentPromotions: rows,
    catalogRefreshes: [],
    action: confirmed.action,
    explicitTargetKeys: ['__ACTION__'],
    now: NOW,
    previousRevalidationRecord: {
      total_activity_count: 2,
      item_read_activity_count: 0,
      scope_review_activity_count: 0,
      revalidation_reasons: {},
    },
    previousConfirmedScope: confirmed,
    getCacheState: (row) => states.get(row.promotion_id)?.cache || null,
    getFetchState: (row, status) => states.get(row.promotion_id)?.[status] || null,
    getFallbackState: (row) => states.get(row.promotion_id)?.fallback || null,
  });

  assert.equal(result.total_activity_count, 2);
  assert.equal(result.item_read_identity_keys.size, 2);
  assert.equal(result.scope_review_identity_keys.size, 2);
  assert.deepEqual([...result.item_read_identity_keys], [
    'A|CH-A|MLM|P-1|DEAL',
    'B|CH-B|MLM|P-2|DEAL',
  ]);
});

test('one dirty activity is the only item range re-read across multiple accounts', () => {
  const rows = [
    activity('A', 'CH-A', 'P-1'),
    activity('A', 'CH-A', 'P-2'),
    activity('B', 'CH-B', 'P-3'),
  ];
  const confirmed = createConfirmedExecutionScope({ action: 'enroll', activities: rows });
  const states = new Map(rows.map((row) => [row.promotion_id, freshState()]));
  states.get('P-2').cache.dirty = 1;
  states.get('P-2').cache.updated_at = '2026-07-17T02:45:00.000Z';
  const result = plan({ confirmed, states });

  assert.deepEqual([...result.item_read_identity_keys], ['A|CH-A|MLM|P-2|DEAL']);
  assert.deepEqual(result.reasons_by_identity['A|CH-A|MLM|P-2|DEAL'], ['candidate:dirty', 'started:dirty']);
});

test('catalog metadata changes, removed activities and unreadable routes remain exact and never reuse stale rows', () => {
  const rows = [
    activity('A', 'CH-A', 'P-1'),
    activity('A', 'CH-A', 'P-2'),
    activity('B', 'CH-B', 'P-3'),
  ];
  const confirmed = createConfirmedExecutionScope({ action: 'enroll', activities: rows });
  const states = new Map(rows.map((row) => [row.promotion_id, freshState()]));
  const current = [rows[0], rows[2]];
  const result = plan({
    confirmed,
    current,
    states,
    refreshes: [{
      catalog_identity_changes: [{ key: 'A|CH-A|MLM|P-1|DEAL', reason: 'metadata_changed' }],
      blocked_route_keys: new Set(['B|CH-B|MLM']),
    }],
  });

  assert.deepEqual([...result.item_read_identity_keys], ['A|CH-A|MLM|P-1|DEAL']);
  assert.deepEqual([...result.removed_identity_keys], ['A|CH-A|MLM|P-2|DEAL']);
  assert.deepEqual([...result.blocked_identity_keys], ['B|CH-B|MLM|P-3|DEAL']);
  assert.deepEqual([...result.scope_review_identity_keys], [
    'A|CH-A|MLM|P-1|DEAL',
    'A|CH-A|MLM|P-2|DEAL',
    'B|CH-B|MLM|P-3|DEAL',
  ]);
});

test('fresh composite sparse candidate cache is stable while a same-day failed read is blocked without retry', () => {
  const seller = activity('A', 'CH-A', 'C-1', { promotion_type: 'SELLER_CAMPAIGN' });
  const deal = activity('A', 'CH-A', 'P-2');
  const confirmed = createConfirmedExecutionScope({ action: 'enroll', activities: [seller, deal] });
  const composite = freshState();
  composite.candidate = { detail_status: 'partial_api_sparse_marketplace_candidate', saved_count: 1, platform_total: 2, updated_at: FRESH };
  composite.fallback = { detail_status: 'candidate_inventory_fallback', saved_count: 2, platform_total: 2, updated_at: FRESH };
  const unreadable = freshState();
  unreadable.candidate = { detail_status: 'error', saved_count: 0, platform_total: null, updated_at: FRESH };
  const states = new Map([['C-1', composite], ['P-2', unreadable]]);
  const result = plan({ confirmed, states });

  assert.deepEqual([...result.item_read_identity_keys], []);
  assert.deepEqual([...result.blocked_identity_keys], ['A|CH-A|MLM|P-2|DEAL']);
  assert.equal(result.reasons_by_identity['A|CH-A|MLM|P-2|DEAL'].includes('candidate:same_day_failed'), true);
});

test('new catalog activities are excluded, except a newly verified seller activity explicitly created for a confirmed route', () => {
  const deal = activity('A', 'CH-A', 'P-1');
  const newDeal = activity('A', 'CH-A', 'P-NEW');
  const newSeller = activity('A', 'CH-A', 'C-NEW', { promotion_type: 'SELLER_CAMPAIGN' });
  const confirmed = createConfirmedExecutionScope({
    action: 'enroll',
    activities: [deal],
    sellerCreateTargetKeys: ['A|CH-A|MLM'],
  });
  const states = new Map([
    ['P-1', freshState()],
    ['P-NEW', freshState()],
    ['C-NEW', freshState()],
  ]);

  const normal = plan({ confirmed, current: [deal, newDeal, newSeller], states });
  assert.equal(normal.item_read_identity_keys.size, 0);
  assert.equal(normal.excluded_new_activity_count, 2);

  const afterCreate = plan({ confirmed, current: [deal, newDeal, newSeller], states, explicit: ['__SELLER__'] });
  assert.deepEqual([...afterCreate.item_read_identity_keys], ['A|CH-A|MLM|C-NEW|SELLER_CAMPAIGN']);
  assert.equal(afterCreate.excluded_new_activity_count, 1);
});
