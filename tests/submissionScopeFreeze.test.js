import assert from 'node:assert/strict';
import test from 'node:test';

import { toChineseError } from '../src/errors.js';

import {
  activityIdentityKey,
  activityReadKey,
  createConfirmedExecutionScope,
  forceReadKeysForStatus,
  reconcileConfirmedExecutionScope,
} from '../src/submissionScopeFreeze.js';

function activity(promotionId, itemIds, extra = {}) {
  return {
    account_id: 'A',
    child_user_id: 'CH-A',
    site_id: 'MLM',
    promotion_id: promotionId,
    promotion_type: 'DEAL',
    item_status: 'candidate',
    item_ids: itemIds,
    ...extra,
  };
}

test('candidate count drift and new activities never expand one confirmed submission', () => {
  const confirmed = createConfirmedExecutionScope({
    action: 'enroll',
    activities: [activity('P-1', ['I-1', 'I-2'], { platform_total: 2, saved_count: 2 })],
  });
  const first = reconcileConfirmedExecutionScope({
    confirmedScope: confirmed,
    observedScope: createConfirmedExecutionScope({
      action: 'enroll',
      activities: [
        activity('P-1', ['I-1', 'I-2', 'I-3'], { platform_total: 3, saved_count: 3 }),
        activity('P-2', ['I-4']),
      ],
    }),
  });
  assert.equal(first.requires_reprepare, false);
  assert.equal(first.requires_reconfirm, false);
  assert.deepEqual(first.execution_scope.activities.map((row) => [row.promotion_id, row.item_ids]), [['P-1', ['I-1', 'I-2']]]);
  assert.equal(first.excluded_new_item_count, 2);

  const second = reconcileConfirmedExecutionScope({
    confirmedScope: first.execution_scope,
    observedScope: createConfirmedExecutionScope({
      action: 'enroll',
      activities: [activity('P-1', ['I-1', 'I-2', 'I-5'], { platform_total: 4, saved_count: 3 })],
    }),
  });
  assert.equal(second.requires_reconfirm, false);
  assert.deepEqual(second.execution_scope.activities[0].item_ids, ['I-1', 'I-2']);
  assert.equal(second.excluded_new_item_count, 1);
});

test('revalidation keys use one activity identity and explicit candidate or started status', () => {
  const value = activity('P-1', ['I-1']);
  assert.equal(activityIdentityKey(value), 'A|CH-A|MLM|P-1|DEAL');
  assert.equal(activityReadKey(value, 'candidate'), 'A|CH-A|MLM|P-1|DEAL|candidate');
  assert.deepEqual(
    [...forceReadKeysForStatus(['A|CH-A|MLM|P-1|DEAL|readable'], 'started')],
    ['A|CH-A|MLM|P-1|DEAL|started'],
  );
  assert.deepEqual(
    [...forceReadKeysForStatus(['A|CH-A|MLM|P-1|DEAL'], 'candidate')],
    ['A|CH-A|MLM|P-1|DEAL|candidate'],
  );
});

test('revalidation key collections accept the real Set contract and reject unknown object shapes', () => {
  assert.deepEqual(
    [...forceReadKeysForStatus(new Set([
      '2651442567|CH-265|MLM|P-1|DEAL',
      '3332096437|CH-333|MLM|P-2|SELLER_CAMPAIGN',
    ]), 'started')],
    [
      '2651442567|CH-265|MLM|P-1|DEAL|started',
      '3332096437|CH-333|MLM|P-2|SELLER_CAMPAIGN|started',
    ],
  );
  assert.deepEqual([...forceReadKeysForStatus(null, 'candidate')], []);
  let contractError = null;
  assert.throws(() => {
    try {
      forceReadKeysForStatus({ values: ['2651442567|CH-265|MLM|P-1|DEAL'] }, 'candidate');
    } catch (error) {
      contractError = error;
      throw error;
    }
  }, (error) => error?.code === 'EXECUTION_SCOPE_COLLECTION_INVALID'
    && error?.status === 422
    && /执行范围/.test(error?.message || ''));
  assert.equal(toChineseError(contractError), '执行范围活动键集合格式异常，已停止准备且未执行商品操作。');
});

test('fresh all-store manual enroll can freeze the confirmed scope with an empty Set of revalidation keys', () => {
  const revalidateIdentityKeys = new Set();
  assert.deepEqual([...forceReadKeysForStatus(revalidateIdentityKeys, 'started')], []);
  assert.deepEqual([...forceReadKeysForStatus(revalidateIdentityKeys, 'candidate')], []);

  const frozen = createConfirmedExecutionScope({
    action: 'enroll',
    activities: [
      { ...activity('P-HB', ['I-1']), account_id: '2651442567' },
      { ...activity('P-HN', ['I-2']), account_id: '3332096437', promotion_type: 'SELLER_CAMPAIGN' },
      { ...activity('P-GD', ['I-3']), account_id: '3408885754' },
    ],
  });
  assert.equal(frozen.action, 'enroll');
  assert.deepEqual(frozen.activities.map((row) => [row.account_id, row.promotion_id, row.item_ids]), [
    ['2651442567', 'P-HB', ['I-1']],
    ['3332096437', 'P-HN', ['I-2']],
    ['3408885754', 'P-GD', ['I-3']],
  ]);
});

test('partial sparse count jitter is audited but does not request reconfirmation', () => {
  const confirmed = createConfirmedExecutionScope({
    action: 'enroll',
    activities: [activity('P-1', ['I-1', 'I-2'], {
      platform_total: 200,
      saved_count: 180,
      detail_status: 'partial_api_sparse_marketplace_candidate',
    })],
  });
  const result = reconcileConfirmedExecutionScope({
    confirmedScope: confirmed,
    observedScope: createConfirmedExecutionScope({
      action: 'enroll',
      activities: [activity('P-1', ['I-1', 'I-2'], {
        platform_total: 201,
        saved_count: 181,
        detail_status: 'partial_api_sparse_marketplace_candidate',
      })],
    }),
  });
  assert.equal(result.requires_reconfirm, false);
  assert.equal(result.execution_scope.activities[0].item_ids.length, 2);
  assert.equal(result.activity_diffs[0].before.platform_total, 200);
  assert.equal(result.activity_diffs[0].after.platform_total, 201);
  assert.deepEqual(result.activity_diffs[0].added_item_ids, []);
  assert.deepEqual(result.activity_diffs[0].removed_item_ids, []);
});

test('item identity drift only shrinks the confirmed set and persists exact added and removed ids', () => {
  const confirmed = createConfirmedExecutionScope({
    action: 'enroll', activities: [activity('P-1', ['I-1', 'I-2'])],
  });
  const result = reconcileConfirmedExecutionScope({
    confirmedScope: confirmed,
    observedScope: createConfirmedExecutionScope({
      action: 'enroll', activities: [activity('P-1', ['I-2', 'I-3'])],
    }),
  });
  assert.equal(result.requires_reconfirm, false);
  assert.deepEqual(result.execution_scope.activities[0].item_ids, ['I-2']);
  assert.deepEqual(result.activity_diffs[0].added_item_ids, ['I-3']);
  assert.deepEqual(result.activity_diffs[0].removed_item_ids, ['I-1']);
  assert.equal(result.excluded_new_item_count, 1);
  assert.equal(result.auto_removed_item_count, 1);
  assert.match(result.messages.join('；'), /新增 1 个商品，本次不纳入/);
  assert.match(result.messages.join('；'), /剔除 1 个/);
});

test('items missing from the current platform read are skipped until a later preparation sees them', () => {
  const previousCacheScope = createConfirmedExecutionScope({
    action: 'enroll',
    activities: [activity('P-1', ['I-KEEP', 'I-NOT-RETURNED'])],
  });
  const currentRead = createConfirmedExecutionScope({
    action: 'enroll',
    activities: [activity('P-1', ['I-KEEP'])],
  });
  const currentCycle = reconcileConfirmedExecutionScope({
    confirmedScope: previousCacheScope,
    observedScope: currentRead,
  });

  assert.deepEqual(currentCycle.execution_scope.activities[0].item_ids, ['I-KEEP']);
  assert.equal(currentCycle.auto_removed_item_count, 1);
  assert.equal(currentCycle.excluded_new_item_count, 0);

  const nextCycle = createConfirmedExecutionScope({
    action: 'enroll',
    activities: [activity('P-1', ['I-KEEP', 'I-NOT-RETURNED'])],
  });
  assert.deepEqual(nextCycle.activities[0].item_ids, ['I-KEEP', 'I-NOT-RETURNED']);
});

test('duplicate observations collapse to one activity and structural definition drift requires one reconfirm', () => {
  const confirmed = createConfirmedExecutionScope({
    action: 'enroll', activities: [activity('P-1', ['I-1'])],
  });
  const duplicated = createConfirmedExecutionScope({
    action: 'enroll', activities: [activity('P-1', ['I-1']), activity('P-1', ['I-1', 'I-2'])],
  });
  assert.equal(duplicated.activities.length, 1);

  const changed = reconcileConfirmedExecutionScope({
    confirmedScope: confirmed,
    observedScope: createConfirmedExecutionScope({
      action: 'enroll',
      activities: [activity('P-1', ['I-1'], { site_id: 'MLB', promotion_type: 'SELLER_CAMPAIGN' })],
    }),
  });
  assert.equal(changed.requires_reconfirm, true);
  assert.equal(changed.requires_reprepare, true);
  assert.ok(changed.structural_changes.some((value) => value.startsWith('activity_definition_changed:')));
  assert.deepEqual(changed.reconfirmation_scope.activities.map((row) => ({
    site_id: row.site_id,
    promotion_type: row.promotion_type,
    item_ids: row.item_ids,
  })), [{ site_id: 'MLB', promotion_type: 'SELLER_CAMPAIGN', item_ids: ['I-1'] }]);
});

test('action or item-status drift keeps only previously confirmed item identities for one explicit reconfirm', () => {
  const confirmed = createConfirmedExecutionScope({
    action: 'enroll', activities: [activity('P-1', ['I-1', 'I-2'])],
  });
  const changed = reconcileConfirmedExecutionScope({
    confirmedScope: confirmed,
    observedScope: createConfirmedExecutionScope({
      action: 'update',
      activities: [activity('P-1', ['I-2', 'I-3'], { item_status: 'started' })],
    }),
  });
  assert.equal(changed.requires_reconfirm, true);
  assert.equal(changed.reconfirmation_scope.action, 'update');
  assert.deepEqual(changed.reconfirmation_scope.activities[0].item_ids, ['I-2']);
});

test('missing blocked and reduced relations are removed without admitting replacements', () => {
  const confirmed = createConfirmedExecutionScope({
    action: 'enroll',
    activities: [activity('P-1', ['I-1', 'I-2']), activity('P-2', ['I-3']), activity('P-3', ['I-4'])],
  });
  const result = reconcileConfirmedExecutionScope({
    confirmedScope: confirmed,
    observedScope: createConfirmedExecutionScope({
      action: 'enroll',
      activities: [activity('P-1', ['I-1']), activity('P-2', ['I-3'], { blocked: true })],
    }),
  });
  assert.deepEqual(result.execution_scope.activities.map((row) => [row.promotion_id, row.item_ids]), [['P-1', ['I-1']]]);
  assert.equal(result.auto_removed_item_count, 3);
  assert.equal(result.blocked_activity_count, 1);
  assert.equal(result.removed_activity_count, 1);
  assert.equal(result.requires_reconfirm, false);
});

test('seller confirmed absent becoming existing is removed while unreadable and action drift require reprepare', () => {
  const confirmed = createConfirmedExecutionScope({
    action: 'enroll',
    activities: [activity('P-1', ['I-1'])],
    sellerCreateTargetKeys: ['A|CH-A|MLM'],
  });
  const existing = reconcileConfirmedExecutionScope({
    confirmedScope: confirmed,
    observedScope: createConfirmedExecutionScope({
      action: 'enroll',
      activities: [activity('P-1', ['I-1'])],
      sellerTargetStates: { 'A|CH-A|MLM': 'existing' },
    }),
  });
  assert.deepEqual(existing.execution_scope.seller_create_target_keys, []);
  assert.equal(existing.seller_existing_count, 1);
  assert.equal(existing.requires_reprepare, false);

  const unreadable = reconcileConfirmedExecutionScope({
    confirmedScope: confirmed,
    observedScope: createConfirmedExecutionScope({
      action: 'enroll', activities: [activity('P-1', ['I-1'])],
      sellerTargetStates: { 'A|CH-A|MLM': 'unreadable' },
    }),
  });
  assert.equal(unreadable.requires_reprepare, true);

  const actionChanged = reconcileConfirmedExecutionScope({
    confirmedScope: confirmed,
    observedScope: createConfirmedExecutionScope({ action: 'update', activities: [activity('P-1', ['I-1'])] }),
  });
  assert.equal(actionChanged.requires_reprepare, true);
});

test('a newly created seller activity is admitted only into the single explicit reconfirmation scope', () => {
  const confirmed = createConfirmedExecutionScope({
    action: 'enroll',
    activities: [activity('D-1', ['I-1'])],
    sellerCreateTargetKeys: ['A|CH-A|MLM'],
    sellerTargetStates: { 'A|CH-A|MLM': 'confirmed_absent' },
  });
  const result = reconcileConfirmedExecutionScope({
    confirmedScope: confirmed,
    observedScope: createConfirmedExecutionScope({
      action: 'enroll',
      activities: [
        activity('D-1', ['I-1']),
        activity('C-NEW', ['I-2', 'I-3'], { promotion_type: 'SELLER_CAMPAIGN' }),
      ],
      sellerTargetStates: { 'A|CH-A|MLM': 'existing' },
    }),
  });
  assert.equal(result.requires_reconfirm, true);
  assert.deepEqual(result.execution_scope.activities.map((row) => row.promotion_id), ['D-1']);
  assert.deepEqual(result.reconfirmation_scope.activities.map((row) => [row.promotion_id, row.item_ids]), [
    ['C-NEW', ['I-2', 'I-3']],
    ['D-1', ['I-1']],
  ]);
  assert.deepEqual(result.reconfirmation_scope.seller_create_target_keys, []);
});
