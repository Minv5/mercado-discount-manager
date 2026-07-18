import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  applyHiddenSellerCampaignSkip,
  isDuplicateSellerCampaignNameError,
  recoverDuplicateSellerCampaign,
  resolveHiddenSellerCampaignTargets,
  validateRecoveredSellerCampaign,
} from '../src/sellerCampaignDuplicateRecovery.js';
import { commitSubmission, createSubmissionPersistence } from '../src/submissionPersistence.js';

const route = {
  account_id: '3408885754',
  child_user_id: '3407224975',
  site_id: 'MCO',
};

function campaign(overrides = {}) {
  return {
    ...route,
    promotion_id: 'C-MCO-HIDDEN-95',
    promotion_type: 'SELLER_CAMPAIGN',
    name: '95',
    status: 'started',
    finish_date: '2026-08-31T23:59:59.000Z',
    ...overrides,
  };
}

test('duplicate seller campaign errors are identified from the explicit platform contract only', () => {
  assert.equal(isDuplicateSellerCampaignNameError({
    status: 400,
    body: { error: 'duplicate_name', message: 'A campaign with the same name already exists' },
  }), true);
  assert.equal(isDuplicateSellerCampaignNameError({
    status: 409,
    body: { code: 'PROMOTION_NAME_ALREADY_EXISTS' },
  }), true);
  assert.equal(isDuplicateSellerCampaignNameError({ status: 400, body: { message: 'invalid date range' } }), false);
  assert.equal(isDuplicateSellerCampaignNameError({ status: 500, body: { message: 'temporary server error' } }), false);
});

test('duplicate name recovery prefers forced live catalog and validates exact route, name, type and status', async () => {
  let detailReads = 0;
  const result = await recoverDuplicateSellerCampaign({
    target: route,
    name: '95',
    now: () => new Date('2026-07-17T00:00:00.000Z'),
    forceCatalogRefresh: async () => [campaign()],
    listHistoricalCandidates: async () => [{ promotion_id: 'C-OLD' }],
    listWebhookCandidates: async () => [{ promotion_id: 'C-WEBHOOK' }],
    readPromotionDetail: async () => { detailReads += 1; return null; },
  });
  assert.equal(result.status, 'existing');
  assert.equal(result.recovery_source, 'live_catalog');
  assert.equal(result.promotion_id, 'C-MCO-HIDDEN-95');
  assert.equal(detailReads, 0);
});

test('duplicate name recovery accepts an exact historical id only after live detail verification', async () => {
  const reads = [];
  const result = await recoverDuplicateSellerCampaign({
    target: route,
    name: '95',
    now: () => new Date('2026-07-17T00:00:00.000Z'),
    forceCatalogRefresh: async () => [],
    listHistoricalCandidates: async () => [
      { ...route, promotion_id: 'C-FINISHED', promotion_type: 'SELLER_CAMPAIGN', name: '95' },
      { ...route, promotion_id: 'C-ACTIVE', promotion_type: 'SELLER_CAMPAIGN', name: '95' },
    ],
    listWebhookCandidates: async () => [],
    readPromotionDetail: async (candidate) => {
      reads.push(candidate.promotion_id);
      return candidate.promotion_id === 'C-FINISHED'
        ? campaign({ promotion_id: 'C-FINISHED', status: 'finished' })
        : campaign({ promotion_id: 'C-ACTIVE' });
    },
  });
  assert.deepEqual(reads, ['C-FINISHED', 'C-ACTIVE']);
  assert.equal(result.status, 'existing');
  assert.equal(result.recovery_source, 'history_detail');
  assert.equal(result.promotion_id, 'C-ACTIVE');
});

test('cross-account history and tampered webhook mappings never recover a hidden seller campaign', async () => {
  const detailReads = [];
  const result = await recoverDuplicateSellerCampaign({
    target: route,
    name: '95',
    now: () => new Date('2026-07-17T00:00:00.000Z'),
    forceCatalogRefresh: async () => [],
    listHistoricalCandidates: async () => [campaign({ account_id: '3332096437', promotion_id: 'C-CROSS' })],
    listWebhookCandidates: async () => [campaign({ child_user_id: '3333531550', promotion_id: 'C-TAMPER' })],
    readPromotionDetail: async (candidate) => {
      detailReads.push(candidate.promotion_id);
      return candidate;
    },
  });
  assert.equal(result.status, 'existing_without_visible_id');
  assert.equal(result.hidden_state, 'duplicate_name_hidden');
  assert.equal(result.promotion_id, '');
  assert.deepEqual(detailReads, []);
});

test('verified webhook promotion id can recover only after the same live detail contract', async () => {
  const result = await recoverDuplicateSellerCampaign({
    target: route,
    name: '95',
    now: () => new Date('2026-07-17T00:00:00.000Z'),
    forceCatalogRefresh: async () => [],
    listHistoricalCandidates: async () => [],
    listWebhookCandidates: async () => [campaign({ promotion_id: 'C-WEBHOOK' })],
    readPromotionDetail: async () => campaign({ promotion_id: 'C-WEBHOOK' }),
  });
  assert.equal(result.status, 'existing');
  assert.equal(result.recovery_source, 'webhook_detail');
  assert.equal(result.promotion_id, 'C-WEBHOOK');
});

test('hidden duplicate without id removes only that seller create target and preserves other activities', () => {
  const sellerSiteKey = '3408885754|3407224975|MCO';
  const prepare = {
    seller_input: {
      name: '95',
      selected_targets: [{ ...route, detection_status: 'confirmed_absent' }],
    },
    seller_detection: {
      existing: [], confirmed_absent: [{ ...route, detection_status: 'confirmed_absent' }],
      existing_without_visible_id: [], needs_manual_review: [], unreadable: [],
    },
    confirmed_execution_scope: {
      version: 2,
      action: 'enroll',
      activities: [
        { ...route, promotion_id: 'P-DEAL', promotion_type: 'DEAL', item_status: 'candidate', item_ids: ['MLM1'] },
      ],
      seller_create_target_keys: [sellerSiteKey],
      seller_target_states: { [sellerSiteKey]: 'confirmed_absent' },
    },
    group_request: { confirmedExecutionScope: null },
    confirmation_summary: '批量报活动。',
  };
  prepare.group_request.confirmedExecutionScope = prepare.confirmed_execution_scope;

  const patch = applyHiddenSellerCampaignSkip(prepare, [{
    ...route,
    promotion_name: '95',
    detection_status: 'existing_without_visible_id',
  }]);

  assert.deepEqual(patch.seller_input.selected_targets, []);
  assert.deepEqual(patch.confirmed_execution_scope.seller_create_target_keys, []);
  assert.equal(patch.confirmed_execution_scope.activities[0].promotion_type, 'DEAL');
  assert.equal(patch.group_request.confirmedExecutionScope.activities[0].item_ids[0], 'MLM1');
  assert.equal(patch.seller_detection.existing_without_visible_id.length, 1);
  assert.match(patch.confirmation_summary, /已确认同名活动存在，但平台未返回活动ID，未报名/);
});

test('hidden duplicate starts the fake group with remaining DEAL scope and never retries seller creation', async () => {
  const sellerSiteKey = '3408885754|3407224975|MCO';
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-hidden-seller-'));
  const store = createSubmissionPersistence({ stateDir, now: () => '2026-07-17T00:00:00.000Z' });
  const prepare = {
    id: 'prepare-hidden-seller', client_submission_id: 'submit-hidden-seller', state: 'prepared',
    scope_hash: 'before', expires_at: '2026-07-18T00:00:00.000Z', resolved_action: 'enroll',
    discounts: { seller: 5, official: 6 },
    seller_input: {
      name: '95', selected_targets: [{ ...route, detection_status: 'confirmed_absent' }],
    },
    seller_detection: {
      existing: [], existing_without_visible_id: [],
      confirmed_absent: [{ ...route, detection_status: 'confirmed_absent' }],
      needs_manual_review: [], unreadable: [],
    },
    confirmed_execution_scope: {
      version: 2, action: 'enroll',
      activities: [{ ...route, promotion_id: 'P-DEAL', promotion_type: 'DEAL', item_status: 'candidate', item_ids: ['MLM1'] }],
      seller_create_target_keys: [sellerSiteKey],
      seller_target_states: { [sellerSiteKey]: 'confirmed_absent' },
    },
    group_request: {}, confirmation_summary: '批量报活动。',
  };
  prepare.group_request.confirmedExecutionScope = prepare.confirmed_execution_scope;
  store.create(prepare);
  const hiddenTarget = {
    ...route, promotion_name: '95', detection_status: 'existing_without_visible_id',
  };
  const creationPatch = {
    ...applyHiddenSellerCampaignSkip(prepare, [hiddenTarget]),
    scope_hash: 'after-hidden-skip',
  };
  let createCalls = 0;
  let groupCalls = 0;
  const result = await commitSubmission({
    store,
    prepareId: prepare.id,
    now: () => '2026-07-17T00:00:00.000Z',
    confirmText: 'REAL_SUBMIT',
    createConfirmText: 'CREATE_SELLER_CAMPAIGN',
    revalidate: async () => ({ scope_hash: 'before', reconfirm_required: false, execution_relation_count: 1 }),
    createSellerCampaigns: async () => {
      createCalls += 1;
      return {
        ok: true, failed_count: 0, recheck_missing_count: 0,
        existing_without_visible_id_count: 1,
        existing_without_visible_id: [hiddenTarget],
        prepared_patch: creationPatch,
      };
    },
    revalidateAfterCreation: async () => ({
      scope_hash: 'after-hidden-skip', reconfirm_required: false, execution_relation_count: 1,
    }),
    startGroup: async (submission) => {
      groupCalls += 1;
      assert.deepEqual(submission.confirmed_execution_scope.seller_create_target_keys, []);
      assert.equal(submission.confirmed_execution_scope.activities[0].promotion_type, 'DEAL');
      return { id: 'group-deal-only', status: 'queued' };
    },
  });
  assert.equal(result.group.id, 'group-deal-only');
  assert.equal(createCalls, 1);
  assert.equal(groupCalls, 1);
  const saved = store.load(prepare.id);
  assert.equal(saved.creation_result.existing_without_visible_id_count, 1);
  assert.equal(saved.seller_detection.existing_without_visible_id.length, 1);
});

test('a later verified webhook or catalog id automatically releases only the exact hidden route on the next prepare', async () => {
  const hidden = {
    ...route,
    detection_status: 'existing_without_visible_id',
    hidden_activity_names: ['95'],
  };
  const other = {
    account_id: '3332096437', child_user_id: '3333531550', site_id: 'MLM',
    detection_status: 'existing_without_visible_id',
    hidden_activity_names: ['95'],
  };
  const calls = [];
  const resolved = await resolveHiddenSellerCampaignTargets({
    targets: [hidden, other],
    recoverByName: async (target, name) => {
      calls.push(`${target.account_id}|${target.child_user_id}|${target.site_id}|${name}`);
      if (target.account_id !== route.account_id) return null;
      return { status: 'existing', promotion_id: 'C-MCO-HIDDEN-95', recovery_source: 'webhook_detail' };
    },
  });
  assert.deepEqual(calls, [
    '3408885754|3407224975|MCO|95',
    '3332096437|3333531550|MLM|95',
  ]);
  assert.equal(resolved[0].detection_status, 'existing');
  assert.equal(resolved[0].promotion_id, 'C-MCO-HIDDEN-95');
  assert.equal(resolved[1].detection_status, 'existing_without_visible_id');
  assert.equal(resolved[1].promotion_id || '', '');
});

test('recovered promotion status must be explicitly enrollable rather than merely nonterminal', () => {
  assert.equal(validateRecoveredSellerCampaign({
    candidate: campaign({ status: 'paused' }), target: route, name: '95',
    now: () => new Date('2026-07-17T00:00:00.000Z'),
  }), null);
  assert.equal(validateRecoveredSellerCampaign({
    candidate: campaign({ status: 'pending' }), target: route, name: '95',
    now: () => new Date('2026-07-17T00:00:00.000Z'),
  })?.promotion_id, 'C-MCO-HIDDEN-95');
});
