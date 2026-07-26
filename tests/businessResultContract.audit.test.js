import test from 'node:test';
import assert from 'node:assert/strict';

import {
  calculateDealPrice,
  moneyRuleForCurrency,
} from '../src/planner.js';
import { buildSubmitPayloadPreview } from '../src/promotionPayload.js';
import {
  CANCEL_LIVE_READ_CLASSIFICATION,
  CANCEL_RESULT_STATUS,
  buildCancelResultContract,
  countMutuallyExclusiveRelationResults,
} from '../src/executionResultContract.js';

test('discount price rounds down to the currency minor unit so the configured discount is not reduced', () => {
  assert.deepEqual(moneyRuleForCurrency('BRL'), {
    currency_id: 'BRL',
    precision: 2,
    minor_unit: 0.01,
  });

  const item = {
    original_price: 37.46,
    price: 37.46,
    currency_id: 'BRL',
  };
  const dealPrice = calculateDealPrice(item, {
    priceMode: 'discount',
    discountPercent: 14,
  });

  assert.equal(dealPrice, 32.21);
  assert.ok((1 - dealPrice / item.original_price) * 100 >= 14);

  const preview = buildSubmitPayloadPreview({
    promotion: { promotion_id: 'P-1', promotion_type: 'DEAL' },
    row: { status: 'planned', deal_price: dealPrice, item },
    action: 'enroll',
  });
  assert.equal(preview.payload.deal_price, 32.21);
  assert.equal(buildSubmitPayloadPreview({
    promotion: { promotion_id: 'P-1', promotion_type: 'DEAL' },
    row: { status: 'planned', deal_price: 32.2156, item },
    action: 'enroll',
  }).payload.deal_price, 32.21);
});

test('currency precision is explicit and discount rounding follows the minimum unit', () => {
  assert.deepEqual(moneyRuleForCurrency('CLP'), {
    currency_id: 'CLP',
    precision: 0,
    minor_unit: 1,
  });
  assert.equal(calculateDealPrice(
    { original_price: 101, currency_id: 'CLP' },
    { priceMode: 'discount', discountPercent: 14 },
  ), 86);

  assert.equal(calculateDealPrice(
    { original_price: 10.007, currency_id: 'BRL', currency_precision: 3 },
    { priceMode: 'discount', discountPercent: 14 },
  ), 8.606);
  assert.equal(calculateDealPrice(
    {
      original_price: 10.03,
      currency_id: 'BRL',
      currency_minor_unit: 0.05,
    },
    { priceMode: 'discount', discountPercent: 10 },
  ), 9);
});

test('cancel readback never infers removal from absence in a truncated live collection', () => {
  const full = buildCancelResultContract({
    plannedItemIds: ['A'],
    outcomes: [{ item_id: 'A', status: 'request_success' }],
    recheck: { completed: true, is_full_fetch: true, remainingItemIds: [] },
  });
  assert.equal(full.final_status_by_item.A, CANCEL_RESULT_STATUS.confirmedRemoved);
  assert.equal(
    full.live_read_classification_by_item.A,
    CANCEL_LIVE_READ_CLASSIFICATION.confirmedRemoved,
  );

  const truncated = buildCancelResultContract({
    plannedItemIds: ['A'],
    outcomes: [{ item_id: 'A', status: 'request_success' }],
    recheck: {
      completed: true,
      is_full_fetch: false,
      truncated: true,
      remainingItemIds: [],
    },
  });
  assert.equal(truncated.final_status_by_item.A, CANCEL_RESULT_STATUS.unverifiable);
  assert.equal(
    truncated.live_read_classification_by_item.A,
    CANCEL_LIVE_READ_CLASSIFICATION.unverifiable,
  );
  assert.deepEqual(truncated.terminal_counts, {
    relation_count: 1,
    success: 0,
    failed: 0,
    skipped: 0,
    platform_pending: 0,
    unresolved: 1,
    classified_count: 1,
    is_closed: true,
    is_resolved: false,
  });
});

test('relation terminal categories are mutually exclusive and exhaustive', () => {
  const counts = countMutuallyExclusiveRelationResults([
    { account_id: 'A', site_id: 'MLB', promotion_id: 'P1', promotion_type: 'DEAL', item_id: '1', status: 'request_success' },
    { account_id: 'A', site_id: 'MLB', promotion_id: 'P1', promotion_type: 'DEAL', item_id: '1', status: 'success' },
    { account_id: 'A', site_id: 'MLB', promotion_id: 'P1', promotion_type: 'DEAL', item_id: '2', status: 'failed' },
    { account_id: 'A', site_id: 'MLB', promotion_id: 'P1', promotion_type: 'DEAL', item_id: '3', status: 'skipped' },
    {
      account_id: 'A',
      site_id: 'MLB',
      promotion_id: 'P1',
      promotion_type: 'DEAL',
      item_id: '4',
      status: 'pending_verification',
      error_cn: '平台已明确返回 pending（待生效），本地执行已完成且不会重复提交',
    },
    { account_id: 'A', site_id: 'MLB', promotion_id: 'P1', promotion_type: 'DEAL', item_id: '5', status: 'live_unverifiable' },
  ]);

  assert.deepEqual(counts, {
    relation_count: 5,
    success: 1,
    failed: 1,
    skipped: 1,
    platform_pending: 1,
    unresolved: 1,
    classified_count: 5,
    is_closed: true,
    is_resolved: false,
  });
  assert.equal(
    counts.success + counts.failed + counts.skipped + counts.platform_pending + counts.unresolved,
    counts.relation_count,
  );
});
