import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildCbtTargetedFallbackPlan,
  isCbtTargetedFallbackCategory,
  summarizeCbtTargetedFallbackOutcome,
} from '../src/cbtTargetedFallback.js';

const EVENT = { resource: '/items/CBT-EXAMPLE', remote_user_id: '2651442567' };

test('targeted fallback plan is one bounded known GET and never a full catalog refresh', () => {
  const plan = buildCbtTargetedFallbackPlan({
    event: EVENT,
    classification: 'quarantined_unknown',
    marketplaceSites: [
      { account_id: '2651442567', child_user_id: '2659555001', site_id: 'MLB' },
      { account_id: '3332096437', child_user_id: '3333555001', site_id: 'MLB' },
    ],
  });
  assert.equal(plan.status, 'planned');
  assert.equal(plan.method, 'GET');
  assert.equal(plan.endpoint_family, 'items_cbt');
  assert.equal(plan.max_parent_gets, 1);
  assert.equal(plan.full_catalog_refresh, false);
  assert.equal(plan.verified_route_count, 1);
  assert.equal(isCbtTargetedFallbackCategory('route_catalog_gap'), true);
});

test('targeted fallback outcomes separate exact-owned, deleted, foreign, route-gap, and unknown', () => {
  assert.equal(summarizeCbtTargetedFallbackOutcome({ targets: [{ child_user_id: 'C1', site_id: 'MLB' }] }).category, 'exact_owned');
  assert.equal(summarizeCbtTargetedFallbackOutcome({ resourceStatus: 'deleted' }).category, 'terminal_irrelevant');
  assert.equal(summarizeCbtTargetedFallbackOutcome({ foreignSkipped: 2 }).category, 'terminal_foreign');
  assert.equal(summarizeCbtTargetedFallbackOutcome({ diagnostics: { codes: [{ code: 'ROUTE_CATALOG_GAP' }] } }).category, 'route_catalog_gap');
  assert.equal(summarizeCbtTargetedFallbackOutcome({ diagnostics: { ambiguous_skipped: 1 } }).category, 'quarantined_unknown');
});

test('targeted fallback rejects invalid resources without inventing an endpoint', () => {
  const plan = buildCbtTargetedFallbackPlan({
    event: { resource: '/items/MLB-123', remote_user_id: '2651442567' },
    classification: 'quarantined_unknown',
  });
  assert.equal(plan.status, 'blocked');
  assert.equal(plan.code, 'CBT_TARGETED_FALLBACK_INPUT_INVALID');
});
