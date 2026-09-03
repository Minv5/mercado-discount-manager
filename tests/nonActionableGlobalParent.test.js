import assert from 'node:assert/strict';
import test from 'node:test';
import {
  classifyCbtUnresolved,
  isNonActionableGlobalParent,
} from '../src/cbtUnresolvedClassification.js';
import { assessStartupReadiness } from '../src/startupRefreshAudit.js';

const READY_AUDITS = ['A', 'B', 'C'].map((account_id) => ({
  account_id,
  status: 'ok',
  stages: [{ stage: 'detail', status: 'ok', ready: true }],
}));

function parentContext(overrides = {}) {
  return {
    resource: '/items/CBT-SYNTHETIC',
    last_error: {
      operation: 'cbt_route_resolution',
      endpoint_family: 'items_cbt',
      code: 'OFFICIAL_SITE_MISSING',
      root_site_id: 'CBT',
      signal_presence: {
        seller_signal_count: 0,
        site_signal_count: 0,
        route_candidate_count: 0,
        child_item_count: 0,
      },
    },
    event_gap: 0,
    local_relation_count: 0,
    route_catalog_ready: true,
    ...overrides,
  };
}

test('global parent with no child signals is terminal and non-blocking', () => {
  const context = parentContext();
  assert.equal(isNonActionableGlobalParent(context), true);
  assert.deepEqual(classifyCbtUnresolved(context), {
    category: 'terminal_no_actionable_global_parent',
    retryable: false,
    terminal: true,
    reason_cn: '全球父商品暂无可操作的站点子商品，已隔离，不影响当前活动。',
  });
});

test('persisted quarantined_unknown is superseded by complete non-actionable parent evidence', () => {
  const context = parentContext({
    last_error: {
      ...parentContext().last_error,
      classification: 'quarantined_unknown',
    },
  });
  assert.equal(classifyCbtUnresolved(context).category, 'terminal_no_actionable_global_parent');
});

test('local relation, route gap, event gap, or incomplete parent evidence remains quarantined', () => {
  for (const overrides of [
    { local_relation_count: 1 },
    { route_catalog_ready: false },
    { event_gap: 1 },
    { last_error: { ...parentContext().last_error, root_site_id: '' } },
    { last_error: { ...parentContext().last_error, signal_presence: { seller_signal_count: 1, site_signal_count: 0, route_candidate_count: 0, child_item_count: 0 } } },
  ]) {
    const context = parentContext(overrides);
    assert.equal(isNonActionableGlobalParent(context), false);
    assert.equal(classifyCbtUnresolved(context).category, 'quarantined_unknown');
  }
});

test('future child mapping reopens a prior terminal classification as eligible', () => {
  const reopened = classifyCbtUnresolved({
    classification: 'terminal_no_actionable_global_parent',
    reopen_on_child_mapping: true,
    child_item_count: 1,
  });
  assert.equal(reopened.category, 'eligible_route_unresolved');
  assert.equal(reopened.retryable, true);
});

test('terminal global parents and deleted rows do not block ready local stores', () => {
  const result = assessStartupReadiness({
    accountAudits: READY_AUDITS,
    requiredAccounts: READY_AUDITS,
    cbtReplay: {
      classification_snapshot: true,
      classification_counts: { terminal_no_actionable_global_parent: 36, terminal_irrelevant: 7 },
      classification_item_counts: { terminal_no_actionable_global_parent: 36, terminal_irrelevant: 7 },
      classification_item_account_counts: { terminal_no_actionable_global_parent: { A: 6, B: 17, C: 13 } },
      classification_account_counts: { terminal_no_actionable_global_parent: { A: 6, B: 17, C: 13 } },
      remaining_events: 43,
      remaining_unique_resources: 43,
      remaining_eligible_events: 0,
      remaining_eligible_resources: 0,
    },
  });
  assert.equal(result.ready, true);
  assert.equal(result.replay_terminal_no_actionable_global_parent, 36);
  assert.equal(result.replay_quarantined_unknown, 0);
  assert.equal(result.replay_route_catalog_gap, 0);
  assert.equal(result.replay_unique_retained_failure, 0);
  assert.ok(result.reasons.some((row) => row.code === 'cbt_terminal_no_actionable_global_parent' && row.blocking === false));
});
