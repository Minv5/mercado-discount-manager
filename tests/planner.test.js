import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { buildBatchPlans, buildPlan, cancelUntilEmpty, fetchCompleteness, filterPromotions, normalizeActivityName, promotionKey, summarizeSites } from '../src/planner.js';
import { decideCycleAction, nextDiscountFor } from '../src/cycle.js';
import { MercadoLibreClient, extractMarketplaceUsers, extractPromotions } from '../src/mlClient.js';
import { realSubmitProtection } from '../src/protection.js';
import { buildBatchConfirmationPackage, buildConfirmationPackage } from '../src/confirmationPackage.js';
import { ADAPTER_STATES, buildSubmitPayloadPreview, getPromotionAdapterState, requireExecutableSubmitPayload, requireItemStatus, summarizeSpecialPromotionFields } from '../src/promotionPayload.js';
import { normalizeSettings } from '../src/settings.js';
import { exportWorkspace } from '../src/exporter.js';
import { decideToday } from '../src/today.js';
import { queryFiltersFromSearchParams } from '../src/filterQuery.js';
import { DEFAULT_WRITE_CONCURRENCY, MAX_READ_CONCURRENCY, MAX_WRITE_CONCURRENCY, mapLimited, mapLimitedWithCap, normalizeConcurrency, normalizeWriteConcurrency } from '../src/concurrency.js';
import { PROMOTION_CREATION_STATUS } from '../src/promotionCreationStatus.js';
import { createAsyncLimiter, executePlannedRowsWithConcurrency } from '../src/executor.js';
import { buildCandidateIncompleteResolution, buildManualCandidateDraftRows, MANUAL_CANDIDATE_IMPORT_SOURCE } from '../src/candidateResolution.js';
import { buildSellerCampaignCreateConfirmation, buildSellerCampaignCreatePreview } from '../src/promotionCreation.js';
import {
  REAL_ENROLL_SMOKE_TARGETS,
  assertSmokeTargetMatches,
  buildRealEnrollSmokeConfirmation,
  buildRealEnrollSmokeExecuteDisabled,
  buildSmokeEnrollBody,
  buildSmokeEnrollRequestPreview,
  listRealEnrollSmokeTargets,
  validateRealEnrollSmokeRequest
} from '../src/realEnrollSmokeTest.js';
import {
  SMART_REAL_TEST_TARGET,
  buildSingleItemRealTestConfirmation,
  buildSmartEnrollPayload,
  buildSmartEnrollRequestPreview,
  validateSmartRealTestRelease
} from '../src/smartRealTest.js';
import {
  buildSmartCancelQuery,
  buildSmartCancelRequestPreview,
  extractSmartOfferId,
  limitSmartCancelPlan
} from '../src/smartCancel.js';
import {
  filterItemsByRequestedIds,
  requestedExecutionItemIds,
  requestedItemFilterErrorMessage
} from '../src/executionItemFilter.js';
import {
  INVENTORY_FALLBACK_SOURCE,
  buildSellerCampaignInventoryFallback,
  inventoryDetailToCandidateRow
} from '../src/inventoryFallback.js';
import { prepareOAuthStartFromConfig } from '../src/oauthConfig.js';
import { parseOAuthCallbackInput, selectCodeOnlyOAuthState } from '../src/oauthCallback.js';
import { toChineseError } from '../src/errors.js';
import { buildLegacyTaskSummaries, classifyFailureReason, summarizeUniqueFinalActionResults } from '../src/repository.js';

test('buildPlan enroll calculates discount price and applies boundaries', () => {
  const plan = buildPlan({
    action: 'enroll',
    promotion: { id: 'P-1', type: 'SELLER_CAMPAIGN', name: '自建活动' },
    discountPercent: 5,
    items: [
      { id: 'MLB1', status: 'candidate', original_price: 100, price: 100, min_discounted_price: 90, max_discounted_price: 99 },
      { id: 'MLB2', status: 'candidate', original_price: 100, price: 100, min_discounted_price: 96, max_discounted_price: 99 }
    ]
  });

  assert.equal(plan.total, 2);
  assert.equal(plan.planned, 1);
  assert.equal(plan.skipped, 1);
  assert.equal(plan.rows[0].deal_price, 95);
  assert.match(plan.rows[1].reason, /低于最低允许价/);
});

test('buildPlan update skips started item when current price already matches target', () => {
  const plan = buildPlan({
    action: 'update',
    promotion: { id: 'P-1', type: 'DEAL' },
    discountPercent: 6,
    items: [
      { id: 'MLB1', status: 'started', original_price: 100, price: 94 },
      { id: 'MLB2', status: 'candidate', original_price: 100, price: 100 }
    ]
  });

  assert.equal(plan.planned, 0);
  assert.equal(plan.skipped, 2);
  assert.match(plan.rows[0].reason, /已等于目标价/);
  assert.match(plan.rows[1].reason, /状态 candidate/);
});

test('buildPlan enroll does not skip candidate when candidate price equals target price', () => {
  const plan = buildPlan({
    action: 'enroll',
    promotion: { id: 'C-1', type: 'SELLER_CAMPAIGN' },
    discountPercent: 5,
    items: [
      { id: 'MLB1', status: 'candidate', original_price: 100, price: 95 }
    ]
  });

  assert.equal(plan.total, 1);
  assert.equal(plan.planned, 1);
  assert.equal(plan.rows[0].deal_price, 95);
});

test('buildPlan cancel only allows started items', () => {
  const plan = buildPlan({
    action: 'cancel',
    promotion: { id: 'P-1', type: 'DEAL' },
    items: [
      { id: 'MLB1', status: 'started' },
      { id: 'MLB2', status: 'pending' }
    ]
  });

  assert.equal(plan.planned, 1);
  assert.equal(plan.skipped, 1);
  assert.equal(plan.rows[0].reason, '将取消已开始活动商品');
});

test('inventory fallback builds seller campaign candidate drafts from child inventory scan', async () => {
  const client = {
    userId: '2668031897',
    scanMarketplaceUserItems: async () => ({
      ids: ['MLB1', 'MLB2', 'MLB3', 'MLB4'],
      total: 4,
      saved: 4,
      isFullFetch: true,
      sampleOnly: false,
      rawSummary: { pages_read: 1 }
    }),
    getMarketplaceItem: async (itemId) => ({
      id: itemId,
      status: 'active',
      price: itemId === 'MLB3' ? 200 : 100,
      original_price: null,
      currency_id: 'USD'
    })
  };
  const result = await buildSellerCampaignInventoryFallback({
    client,
    promotion: { promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', child_user_id: '2668031897' },
    startedItems: [{ id: 'MLB1' }],
    pendingItems: [{ id: 'MLB2' }],
    existingCandidateItems: [{ item_id: 'MLB4' }],
    listingStatus: 'all',
    discountPercent: 5
  });

  assert.equal(result.added_count, 1);
  assert.equal(result.fallback_rows[0].id, 'MLB3');
  assert.equal(result.fallback_rows[0].source, INVENTORY_FALLBACK_SOURCE);
  assert.equal(result.fallback_rows[0].suggested_discounted_price, 190);
  assert.equal(result.excluded_started_pending, 2);
  assert.equal(result.existing_candidate_count, 1);
});

test('inventory fallback candidate row uses current marketplace price as discount base', () => {
  const row = inventoryDetailToCandidateRow({
    detail: { id: 'MLM1', status: 'active', price: 35.55, original_price: 40.72, currency_id: 'USD' },
    promotion: { promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN' },
    discountPercent: 5
  });

  assert.equal(row.original_price, 35.55);
  assert.equal(row.price, 35.55);
  assert.equal(row.suggested_discounted_price, 33.77);
  assert.equal(row.candidate_source_quality, 'eligibility_unconfirmed_until_mercado_write');
});

test('fetchAllPromotionItems follows pagination until total is reached', async () => {
  const client = new MercadoLibreClient();
  const calls = [];
  client.getPromotionItems = async ({ offset, limit }) => {
    calls.push({ offset, limit });
    const allRows = [{ id: 'A' }, { id: 'B' }, { id: 'C' }];
    return { results: allRows.slice(offset, offset + limit), paging: { total: 3 } };
  };

  const result = await client.fetchAllPromotionItems({ promotionId: 'P', promotionType: 'DEAL', status: 'candidate', limit: 2 });

  assert.equal(result.results.length, 3);
  assert.deepEqual(calls, [{ offset: 0, limit: 2 }, { offset: 2, limit: 2 }]);
});

test('SMART promotion item live fetch uses a limit below 50', async () => {
  const client = new MercadoLibreClient();
  const calls = [];
  client.getPromotionItems = async ({ promotionType, status, offset, limit }) => {
    calls.push({ promotionType, status, offset, limit });
    return { results: [{ id: 'MLB1' }], paging: { total: 1 } };
  };

  const result = await client.fetchAllPromotionItems({ promotionId: 'P-SMART', promotionType: 'SMART', status: 'started', limit: 200 });

  assert.equal(result.results.length, 1);
  assert.deepEqual(calls, [{ promotionType: 'SMART', status: 'started', offset: 0, limit: 49 }]);
});

test('scanMarketplaceUserItems uses scan scroll_id for marketplace child inventory', async () => {
  const client = new MercadoLibreClient({ marketplace: true, userId: '2668031897' });
  const calls = [];
  client.searchMarketplaceUserItems = async ({ scrollId, status, limit }) => {
    calls.push({ scrollId: scrollId || null, status, limit });
    if (!scrollId) return { scroll_id: 'next-1', results: ['MLB1', 'MLB2'], paging: { total: 3 } };
    return { scroll_id: 'next-2', results: ['MLB2', 'MLB3'], paging: { total: 3 } };
  };

  const result = await client.scanMarketplaceUserItems({ status: 'all', maxItems: 'all' });

  assert.deepEqual(result.ids, ['MLB1', 'MLB2', 'MLB3']);
  assert.equal(result.total, 3);
  assert.equal(result.isFullFetch, true);
  assert.equal(result.rawSummary.duplicate_count, 1);
  assert.deepEqual(calls.map((call) => call.scrollId), [null, 'next-1']);
});

test('fetchAllPromotionItems marks total without details as api_incomplete', async () => {
  const client = new MercadoLibreClient();
  client.getPromotionItems = async () => ({
    results: null,
    paging: { total: 1108, limit: 10, offset: 0, searchAfter: 'opaque' }
  });

  const result = await client.fetchAllPromotionItems({ promotionId: 'C-1', promotionType: 'SELLER_CAMPAIGN', status: 'candidate', limit: 10 });

  assert.equal(result.total, 1108);
  assert.equal(result.results.length, 0);
  assert.equal(result.detailStatus, 'api_incomplete');
  assert.equal(result.blocked, true);
  assert.match(result.warning, /未返回候选明细/);
  assert.match(result.warning, /人工导入/);
});

test('fetchAllPromotionItems marks marketplace candidate detail gap with precise status', async () => {
  const client = new MercadoLibreClient({ marketplace: true });
  client.getPromotionItems = async () => ({
    results: null,
    paging: { total: 1108, limit: 10, offset: 0, searchAfter: 'opaque' }
  });

  const result = await client.fetchAllPromotionItems({ promotionId: 'C-MLB4605191', promotionType: 'SELLER_CAMPAIGN', status: 'candidate', limit: 10 });

  assert.equal(result.total, 1108);
  assert.equal(result.detailStatus, 'api_incomplete_marketplace_candidate');
  assert.equal(result.blocked, true);
  assert.match(result.warning, /marketplace child/);
  assert.match(result.warning, /禁止作为 fallback/);
});

test('fetchAllPromotionItems distinguishes sample partial fetch from full fetch', async () => {
  const client = new MercadoLibreClient();
  client.getPromotionItems = async ({ offset, limit }) => {
    const allItems = Array.from({ length: 120 }, (_, index) => ({
      id: `MLB${index + 1}`,
      status: 'candidate',
      original_price: 100,
      price: 100
    }));
    return {
      paging: { total: allItems.length, limit, offset },
      results: allItems.slice(offset, offset + limit)
    };
  };

  const sample = await client.fetchAllPromotionItems({ promotionId: 'P-1', promotionType: 'DEAL', status: 'candidate', maxItems: 50 });
  assert.equal(sample.saved, 50);
  assert.equal(sample.total, 120);
  assert.equal(sample.detailStatus, 'partial');
  assert.equal(sample.sampleOnly, true);
  assert.equal(sample.isFullFetch, false);

  const full = await client.fetchAllPromotionItems({ promotionId: 'P-1', promotionType: 'DEAL', status: 'candidate', maxItems: 'all' });
  assert.equal(full.saved, 120);
  assert.equal(full.total, 120);
  assert.equal(full.detailStatus, 'ok');
  assert.equal(full.sampleOnly, false);
  assert.equal(full.isFullFetch, true);
});

test('fetchAllPromotionItems clips collected rows to platform total', async () => {
  const client = new MercadoLibreClient();
  client.getPromotionItems = async ({ offset, limit }) => ({
    paging: { total: 8, limit, offset },
    results: Array.from({ length: limit }, (_, index) => ({
      id: `MLB${offset + index + 1}`,
      status: 'candidate',
      original_price: 100,
      price: 100
    }))
  });

  const result = await client.fetchAllPromotionItems({ promotionId: 'P-1', promotionType: 'DEAL', status: 'candidate', limit: 5, maxItems: 'all' });
  assert.equal(result.total, 8);
  assert.equal(result.saved, 8);
  assert.equal(result.results.length, 8);
  assert.equal(result.isFullFetch, true);
});

test('fetchAllPromotionItems uses searchAfter when offset repeats first page', async () => {
  const client = new MercadoLibreClient();
  const pages = {
    start: Array.from({ length: 50 }, (_, index) => ({ id: `MLB${index + 1}`, status: 'candidate' })),
    token1: Array.from({ length: 50 }, (_, index) => ({ id: `MLB${index + 51}`, status: 'candidate' })),
    token2: Array.from({ length: 20 }, (_, index) => ({ id: `MLB${index + 101}`, status: 'candidate' }))
  };
  client.getPromotionItems = async ({ searchAfter }) => {
    if (!searchAfter) return { paging: { total: 120, searchAfter: 'token1' }, results: pages.start };
    if (searchAfter === 'token1') return { paging: { total: 120, searchAfter: 'token2' }, results: pages.token1 };
    return { paging: { total: 120 }, results: pages.token2 };
  };

  const result = await client.fetchAllPromotionItems({ promotionId: 'P-1', promotionType: 'DEAL', status: 'candidate', maxItems: 'all' });
  assert.equal(result.saved, 120);
  assert.equal(result.total, 120);
  assert.equal(result.isFullFetch, true);
  assert.equal(result.results.at(-1).id, 'MLB120');
});

test('marketplace candidate pagination crosses null and empty searchAfter pages', async () => {
  const client = new MercadoLibreClient({ marketplace: true });
  const calls = [];
  client.getPromotionItems = async ({ searchAfter, limit }) => {
    calls.push({ searchAfter: searchAfter || null, limit });
    if (!searchAfter) return { paging: { total: 4, searchAfter: 'token-a' }, results: null };
    if (searchAfter === 'token-a') return { paging: { total: 4, searchAfter: 'token-b' }, results: [] };
    if (searchAfter === 'token-b') return { paging: { total: 4, searchAfter: 'token-c' }, results: [{ id: 'MLB1', status: 'candidate' }, { id: 'MLB2', status: 'candidate' }] };
    return { paging: { total: 4 }, results: [{ id: 'MLB3', status: 'candidate' }, { id: 'MLB4', status: 'candidate' }] };
  };

  const result = await client.fetchAllPromotionItems({ promotionId: 'C-1', promotionType: 'SELLER_CAMPAIGN', status: 'candidate', limit: 200, maxItems: 'all' });

  assert.equal(result.saved, 4);
  assert.equal(result.detailStatus, 'ok');
  assert.equal(result.isFullFetch, true);
  assert.equal(result.rawSummary.empty_page_count, 2);
  assert.deepEqual(calls.map((call) => call.searchAfter), [null, 'token-a', 'token-b', 'token-c']);
  assert.ok(calls.every((call) => call.limit <= 50));
});

test('marketplace candidate empty page safety limit marks readable subset as partial sparse', async () => {
  const client = new MercadoLibreClient({ marketplace: true });
  let tokenIndex = 0;
  client.getPromotionItems = async ({ searchAfter }) => {
    if (!searchAfter) return { paging: { total: 10, searchAfter: 'token-1' }, results: [{ id: 'MLB1', status: 'candidate' }] };
    tokenIndex += 1;
    return { paging: { total: 10, searchAfter: `token-${tokenIndex + 1}` }, results: [] };
  };

  const result = await client.fetchAllPromotionItems({
    promotionId: 'C-1',
    promotionType: 'SELLER_CAMPAIGN',
    status: 'candidate',
    maxItems: 'all',
    maxConsecutiveEmptyPages: 2,
    maxTotalEmptyPages: 5
  });

  assert.equal(result.saved, 1);
  assert.equal(result.detailStatus, 'partial_api_sparse_marketplace_candidate');
  assert.equal(result.sampleOnly, true);
  assert.equal(result.isFullFetch, false);
  assert.equal(result.blocked, false);
  assert.equal(result.rawSummary.empty_page_count, 2);
  assert.match(result.warning, /可见候选/);
});

test('marketplace candidate repeated token or duplicate page is treated as stalled partial', async () => {
  const client = new MercadoLibreClient({ marketplace: true });
  client.getPromotionItems = async ({ searchAfter }) => {
    if (!searchAfter) return { paging: { total: 3, searchAfter: 'same-token' }, results: [{ id: 'MLB1', status: 'candidate' }] };
    return { paging: { total: 3, searchAfter: 'same-token' }, results: [{ id: 'MLB1', status: 'candidate' }] };
  };

  const result = await client.fetchAllPromotionItems({ promotionId: 'C-1', promotionType: 'SELLER_CAMPAIGN', status: 'candidate', maxItems: 'all' });

  assert.equal(result.saved, 1);
  assert.equal(result.detailStatus, 'partial_api_sparse_marketplace_candidate');
  assert.equal(result.rawSummary.duplicate_count, 1);
  assert.equal(result.rawSummary.stop_reason, 'duplicate_page_stalled');
});

test('C-MLB style first null page can still save later searchAfter candidates as partial', async () => {
  const client = new MercadoLibreClient({ marketplace: true });
  client.getPromotionItems = async ({ searchAfter }) => {
    if (!searchAfter) return { paging: { total: 964, searchAfter: 'after-null' }, results: null };
    if (searchAfter === 'after-null') return { paging: { total: 964, searchAfter: 'after-items' }, results: [{ id: 'MLB6729392606', status: 'candidate', original_price: 100, price: 100 }] };
    return { paging: { total: 964 }, results: [] };
  };

  const result = await client.fetchAllPromotionItems({
    promotionId: 'C-MLB4605191',
    promotionType: 'SELLER_CAMPAIGN',
    status: 'candidate',
    maxItems: 'all',
    maxConsecutiveEmptyPages: 2
  });

  assert.equal(result.saved, 1);
  assert.equal(result.results[0].id, 'MLB6729392606');
  assert.equal(result.detailStatus, 'partial_api_sparse_marketplace_candidate');
  assert.equal(result.sampleOnly, true);
  assert.equal(result.isFullFetch, false);
});

test('cancelUntilEmpty keeps checking started items until none remain', async () => {
  let remaining = ['A', 'B'];
  const result = await cancelUntilEmpty({
    fetchStartedItems: async () => remaining.map((id) => ({ id })),
    cancelItem: async (item) => {
      remaining = remaining.filter((id) => id !== item.id);
    }
  });

  assert.equal(result.completed, true);
  assert.deepEqual(result.rounds.map((round) => round.remaining), [2, 0]);
});

test('cycle discounts increment only after complete run and switch to cancel at 10 percent', () => {
  assert.equal(nextDiscountFor({ promotionType: 'SELLER_CAMPAIGN' }), 5);
  assert.equal(nextDiscountFor({ promotionType: 'DEAL' }), 6);
  assert.equal(nextDiscountFor({ promotionType: 'DEAL', lastDiscount: 7, lastStatus: 'partial_or_failed' }), 7);
  assert.equal(nextDiscountFor({ promotionType: 'DEAL', lastDiscount: 9, lastStatus: 'completed' }), 10);
  assert.deepEqual(
    decideCycleAction({ promotionType: 'DEAL', currentDiscount: 10, hasStartedItems: true }).action,
    'cancel'
  );
});

test('decideToday prevents duplicate completed action and prioritizes cancel at 10 percent', () => {
  const today = new Date('2026-07-02T10:00:00');
  const promotions = [
    { account_id: 'A', site_id: 'MLB', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '95' },
    { account_id: 'A', site_id: 'MLM', promotion_id: 'P-1', promotion_type: 'DEAL', status: 'started', name: 'Deal' }
  ];
  const states = new Map();
  states.set(promotionKey(promotions[0]), { seller_discount_percent: 10, status: 'completed', updated_at: '2026-07-01T10:00:00' });
  states.set(promotionKey(promotions[1]), { official_discount_percent: 7, status: 'completed', updated_at: '2026-07-02T08:00:00' });
  const startedCounts = new Map([[promotionKey(promotions[0]), 3], [promotionKey(promotions[1]), 2]]);
  const decision = decideToday({ promotions, cycleStatesByPromotion: states, startedCountsByPromotion: startedCounts, today });
  assert.equal(decision.today_action, 'cancel');
  assert.match(decision.reason, /取消/);

  states.set(promotionKey(promotions[0]), { seller_discount_percent: 10, status: 'cancelled_complete', updated_at: '2026-07-02T08:00:00' });
  const completed = decideToday({ promotions: [promotions[0]], cycleStatesByPromotion: states, startedCountsByPromotion: new Map([[promotionKey(promotions[0]), 3]]), today });
  assert.equal(completed.today_action, 'completed');
  assert.equal(completed.already_completed, true);
});

test('marketplace helpers extract child users and promotions from common response shapes', () => {
  assert.deepEqual(
    extractMarketplaceUsers({
      marketplace_users: [
        { user_id: 2668031897, site_id: 'MLB', logistic_type: 'remote' },
        { id: 2668034127, site: 'MLM', type: 'remote' }
      ]
    }).map((row) => ({ user_id: row.user_id, site_id: row.site_id, logistic_type: row.logistic_type })),
    [
      { user_id: 2668031897, site_id: 'MLB', logistic_type: 'remote' },
      { user_id: 2668034127, site_id: 'MLM', logistic_type: 'remote' }
    ]
  );

  assert.deepEqual(
    extractPromotions({ results: [{ id: 'P-MLB1' }, { id: 'C-MLB2' }] }).map((row) => row.id),
    ['P-MLB1', 'C-MLB2']
  );
});

test('promotion filters and site summaries support site/type/status workbench views', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLB', child_user_id: '1', logistic_type: 'remote', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '95' },
    { account_id: 'A', site_id: 'MLB', child_user_id: '1', logistic_type: 'remote', promotion_id: 'P-1', promotion_type: 'DEAL', status: 'started', name: '07.07' },
    { account_id: 'A', site_id: 'MLM', child_user_id: '2', logistic_type: 'remote', promotion_id: 'P-2', promotion_type: 'SMART', status: 'pending', name: 'Smart Discounts' }
  ];

  assert.deepEqual(filterPromotions(promotions, { siteId: 'MLB' }).map((row) => row.promotion_id), ['C-1', 'P-1']);
  assert.deepEqual(filterPromotions(promotions, { promotionType: 'SELLER_CAMPAIGN' }).map((row) => row.promotion_id), ['C-1']);
  assert.deepEqual(filterPromotions(promotions, { status: 'pending' }).map((row) => row.promotion_id), ['P-2']);
  assert.equal(summarizeSites(promotions).find((row) => row.site_id === 'MLB').by_type.DEAL, 1);
});

test('promotion filters support multi site, multi type, keywords, and seller/official exclusions', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLB', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '95' },
    { account_id: 'A', site_id: 'MLB', promotion_id: 'P-1', promotion_type: 'DEAL', status: 'started', name: 'Hot Sale' },
    { account_id: 'A', site_id: 'MLM', promotion_id: 'P-2', promotion_type: 'SMART', status: 'started', name: 'Smart Discounts' },
    { account_id: 'A', site_id: 'MLC', promotion_id: 'L-1', promotion_type: 'LIGHTNING', status: 'pending', name: 'Lightning' }
  ];

  assert.deepEqual(
    filterPromotions(promotions, { siteIds: ['MLB', 'MLM'], promotionTypes: ['DEAL', 'SMART'], keywords: ['Hot', 'Smart'] }).map((row) => row.promotion_id),
    ['P-1', 'P-2']
  );
  assert.deepEqual(filterPromotions(promotions, { excludeSeller: true }).map((row) => row.promotion_id), ['P-1', 'P-2', 'L-1']);
  assert.deepEqual(filterPromotions(promotions, { excludeOfficial: true }).map((row) => row.promotion_id), ['C-1']);
});

test('promotion filters support deduped seller and official activity names without binding one promotion id', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLB', promotion_id: 'C-MLB-1', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '95' },
    { account_id: 'B', site_id: 'MLM', promotion_id: 'C-MLM-1', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '95' },
    { account_id: 'B', site_id: 'MLM', promotion_id: 'C-MLM-2', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '90' },
    { account_id: 'A', site_id: 'MLB', promotion_id: 'P-MLB-1', promotion_type: 'DEAL', status: 'started', name: 'Hot Sale' },
    { account_id: 'B', site_id: 'MLM', promotion_id: 'P-MLM-1', promotion_type: 'SMART', status: 'started', name: 'Hot Sale' }
  ];

  assert.deepEqual(
    filterPromotions(promotions, { sellerActivityNames: ['95'] }).map((row) => row.promotion_id),
    ['C-MLB-1', 'C-MLM-1', 'P-MLB-1', 'P-MLM-1']
  );
  assert.deepEqual(
    filterPromotions(promotions, { siteIds: ['MLM'], sellerActivityNames: ['95'] }).map((row) => row.promotion_id),
    ['C-MLM-1', 'P-MLM-1']
  );
  assert.deepEqual(
    filterPromotions(promotions, { officialActivityNames: ['Hot Sale'] }).map((row) => row.promotion_id),
    ['C-MLB-1', 'C-MLM-1', 'C-MLM-2', 'P-MLB-1', 'P-MLM-1']
  );
  assert.deepEqual(
    filterPromotions(promotions, { sellerActivityNames: ['95'], officialActivityNames: ['Hot Sale'] }).map((row) => row.promotion_id),
    ['C-MLB-1', 'C-MLM-1', 'P-MLB-1', 'P-MLM-1']
  );
});

test('activity name normalization merges visible whitespace variants but keeps real names distinct', () => {
  const variants = [
    '07.07 e Descontaco',
    ' 07.07  e   Descontaco ',
    '07.07\u00a0e\u3000Descontaco',
    '07.07 E DESCONTACO;'
  ];
  assert.equal(new Set(variants.map(normalizeActivityName)).size, 1);
  assert.notEqual(
    normalizeActivityName('CBT SaD - Est. Alta - Tech Julho'),
    normalizeActivityName('CBT SaD - Est. Alta - Industries Julho')
  );

  const promotions = variants.map((name, index) => ({
    account_id: 'A',
    site_id: index % 2 ? 'MLM' : 'MLB',
    promotion_id: `P-${index}`,
    promotion_type: 'DEAL',
    status: 'started',
    name
  })).concat([
    { account_id: 'A', site_id: 'MLM', promotion_id: 'P-tech', promotion_type: 'SMART', status: 'started', name: 'CBT SaD - Est. Alta - Tech Julho' },
    { account_id: 'A', site_id: 'MLM', promotion_id: 'P-ind', promotion_type: 'SMART', status: 'started', name: 'CBT SaD - Est. Alta - Industries Julho' }
  ]);

  assert.deepEqual(
    filterPromotions(promotions, { officialActivityNames: ['07.07 e Descontaco'] }).map((row) => row.promotion_id),
    ['P-0', 'P-1', 'P-2', 'P-3']
  );
  assert.deepEqual(
    filterPromotions(promotions, { officialActivityNames: ['CBT SaD - Est. Alta - Tech Julho'] }).map((row) => row.promotion_id),
    ['P-tech']
  );
});

test('GET promotion query parser matches JSON filter semantics for comma and repeated query', () => {
  const comma = queryFiltersFromSearchParams(new URLSearchParams('siteIds=MLB,MLM&promotionTypes=SELLER_CAMPAIGN,DEAL&keywords=95,C-MLM1209743&sellerActivityNames=95&officialActivityNames=Hot%20Sale&excludeSeller=1&excludeOfficial=false&status=started'));
  assert.deepEqual(comma.siteIds, ['MLB', 'MLM']);
  assert.deepEqual(comma.promotionTypes, ['SELLER_CAMPAIGN', 'DEAL']);
  assert.deepEqual(comma.keywords, ['95', 'C-MLM1209743']);
  assert.deepEqual(comma.sellerActivityNames, ['95']);
  assert.deepEqual(comma.officialActivityNames, ['Hot Sale']);
  assert.equal(comma.excludeSeller, true);
  assert.equal(comma.excludeOfficial, false);
  assert.equal(comma.status, 'started');

  const repeated = new URLSearchParams();
  repeated.append('siteIds', 'MLB');
  repeated.append('siteIds', 'MLM');
  repeated.append('promotionTypes', 'seller_campaign');
  repeated.append('promotionTypes', 'deal');
  repeated.append('keywords', '95');
  repeated.append('keywords', 'C-MLM1209743');
  repeated.append('sellerActivityNames', '95');
  repeated.append('officialActivityNames', 'Hot Sale');
  repeated.append('excludeOfficial', 'true');
  const parsed = queryFiltersFromSearchParams(repeated);
  assert.deepEqual(parsed.siteIds, ['MLB', 'MLM']);
  assert.deepEqual(parsed.promotionTypes, ['SELLER_CAMPAIGN', 'DEAL']);
  assert.deepEqual(parsed.keywords, ['95', 'C-MLM1209743']);
  assert.deepEqual(parsed.sellerActivityNames, ['95']);
  assert.deepEqual(parsed.officialActivityNames, ['Hot Sale']);
  assert.equal(parsed.excludeOfficial, true);
});

test('batch dry-run summarizes enroll and cancel plans across filtered activities', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLB', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '95' },
    { account_id: 'A', site_id: 'MLM', promotion_id: 'P-2', promotion_type: 'DEAL', status: 'started', name: 'Deal' }
  ];
  const itemsByPromotion = new Map();
  itemsByPromotion.set(promotionKey(promotions[0]), [
    { id: 'MLB1', status: 'candidate', original_price: 100, price: 100, min_discounted_price: 90, max_discounted_price: 99 }
  ]);
  itemsByPromotion.set(promotionKey(promotions[1]), [
    { id: 'MLM1', status: 'candidate', original_price: 100, price: 100, min_discounted_price: 93, max_discounted_price: 99 },
    { id: 'MLM2', status: 'started', original_price: 100, price: 94 }
  ]);

  const enroll = buildBatchPlans({
    action: 'enroll',
    promotions,
    itemsByPromotion,
    sellerDiscountPercent: 5,
    officialDiscountPercent: 6
  });
  assert.equal(enroll.totals.promotions, 2);
  assert.equal(enroll.totals.total, 3);
  assert.equal(enroll.totals.planned, 2);
  assert.equal(enroll.totals.skipped, 1);

  const cancelItems = new Map();
  cancelItems.set(promotionKey(promotions[0]), [{ id: 'MLB2', status: 'started' }]);
  cancelItems.set(promotionKey(promotions[1]), []);
  const cancel = buildBatchPlans({ action: 'cancel', promotions, itemsByPromotion: cancelItems });
  assert.equal(cancel.totals.planned, 1);
  assert.equal(cancel.totals.empty, 1);
});

test('filtered cancel dry-run plans started items only', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLB', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '95' },
    { account_id: 'A', site_id: 'MLM', promotion_id: 'P-2', promotion_type: 'DEAL', status: 'started', name: 'Deal' }
  ];
  const filtered = filterPromotions(promotions, { siteIds: ['MLB'], promotionTypes: ['SELLER_CAMPAIGN'] });
  const itemsByPromotion = new Map([[promotionKey(filtered[0]), [
    { id: 'MLB1', status: 'started' },
    { id: 'MLB2', status: 'candidate' }
  ]]]);
  const batch = buildBatchPlans({ action: 'cancel', promotions: filtered, itemsByPromotion });
  assert.equal(batch.totals.promotions, 1);
  assert.equal(batch.totals.planned, 1);
  assert.equal(batch.totals.skipped, 1);
});

test('batch plan treats api_incomplete fetch state as blocked, not empty', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLB', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '95' }
  ];
  const fetchStatesByPromotion = new Map();
  fetchStatesByPromotion.set(promotionKey(promotions[0]), {
    detail_status: 'api_incomplete',
    warning: '平台返回候选总数但未返回候选明细，需要接口专项处理'
  });

  const batch = buildBatchPlans({
    action: 'enroll',
    promotions,
    itemsByPromotion: new Map(),
    fetchStatesByPromotion
  });

  assert.equal(batch.totals.blocked, 1);
  assert.equal(batch.totals.empty, 0);
  assert.equal(batch.plans[0].blocked, true);
  assert.match(batch.plans[0].warning, /替代 API|人工导入/);
});

test('full enroll precheck blocks when platform total exceeds saved candidate count', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLB', promotion_id: 'P-1', promotion_type: 'DEAL', status: 'started', name: 'Deal' }
  ];
  const itemsByPromotion = new Map([[promotionKey(promotions[0]), [
    { id: 'MLB1', status: 'candidate', original_price: 100, price: 100 },
    { id: 'MLB2', status: 'candidate', original_price: 120, price: 120 }
  ]]]);
  const fetchStatesByPromotion = new Map([[promotionKey(promotions[0]), {
    detail_status: 'partial',
    platform_total: 5,
    saved_count: 2
  }]]);

  const batch = buildBatchPlans({
    action: 'enroll',
    promotions,
    itemsByPromotion,
    fetchStatesByPromotion,
    requireFullFetch: true
  });

  assert.equal(batch.plans[0].blocked, true);
  assert.equal(batch.plans[0].detail_status, 'not_full_fetch');
  assert.match(batch.plans[0].warning, /候选未全量读取/);

  const pkg = buildBatchConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    action: 'enroll',
    status: 'candidate',
    batch,
    request: { requireFullFetch: true, sampleOnly: false }
  });
  assert.equal(pkg.status, 'blocked');
  assert.equal(pkg.can_request_final_confirmation, false);
  assert.equal(pkg.promotions[0].platform_total, 5);
  assert.equal(pkg.promotions[0].saved_count, 2);
  assert.equal(pkg.promotions[0].is_full_fetch, false);
  assert.match(pkg.blocking_reasons.join('\n'), /先执行全量读取候选/);
});

test('seller campaign full enroll can use inventory fallback state when explicitly allowed', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLM', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '95' }
  ];
  const key = promotionKey(promotions[0]);
  const itemsByPromotion = new Map([[key, [
    { id: 'MLM1', status: 'candidate', original_price: 100, price: 100 },
    { id: 'MLM2', status: 'candidate', original_price: 200, price: 200 }
  ]]]);
  const fetchStatesByPromotion = new Map([[key, {
    detail_status: 'inventory_scan_fallback_ready',
    platform_total: 2,
    saved_count: 2,
    raw_json: JSON.stringify({
      source: 'inventory_scan_fallback',
      scan_total: 2632,
      scan_saved: 2632,
      added_count: 2,
      excluded_started_pending: 1273,
      detail_success: 1359,
      detail_failed: 0,
      listing_status: 'all'
    })
  }]]);

  const batch = buildBatchPlans({
    action: 'enroll',
    promotions,
    itemsByPromotion,
    fetchStatesByPromotion,
    requireFullFetch: true,
    allowInventoryFallback: true
  });

  assert.equal(batch.plans[0].blocked, undefined);
  assert.equal(batch.plans[0].fetch_info.inventory_fallback_ready, true);
  assert.equal(batch.totals.planned, 2);
  assert.equal(batch.sample_only, false);
});

test('sample enroll preview is marked sample_only and full fetch clears the block', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLB', promotion_id: 'P-1', promotion_type: 'DEAL', status: 'started', name: 'Deal' }
  ];
  const key = promotionKey(promotions[0]);
  const itemsByPromotion = new Map([[key, [
    { id: 'MLB1', status: 'candidate', original_price: 100, price: 100 }
  ]]]);
  const sampleStates = new Map([[key, { detail_status: 'partial', platform_total: 3, saved_count: 1 }]]);
  const sampleBatch = buildBatchPlans({
    action: 'enroll',
    promotions,
    itemsByPromotion,
    fetchStatesByPromotion: sampleStates,
    sampleOnly: true
  });
  assert.equal(sampleBatch.sample_only, true);
  assert.equal(sampleBatch.totals.sample_only, true);
  assert.equal(sampleBatch.plans[0].blocked, undefined);
  assert.equal(sampleBatch.plans[0].fetch_info.is_full_fetch, false);

  const fullStates = new Map([[key, { detail_status: 'ok', platform_total: 1, saved_count: 1 }]]);
  const fullBatch = buildBatchPlans({
    action: 'enroll',
    promotions,
    itemsByPromotion,
    fetchStatesByPromotion: fullStates,
    requireFullFetch: true
  });
  assert.equal(fullBatch.sample_only, false);
  assert.equal(fullBatch.plans[0].blocked, undefined);
  assert.equal(fullBatch.plans[0].fetch_info.is_full_fetch, true);
  const fullPkg = buildBatchConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    action: 'enroll',
    status: 'candidate',
    batch: fullBatch,
    request: { requireFullFetch: true, sampleOnly: false }
  });
  assert.equal(fullPkg.sample_only, false);
  assert.equal(fullPkg.promotions[0].is_full_fetch, true);
});

test('partial sparse marketplace candidate can dry-run readable subset but cannot full-release', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLM', promotion_id: 'C-MLM1209743', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: '95' }
  ];
  const key = promotionKey(promotions[0]);
  const itemsByPromotion = new Map([[key, [
    { id: 'MLM1', status: 'candidate', original_price: 100, price: 100 },
    { id: 'MLM2', status: 'candidate', original_price: 120, price: 120 }
  ]]]);
  const fetchStatesByPromotion = new Map([[key, {
    detail_status: 'partial_api_sparse_marketplace_candidate',
    platform_total: 1566,
    saved_count: 2,
    warning: '平台剩余候选未返回明细，不能标记为全量。',
    raw_json: JSON.stringify({
      pages_read: 14,
      empty_page_count: 5,
      unique_count: 2,
      duplicate_count: 1,
      stop_reason: 'empty_page_limit_or_no_next_results'
    })
  }]]);

  const subsetBatch = buildBatchPlans({
    action: 'enroll',
    promotions,
    itemsByPromotion,
    fetchStatesByPromotion,
    sampleOnly: true
  });
  assert.equal(subsetBatch.plans[0].blocked, undefined);
  assert.equal(subsetBatch.plans[0].plan.total, 2);
  assert.equal(subsetBatch.plans[0].fetch_info.partial_readable_subset, true);
  assert.equal(subsetBatch.plans[0].fetch_info.empty_page_count, 5);

  const fullBatch = buildBatchPlans({
    action: 'enroll',
    promotions,
    itemsByPromotion,
    fetchStatesByPromotion,
    requireFullFetch: true
  });
  assert.equal(fullBatch.plans[0].blocked, true);
  assert.equal(fullBatch.plans[0].detail_status, 'not_full_fetch');
  assert.match(fullBatch.plans[0].warning, /可见候选子集/);

  const pkg = buildBatchConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    action: 'enroll',
    status: 'candidate',
    batch: fullBatch,
    request: { requireFullFetch: true, sampleOnly: false }
  });
  assert.equal(pkg.status, 'blocked');
  assert.equal(pkg.can_request_final_confirmation, false);
  assert.equal(pkg.promotions[0].partial_readable_subset, true);
  assert.equal(pkg.promotions[0].empty_page_count, 5);
  assert.equal(pkg.promotions[0].saved_count, 2);
  assert.match(pkg.blocking_reasons.join('\n'), /平台剩余候选未返回明细|可见候选子集/);
});

test('batch plan and confirmation use all saved candidate rows and do not mix platform total with row count', () => {
  const promotion = { account_id: 'A', site_id: 'MLB', promotion_id: 'P-ALL', promotion_type: 'DEAL', status: 'started', name: 'All rows' };
  const rows = Array.from({ length: 120 }, (_, index) => ({
    id: `MLB${index + 1}`,
    status: 'candidate',
    original_price: 100,
    price: 100,
    min_discounted_price: 1,
    max_discounted_price: 99
  }));
  const key = promotionKey(promotion);
  const batch = buildBatchPlans({
    action: 'enroll',
    promotions: [promotion],
    itemsByPromotion: new Map([[key, rows]]),
    fetchStatesByPromotion: new Map([[key, { detail_status: 'ok', platform_total: 200, saved_count: 120 }]]),
    sampleOnly: true
  });

  assert.equal(batch.totals.total, 120);
  assert.equal(batch.plans[0].plan.rows.length, 120);

  const pkg = buildBatchConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    action: 'enroll',
    status: 'candidate',
    batch,
    request: { sampleOnly: true, requireFullFetch: false }
  });

  assert.equal(pkg.items_total, 120);
  assert.equal(pkg.platform_total, 200);
  assert.equal(pkg.promotions[0].items_total, 120);
  assert.equal(pkg.promotions[0].platform_total, 200);
  assert.equal(pkg.sample_items.length, 10);
});

test('api_incomplete keeps platform total as blocked but does not pretend rows exist', () => {
  const promotion = { account_id: 'A', site_id: 'MLB', promotion_id: 'C-BLOCK', promotion_type: 'SELLER_CAMPAIGN', status: 'started', name: 'Blocked' };
  const key = promotionKey(promotion);
  const batch = buildBatchPlans({
    action: 'enroll',
    promotions: [promotion],
    itemsByPromotion: new Map([[key, []]]),
    fetchStatesByPromotion: new Map([[key, { detail_status: 'api_incomplete_marketplace_candidate', platform_total: 1566, saved_count: 0 }]]),
    requireFullFetch: true
  });
  const pkg = buildBatchConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    action: 'enroll',
    status: 'candidate',
    batch,
    request: { requireFullFetch: true, sampleOnly: false }
  });

  assert.equal(pkg.items_total, 0);
  assert.equal(pkg.platform_total, 1566);
  assert.equal(pkg.blocked, 1566);
  assert.equal(pkg.promotions[0].items_total, 0);
  assert.equal(pkg.promotions[0].platform_total, 1566);
});

test('fetch completeness helper reports partial and full states', () => {
  const partial = fetchCompleteness({ detail_status: 'partial', platform_total: 50, saved_count: 20 }, 20);
  assert.equal(partial.platform_total, 50);
  assert.equal(partial.saved_count, 20);
  assert.equal(partial.detail_status, 'partial');
  assert.equal(partial.is_full_fetch, false);
  assert.equal(partial.sample_only, true);
  assert.equal(partial.missing_count, 30);
  assert.equal(fetchCompleteness({ detail_status: 'ok', platform_total: 20, saved_count: 20 }, 20).is_full_fetch, true);
});

test('real submit protection requires REAL_SUBMIT and then allows execution flow', () => {
  const missing = realSubmitProtection({ mode: 'real', confirmText: '' }, { batch: true });
  assert.equal(missing.allowed, false);
  assert.equal(missing.status, 400);

  const single = realSubmitProtection({ mode: 'real', confirmText: 'REAL_SUBMIT' });
  assert.equal(single.allowed, true);
  assert.equal(single.status, 200);

  const batch = realSubmitProtection({ mode: 'real', confirmText: 'REAL_SUBMIT' }, { batch: true });
  assert.equal(batch.allowed, true);
  assert.equal(batch.status, 200);
  assert.match(batch.message, /允许执行批量真实提交/);
  assert.doesNotMatch(batch.message, /预检|确认流程/);
});

test('real execution routes do not stop at confirmation package after REAL_SUBMIT', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');

  assert.doesNotMatch(serverSource, /protection\.status !== 409/);
  assert.match(serverSource, /if \(!protection\.allowed\) return sendJson\(res, protection\.status/);
  assert.match(serverSource, /url\.pathname === '\/api\/today\/execute'/);
  assert.doesNotMatch(standaloneSource, /真实写入预检包|不会自动执行 Mercado 写接口|生成真实写入预检包/);
  assert.match(standaloneSource, /\/api\/today\/execute/);
});

test('WinForms submit flow hides REAL_SUBMIT input dialog and sends internal confirm text', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');
  assert.doesNotMatch(standaloneSource, /ConfirmTextDialog|真实执行口令|请输入 REAL_SUBMIT/);
  assert.match(standaloneSource, /confirmText = "REAL_SUBMIT"/);
  assert.match(standaloneSource, /SubmitExecutionAsync/);
});

test('real submit flow prepares promotions and items before execution', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');

  assert.match(serverSource, /preparePromotionsForExecution/);
  assert.match(serverSource, /prepareItemsForExecution/);
  assert.match(serverSource, /未找到匹配活动。已尝试自动读取活动列表/);
  assert.match(serverSource, /商品未读取到或当前筛选下无可处理商品/);
  assert.match(standaloneSource, /开始\{SelectedSubmitModeText\(\)\}：店铺=\{SelectedComboText\(_accountSelect\)\}，站点=\{SelectedComboText\(_siteSelect\)\}/);
  assert.match(standaloneSource, /storeName = StoreNameForAccountId\(accountId\)/);
  assert.match(standaloneSource, /selectedStoreName = SelectedComboText\(_accountSelect\)/);
  assert.match(standaloneSource, /selectedSiteName = SelectedComboText\(_siteSelect\)/);
  assert.match(standaloneSource, /KnownStoreNameForAccountId\(accountId\)/);
  assert.match(standaloneSource, /"2651442567" => "湖北店"/);
  assert.match(standaloneSource, /"3332096437" => "湖南店"/);
  assert.match(standaloneSource, /"3408885754" => "广东店"/);
  assert.doesNotMatch(standaloneSource, /return "未命名店铺"/);
  assert.match(standaloneSource, /准备活动：/);
  assert.match(standaloneSource, /准备商品：/);
  assert.match(standaloneSource, /sellerActivityNames = sellerPromotion\.Length > 0/);
  assert.match(standaloneSource, /officialActivityNames = officialPromotion\.Length > 0/);
  assert.match(standaloneSource, /keywords = Array\.Empty<string>\(\)/);
});

test('WinForms activity selectors display deduped names and task grid hides raw internal ids/statuses', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');
  assert.match(standaloneSource, /new Dictionary<string, ActivityChoice>/);
  assert.match(standaloneSource, /NormalizeActivityNameKey\(displayName\)/);
  assert.match(standaloneSource, /AddActivityChoice\(sellerNames, key, displayName\)/);
  assert.match(standaloneSource, /AddActivityChoice\(officialNames, key, displayName\)/);
  assert.match(standaloneSource, /_sellerActivitySelect\.Items\.Add\(new ComboItem\(choice\.Key, choice\.DisplayName\)\)/);
  assert.match(standaloneSource, /_officialActivitySelect\.Items\.Add\(new ComboItem\(choice\.Key, choice\.DisplayName\)\)/);
  assert.match(standaloneSource, /NormalizeActivityDisplayName/);
  assert.match(standaloneSource, /UpdateComboDropDownWidth\(_sellerActivitySelect\)/);
  assert.match(standaloneSource, /UpdateComboDropDownWidth\(ComboBox combo\)/);
  assert.match(standaloneSource, /TextRenderer\.MeasureText/);
  assert.doesNotMatch(standaloneSource, /storePrefix.*PromotionTypeDisplayName/);
  assert.match(standaloneSource, /TaskActivityDisplayName\(task\)/);
  assert.match(standaloneSource, /TaskStatusDisplayName\(status\)/);
  assert.match(standaloneSource, /"running" => "执行中"/);
  assert.match(standaloneSource, /"partial_or_failed" => "部分完成\/有失败"/);
  assert.match(standaloneSource, /"empty_or_failed" => "未执行\/无可处理商品"/);
  assert.match(standaloneSource, /ActivityCountText\(existing\.SellerActivity, activityCount\)/);
});

test('WinForms task grid supports multi-select context menu copy and local-only delete', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  const repositorySource = fs.readFileSync(path.join(process.cwd(), 'src/repository.js'), 'utf8');

  assert.match(standaloneSource, /_taskGrid\.MultiSelect = true/);
  assert.match(standaloneSource, /_taskGrid\.ContextMenuStrip = _taskMenu/);
  assert.match(standaloneSource, /查看详情/);
  assert.match(standaloneSource, /复制详情/);
  assert.match(standaloneSource, /TextDetailForm/);
  assert.match(standaloneSource, /BuildSelectedTaskDetails/);
  assert.match(standaloneSource, /复制选中行/);
  assert.match(standaloneSource, /删除选中记录/);
  assert.match(standaloneSource, /_taskGrid\.Rows\[index\]\.Tag = row/);
  assert.match(standaloneSource, /\/api\/tasks\/delete/);
  assert.match(serverSource, /url\.pathname === '\/api\/tasks\/delete'/);
  assert.match(repositorySource, /DELETE FROM promo_action_results WHERE task_id IN/);
  assert.match(repositorySource, /DELETE FROM promo_tasks WHERE id IN/);
  const deleteTasksBody = repositorySource.match(/export function deleteTasks[\s\S]*?\n}\n/)?.[0] || '';
  assert.doesNotMatch(deleteTasksBody, /oauth_tokens/);
  assert.doesNotMatch(deleteTasksBody, /settings/);
});

test('WinForms separates service health failures from long business request timeouts', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');
  const clientSource = fs.readFileSync(path.join(process.cwd(), 'src/mlClient.js'), 'utf8');
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');

  assert.match(standaloneSource, /Timeout = Timeout\.InfiniteTimeSpan/);
  assert.match(standaloneSource, /RequestTimeoutFor\(path, isPost: true\)/);
  assert.match(standaloneSource, /BuildBusinessTimeoutMessage/);
  assert.match(standaloneSource, /if \(await IsHealthy\(\)\) throw new InvalidOperationException\(BuildBusinessTimeoutMessage/);
  assert.match(standaloneSource, /业务请求超时/);
  assert.match(clientSource, /AbortController/);
  assert.match(clientSource, /Mercado Libre API 请求超时/);
  assert.match(serverSource, /res\.on\('finish'/);
  assert.match(serverSource, /Date\.now\(\) - startedAt/);
});

test('submit execution uses background job progress instead of one long synchronous request', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');

  assert.match(serverSource, /executionJobs = new Map\(\)/);
  assert.match(serverSource, /\/api\/execution\/jobs\/start/);
  assert.match(serverSource, /executionJobCancelMatch/);
  assert.match(serverSource, /appendExecutionJobLog\(job, `读取 \${itemStatus} 商品/);
  assert.match(serverSource, /prepareOnly/);
  assert.match(serverSource, /onProgress: \(event\) =>/);
  assert.match(serverSource, /shouldCancel: \(\) => job\.cancel_requested/);

  assert.match(standaloneSource, /SubmitExecutionJobWrapperAsync/);
  assert.match(standaloneSource, /\/api\/execution\/jobs\/start/);
  assert.match(standaloneSource, /PollExecutionJobAsync/);
  assert.match(standaloneSource, /\/api\/execution\/jobs\/\{Uri\.EscapeDataString\(jobId\)\}/);
  assert.match(standaloneSource, /CancelCurrentExecutionJobAsync/);
  assert.match(standaloneSource, /_submitButton\.Text = "停止"/);
  assert.doesNotMatch(standaloneSource, /var endpoint = action\.Length == 0 \? "\/api\/today\/execute" : "\/api\/batch\/execute";\s+Log\("准备执行/s);
});

test('execution job exposes business userLogs and WinForms prefers them over debug logs', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');

  assert.match(serverSource, /userLogs: \[\]/);
  assert.match(serverSource, /function appendExecutionUserLog/);
  assert.match(serverSource, /storeName = request\.storeName/);
  assert.match(serverSource, /function siteDisplayName/);
  assert.match(serverSource, /function businessScope/);
  assert.match(serverSource, /店铺=\$\{request\.selectedStoreName/);
  assert.match(serverSource, /读取到活动/);
  assert.match(serverSource, /计划\$\{actionVerb\(action\)\}/);
  assert.match(serverSource, /提交完成，处理/);
  assert.match(standaloneSource, /userLogs/);
  assert.match(standaloneSource, /logProperty\.ValueKind != JsonValueKind\.Array && job\.TryGetProperty\("logs"/);
  assert.match(standaloneSource, /storeName = StoreNameForAccountId\(accountId\)/);
  assert.match(standaloneSource, /selectedSiteName = SelectedComboText\(_siteSelect\)/);
});

test('WinForms shows Chinese fallback when execution job result is null', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');
  const submitWrapper = standaloneSource.match(/private async Task SubmitExecutionJobWrapperAsync\(\)[\s\S]*?private async Task StartAndPollExecutionJobAsync/)?.[0] || '';

  assert.match(standaloneSource, /FriendlyExecutionErrorMessage/);
  assert.match(standaloneSource, /任务已结束，但后台没有返回完整汇总/);
  assert.match(standaloneSource, /TerminalJobMessage\(status, StringValue\(job, "error", ""\)\)/);
  assert.match(standaloneSource, /root\.ValueKind != JsonValueKind\.Object/);
  assert.match(standaloneSource, /任务已按停止规则结束，已保存已完成结果，请查看历史记录/);
  assert.match(submitWrapper, /提交执行未完整完成/);
  assert.doesNotMatch(submitWrapper, /Log\(\$"\{storeName\}：店铺任务失败："\s*\+\s*ex\.Message/);
  assert.doesNotMatch(submitWrapper, /MessageBox\.Show\(ex\.Message, "美客多折扣管家"/);
});

test('execution user logs use display total that cannot be lower than result counts', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');

  assert.match(serverSource, /function displayExecutionTotal/);
  assert.match(serverSource, /function displayProgressTotal/);
  assert.match(serverSource, /displayProgressTotal\(event\)/);
  assert.match(serverSource, /提交完成，处理 \$\{displayTotal\} 个，成功/);
  assert.match(serverSource, /const executionDisplayTotal = displayExecutionTotal\(execution\)/);
  assert.match(serverSource, /商品 \$\{executionDisplayTotal\}/);
  assert.match(standaloneSource, /var displayTotal = Math\.Max\(total, success \+ failed \+ skipped\)/);
  assert.match(standaloneSource, /商品 \{displayTotal\}/);
  assert.doesNotMatch(serverSource, /商品 \$\{execution\.total\}，成功/);
});

test('WinForms task grid keeps old assistant columns and moves store-site scope to details', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');
  const standaloneProjectSource = fs.readFileSync(path.join(process.cwd(), 'standalone/MercadoDiscountManager.Standalone.csproj'), 'utf8');
  const repositorySource = fs.readFileSync(path.join(process.cwd(), 'src/repository.js'), 'utf8');
  const dbSource = fs.readFileSync(path.join(process.cwd(), 'src/db.js'), 'utf8');
  const webSource = fs.readFileSync(path.join(process.cwd(), 'public/app.js'), 'utf8');

  assert.doesNotMatch(standaloneSource, /AddGridColumn\("store", "店铺"/);
  assert.doesNotMatch(standaloneSource, /AddGridColumn\("site", "站点"/);
  assert.match(repositorySource, /store_name: storeDisplayName/);
  assert.match(repositorySource, /site_name: siteDisplayName/);
  assert.match(standaloneSource, /StringValue\(task, "store_name"/);
  assert.match(standaloneSource, /StringValue\(task, "site_name"/);
  assert.match(standaloneSource, /DetailLines/);
  assert.match(standaloneSource, /范围：多个店铺\/站点，右键查看详情/);
  assert.match(standaloneSource, /ScopeText\(existing\.StoreNames, "多个店铺"\)/);
  assert.match(standaloneSource, /ScopeText\(existing\.SiteNames, "多个站点"\)/);
  assert.match(standaloneSource, /TaskMergeKey\(createdAt[\s\S]*isBatch\)/);
  assert.match(standaloneSource, /isBatch && string\.Equals\(action, "enroll"/);
  assert.match(standaloneSource, /activityKey = isBatch \? "__batch__"/);
  assert.match(standaloneSource, /seller_activity_text/);
  assert.match(standaloneSource, /official_activity_text/);
  assert.match(standaloneSource, /MergeActivityText\(existing\.SellerActivity, next\.SellerActivity\)/);
  assert.match(standaloneSource, /MergeActivityText\(existing\.OfficialActivity, next\.OfficialActivity\)/);
  assert.match(standaloneSource, /_taskGrid\.Font = new Font\("Microsoft YaHei", 10F/);
  assert.match(standaloneSource, /_taskGrid\.ColumnHeadersHeight = 38/);
  assert.match(standaloneSource, /_taskGrid\.RowTemplate\.Height = 34/);
  assert.match(standaloneSource, /_taskGrid\.DefaultCellStyle\.Padding = new Padding\(8, 5, 8, 5\)/);
  assert.match(standaloneSource, /MinimumWidth = 420/);
  assert.match(standaloneProjectSource, /<ApplicationHighDpiMode>PerMonitorV2<\/ApplicationHighDpiMode>/);
  assert.match(standaloneSource, /root\.RowStyles\.Add\(new RowStyle\(SizeType\.Absolute, 50\)\)/);
  assert.match(standaloneSource, /_todayLabel\.Font = new Font\("Microsoft YaHei", 10F, FontStyle\.Bold\)/);
  assert.match(standaloneSource, /_statusLabel\.Font = new Font\("Microsoft YaHei", 10F, FontStyle\.Regular\)/);
  assert.match(standaloneSource, /_logBox\.Font = new Font\("Microsoft YaHei", 10F, FontStyle\.Regular\)/);
  assert.match(standaloneSource, /combo\.Font = new Font\("Microsoft YaHei", 10F, FontStyle\.Regular\)/);
  assert.match(standaloneSource, /number\.Font = new Font\("Microsoft YaHei", 10F, FontStyle\.Regular\)/);
  assert.match(standaloneSource, /button\.Font = new Font\("Microsoft YaHei", 10F, FontStyle\.Regular\)/);
  assert.match(standaloneSource, /Font = new Font\("Microsoft YaHei", 10F\)/);
  assert.match(standaloneSource, /TaskDetailLines\(task/);
  assert.match(standaloneSource, /IntArray\(task, "task_ids"\)/);
  assert.match(standaloneSource, /\/api\/tasks\?limit=300/);
  assert.match(standaloneSource, /\/api\/tasks\/details\?taskIds=/);
  assert.match(standaloneSource, /EnsureSelectedTaskDetailsAsync/);
  assert.match(repositorySource, /function fetchTaskSummaryRows/);
  assert.match(repositorySource, /listTaskSummaries\(limit = 300, options = \{\}\)/);
  assert.match(repositorySource, /\.\.\.\(includeDetails \? \{ details: details\.map\(taskDetail\) \} : \{\}\)/);
  assert.match(repositorySource, /export function listTaskDetails/);
  assert.match(repositorySource, /ROW_NUMBER\(\) OVER/);
  assert.match(dbSource, /CREATE TABLE IF NOT EXISTS history_task_summary_cache/);
  assert.match(repositorySource, /TASK_SUMMARY_CACHE_VERSION/);
  assert.match(repositorySource, /loadPersistentTaskSummaryCache/);
  assert.match(repositorySource, /savePersistentTaskSummaryCache/);
  assert.match(repositorySource, /DELETE FROM history_task_summary_cache/);
  assert.match(standaloneSource, /"时间\\t动作\\t自建活动\\t官方活动/);
  assert.doesNotMatch(standaloneSource, /"时间\\t动作\\t店铺\\t站点\\t自建活动/);
  assert.match(standaloneSource, /"已报名商品数"/);
  assert.match(standaloneSource, /数量口径：主表商品数=真实已报名\/上架商品数/);
  assert.doesNotMatch(standaloneSource, /"候选商品数"/);
  assert.match(webSource, /已报名商品数/);
  assert.match(webSource, /实际处理数/);
  assert.doesNotMatch(webSource, /候选商品数/);
});

test('legacy task summaries collapse one execution window into a single old-assistant batch row', () => {
  const rows = [
    {
      id: 20,
      account_id: 'A2',
      store_name: '湖南店',
      promotion_id: '__BATCH__',
      promotion_type: 'BATCH',
      action: 'enroll',
      mode: 'real',
      total_count: 30,
      success_count: 8,
      failed_count: 10,
      skipped_count: 12,
      completed: 0,
      summary_json: '{"promotions_total":2}',
      created_at: '2026-07-03T10:40:00.000Z',
      updated_at: '2026-07-03T10:40:00.000Z'
    },
    {
      id: 19,
      account_id: 'A2',
      store_name: '湖南店',
      site_name: '墨西哥站',
      site_id: 'MLM',
      promotion_id: 'P-2',
      promotion_type: 'DEAL',
      promotion_name: 'Hot Sale',
      action: 'enroll',
      mode: 'real',
      discount_percent: 6,
      total_count: 30,
      success_count: 8,
      failed_count: 10,
      skipped_count: 12,
      status: 'partial_or_failed',
      created_at: '2026-07-03T10:05:00.000Z',
      updated_at: '2026-07-03T10:40:00.000Z'
    },
    {
      id: 18,
      account_id: 'A1',
      store_name: '广东店',
      promotion_id: '__BATCH__',
      promotion_type: 'BATCH',
      action: 'enroll',
      mode: 'real',
      total_count: 70,
      success_count: 40,
      failed_count: 20,
      skipped_count: 10,
      completed: 0,
      summary_json: '{"promotions_total":3}',
      created_at: '2026-07-03T10:30:00.000Z',
      updated_at: '2026-07-03T10:30:00.000Z'
    },
    {
      id: 17,
      account_id: 'A1',
      store_name: '广东店',
      site_name: '巴西站',
      site_id: 'MLB',
      promotion_id: 'C-1',
      promotion_type: 'SELLER_CAMPAIGN',
      promotion_name: '95',
      action: 'enroll',
      mode: 'real',
      discount_percent: 5,
      total_count: 70,
      success_count: 40,
      failed_count: 20,
      skipped_count: 10,
      status: 'partial_or_failed',
      created_at: '2026-07-03T10:00:00.000Z',
      updated_at: '2026-07-03T10:30:00.000Z'
    }
  ];
  const summaries = buildLegacyTaskSummaries(rows, 10);
  assert.equal(summaries.length, 1);
  assert.equal(summaries[0].promotion_id, '__BATCH__');
  assert.equal(summaries[0].store_name, '多个店铺（2个）');
  assert.equal(summaries[0].total_count, 48);
  assert.equal(summaries[0].success_count, 48);
  assert.equal(summaries[0].failed_count, 30);
  assert.equal(summaries[0].skipped_count, 22);
  assert.equal(JSON.parse(summaries[0].summary_json).processed_total, 100);
  assert.equal(JSON.parse(summaries[0].summary_json).main_quantity_type, '已报名商品数');
  assert.equal(summaries[0].promotions_total, 5);
  assert.equal(summaries[0].seller_activity_text, '5%');
  assert.equal(summaries[0].official_activity_text, '6%');
  assert.equal(summaries[0].details.length, 2);
  assert.deepEqual(summaries[0].task_ids.sort((a, b) => a - b), [17, 18, 19, 20]);
});

test('legacy task summaries keep batch details on the same day and hide prepare-only activity rows', () => {
  const rows = [
    {
      id: 900,
      account_id: 'A1',
      promotion_id: '__BATCH__',
      promotion_type: 'BATCH',
      action: 'enroll',
      mode: 'real',
      total_count: 30,
      success_count: 10,
      failed_count: 2,
      skipped_count: 18,
      summary_json: '{"promotions_total":2}',
      created_at: '2026-07-05T10:10:00.000Z',
      updated_at: '2026-07-05T10:10:00.000Z'
    },
    {
      id: 899,
      account_id: 'A1',
      promotion_id: 'C-SAME-DAY',
      promotion_type: 'SELLER_CAMPAIGN',
      action: 'enroll',
      mode: 'real',
      discount_percent: 6,
      total_count: 12,
      success_count: 8,
      failed_count: 1,
      skipped_count: 3,
      created_at: '2026-07-05T10:02:00.000Z',
      updated_at: '2026-07-05T10:07:00.000Z'
    },
    {
      id: 898,
      account_id: 'A1',
      promotion_id: 'P-SAME-DAY',
      promotion_type: 'DEAL',
      action: 'enroll',
      mode: 'real',
      discount_percent: 7,
      total_count: 18,
      success_count: 2,
      failed_count: 1,
      skipped_count: 15,
      created_at: '2026-07-05T10:01:00.000Z',
      updated_at: '2026-07-05T10:08:00.000Z'
    },
    {
      id: 897,
      account_id: 'A2',
      promotion_id: 'P-PREP-ONLY',
      promotion_type: 'DEAL',
      action: 'enroll',
      mode: 'dry-run',
      discount_percent: 7,
      total_count: 99,
      success_count: 0,
      failed_count: 0,
      skipped_count: 1,
      created_at: '2026-07-05T10:00:00.000Z',
      updated_at: '2026-07-05T10:00:00.000Z'
    },
    {
      id: 100,
      account_id: 'A1',
      promotion_id: 'P-OLD-DAY',
      promotion_type: 'DEAL',
      action: 'enroll',
      mode: 'real',
      discount_percent: 5,
      total_count: 20,
      success_count: 1,
      failed_count: 19,
      skipped_count: 0,
      created_at: '2026-07-03T10:01:00.000Z',
      updated_at: '2026-07-03T10:08:00.000Z'
    }
  ];

  const summaries = buildLegacyTaskSummaries(rows, 10);
  const batch = summaries.find((row) => row.promotion_id === '__BATCH__');
  assert.ok(batch);
  assert.equal(batch.seller_activity_text, '6%');
  assert.equal(batch.official_activity_text, '7%');
  assert.deepEqual(batch.details.map((detail) => detail.id).sort((a, b) => a - b), [898, 899]);
  assert.deepEqual(batch.task_ids.sort((a, b) => a - b), [897, 898, 899, 900]);
  assert.equal(summaries.some((row) => row.id === 897), false);
  assert.equal(summaries.some((row) => row.id === 100), true);
});

test('history failure summaries use business reasons and a wide reason column', () => {
  const repositorySource = fs.readFileSync(path.join(process.cwd(), 'src/repository.js'), 'utf8');
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');

  assert.match(repositorySource, /under_review\|item status is not allowed/);
  assert.match(repositorySource, /reason: '商品审核中'/);
  assert.match(repositorySource, /reason: '授权中途失效'/);
  assert.match(repositorySource, /reason: 'SMART未发送'/);
  assert.match(repositorySource, /sent_to_api: false/);
  assert.match(repositorySource, /刷新授权后重跑失败商品/);
  assert.doesNotMatch(repositorySource, /账号权限不足或应用权限不足'\\s*,\\s*count/);

  assert.match(standaloneSource, /MinimumWidth = 420/);
  assert.match(standaloneSource, /ToolTipText = row\.ReasonTooltipText/);
  assert.match(standaloneSource, /失败原因汇总：/);
  assert.match(standaloneSource, /未发送接口/);
});

test('history failure summaries strip raw json and technical token wording', () => {
  const auth = classifyFailureReason('{"message":"invalid access token","status":401}');
  assert.equal(auth.reason, '授权中途失效');
  assert.doesNotMatch(auth.reason, /token/i);

  const jsonWrapped = classifyFailureReason('{"message":"Mercado 2889 invalid_parameter bad_request","error":"bad_request","status":400}');
  assert.equal(jsonWrapped.reason, '请求参数不符合平台要求');
  assert.doesNotMatch(jsonWrapped.reason, /[{}"]|message|Mercado/i);

  const fallback = classifyFailureReason('{"message":"Mercado 未分类失败"}');
  assert.doesNotMatch(fallback.reason, /[{}"]|message|Mercado/i);

  const rows = buildLegacyTaskSummaries([
    {
      id: 32,
      account_id: 'A1',
      store_name: '湖北店',
      promotion_id: '__BATCH__',
      promotion_type: 'BATCH',
      action: 'enroll',
      mode: 'real',
      total_count: 3,
      success_count: 0,
      failed_count: 3,
      skipped_count: 0,
      completed: 0,
      summary_json: JSON.stringify({
        failure_reasons: [
          { reason: 'token中途失效', count: 2 },
          { reason: '{"message":"Mercado 2889 invalid_parameter"}', count: 1 }
        ]
      }),
      created_at: '2026-07-03T11:00:00.000Z',
      updated_at: '2026-07-03T11:00:00.000Z'
    }
  ], 5);
  const summary = JSON.parse(rows[0].summary_json);
  const text = summary.failure_reasons.map((reason) => reason.reason).join('，');
  assert.match(text, /授权中途失效/);
  assert.match(text, /请求参数不符合平台要求/);
  assert.doesNotMatch(text, /token|[{}"]|message|Mercado/i);
  assert.equal(rows[0].short_failure_reason, '账号授权需刷新2，参数错误1');
  assert.doesNotMatch(rows[0].short_failure_reason, /授权失效|授权2|审核中\d|SMART\d/);
  assert.equal(rows[0].full_failure_reasons[0].reason, '授权中途失效');
  assert.equal(rows[0].full_failure_reasons[1].reason, '请求参数不符合平台要求');
});

test('history short failure summary stays readable instead of code-like abbreviations', () => {
  const rows = buildLegacyTaskSummaries([
    {
      id: 42,
      account_id: 'A1',
      store_name: '湖北店',
      promotion_id: '__BATCH__',
      promotion_type: 'BATCH',
      action: 'enroll',
      mode: 'real',
      total_count: 10,
      success_count: 0,
      failed_count: 10,
      skipped_count: 4,
      completed: 0,
      summary_json: JSON.stringify({
        failure_reasons: [
          { reason: '授权中途失效', count: 3 },
          { reason: '商品审核中', count: 2 },
          { reason: 'SMART未发送', count: 1 }
        ]
      }),
      created_at: '2026-07-03T12:00:00.000Z',
      updated_at: '2026-07-03T12:00:00.000Z'
    }
  ], 5);
  assert.equal(rows[0].short_failure_reason, '账号授权需刷新3，商品审核中2，SMART未报名1，其他跳过4');
  assert.match(rows[0].short_failure_reason, /账号授权需刷新/);
  assert.match(rows[0].short_failure_reason, /商品审核中/);
  assert.match(rows[0].short_failure_reason, /SMART未报名/);
  assert.doesNotMatch(rows[0].short_failure_reason, /授权失效|(^|，)授权3($|，)|(^|，)审核中2($|，)|(^|，)SMART1($|，)/);
});

test('history failure summaries translate rate limit and fetch failed from old summaries', () => {
  assert.equal(classifyFailureReason('rate limit exceeded Libr').reason, '平台限流');
  assert.equal(classifyFailureReason('fetch failed').reason, '网络失败');
  assert.equal(classifyFailureReason('ratelimi467').reason, '平台限流');
  assert.equal(classifyFailureReason('fetchfai129').reason, '网络失败');

  const rows = buildLegacyTaskSummaries([
    {
      id: 450,
      account_id: 'A1',
      store_name: '湖南店',
      promotion_id: '__BATCH__',
      promotion_type: 'BATCH',
      action: 'cancel',
      mode: 'real',
      total_count: 9007,
      success_count: 7725,
      failed_count: 609,
      skipped_count: 673,
      completed: 0,
      summary_json: JSON.stringify({
        failure_reasons: [
          { reason: 'rate limit exceeded Libr', count: 467 },
          { reason: 'fetch failed', count: 129 },
          { reason: '未读取到可处理候选商品', count: 13 }
        ],
        skipped_reasons: [{ reason: '其他跳过', count: 673 }]
      }),
      created_at: '2026-07-07T02:00:25.502Z',
      updated_at: '2026-07-07T02:00:25.505Z'
    }
  ], 5);

  assert.equal(rows[0].short_failure_reason, '平台限流467，网络失败129，无候选商品13，其他跳过673');
  assert.doesNotMatch(rows[0].short_failure_reason, /ratelimi|fetchfai|rate limit|fetch failed/i);
  const summary = JSON.parse(rows[0].summary_json);
  assert.deepEqual(summary.failure_reasons.map((reason) => reason.reason), ['平台限流', '网络失败', '未读取到可处理候选商品']);
});

test('history short failure summary never shows skipped reason count above skipped total', () => {
  const rows = buildLegacyTaskSummaries([
    {
      id: 50,
      account_id: 'A1',
      store_name: '湖北店',
      promotion_id: '__BATCH__',
      promotion_type: 'BATCH',
      action: 'update',
      mode: 'real',
      total_count: 5,
      success_count: 0,
      failed_count: 5,
      skipped_count: 2,
      completed: 0,
      summary_json: JSON.stringify({
        failure_reasons: [{ reason: '请求参数不符合平台要求', count: 5 }],
        skipped_reasons: [{ reason: '已是目标价格', count: 99 }]
      }),
      created_at: '2026-07-03T13:00:00.000Z',
      updated_at: '2026-07-03T13:00:00.000Z'
    }
  ], 5);
  assert.equal(rows[0].short_failure_reason, '参数错误5，已是目标价格2');
  assert.doesNotMatch(rows[0].short_failure_reason, /99/);
});

test('history unique final result counts ignore retry inflation and recheck-only rows', () => {
  const summary = summarizeUniqueFinalActionResults([
    { id: 1, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'cancel', item_id: 'MLB1', status: 'failed', error_cn: '平台接口超时' },
    { id: 2, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'cancel', item_id: 'MLB1', status: 'success' },
    { id: 3, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'cancel', item_id: 'MLB1', status: 'success' },
    { id: 4, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'cancel', item_id: 'MLB2', status: 'success' },
    { id: 5, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'cancel', item_id: 'MLB2', status: 'item_remaining_started' },
    { id: 6, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'cancel', item_id: 'MLB3', status: 'skipped', error_cn: '执行任务已停止，未开始的商品已跳过' },
    { id: 7, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'cancel', item_id: 'MLB4', status: 'failed', error_cn: '缺少或无效的活动报价信息' }
  ]);

  assert.equal(summary.total, 4);
  assert.equal(summary.success, 2);
  assert.equal(summary.failed, 1);
  assert.equal(summary.skipped, 1);
  assert.ok(summary.success + summary.failed + summary.skipped <= summary.total);
  assert.equal(summary.failure_reasons[0].reason, '缺少活动报价ID');
  assert.equal(summary.failure_reasons[0].count, 1);
  assert.equal(summary.skipped_reasons[0].count, 1);
});

test('history batch counts non-item activity-level failures without splitting activity rows', () => {
  const summary = summarizeUniqueFinalActionResults([
    { id: 1, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'enroll', item_id: 'MLB1', status: 'success' },
    { id: 2, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'enroll', item_id: 'MLB2', status: 'success' },
    { id: 3, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'enroll', item_id: 'MLB3', status: 'failed', error_cn: 'Item status is not allowed (under_review)' },
    { id: 4, account_id: 'A1', promotion_id: 'P-1', promotion_type: 'DEAL', action: 'enroll', item_id: '', status: 'failed', error_cn: '平台还有 20 个候选未返回明细，本次未执行。' }
  ]);

  assert.equal(summary.total, 23);
  assert.equal(summary.success, 2);
  assert.equal(summary.failed, 21);
  assert.equal(summary.skipped, 0);
  assert.ok(summary.success + summary.failed + summary.skipped <= summary.total);

  const rows = buildLegacyTaskSummaries([
    {
      id: 90,
      account_id: 'A1',
      promotion_id: '__BATCH__',
      promotion_type: 'BATCH',
      action: 'enroll',
      mode: 'real',
      total_count: 3,
      success_count: 2,
      failed_count: 21,
      skipped_count: 0,
      summary_json: '{"promotions_total":1}',
      created_at: '2026-07-05T10:10:00.000Z',
      updated_at: '2026-07-05T10:10:00.000Z'
    },
    {
      id: 89,
      account_id: 'A1',
      promotion_id: 'P-1',
      promotion_type: 'DEAL',
      promotion_name: 'Hot Sale',
      action: 'enroll',
      mode: 'real',
      discount_percent: 7,
      total_count: 3,
      success_count: 2,
      failed_count: 21,
      skipped_count: 0,
      created_at: '2026-07-05T10:00:00.000Z',
      updated_at: '2026-07-05T10:08:00.000Z'
    }
  ], 10);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].promotion_id, '__BATCH__');
  assert.equal(rows[0].official_activity_text, '7%');
  assert.notEqual(rows[0].official_activity_text, 'Hot Sale');
});

test('history grid uses short failure summary while selected row exposes full reason table', () => {
  const repositorySource = fs.readFileSync(path.join(process.cwd(), 'src', 'repository.js'), 'utf8');
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone', 'Program.cs'), 'utf8');
  const webSource = fs.readFileSync(path.join(process.cwd(), 'public', 'app.js'), 'utf8');

  assert.match(repositorySource, /short_failure_reason/);
  assert.match(repositorySource, /full_failure_reasons/);
  assert.match(repositorySource, /SMART未参与批量报名/);
  assert.match(standaloneSource, /StringValue\(task, "short_failure_reason", TaskReason\(task\)\)/);
  assert.match(standaloneSource, /full_failure_reasons/);
  assert.match(standaloneSource, /SelectionChanged \+= \(_, _\) => ShowSelectedTaskSummaryInLog\(\)/);
  assert.match(standaloneSource, /完整失败原因/);
  assert.match(webSource, /row\.short_failure_reason/);
});

test('concurrency wording says local protection limit, not tested platform maximum', () => {
  const files = [
    'README.md',
    '使用说明.txt',
    'docs/assistant-parity-checklist.md',
    'docs/official-api-notes.md',
    'public/index.html',
    'standalone/Program.cs'
  ];
  const combined = files.map((file) => fs.readFileSync(path.join(process.cwd(), file), 'utf8')).join('\n');

  assert.doesNotMatch(combined, /最大并发/);
  assert.doesNotMatch(combined, /最大 5/);
  assert.doesNotMatch(combined, /max concurrency/i);
  assert.doesNotMatch(combined, /最大写入/);
  assert.match(combined, /只读压测建议|后台压测工具已启用逐商品落盘|本地保护范围/);
  assert.match(combined, /不是 Mercado 平台最大值|不是平台最大值/);
  assert.match(combined, /保守 300-320|300-320/);
});

test('confirmation package includes required real-write precheck fields', () => {
  const campaign = {
    account_id: '2651442567',
    merchant_id: '2651442567',
    site_id: 'MLB',
    child_user_id: '2668031897',
    logistic_type: 'remote',
    promotion_id: 'C-MLB4605191',
    promotion_type: 'SELLER_CAMPAIGN',
    name: '95'
  };
  const plan = buildPlan({
    action: 'update',
    promotion: campaign,
    discountPercent: 5,
    items: [{ id: 'MLB1', status: 'started', original_price: 100, price: 99, min_discounted_price: 90, max_discounted_price: 99 }]
  });

  const pkg = buildConfirmationPackage({
    account: { account_id: '2651442567', site_id: 'CBT' },
    campaign,
    action: 'update',
    status: 'started',
    plan,
    request: { priceMode: 'discount', writeConcurrency: 4 }
  });

  assert.equal(pkg.package_type, 'real_write_precheck');
  assert.equal(pkg.account_id, '2651442567');
  assert.equal(pkg.merchant_id, '2651442567');
  assert.equal(pkg.site_id, 'MLB');
  assert.equal(pkg.child_user_id, '2668031897');
  assert.equal(pkg.promotion_id, 'C-MLB4605191');
  assert.equal(pkg.promotion_type, 'SELLER_CAMPAIGN');
  assert.equal(pkg.planned, 1);
  assert.equal(pkg.skipped, 0);
  assert.equal(pkg.blocked, 0);
  assert.equal(pkg.write_concurrency, 4);
  assert.equal(pkg.can_request_final_confirmation, true);
  assert.equal(pkg.sample_items[0].item_id, 'MLB1');
  assert.equal(pkg.sample_items[0].target_deal_price, 95);
  assert.match(pkg.expected_impact_summary, /可执行 1/);
  assert.match(pkg.recheck_method, /重新读取 started/);
  assert.match(pkg.risk_prompts.join('\n'), /写入并发 4/);
});

test('batch confirmation package includes normalized write concurrency', () => {
  const promotion = { account_id: 'A', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', site_id: 'MLB', name: '95' };
  const batch = buildBatchPlans({
    action: 'enroll',
    promotions: [promotion],
    itemsByPromotion: new Map([[promotionKey(promotion), [{ item_id: 'MLB1', status: 'candidate', original_price: 100, price: 100 }]]])
  });
  const pkg = buildBatchConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    action: 'enroll',
    status: 'candidate',
    batch,
    request: { writeConcurrency: 999 }
  });

  assert.equal(pkg.write_concurrency, MAX_WRITE_CONCURRENCY);
  assert.equal(pkg.promotions[0].planned, 1);
  assert.match(pkg.expected_impact_summary, new RegExp(`写入并发为 ${MAX_WRITE_CONCURRENCY}`));
  assert.match(pkg.risk_prompts.join('\n'), new RegExp(`写入并发 ${MAX_WRITE_CONCURRENCY}`));
});

test('api_incomplete confirmation package is blocked and cannot recommend real execution', () => {
  const campaign = { promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', site_id: 'MLB', name: '95' };
  const plan = buildPlan({ action: 'enroll', promotion: campaign, items: [] });
  const pkg = buildConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    campaign,
    action: 'enroll',
    status: 'candidate',
    plan,
    fetchState: {
      detail_status: 'api_incomplete',
      platform_total: 1108,
      warning: '平台返回候选总数但未返回候选明细，需要接口专项处理'
    }
  });

  assert.equal(pkg.status, 'blocked');
  assert.equal(pkg.blocked, 1108);
  assert.equal(pkg.can_request_final_confirmation, false);
  assert.match(pkg.blocking_reasons[0], /未返回候选明细/);
  assert.equal(pkg.candidate_resolution.can_real_enroll, false);
  assert.match(pkg.candidate_resolution.safe_options.map((item) => item.label).join('\n'), /人工导入/);
});

test('marketplace candidate incomplete confirmation package exposes forbidden fallback metadata', () => {
  const campaign = { promotion_id: 'C-MLB4605191', promotion_type: 'SELLER_CAMPAIGN', site_id: 'MLB', name: '95' };
  const plan = buildPlan({ action: 'enroll', promotion: campaign, items: [] });
  const pkg = buildConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    campaign,
    action: 'enroll',
    status: 'candidate',
    plan,
    fetchState: {
      detail_status: 'api_incomplete_marketplace_candidate',
      platform_total: 1108
    }
  });

  assert.equal(pkg.status, 'blocked');
  assert.equal(pkg.blocked, 1108);
  assert.equal(pkg.candidate_resolution.state, 'api_incomplete_marketplace_candidate');
  assert.match(pkg.candidate_resolution.message, /marketplace child/);
  assert.match(pkg.candidate_resolution.forbidden_fallbacks.join('\n'), /近似参数/);
  assert.match(pkg.candidate_resolution.safe_options.map((item) => item.label).join('\n'), /Mercado 支持/);
});

test('candidate api_incomplete resolution offers safe alternatives and manual drafts require readonly details', () => {
  const resolution = buildCandidateIncompleteResolution({
    promotionId: 'C-MLB4605191',
    promotionType: 'SELLER_CAMPAIGN',
    platformTotal: 1108
  });
  assert.equal(resolution.severity, 'blocking');
  assert.equal(resolution.can_real_enroll, false);
  assert.match(resolution.safe_options.map((item) => item.code).join(','), /wait_alternative_api/);
  assert.match(resolution.safe_options.map((item) => item.code).join(','), /manual_candidate_import/);
  assert.match(resolution.manual_import_requirements.join('\n'), /只读商品详情|真实报名/);

  const drafts = buildManualCandidateDraftRows({ itemIds: 'MLB1, MLB2\nMLB1' });
  assert.equal(drafts.length, 2);
  assert.equal(drafts[0].status, 'candidate');
  assert.equal(drafts[0].raw.source, MANUAL_CANDIDATE_IMPORT_SOURCE);
  assert.equal(drafts[0].raw.detail_status, 'needs_readonly_detail');
  assert.equal(drafts[0].raw.requires_readonly_detail, true);
});

test('SMART offer_id rows produce preview payload but remain not submit-ready', () => {
  const smartRow = {
    status: 'planned',
    deal_price: 94,
    item: {
      item_id: 'MLB1',
      status: 'candidate',
      raw: {
        offer_id: 'CANDIDATE-MLB1-1',
        seller_percentage: 11.5,
        meli_percentage: 1.3,
        price: 26.18,
        original_price: 30.01,
        start_date: '2026-07-01T03:00:00Z',
        end_date: '2026-08-01T03:00:00Z'
      }
    },
    reason: '可执行'
  };
  const smartPreview = buildSubmitPayloadPreview({
    promotion: { promotion_id: 'P-1', promotion_type: 'SMART' },
    row: smartRow,
    action: 'enroll'
  });
  assert.equal(smartPreview.can_submit, false);
  assert.equal(smartPreview.preview_only, true);
  assert.equal(smartPreview.status, 'ready_for_preview');
  assert.equal(smartPreview.adapter_state, ADAPTER_STATES.ready_for_preview);
  assert.equal(smartPreview.requires_limited_real_test, true);
  assert.equal(smartPreview.adapter.adapter_state, ADAPTER_STATES.ready_for_preview);
  assert.equal(smartPreview.adapter.official_parameters_confirmed, false);
  assert.equal(smartPreview.payload.offer_id, 'CANDIDATE-MLB1-1');
  assert.equal(smartPreview.payload.promotion_type, 'SMART');
  assert.match(smartPreview.adapter.next_step, /单商品真实验证/);
  assert.match(smartPreview.adapter.payload_evidence, /PIX\/BANK/);
  assert.equal(smartPreview.field_summary.offer_id, 'CANDIDATE-MLB1-1');
  assert.match(smartPreview.reason, /实验预览已就绪/);
});

test('SMART single item real test payload builder outputs offer_id body only for exact target', () => {
  const payload = buildSmartEnrollPayload(SMART_REAL_TEST_TARGET);
  assert.deepEqual(payload, {
    promotion_id: 'P-MLB17755282',
    promotion_type: 'SMART',
    offer_id: 'CANDIDATE-MLB6729392606-76453189919'
  });

  const requestPreview = buildSmartEnrollRequestPreview(SMART_REAL_TEST_TARGET);
  assert.equal(requestPreview.method, 'POST');
  assert.equal(requestPreview.item_id, 'MLB6729392606');
  assert.equal(requestPreview.preview_only, true);
  assert.deepEqual(requestPreview.body, payload);
  assert.throws(
    () => buildSmartEnrollPayload({ ...SMART_REAL_TEST_TARGET, item_id: 'MLB-OTHER' }),
    /字段不匹配/
  );
});

test('SMART single item real test release rejects missing or mismatched fields', () => {
  const missingCode = validateSmartRealTestRelease({
    ...SMART_REAL_TEST_TARGET,
    confirmText: 'REAL_SUBMIT'
  });
  assert.equal(missingCode.allowed, false);
  assert.equal(missingCode.target_match, true);
  assert.equal(missingCode.confirm_text_ok, true);
  assert.equal(missingCode.release_code_present, false);
  assert.match(missingCode.reasons.join('\n'), /缺少 supervisorReleaseCode/);

  const mismatch = validateSmartRealTestRelease({
    ...SMART_REAL_TEST_TARGET,
    item_id: 'MLB-OTHER',
    confirmText: 'REAL_SUBMIT',
    supervisorReleaseCode: 'ANY'
  });
  assert.equal(mismatch.allowed, false);
  assert.equal(mismatch.target_match, false);
  assert.match(mismatch.mismatches.join(','), /item_id/);
});

test('SMART single item release code remains non-executable while policy is disabled', () => {
  const policy = {
    enabled: false,
    release_code_issued: true,
    release_code: 'ONE_TIME_CODE',
    requires_confirm_text: 'REAL_SUBMIT',
    requires_supervisor_release_code: true,
    release_code_source: 'test only'
  };
  const release = validateSmartRealTestRelease({
    ...SMART_REAL_TEST_TARGET,
    confirmText: 'REAL_SUBMIT',
    supervisorReleaseCode: 'ONE_TIME_CODE'
  }, policy);
  assert.equal(release.allowed, false);
  assert.equal(release.release_code_matches, true);
  assert.equal(release.would_allow_if_enabled, false);
  assert.match(release.reasons.join('\n'), /未启用/);
});

test('SMART single item final confirmation package contains recheck and no auto retry/cancel plan', () => {
  const pkg = buildSingleItemRealTestConfirmation({
    ...SMART_REAL_TEST_TARGET,
    confirmText: 'REAL_SUBMIT'
  });
  assert.equal(pkg.package_type, 'single_item_real_test_confirmation');
  assert.equal(pkg.status, 'blocked_release_not_enabled');
  assert.equal(pkg.can_execute_now, false);
  assert.equal(pkg.real_write_not_executed, true);
  assert.equal(pkg.write_concurrency, 1);
  assert.equal(pkg.target.item_id, 'MLB6729392606');
  assert.equal(pkg.request_preview.body.offer_id, 'CANDIDATE-MLB6729392606-76453189919');
  assert.match(pkg.recheck_method.join('\n'), /candidate\/pending\/started/);
  assert.match(pkg.failure_handling.join('\n'), /不自动重试/);
  assert.match(pkg.failure_handling.join('\n'), /不自动取消/);
  assert.match(pkg.hard_blocks.join('\n'), /不支持批量 SMART/);
});

test('SMART started cancel preview uses OFFER offer_id query from raw item fields', () => {
  const promotion = { promotion_id: 'P-MLB17755282', promotion_type: 'SMART', child_user_id: '2668031897', site_id: 'MLB' };
  const item = {
    item_id: 'MLB6729392606',
    status: 'started',
    price: 19.62,
    original_price: 21.76,
    raw_json: JSON.stringify({
      id: 'MLB6729392606',
      status: 'started',
      offer_id: 'OFFER-MLB6729392606-13231665297',
      seller_percentage: 8.9,
      meli_percentage: 1,
      price: 19.62,
      original_price: 21.76
    })
  };

  assert.equal(extractSmartOfferId(item), 'OFFER-MLB6729392606-13231665297');
  const query = buildSmartCancelQuery({ promotion, item });
  assert.deepEqual(query, {
    promotion_type: 'SMART',
    promotion_id: 'P-MLB17755282',
    offer_id: 'OFFER-MLB6729392606-13231665297'
  });
  const preview = buildSmartCancelRequestPreview({ promotion, item, marketplace: true });
  assert.equal(preview.method, 'DELETE');
  assert.equal(preview.body, null);
  assert.equal(preview.query.offer_id, 'OFFER-MLB6729392606-13231665297');
  assert.match(preview.path_template, /offer_id/);
  assert.equal(preview.field_evidence.offer_id_is_started_offer, true);
});

test('SMART cancel requires started OFFER offer_id and sample limiter keeps remaining rows unsubmitted', () => {
  const promotion = { promotion_id: 'P-1', promotion_type: 'SMART' };
  assert.throws(
    () => buildSmartCancelQuery({
      promotion,
      item: { item_id: 'MLB1', status: 'started', raw_json: JSON.stringify({ offer_id: 'CANDIDATE-MLB1-1' }) }
    }),
    /started OFFER-\*/
  );

  const plan = {
    total: 3,
    planned: 3,
    skipped: 0,
    rows: [
      { status: 'planned', item: { item_id: 'MLB1' } },
      { status: 'planned', item: { item_id: 'MLB2' } },
      { status: 'planned', item: { item_id: 'MLB3' } }
    ]
  };
  const limited = limitSmartCancelPlan(plan, 1);
  assert.equal(limited.planned, 1);
  assert.equal(limited.skipped, 2);
  assert.equal(limited.rows[0].status, 'planned');
  assert.equal(limited.rows[1].status, 'skipped');
  assert.match(limited.rows[1].reason, /未发送接口/);
});

test('execution job item filter plans only requested item ids before batch planning', () => {
  const promotions = [
    { account_id: '2651442567', promotion_id: 'P-MLB17757148', promotion_type: 'SMART' }
  ];
  const key = promotionKey(promotions[0]);
  const itemsByPromotion = new Map([[key, [
    { item_id: 'MLB6927588934', status: 'started', raw_json: JSON.stringify({ offer_id: 'OFFER-MLB6927588934-13231665526' }) },
    { item_id: 'MLB9999999999', status: 'started', raw_json: JSON.stringify({ offer_id: 'OFFER-MLB9999999999-1' }) }
  ]]]);

  const request = { itemIds: ['MLB6927588934'] };
  assert.deepEqual(requestedExecutionItemIds(request), ['MLB6927588934']);
  const filtered = filterItemsByRequestedIds({ promotions, itemsByPromotion, request });
  assert.equal(filtered.hasFilter, true);
  assert.deepEqual(filtered.missingItemIds, []);
  assert.deepEqual(filtered.matchedItemIds, ['MLB6927588934']);
  assert.equal(filtered.itemsByPromotion.get(key).length, 1);
  assert.equal(filtered.itemsByPromotion.get(key)[0].item_id, 'MLB6927588934');

  const batch = buildBatchPlans({
    action: 'cancel',
    promotions,
    itemsByPromotion: filtered.itemsByPromotion
  });
  assert.equal(batch.totals.total, 1);
  assert.equal(batch.plans[0].plan.rows[0].item.item_id, 'MLB6927588934');
});

test('execution job item filter blocks missing item ids instead of filling with other activity items', () => {
  const promotions = [
    { account_id: '2651442567', promotion_id: 'P-MLB17757148', promotion_type: 'SMART' }
  ];
  const key = promotionKey(promotions[0]);
  const itemsByPromotion = new Map([[key, [
    { item_id: 'MLB6927588934', status: 'started', raw_json: JSON.stringify({ offer_id: 'OFFER-MLB6927588934-13231665526' }) }
  ]]]);

  const filtered = filterItemsByRequestedIds({
    promotions,
    itemsByPromotion,
    request: { items: [{ itemId: 'MLB-NOT-FOUND' }] }
  });
  assert.equal(filtered.hasFilter, true);
  assert.deepEqual(filtered.missingItemIds, ['MLB-NOT-FOUND']);
  assert.equal(filtered.itemsByPromotion.get(key).length, 0);
  assert.match(requestedItemFilterErrorMessage(filtered, 'started'), /未改为处理同活动其它商品/);

  const batch = buildBatchPlans({
    action: 'cancel',
    promotions,
    itemsByPromotion: filtered.itemsByPromotion
  });
  assert.equal(batch.totals.total, 0);
});

test('execution job item filter does not duplicate one requested item across activities', () => {
  const promotions = [
    { account_id: '2651442567', promotion_id: 'P-SMART-1', promotion_type: 'SMART' },
    { account_id: '2651442567', promotion_id: 'P-SMART-2', promotion_type: 'SMART' }
  ];
  const itemsByPromotion = new Map([
    [promotionKey(promotions[0]), [
      { item_id: 'MLB-DUP', status: 'started', raw_json: JSON.stringify({ offer_id: 'OFFER-MLB-DUP-1' }) }
    ]],
    [promotionKey(promotions[1]), [
      { item_id: 'MLB-DUP', status: 'started', raw_json: JSON.stringify({ offer_id: 'OFFER-MLB-DUP-2' }) },
      { item_id: 'MLB-OTHER', status: 'started', raw_json: JSON.stringify({ offer_id: 'OFFER-MLB-OTHER-1' }) }
    ]]
  ]);

  const filtered = filterItemsByRequestedIds({
    promotions,
    itemsByPromotion,
    request: { itemIds: ['MLB-DUP'] }
  });
  const plannedCount = [...filtered.itemsByPromotion.values()].reduce((sum, items) => sum + items.length, 0);
  const batch = buildBatchPlans({ action: 'cancel', promotions, itemsByPromotion: filtered.itemsByPromotion });

  assert.deepEqual(filtered.matchedItemIds, ['MLB-DUP']);
  assert.equal(plannedCount, 1);
  assert.equal(batch.totals.total, 1);
  assert.equal(batch.totals.planned, 1);
});

test('SMART cancel planned row keeps raw_json offer_id for execution query building', () => {
  const promotion = { promotion_id: 'P-MLB17757148', promotion_type: 'SMART' };
  const batch = buildBatchPlans({
    action: 'cancel',
    promotions: [promotion],
    itemsByPromotion: new Map([[promotionKey(promotion), [{
      item_id: 'MLB6927588934',
      status: 'started',
      raw_json: JSON.stringify({ offer_id: 'OFFER-MLB6927588934-13231665526' })
    }]]])
  });
  const row = batch.plans[0].plan.rows[0];
  assert.equal(row.item.offer_id, 'OFFER-MLB6927588934-13231665526');
  const query = buildSmartCancelQuery({ promotion, item: row.item });
  assert.equal(query.offer_id, 'OFFER-MLB6927588934-13231665526');
});

test('Mercado cancelItem appends offer_id to DELETE query when provided', async () => {
  const client = new MercadoLibreClient({ accessToken: 'test-token', userId: '2668031897', callerId: '2668031897', marketplace: true });
  client.request = async (requestPath, options) => ({ requestPath, options });
  const response = await client.cancelItem({
    itemId: 'MLB6927588934',
    promotionId: 'P-MLB17757148',
    promotionType: 'SMART',
    offerId: 'OFFER-MLB6927588934-13231665526'
  });

  const requestPath = String(response.requestPath);
  assert.match(requestPath, /offer_id=OFFER-MLB6927588934-13231665526/);
  assert.match(requestPath, /promotion_type=SMART/);
  assert.match(requestPath, /promotion_id=P-MLB17757148/);
  assert.equal(response.options.method, 'DELETE');
});

test('Mercado client can return safe HTTP response metadata when requested', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ message: 'accepted', id: 'MLB1' }), {
    status: 202,
    headers: { 'content-type': 'application/json', 'x-request-id': 'REQ-1', authorization: 'should-not-return' }
  });
  try {
    const client = new MercadoLibreClient({ accessToken: 'test-token' });
    const response = await client.request('/test', { includeResponseMeta: true });
    assert.equal(response.http_status, 202);
    assert.equal(response.body.message, 'accepted');
    assert.equal(response.headers['x-request-id'], 'REQ-1');
    assert.equal(Object.hasOwn(response.headers, 'authorization'), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('SMART missing offer_id blocks preview with adapter_fields_incomplete', () => {
  const preview = buildSubmitPayloadPreview({
    promotion: { promotion_id: 'P-1', promotion_type: 'SMART' },
    row: {
      status: 'planned',
      deal_price: 94,
      item: {
        item_id: 'MLB1',
        status: 'candidate',
        raw: { seller_percentage: 11.5, meli_percentage: 1.3, price: 26.18, original_price: 30.01 }
      }
    },
    action: 'enroll'
  });

  assert.equal(preview.can_submit, false);
  assert.equal(preview.preview_only, false);
  assert.equal(preview.status, 'adapter_fields_incomplete');
  assert.equal(preview.adapter_state, ADAPTER_STATES.parameters_unconfirmed);
  assert.match(preview.adapter.missing_fields.join(','), /offer_id/);
});

test('LIGHTNING generates official body preview but remains not submit-ready', () => {
  const lightningRow = {
    status: 'planned',
    deal_price: 120,
    item: {
      item_id: 'MLM1',
      status: 'candidate',
      raw: {
        stock: { min: 5, max: 197 },
        min_discounted_price: 2.9,
        price: 144.45,
        original_price: 168.93
      }
    },
    reason: '可执行'
  };
  const lightningPreview = buildSubmitPayloadPreview({
    promotion: { promotion_id: 'LGH-1', promotion_type: 'LIGHTNING' },
    row: lightningRow,
    action: 'enroll'
  });
  assert.equal(lightningPreview.can_submit, false);
  assert.equal(lightningPreview.preview_only, true);
  assert.equal(lightningPreview.status, ADAPTER_STATES.ready_for_limited_real_test);
  assert.equal(lightningPreview.adapter_state, ADAPTER_STATES.ready_for_limited_real_test);
  assert.equal(lightningPreview.requires_limited_real_test, true);
  assert.deepEqual(lightningPreview.field_summary.stock, { min: 5, max: 197 });
  assert.deepEqual(lightningPreview.payload, {
    deal_id: 'LGH-1',
    deal_price: 144.45,
    original_price: 168.93,
    promotion_type: 'LIGHTNING',
    stock: 5
  });
  assert.equal(lightningPreview.adapter.preview_only, true);
  assert.match(lightningPreview.reason, /官方 body 已确认/);
  assert.match(lightningPreview.adapter.payload_evidence, /Lightning Deal/);
});

test('executable payload guard only allows submit-ready direct promotion payloads', () => {
  const directPreview = buildSubmitPayloadPreview({
    promotion: { promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN' },
    row: {
      status: 'planned',
      deal_price: 95,
      item: { item_id: 'MLB1', status: 'candidate', original_price: 100, price: 100 }
    },
    action: 'enroll'
  });
  assert.deepEqual(requireExecutableSubmitPayload(directPreview), {
    promotion_id: 'C-1',
    promotion_type: 'SELLER_CAMPAIGN',
    deal_price: 95
  });

  const lightningPreview = buildSubmitPayloadPreview({
    promotion: { promotion_id: 'LGH-1', promotion_type: 'LIGHTNING' },
    row: {
      status: 'planned',
      deal_price: 120,
      item: {
        item_id: 'MLM1',
        status: 'candidate',
        raw: { stock: { min: 5 }, min_discounted_price: 2.9, price: 144.45, original_price: 168.93 }
      }
    },
    action: 'enroll'
  });
  assert.equal(lightningPreview.payload.promotion_type, 'LIGHTNING');
  assert.throws(() => requireExecutableSubmitPayload(lightningPreview), /不能批量放行|真实写入仍需小样本验证/);
});

test('LIGHTNING missing required fields blocks preview with explicit reasons', () => {
  const preview = buildSubmitPayloadPreview({
    promotion: { promotion_id: 'LGH-1', promotion_type: 'LIGHTNING' },
    row: {
      status: 'planned',
      deal_price: 120,
      item: {
        item_id: 'MLM1',
        status: 'candidate',
        raw: { stock: {}, price: 144.45 }
      }
    },
    action: 'enroll'
  });

  assert.equal(preview.can_submit, false);
  assert.equal(preview.preview_only, false);
  assert.equal(preview.status, 'adapter_fields_incomplete');
  assert.equal(preview.adapter_state, ADAPTER_STATES.parameters_unconfirmed);
  assert.match(preview.adapter.missing_fields.join(','), /stock.min/);
  assert.match(preview.adapter.missing_fields.join(','), /original_price/);
});

test('special promotion adapter_state lists missing SMART offer_id fields', () => {
  const state = getPromotionAdapterState({
    promotionType: 'SMART',
    row: {
      item: {
        raw: {
          price: 50
        }
      }
    },
    action: 'enroll'
  });
  assert.equal(state.adapter_state, ADAPTER_STATES.parameters_unconfirmed);
  assert.equal(state.status, 'adapter_fields_incomplete');
  assert.equal(state.can_submit, false);
  assert.equal(state.preview_only, false);
  assert.equal(state.official_parameters_confirmed, false);
  assert.match(state.missing_fields.join(','), /offer_id/);
  assert.match(state.reason, /不能生成 offer_id/);
});

test('SMART and LIGHTNING field summaries enter confirmation package samples', () => {
  const campaign = { promotion_id: 'P-SMART', promotion_type: 'SMART', site_id: 'MLM', name: 'Smart Discounts' };
  const plan = buildPlan({
    action: 'enroll',
    promotion: campaign,
    items: [{
      id: 'MLB1',
      status: 'candidate',
      price: 26.18,
      original_price: 30.01,
      offer_id: 'CANDIDATE-MLB1-1',
      seller_percentage: 11.5,
      meli_percentage: 1.3
    }]
  });
  const pkg = buildConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    campaign,
    action: 'enroll',
    status: 'candidate',
    plan
  });

  assert.equal(pkg.status, 'blocked');
  assert.equal(pkg.can_request_final_confirmation, false);
  assert.equal(pkg.blocked, 1);
  assert.equal(pkg.sample_items[0].special_fields.offer_id, 'CANDIDATE-MLB1-1');
  assert.equal(pkg.sample_items[0].special_fields.seller_percentage, 11.5);
  assert.equal(pkg.sample_items[0].adapter_state, ADAPTER_STATES.ready_for_preview);
  assert.equal(pkg.sample_items[0].requires_limited_real_test, true);
  assert.equal(pkg.sample_items[0].preview_payload.offer_id, 'CANDIDATE-MLB1-1');
  assert.match(pkg.sample_items[0].payload_evidence, /PIX\/BANK/);
  assert.match(pkg.sample_items[0].adapter_next_step, /单商品真实验证/);
  assert.match(pkg.blocking_reasons[0], /实验预览已就绪/);
});

test('LIGHTNING confirmation package exposes official body preview and remains blocked', () => {
  const campaign = { promotion_id: 'LGH-MLM1000', promotion_type: 'LIGHTNING', site_id: 'MLM', name: 'LIGHTNING' };
  const plan = buildPlan({
    action: 'enroll',
    promotion: campaign,
    items: [{
      id: 'MLM1',
      status: 'candidate',
      price: 144.45,
      original_price: 168.93,
      stock: { min: 5, max: 197 },
      min_discounted_price: 2.9
    }]
  });
  const pkg = buildConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    campaign,
    action: 'enroll',
    status: 'candidate',
    plan
  });

  assert.equal(pkg.status, 'blocked');
  assert.equal(pkg.can_request_final_confirmation, false);
  assert.equal(pkg.sample_items[0].adapter_state, ADAPTER_STATES.ready_for_limited_real_test);
  assert.equal(pkg.sample_items[0].preview_payload.deal_id, 'LGH-MLM1000');
  assert.equal(pkg.sample_items[0].preview_payload.stock, 5);
  assert.equal(pkg.sample_items[0].preview_payload.original_price, 168.93);
  assert.equal(pkg.sample_items[0].preview_payload.deal_price, 144.45);
  assert.match(pkg.sample_items[0].payload_evidence, /Lightning Deal/);
  assert.match(pkg.risk_prompts.join('\n'), /LIGHTNING 官方报名 body 已确认/);
  assert.doesNotMatch(pkg.risk_prompts.join('\n'), /缺官方写入 body/);
});

test('SMART confirmation package is blocked even when no submit-ready item sample exists', () => {
  const campaign = { promotion_id: 'P-SMART', promotion_type: 'SMART', site_id: 'MLM', name: 'Smart Discounts' };
  const plan = buildPlan({ action: 'update', promotion: campaign, items: [] });
  const pkg = buildConfirmationPackage({
    account: { account_id: 'A', site_id: 'CBT' },
    campaign,
    action: 'update',
    status: 'started',
    plan
  });

  assert.equal(pkg.status, 'blocked');
  assert.equal(pkg.can_request_final_confirmation, false);
  assert.equal(pkg.blocked, 1);
  assert.match(pkg.blocking_reasons[0], /官方写入参数未完整确认/);
});

test('special promotion field summarizer extracts SMART and LIGHTNING keys', () => {
  const smartFields = summarizeSpecialPromotionFields({
    raw: {
      offer_id: 'OFFER-1',
      seller_percentage: 10.5,
      meli_percentage: 2,
      price: 88,
      original_price: 100
    }
  });
  assert.equal(smartFields.offer_id, 'OFFER-1');
  assert.equal(smartFields.seller_percentage, 10.5);
  assert.equal(smartFields.meli_percentage, 2);

  const lightningFields = summarizeSpecialPromotionFields({
    raw: {
      stock: { min: 5, max: 10 },
      min_discounted_price: 7.1,
      price: 80,
      original_price: 100
    }
  });
  assert.deepEqual(lightningFields.stock, { min: 5, max: 10 });
  assert.equal(lightningFields.min_discounted_price, 7.1);
});

test('item status whitelist rejects illegal status before Mercado defaults apply', () => {
  assert.equal(requireItemStatus('candidate'), 'candidate');
  assert.throws(() => requireItemStatus('bad-status'), /只允许 candidate/);
});

test('SELLER_CAMPAIGN and DEAL still produce dry-run preview payloads', () => {
  const row = {
    status: 'planned',
    deal_price: 95,
    item: { item_id: 'MLB1', status: 'candidate' },
    reason: '可执行'
  };

  for (const promotionType of ['SELLER_CAMPAIGN', 'DEAL']) {
    const preview = buildSubmitPayloadPreview({
      promotion: { promotion_id: 'C-1', promotion_type: promotionType },
      row,
      action: 'enroll'
    });
    assert.equal(preview.can_submit, true);
    assert.equal(preview.status, 'preview_ready');
    assert.equal(preview.payload.deal_price, 95);
    assert.equal(preview.payload.promotion_type, promotionType);
  }
});

test('SELLER_CAMPAIGN and DEAL preview payloads include optional top_deal_price only when present', () => {
  const rowWithTop = {
    status: 'planned',
    deal_price: 95,
    item: { item_id: 'MLB1', status: 'candidate', raw: { top_deal_price: 92 } },
    reason: '可执行'
  };
  const withTop = buildSubmitPayloadPreview({
    promotion: { promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN' },
    row: rowWithTop,
    action: 'enroll'
  });
  assert.equal(withTop.payload.top_deal_price, 92);

  const withoutTop = buildSubmitPayloadPreview({
    promotion: { promotion_id: 'P-1', promotion_type: 'DEAL' },
    row: { status: 'planned', deal_price: 94, item: { item_id: 'MLB2', status: 'candidate' } },
    action: 'enroll'
  });
  assert.equal(Object.hasOwn(withoutTop.payload, 'top_deal_price'), false);
});

test('settings normalization stores only non-sensitive paths and numeric workflow defaults', () => {
  const settings = normalizeSettings({
    authDir: 'C:/auth',
    outputDir: 'C:/out',
    sellerDefaultDiscount: 0,
    officialDefaultDiscount: 99,
    cancelMaxRounds: 3,
    maxItemsPerPromotion: 20,
    readConcurrency: 99,
    previewConcurrency: 0,
    writeConcurrency: 999,
    storeAliases: { '2651442567': '湖南店', empty: '', blank: '   ' },
    defaultFilters: { siteIds: 'MLB,MLM', promotionTypes: 'deal,smart', keywords: '95,Hot' }
  });
  assert.equal(settings.authDir, 'C:/auth');
  assert.equal(settings.outputDir, 'C:/out');
  assert.equal(settings.sellerDefaultDiscount, 1);
  assert.equal(settings.officialDefaultDiscount, 90);
  assert.equal(settings.cancelMaxRounds, 3);
  assert.equal(settings.readConcurrency, MAX_READ_CONCURRENCY);
  assert.equal(settings.previewConcurrency, 1);
  assert.equal(settings.writeConcurrency, MAX_WRITE_CONCURRENCY);
  assert.deepEqual(settings.storeAliases, { '2651442567': '湖南店' });
  assert.deepEqual(settings.defaultFilters.siteIds, ['MLB', 'MLM']);
  assert.deepEqual(settings.defaultFilters.promotionTypes, ['DEAL', 'SMART']);
});

test('OAuth start from standalone config returns authorization URL without leaking secret', () => {
  const prepared = prepareOAuthStartFromConfig(
    {
      client_id: 'client-123',
      client_secret: 'secret-should-not-leak',
      redirect_uri: 'https://xingtupro1020.com/callback/'
    },
    {
      pkce: { verifier: 'verifier', challenge: 'challenge' },
      state: 'state-123'
    }
  );

  assert.equal(prepared.response.ok, true);
  assert.equal(prepared.response.redirectUri, 'https://xingtupro1020.com/callback/');
  assert.match(prepared.response.authorizationUrl, /^https:\/\/global-selling\.mercadolibre\.com\/authorization\?/);
  assert.match(prepared.response.authorizationUrl, /client_id=client-123/);
  assert.match(prepared.response.authorizationUrl, /code_challenge=challenge/);
  assert.match(prepared.response.authorizationUrl, /code_challenge_method=S256/);
  assert.doesNotMatch(JSON.stringify(prepared.response), /secret-should-not-leak/);
  assert.equal(prepared.stateRecord.clientSecret, 'secret-should-not-leak');
});

test('OAuth start from standalone config prefers previously successful token redirect uri', () => {
  const prepared = prepareOAuthStartFromConfig(
    {
      client_id: 'client-123',
      client_secret: 'secret-should-not-leak',
      redirect_uri: 'https://configured.example/callback'
    },
    {
      pkce: { verifier: 'verifier', challenge: 'challenge' },
      state: 'state-123',
      tokenRedirectUri: 'https://xingtupro1020.com/callback/'
    }
  );

  assert.equal(prepared.response.redirectUri, 'https://xingtupro1020.com/callback/');
  assert.match(prepared.response.authorizationUrl, /redirect_uri=https%3A%2F%2Fxingtupro1020\.com%2Fcallback%2F/);
  assert.doesNotMatch(JSON.stringify(prepared.response), /secret-should-not-leak/);
});

test('OAuth start from standalone config reports missing config and defaults stable standalone redirect when absent', () => {
  assert.throws(
    () => prepareOAuthStartFromConfig(null, { pkce: { verifier: 'v', challenge: 'c' }, state: 's' }),
    /缺少 Mercado OAuth 配置文件/
  );

  const prepared = prepareOAuthStartFromConfig(
    { client_id: 'client-123', client_secret: 'secret' },
    { pkce: { verifier: 'verifier', challenge: 'challenge' }, state: 'state-123' }
  );
  assert.equal(prepared.response.redirectUri, 'https://xingtupro1020.com/callback/');
  assert.match(prepared.response.warning, /xingtupro1020\.com\/callback/);
  assert.doesNotMatch(JSON.stringify(prepared.response), /secret/);
});

test('OAuth callback parser supports full callback links, fragments, and code-only pending state', () => {
  assert.deepEqual(
    parseOAuthCallbackInput('https://xingtupro1020.com/callback/?code=code-1&state=state-1'),
    { code: 'code-1', state: 'state-1' }
  );
  assert.deepEqual(
    parseOAuthCallbackInput('https://xingtupro1020.com/callback/#code=code-2&state=state-2'),
    { code: 'code-2', state: 'state-2' }
  );
  assert.deepEqual(
    parseOAuthCallbackInput('code-only', null, () => selectCodeOnlyOAuthState([{ state: 'recent-state' }])),
    { code: 'code-only', state: 'recent-state' }
  );
});

test('OAuth callback parser rejects ambiguous or missing pending state with readable local errors', () => {
  assert.throws(
    () => parseOAuthCallbackInput('code-only', null, () => selectCodeOnlyOAuthState([])),
    /没有找到可匹配的未完成授权记录/
  );
  assert.throws(
    () => parseOAuthCallbackInput('code-only', null, () => selectCodeOnlyOAuthState([{ state: 's1' }, { state: 's2' }])),
    /多条未完成授权记录/
  );
  const localError = new Error('缺少 state。请重新点击“新增账号授权”');
  localError.status = 400;
  assert.equal(toChineseError(localError), '缺少 state。请重新点击“新增账号授权”');
});

test('under review item status is not mislabeled as account permission failure', () => {
  const label = toChineseError({
    status: 400,
    body: {
      error: 'bad_request',
      message: 'Item status is not allowed (under_review)'
    }
  });
  assert.equal(label, '商品正在审核中，平台不允许报名');
});

test('concurrency settings default, cap, min, and preserve per-item failures', async () => {
  assert.equal(normalizeConcurrency(undefined), 2);
  assert.equal(normalizeConcurrency(99), MAX_READ_CONCURRENCY);
  assert.equal(normalizeConcurrency('bad'), 2);
  assert.equal(normalizeWriteConcurrency(undefined), DEFAULT_WRITE_CONCURRENCY);
  assert.equal(normalizeWriteConcurrency(999), MAX_WRITE_CONCURRENCY);
  assert.equal(normalizeWriteConcurrency(0), 1);
  assert.equal(normalizeWriteConcurrency('bad'), DEFAULT_WRITE_CONCURRENCY);

  let active = 0;
  let observedMax = 0;
  const results = await mapLimited([1, 2, 3, 4, 5], 2, async (value) => {
    active += 1;
    observedMax = Math.max(observedMax, active);
    await new Promise((resolve) => setTimeout(resolve, 5));
    active -= 1;
    if (value === 3) throw new Error('single failure');
    return { ok: true, value };
  });

  assert.equal(observedMax <= 2, true);
  assert.equal(results.maxActive <= 2, true);
  assert.equal(results.length, 5);
  assert.deepEqual(results.filter((row) => row?.ok).map((row) => row.value), [1, 2, 4, 5]);
  assert.equal(results[2].ok, false);
  assert.match(results[2].error.message, /single failure/);
});

test('read benchmark can test readonly tiers up to 20', async () => {
  assert.equal(MAX_READ_CONCURRENCY, 20);
  assert.equal(normalizeConcurrency(99), MAX_READ_CONCURRENCY);

  let active = 0;
  let observedMax = 0;
  const rows = await mapLimitedWithCap([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], 20, 20, async (value) => {
    active += 1;
    observedMax = Math.max(observedMax, active);
    await new Promise((resolve) => setTimeout(resolve, 5));
    active -= 1;
    return value;
  });

  assert.equal(rows.maxActive > 5, true);
  assert.equal(rows.maxActive <= 12, true);
  assert.equal(observedMax, rows.maxActive);
});

test('executePlannedRowsWithConcurrency limits write concurrency and records all row results', async () => {
  let active = 0;
  let observedMax = 0;
  const saved = [];
  const plan = {
    rows: [
      { status: 'planned', item: { item_id: 'MLB1' }, deal_price: 95 },
      { status: 'planned', item: { item_id: 'MLB2' }, deal_price: 96 },
      { status: 'planned', item: { item_id: 'MLB3' }, deal_price: 97 },
      { status: 'skipped', item: { item_id: 'MLB4' }, deal_price: null, reason: '当前活动价已等于目标价' }
    ]
  };

  const result = await executePlannedRowsWithConcurrency({
    plan,
    action: 'enroll',
    promotionId: 'C-1',
    promotionType: 'SELLER_CAMPAIGN',
    accountId: 'A',
    taskId: 10,
    writeConcurrency: 2,
    executeOne: async ({ itemId }) => {
      active += 1;
      observedMax = Math.max(observedMax, active);
      await new Promise((resolve) => setTimeout(resolve, 5));
      active -= 1;
      if (itemId === 'MLB2') throw new Error('fake write failure');
      return { item_id: itemId, ok: true };
    },
    saveResult: (row) => saved.push(row),
    toErrorText: (error) => `中文失败：${error.message}`
  });

  assert.equal(observedMax <= 2, true);
  assert.equal(observedMax > 1, true);
  assert.equal(result.writeConcurrency, 2);
  assert.equal(result.maxActive <= 2, true);
  assert.equal(result.maxActive > 1, true);
  assert.deepEqual(result.counts, { success: 2, failed: 1, skipped: 1 });
  assert.equal(saved.length, 4);
  assert.deepEqual(saved.map((row) => row.status).sort(), ['failed', 'skipped', 'success', 'success']);
  assert.match(saved.find((row) => row.status === 'failed').errorCn, /中文失败/);
});

test('policy blocked special promotion rows are skipped instead of failed', async () => {
  const saved = [];
  const plan = {
    rows: [
      { status: 'planned', item: { item_id: 'MLB-SMART-1' }, deal_price: 19.62 }
    ]
  };

  const result = await executePlannedRowsWithConcurrency({
    plan,
    action: 'enroll',
    promotionId: 'P-SMART',
    promotionType: 'SMART',
    accountId: 'A',
    taskId: 30,
    writeConcurrency: 2,
    executeOne: async () => {
      const error = new Error('SMART 实验预览已就绪，等待单商品真实验证；不能批量真实放行');
      error.code = 'submit_payload_blocked';
      error.policyBlocked = true;
      throw error;
    },
    saveResult: (row) => saved.push(row),
    toErrorText: (error) => error.message
  });

  assert.deepEqual(result.counts, { success: 0, failed: 0, skipped: 1 });
  assert.equal(saved.length, 1);
  assert.equal(saved[0].status, 'skipped');
  assert.match(saved[0].errorCn, /SMART/);
});

test('single promotion can submit multiple planned rows concurrently without stopping on one failure', async () => {
  let active = 0;
  let observedMax = 0;
  const saved = [];
  const plan = {
    rows: Array.from({ length: 5 }, (_, index) => ({
      status: 'planned',
      item: { item_id: `MLB${index + 1}` },
      deal_price: 90 + index
    }))
  };

  const result = await executePlannedRowsWithConcurrency({
    plan,
    action: 'enroll',
    promotionId: 'P-1',
    promotionType: 'DEAL',
    accountId: 'A',
    taskId: 20,
    writeConcurrency: 3,
    executeOne: async ({ itemId }) => {
      active += 1;
      observedMax = Math.max(observedMax, active);
      await new Promise((resolve) => setTimeout(resolve, 8));
      active -= 1;
      if (itemId === 'MLB3') throw new Error('fake item failure');
      return { ok: true, item_id: itemId };
    },
    saveResult: (row) => saved.push(row),
    toErrorText: (error) => `中文失败：${error.message}`
  });

  assert.equal(observedMax > 1, true);
  assert.equal(observedMax <= 3, true);
  assert.equal(result.maxActive > 1, true);
  assert.equal(result.maxActive <= 3, true);
  assert.deepEqual(result.counts, { success: 4, failed: 1, skipped: 0 });
  assert.equal(saved.length, 5);
});

test('single promotion write concurrency is not capped by read concurrency', async () => {
  let active = 0;
  let observedMax = 0;
  const plan = {
    rows: Array.from({ length: 30 }, (_, index) => ({
      status: 'planned',
      item: { item_id: `MLB-HIGH-${index + 1}` },
      deal_price: 80 + index
    }))
  };

  const result = await executePlannedRowsWithConcurrency({
    plan,
    action: 'update',
    promotionId: 'P-HIGH',
    promotionType: 'DEAL',
    accountId: 'A',
    taskId: 21,
    writeConcurrency: 30,
    executeOne: async () => {
      active += 1;
      observedMax = Math.max(observedMax, active);
      await new Promise((resolve) => setTimeout(resolve, 5));
      active -= 1;
      return { ok: true };
    }
  });

  assert.equal(observedMax > MAX_READ_CONCURRENCY, true);
  assert.equal(result.maxActive > MAX_READ_CONCURRENCY, true);
  assert.equal(result.maxActive <= 30, true);
  assert.equal(result.counts.success, 30);
});

test('execution rows request stop only after deferred interface failure is final', async () => {
  const saved = [];
  const events = [];
  let stop = false;
  const plan = {
    rows: Array.from({ length: 12 }, (_, index) => ({
      status: 'planned',
      item: { item_id: `MLB-STOP-${index + 1}` },
      deal_price: 80 + index
    }))
  };

  const result = await executePlannedRowsWithConcurrency({
    plan,
    action: 'update',
    promotionId: 'P-STOP',
    promotionType: 'DEAL',
    accountId: 'A',
    taskId: 22,
    writeConcurrency: 1,
    shouldCancel: () => stop,
    executeOne: async ({ itemId }) => {
      if (itemId === 'MLB-STOP-2') {
        const error = new Error('fetch failed');
        error.status = 504;
        throw error;
      }
      return { ok: true };
    },
    saveResult: (row) => saved.push(row),
    classifyError: (error) => ({ interfaceFailure: Number(error.status) === 504, transientFailure: true }),
    onStopRequested: () => { stop = true; },
    onItemEvent: (event) => events.push(event),
    retryOptions: { retryBackoffMs: [0, 0, 0], deferredConcurrency: 1 }
  });

  assert.equal(result.counts.failed, 1);
  assert.equal(result.counts.skipped, 0);
  assert.equal(result.retrySummary.deferred, 1);
  assert.equal(result.retrySummary.deferred_failed, 1);
  assert.equal(stop, true);
  assert.equal(saved.some((row) => row.errorCn === '执行任务已停止，未开始的商品已跳过'), false);
  assert.equal(events.some((event) => event.type === 'item_deferred'), true);
  assert.equal(events.some((event) => event.type === 'deferred_retry_done' && event.failed === 1), true);
});

test('execution rows do not stop remaining items after business write errors', async () => {
  const saved = [];
  const events = [];
  const calls = [];
  let stop = false;
  const plan = {
    rows: Array.from({ length: 4 }, (_, index) => ({
      status: 'planned',
      item: { item_id: `MLB-BUSINESS-${index + 1}` },
      deal_price: 80 + index
    }))
  };

  const result = await executePlannedRowsWithConcurrency({
    plan,
    action: 'enroll',
    promotionId: 'P-BUSINESS',
    promotionType: 'DEAL',
    accountId: 'A',
    taskId: 23,
    writeConcurrency: 1,
    shouldCancel: () => stop,
    executeOne: async ({ itemId }) => {
      calls.push(itemId);
      if (itemId === 'MLB-BUSINESS-2') {
        const error = new Error('缺少或无效的活动报价信息');
        error.status = 400;
        throw error;
      }
      return { ok: true };
    },
    saveResult: (row) => saved.push(row),
    classifyError: () => ({ interfaceFailure: false, businessFailure: true }),
    onStopRequested: () => { stop = true; },
    onItemEvent: (event) => events.push(event)
  });

  assert.equal(stop, false);
  assert.equal(result.counts.success, 3);
  assert.equal(result.counts.failed, 1);
  assert.equal(result.counts.skipped, 0);
  assert.equal(saved.length, 4);
  assert.equal(calls.filter((itemId) => itemId === 'MLB-BUSINESS-2').length, 1);
  assert.equal(events.some((event) => event.type === 'item_cancelled_before_start'), false);
});

test('transient interface failures retry three times then defer to final retry', async () => {
  const saved = [];
  const events = [];
  let calls = 0;
  const plan = {
    rows: [{ status: 'planned', item: { item_id: 'MLB-RETRY-1' }, deal_price: 88 }]
  };

  const result = await executePlannedRowsWithConcurrency({
    plan,
    action: 'update',
    promotionId: 'P-RETRY',
    promotionType: 'DEAL',
    accountId: 'A',
    taskId: 24,
    writeConcurrency: 1,
    executeOne: async () => {
      calls += 1;
      if (calls <= 4) {
        const error = new Error('fetch failed');
        error.status = 504;
        throw error;
      }
      return { ok: true };
    },
    saveResult: (row) => saved.push(row),
    classifyError: (error) => ({ interfaceFailure: Number(error.status) === 504, transientFailure: true }),
    onItemEvent: (event) => events.push(event),
    retryOptions: { retryBackoffMs: [0, 0, 0], deferredConcurrency: 1 }
  });

  assert.equal(calls, 5);
  assert.deepEqual(result.counts, { success: 1, failed: 0, skipped: 0 });
  assert.equal(saved.length, 1);
  assert.equal(saved[0].status, 'success');
  assert.equal(result.retrySummary.immediate_retries, 3);
  assert.equal(result.retrySummary.deferred, 1);
  assert.equal(result.retrySummary.deferred_success, 1);
  assert.equal(events.filter((event) => event.type === 'item_retry').length, 3);
  assert.equal(events.some((event) => event.type === 'item_deferred' && /网络失败，已留到本批末尾补跑/.test(event.reason)), true);
  assert.equal(events.some((event) => event.type === 'deferred_retry_done' && event.success === 1), true);
});

test('deferred transient failure is final failed only after the tail retry', async () => {
  const saved = [];
  const events = [];
  let stopRequests = 0;
  const plan = {
    rows: [{ status: 'planned', item: { item_id: 'MLB-RETRY-FAIL' }, deal_price: 88 }]
  };

  const result = await executePlannedRowsWithConcurrency({
    plan,
    action: 'cancel',
    promotionId: 'P-RETRY',
    promotionType: 'DEAL',
    accountId: 'A',
    taskId: 25,
    writeConcurrency: 1,
    executeOne: async () => {
      const error = new Error('rate limit exceeded');
      error.status = 429;
      throw error;
    },
    saveResult: (row) => saved.push(row),
    classifyError: (error) => ({ interfaceFailure: true, rateLimited: Number(error.status) === 429, category: 'rate_limited' }),
    onStopRequested: () => { stopRequests += 1; },
    onItemEvent: (event) => events.push(event),
    retryOptions: { retryBackoffMs: [0, 0, 0], deferredConcurrency: 1 }
  });

  assert.deepEqual(result.counts, { success: 0, failed: 1, skipped: 0 });
  assert.equal(saved.length, 1);
  assert.equal(saved[0].status, 'failed');
  assert.equal(result.retrySummary.immediate_retries, 3);
  assert.equal(result.retrySummary.deferred, 1);
  assert.equal(result.retrySummary.deferred_failed, 1);
  assert.equal(stopRequests, 1);
  assert.equal(events.some((event) => event.type === 'item_retry' && /平台限流，正在第 1 次重试/.test(event.reason)), true);
  assert.equal(events.some((event) => event.type === 'item_finish' && event.finalRetry === true && event.status === 'failed'), true);
});

test('execution job item audit resolves run-id event files and counts true write attempts only', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');

  assert.match(serverSource, /function findExecutionJobEventPath/);
  assert.match(serverSource, /name\.startsWith\(`\$\{safeJobId\}-`\)/);
  assert.match(serverSource, /String\(event\.jobId \|\| ''\) === String\(jobId \|\| ''\)/);
  assert.match(serverSource, /hasPlanEvent: false/);
  assert.match(serverSource, /\['item_start', 'item_finish'\]\.includes\(eventType\)/);
  assert.match(serverSource, /summary\.planned_unique \+= 1/);
  assert.match(serverSource, /summary\.write_attempt_unique \+= 1/);
  assert.doesNotMatch(serverSource, /\['item_start', 'item_finish', 'item_skipped', 'item_cancelled_before_start'\]\.includes\(eventType\)[\s\S]{0,80}row\.hasWriteEvent = true/);
  assert.match(serverSource, /businessFailure/);
  assert.match(serverSource, /缺少或无效/);
  assert.match(serverSource, /interfaceFailure: !businessFailure/);
});

test('shared write limiter caps global in-flight writes across concurrent activities', async () => {
  const activeChanges = [];
  const limiter = createAsyncLimiter(4, { onActiveChange: (state) => activeChanges.push(state) });
  let active = 0;
  let observedMax = 0;
  const makePlan = (prefix) => ({
    rows: Array.from({ length: 4 }, (_, index) => ({
      status: 'planned',
      item: { item_id: `${prefix}${index + 1}` },
      deal_price: 80 + index
    }))
  });
  await Promise.all(['A', 'B', 'C'].map((prefix) => executePlannedRowsWithConcurrency({
    plan: makePlan(prefix),
    action: 'enroll',
    promotionId: `P-${prefix}`,
    promotionType: 'DEAL',
    accountId: 'A',
    taskId: prefix.charCodeAt(0),
    writeConcurrency: 3,
    schedule: limiter.run,
    executeOne: async () => {
      active += 1;
      observedMax = Math.max(observedMax, active);
      await new Promise((resolve) => setTimeout(resolve, 6));
      active -= 1;
      return { ok: true };
    }
  })));

  assert.equal(observedMax > 1, true);
  assert.equal(observedMax <= 4, true);
  assert.equal(limiter.maxActive <= 4, true);
  assert.equal(activeChanges.some((state) => state.maxActive > 1), true);
});

test('execution job path carries normalized write concurrency into real execution logs', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');

  assert.match(standaloneSource, /ResolveReadConcurrencyPlanAsync/);
  assert.match(standaloneSource, /readConcurrency = Math\.Max\(1, Math\.Min\(readConcurrency, ReadProbeCap\)\)/);
  assert.match(standaloneSource, /perJobWriteConcurrency/);
  assert.match(standaloneSource, /writeConcurrency\s*\r?\n\s*\}/);
  assert.match(standaloneSource, /siteConcurrency/);
  assert.match(standaloneSource, /activityConcurrency/);
  assert.match(serverSource, /\/api\/execution\/jobs\/start[\s\S]*normalizeWriteConcurrency\(body\.writeConcurrency, settings\.writeConcurrency\)/);
  assert.match(serverSource, /const jobReadConcurrency = normalizeConcurrency\(request\.readConcurrency, settings\.readConcurrency\)/);
  assert.match(serverSource, /const jobActivityConcurrency = normalizeConcurrency/);
  assert.match(serverSource, /const jobWriteConcurrency = normalizeWriteConcurrency\(request\.writeConcurrency, settings\.writeConcurrency\)/);
  assert.equal(serverSource.includes('\u8bfb\u53d6\u5e76\u53d1=${jobReadConcurrency}\uff0c\u7ad9\u70b9\u5e76\u53d1=${jobSiteConcurrency}\uff0c\u6d3b\u52a8\u5e76\u53d1=${jobActivityConcurrency}\uff0c\u5546\u54c1\u5199\u5165\u5e76\u53d1=${jobWriteConcurrency}'), true);
  assert.match(serverSource, /并发读取站点活动/);
  assert.match(serverSource, /并发读取活动商品/);
  assert.match(serverSource, /并发处理活动任务/);
  assert.equal(serverSource.includes('\u5546\u54c1\u5199\u5165\u5e76\u53d1=${jobWriteConcurrency}'), true);
  assert.equal(serverSource.includes('\u6309\u6d3b\u52a8\u5e76\u53d1 ${event.activityConcurrency || jobActivityConcurrency}\u3001\u5546\u54c1\u5199\u5165\u5e76\u53d1 ${event.writeConcurrency || jobWriteConcurrency}'), true);
  assert.match(serverSource, /writeConcurrency: jobWriteConcurrency/);
  assert.match(serverSource, /activityConcurrency: jobActivityConcurrency/);
  assert.match(serverSource, /getSharedWriteLimiter\(request\.executionGroupId, normalizedGlobalWriteConcurrency/);
  assert.match(serverSource, /sharedWriteLimiters/);
  assert.match(serverSource, /const normalizedWriteConcurrency = normalizeWriteConcurrency\(writeConcurrency, readSettings\(\)\.writeConcurrency\)/);
  assert.match(serverSource, /mapLimited\(indexedPlans, normalizedActivityConcurrency/);
  assert.match(serverSource, /requestedWriteConcurrency/);
  assert.match(serverSource, /requestedGlobalWriteConcurrency/);
  assert.match(serverSource, /global_peak_in_flight/);
  assert.match(serverSource, /executionJobItemsMatch/);
  assert.match(serverSource, /appendExecutionItemAuditEvent/);
  assert.match(serverSource, /summarizeExecutionUniqueItems/);
  assert.match(serverSource, /createExecutionJobId/);
  assert.match(serverSource, /run_id/);
  assert.match(serverSource, /event_file_key/);
  assert.match(serverSource, /events\.filter\(\(event\) => event\.run_id === job\.run_id\)/);
  assert.match(serverSource, /filterItemsByRequestedIds/);
  assert.match(serverSource, /requestedItemFilterErrorMessage/);
  assert.match(serverSource, /validateRequestedSmartCancelItems/);
  assert.match(serverSource, /request_summary: smartCancelAuditRequestSummary/);
  assert.match(serverSource, /offer_id: smartCancelFieldEvidence/);
  assert.match(serverSource, /\/api\/smart-cancel\/detail/);
  assert.match(serverSource, /\/api\/smart-cancel\/remaining/);
  assert.match(serverSource, /\(method === 'GET' \|\| method === 'POST'\) && url\.pathname === '\/api\/smart-cancel\/remaining'/);
  assert.match(serverSource, /buildSmartCancelDetail/);
  assert.match(serverSource, /fetchAndSyncSmartStarted/);
  assert.match(serverSource, /cache_updated_from_live/);
  assert.match(serverSource, /local_cache_stale/);
  assert.match(serverSource, /target_item_remaining/);
  assert.match(serverSource, /responseSummary\(response\)/);
  assert.match(serverSource, /http_status: response\.http_status/);
  assert.match(serverSource, /hasWriteEvent/);
  assert.doesNotMatch(serverSource, /remaining_started'\]\.includes\(status\)\) summary\.planned_unique/);
  assert.match(serverSource, /allowSmartCancel/);
  assert.match(serverSource, /limitSmartCancelPlan/);
  assert.match(serverSource, /buildSmartCancelQuery/);
  assert.match(serverSource, /item_cancelled_before_start/);
  assert.match(serverSource, /writeConcurrency: normalizedWriteConcurrency/);
  assert.match(serverSource, /writeConcurrency: execution\.writeConcurrency/);
  assert.match(serverSource, /executeOnePlannedWithTokenRefresh/);
  assert.match(serverSource, /refreshAccountForWriteRetry/);
  assert.match(serverSource, /ensureFreshAccount\(accountId, \{ force: true \}\)/);
});

test('read and write concurrency benchmark endpoints are present and write benchmark is disabled', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');

  assert.match(serverSource, /\/api\/concurrency-benchmark\/read/);
  assert.match(serverSource, /\/api\/concurrency-benchmark\/write\/plan/);
  assert.match(serverSource, /旧同步真实写入压测接口已停用/);
  assert.match(serverSource, /READ_BENCHMARK_LEVELS = \[1, 2, 3, 4, 5, 8, 10, 15, 20\]/);
  assert.match(serverSource, /WRITE_BENCHMARK_LEVELS = \[1, 2, 3, 5, 8, 10, 15, 20\]/);
  assert.match(serverSource, /READ_BENCHMARK_MAX_CONCURRENCY = 20/);
  assert.match(serverSource, /disabled_reason: '真实写入压测会改变 Mercado 活动商品状态/);
  assert.match(serverSource, /requires_final_confirmation: true/);
  assert.match(serverSource, /CONCURRENCY_BENCHMARK_PATH/);
  assert.match(standaloneSource, /并发实测：/);
  assert.match(standaloneSource, /后台压测工具已启用逐商品落盘/);
  assert.match(standaloneSource, /当前重复验证最高稳定档：\{stable\}/);
  assert.match(standaloneSource, /真实测试线程最新回传/);
});

test('settings window keeps daily settings simple and moves concurrency to advanced diagnostics', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');
  const indexSource = fs.readFileSync(path.join(process.cwd(), 'public/index.html'), 'utf8');
  const concurrencySource = fs.readFileSync(path.join(process.cwd(), 'src/concurrency.js'), 'utf8');
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');

  assert.match(concurrencySource, /MAX_WRITE_CONCURRENCY = 700/);
  assert.match(indexSource, /max="700"/);
  assert.match(standaloneSource, /自动并发策略（推荐）/);
  assert.match(standaloneSource, /高级设置 \/ 诊断/);
  assert.match(standaloneSource, /AddRowTo\(advancedGroup, "诊断文件目录"/);
  assert.match(standaloneSource, /AddNumberRowTo\(advancedGroup, "商品写入并发（当前使用值）", _writeConcurrency, writeConcurrency, 1, 700/);
  assert.doesNotMatch(standaloneSource, /AddRow\("输出目录"/);
  assert.doesNotMatch(standaloneSource, /AddNumberRow\("读取并发（高级）"/);
  assert.doesNotMatch(standaloneSource, /AddNumberRow\("活动并发（高级）"/);
  assert.match(serverSource, /write_latest_status/);
  assert.match(serverSource, /verified_stable_concurrency: 350/);
  assert.match(serverSource, /daily_recommended_min: 300/);
  assert.match(serverSource, /daily_recommended_max: 320/);
  assert.doesNotMatch(standaloneSource, /最高稳定写入并发 2/);
  assert.doesNotMatch(standaloneSource, /日常建议 2/);
});

test('write benchmark job API persists per-item events and protects unfinished jobs', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');

  assert.match(serverSource, /\/api\/concurrency-benchmark\/write\/jobs\/start/);
  assert.match(serverSource, /\/api\/concurrency-benchmark\/write\/jobs\/mark-legacy-unknown/);
  assert.match(serverSource, /writeBenchmarkStopMatch/);
  assert.match(serverSource, /writeBenchmarkItemsMatch/);
  assert.match(serverSource, /writeBenchmarkRecheckMatch/);
  assert.match(serverSource, /WRITE_BENCHMARK_JOB_DIR/);
  assert.match(serverSource, /WRITE_BENCHMARK_JOB_INDEX_PATH/);
  assert.match(serverSource, /appendWriteBenchmarkJobEvent/);
  assert.match(serverSource, /type: 'item_start'/);
  assert.match(serverSource, /type: 'item_finish'/);
  assert.match(serverSource, /type: 'level_start'/);
  assert.match(serverSource, /type: 'level_finish'/);
  assert.match(serverSource, /findUnfinishedWriteBenchmarkJob/);
  assert.match(serverSource, /legacy_unknown/);
  assert.match(serverSource, /job\.status === 'legacy_unknown'/);
  assert.match(serverSource, /dryRun !== false/);
  assert.match(serverSource, /fakeBenchmarkWrite/);
  assert.match(serverSource, /sampleOffset/);
  assert.match(serverSource, /excludeItemIds/);
  assert.match(serverSource, /targetMatch/);
  assert.match(serverSource, /fetchFailed/);
  assert.match(serverSource, /items: writeBenchmarkItemsFromEvents\(events\)/);
  assert.match(serverSource, /function writeBenchmarkItemsFromEvents/);
  assert.match(serverSource, /activeWrites \+= 1/);
  assert.match(serverSource, /job\.progress\.peak_in_flight = Math\.max\(job\.progress\.peak_in_flight, maxActiveWrites\)/);
  assert.match(serverSource, /report_path = null/);
});

test('WinForms all-store execution logs every queued store before concurrency slots run', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');

  assert.match(standaloneSource, /展开店铺任务/);
  assert.match(standaloneSource, /已加入执行队列/);
  assert.match(standaloneSource, /等待并发槽位/);
  assert.match(standaloneSource, /开始店铺任务/);
  assert.match(standaloneSource, /店铺任务完成/);
  assert.match(standaloneSource, /accountIds\.Select\(async \(accountId, index\)/);
  assert.match(standaloneSource, /index >= siteConcurrency/);
  assert.match(standaloneSource, /_currentExecutionJobIds/);
});

test('WinForms startup reuses healthy local service and starts service from the UI flow', () => {
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');

  assert.match(standaloneSource, /if \(await IsHealthy\(\)\)\s*\{\s*return null;\s*\}/);
  assert.match(standaloneSource, /serviceWarmupTask = Task\.Run\(EnsureServiceAsync\)/);
  assert.match(standaloneSource, /Application\.Run\(new MainForm\(startedService, serviceWarmupTask\)\)/);
  assert.match(standaloneSource, /_serviceWarmupTask/);
  assert.match(standaloneSource, /SetBusy\(true, "正在连接本地服务\.\.\."\)/);
  assert.match(standaloneSource, /EnsureServiceReadyForUiAsync/);
  assert.match(standaloneSource, /payload\.version/);
  assert.match(standaloneSource, /File\.WriteAllText\(markerPath, payloadVersion/);
  assert.match(standaloneSource, /LoadAccountsAsync\(verifyAccounts: false\)/);
  assert.match(standaloneSource, /_ = LoadStartupDataAsync\(\)/);
  assert.match(standaloneSource, /Task\.WhenAll\(RefreshTasksAsync\(\), RefreshActivitiesAsync\(false\)\)/);
  assert.match(standaloneSource, /if \(portOwner\.HasValue\)[\s\S]*IsOwnNodeService\(portOwner\.Value, root\)[\s\S]*StopProcessTree\(portOwner\.Value\)/);
  assert.doesNotMatch(standaloneSource, /if \(await IsHealthy\(\)\)[\s\S]{0,180}StopProcessTree\(portOwner\.Value\)/);
});

test('execution logs include old-assistant style shop-site discovery stage', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src/server.js'), 'utf8');
  const standaloneSource = fs.readFileSync(path.join(process.cwd(), 'standalone/Program.cs'), 'utf8');

  assert.match(serverSource, /加载店铺站点列表/);
  assert.match(serverSource, /店铺站点列表完成/);
  assert.match(serverSource, /店铺站点数：/);
  assert.match(serverSource, /已加载店铺站点：/);
  assert.match(serverSource, /其中当前有活动：/);
  assert.match(standaloneSource, /CountSelectedStoreSitesAsync/);
  assert.match(standaloneSource, /加载店铺站点列表/);
  assert.match(standaloneSource, /店铺站点数：\{shopSites\.Total\}/);
  assert.match(standaloneSource, /已加载店铺站点：\{shopSites\.Total\} 个/);
  assert.match(standaloneSource, /其中当前有活动：\{shopSites\.Active\} 个，未开放\/未读取到活动：\{shopSites\.Inactive\} 个/);
});

test('saved discount defaults are usable by batch preview while one-off direct price remains supported', () => {
  const settings = normalizeSettings({ sellerDefaultDiscount: 7, officialDefaultDiscount: 8 });
  const promotions = [
    { account_id: 'A', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', name: '自建' },
    { account_id: 'A', promotion_id: 'P-1', promotion_type: 'DEAL', name: '官方' }
  ];
  const itemsByPromotion = new Map([
    [promotionKey(promotions[0]), [{ item_id: 'MLB1', status: 'candidate', original_price: 100, price: 100 }]],
    [promotionKey(promotions[1]), [{ item_id: 'MLB2', status: 'candidate', original_price: 100, price: 100 }]]
  ]);

  const batch = buildBatchPlans({
    action: 'enroll',
    promotions,
    itemsByPromotion,
    sellerDiscountPercent: settings.sellerDefaultDiscount,
    officialDiscountPercent: settings.officialDefaultDiscount
  });
  assert.equal(batch.plans[0].plan.discountPercent, 7);
  assert.equal(batch.plans[1].plan.discountPercent, 8);

  const direct = buildBatchPlans({
    action: 'enroll',
    promotions: [promotions[0]],
    itemsByPromotion,
    priceMode: 'direct',
    directPrice: 88,
    sellerDiscountPercent: settings.sellerDefaultDiscount
  });
  assert.equal(direct.plans[0].plan.priceMode, 'direct');
  assert.equal(direct.plans[0].plan.rows[0].deal_price, 88);
});

test('promotion creation status supports Seller Campaign preview and blocks real create workflow', () => {
  assert.equal(PROMOTION_CREATION_STATUS.supported, true);
  assert.equal(PROMOTION_CREATION_STATUS.canPreviewDraft, true);
  assert.equal(PROMOTION_CREATION_STATUS.canRealCreate, false);
  assert.match(PROMOTION_CREATION_STATUS.summary, /SELLER_CAMPAIGN/);
  assert.equal(PROMOTION_CREATION_STATUS.createEndpoint.maxDurationDays, 14);
  assert.ok(PROMOTION_CREATION_STATUS.officialEvidence.every((source) => source.url.startsWith('https://')));
});

test('Seller Campaign create preview builds official request and enforces 14 day limit', () => {
  const preview = buildSellerCampaignCreatePreview({
    accountId: '2651442567',
    siteId: 'MLB',
    childUserId: '2668031897',
    name: '95',
    subType: 'FLEXIBLE_PERCENTAGE',
    startDate: '2026-07-03T00:00:00.000Z',
    finishDate: '2026-07-16T00:00:00.000Z'
  });
  assert.deepEqual(preview.errors, []);
  assert.equal(preview.request_preview.method, 'POST');
  assert.equal(preview.request_preview.path, '/marketplace/seller-promotions/seller-campaign/2668031897');
  assert.equal(preview.request_preview.headers.version, 'v2');
  assert.equal(preview.request_preview.body.promotion_type, 'SELLER_CAMPAIGN');
  assert.equal(preview.request_preview.body.sub_type, 'FLEXIBLE_PERCENTAGE');

  const tooLong = buildSellerCampaignCreateConfirmation({
    accountId: '2651442567',
    siteId: 'MLB',
    childUserId: '2668031897',
    name: 'Too long',
    startDate: '2026-07-03T00:00:00.000Z',
    finishDate: '2026-07-18T00:00:00.000Z'
  });
  assert.equal(tooLong.status, 'blocked');
  assert.equal(tooLong.can_request_final_confirmation, false);
  assert.match(tooLong.validation_errors.join('\n'), /14 天/);
});

test('Seller Campaign create confirmation is preview-only even when valid', () => {
  const pkg = buildSellerCampaignCreateConfirmation({
    accountId: '2651442567',
    siteId: 'MLM',
    childUserId: '2668034127',
    name: '95',
    startDate: '2026-07-03T00:00:00.000Z',
    finishDate: '2026-07-10T00:00:00.000Z'
  });
  assert.equal(pkg.package_type, 'seller_campaign_create_precheck');
  assert.equal(pkg.status, 'awaiting_supervisor_confirmation');
  assert.equal(pkg.can_request_final_confirmation, true);
  assert.equal(pkg.request_preview.preview_only, true);
  assert.equal(pkg.request_preview.writes_external_state, true);
  assert.match(pkg.risk_prompts.join('\n'), /不执行 POST/);
});

test('real enroll smoke targets are fixed to exactly four allowed items', () => {
  const targets = listRealEnrollSmokeTargets();
  assert.equal(targets.length, 4);
  assert.deepEqual(targets.map((target) => target.key), [
    'deal_mlb_1',
    'seller_campaign_mlm_1',
    'lightning_mlm_1',
    'smart_mlb_1'
  ]);
  assert.ok(targets.every((target) => target.account_id === '2651442567'));
  assert.ok(targets.every((target) => target.request_preview.preview_only));
  assert.ok(targets.every((target) => target.request_preview.writes_external_state));
});

test('real enroll smoke body builders match fixed official previews', () => {
  assert.deepEqual(buildSmokeEnrollBody('deal_mlb_1'), {
    promotion_id: 'P-MLB17489058',
    promotion_type: 'DEAL',
    deal_price: 12.30
  });
  assert.deepEqual(buildSmokeEnrollBody('seller_campaign_mlm_1'), {
    promotion_id: 'C-MLM1209743',
    promotion_type: 'SELLER_CAMPAIGN',
    deal_price: 11.31
  });
  assert.deepEqual(buildSmokeEnrollBody('lightning_mlm_1'), {
    deal_id: 'LGH-MLM1000',
    deal_price: 144.45,
    original_price: 168.93,
    promotion_type: 'LIGHTNING',
    stock: 5
  });
  assert.deepEqual(buildSmokeEnrollBody('smart_mlb_1'), {
    promotion_id: 'P-MLB17755282',
    promotion_type: 'SMART',
    offer_id: 'CANDIDATE-MLB6729392606-76453189919'
  });
});

test('real enroll smoke request preview uses marketplace item endpoint and required headers', () => {
  const deal = buildSmokeEnrollRequestPreview('deal_mlb_1');
  assert.equal(deal.method, 'POST');
  assert.equal(deal.path, '/marketplace/seller-promotions/items/MLB4685849149?user_id=2668031897');
  assert.equal(deal.headers.version, 'v2');
  assert.equal(deal.headers['X-Caller-Id'], '2668031897');
  assert.equal(Object.hasOwn(deal.headers, 'Authorization'), false);

  const lightning = buildSmokeEnrollRequestPreview('lightning_mlm_1');
  assert.equal(lightning.headers['X-Client-Id'], '<client_id_required_by_mercado>');
  assert.equal(lightning.body.stock, 5);
});

test('real enroll smoke confirmation keeps SMART blocked and execute disabled', () => {
  const pkg = buildRealEnrollSmokeConfirmation();
  assert.equal(pkg.package_type, 'real_enroll_smoke_precheck');
  assert.equal(pkg.enabled, false);
  assert.equal(pkg.can_execute_now, false);
  assert.equal(pkg.real_write_not_executed, true);
  assert.equal(pkg.targets_total, 4);
  assert.equal(pkg.planned_targets, 3);
  assert.equal(pkg.blocked_targets, 1);
  assert.equal(pkg.targets.find((target) => target.key === 'smart_mlb_1').policy_state, 'blocked_by_policy');
  assert.match(pkg.recheck_method.map((row) => row.method).join('\n'), /candidate\/pending\/started/);

  const disabled = buildRealEnrollSmokeExecuteDisabled({ confirmText: 'REAL_SUBMIT' });
  assert.equal(disabled.status, 409);
  assert.equal(disabled.confirmation_package.enabled, false);
  assert.match(disabled.error, /禁止执行 Mercado POST/);
});

test('real enroll smoke validation rejects mismatched or expanded item lists', () => {
  assert.equal(assertSmokeTargetMatches({
    key: 'deal_mlb_1',
    account_id: '2651442567',
    site_id: 'MLB',
    child_user_id: '2668031897',
    promotion_id: 'P-MLB17489058',
    promotion_type: 'DEAL',
    item_id: 'MLB4685849149',
    status: 'candidate',
    deal_price: 12.30
  }), true);

  assert.throws(() => assertSmokeTargetMatches({
    key: 'deal_mlb_1',
    account_id: '2651442567',
    site_id: 'MLB',
    child_user_id: '2668031897',
    promotion_id: 'P-MLB17489058',
    promotion_type: 'DEAL',
    item_id: 'MLB-OTHER',
    status: 'candidate'
  }), /item_id/);

  const validation = validateRealEnrollSmokeRequest({
    targetKeys: [...REAL_ENROLL_SMOKE_TARGETS.map((target) => target.key), 'extra_item'],
    items: [{ key: 'extra_item', item_id: 'MLB-OTHER' }]
  });
  assert.equal(validation.unknown_keys.includes('extra_item'), true);
  assert.match(validation.reasons.join('\n'), /未授权|不在固定候选包/);

  const bodyMismatch = validateRealEnrollSmokeRequest({
    targets: [{ key: 'deal_mlb_1', body: { promotion_id: 'P-MLB17489058', promotion_type: 'DEAL', deal_price: 9.99 } }]
  });
  assert.match(bodyMismatch.body_mismatches.join('\n'), /body 不完全匹配/);
});

test('workspace export writes shops, activities, preview, precheck, and history files', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-export-'));
  const result = exportWorkspace({
    outputDir: dir,
    accounts: [{ account_id: 'A', display_name: 'Shop', site_id: 'CBT' }],
    sites: [{ site_id: 'MLB', child_user_id: '1', logistic_type: 'remote', total: 2 }],
    activities: [{ account_id: 'A', site_id: 'MLB', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', name: '95', status: 'started' }],
    results: [{ created_at: '2026-07-02', account_id: 'A', promotion_id: 'C-1', item_id: 'MLB1', action: 'enroll', mode: 'dry-run', status: 'planned' }],
    preview: { batch: { plans: [{ promotion: { site_id: 'MLB', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN' }, plan: { rows: [{ item: { item_id: 'MLB1' }, status: 'planned', deal_price: 95, reason: '可执行' }] } }] } },
    precheck: { confirmation_package: { promotions: [{ site_id: 'MLB', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN', planned: 1, skipped: 0, blocked: 0, status: 'awaiting' }] } }
  });
  assert.equal(result.files.length, 10);
  assert.ok(result.files.every((file) => fs.existsSync(file)));
});

test('WinForms site selector uses business labels and keeps internal site ids', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'standalone', 'Program.cs'), 'utf8');
  assert.match(source, /"MLB" => "巴西站"/);
  assert.match(source, /"MLM" => "墨西哥站"/);
  assert.match(source, /"MLA" => "阿根廷站"/);
  assert.match(source, /new ComboItem\(id, display\)/);
  assert.doesNotMatch(source, /\$\{id\} \(\$\{total\}\)/);
});

test('WinForms all-store workflow iterates all mapped accounts instead of first account only', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'standalone', 'Program.cs'), 'utf8');
  assert.match(source, /SelectedAccountIds/);
  assert.match(source, /foreach \(var accountId in accountIds\)/);
  assert.match(source, /\/api\/accounts\/\{Uri\.EscapeDataString\(accountId\)\}\/sites/);
  assert.match(source, /\/api\/accounts\/\{Uri\.EscapeDataString\(accountId\)\}\/promotions\/fetch/);
  assert.match(source, /accountId,/);
});

test('execution jobs default to full-scope wording and preserve batch summaries', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  assert.match(source, /const sampleOnly = action === 'enroll' \? Boolean\(request\.sampleOnly\) : Boolean\(request\.sampleOnly\)/);
  assert.match(source, /saveBatchExecutionSummaryTask/);
  assert.match(source, /promotionType: 'BATCH'/);
  assert.match(source, /shouldPreserveExistingFetchState/);
});

test('sites endpoint is backed by marketplace child discovery, not only campaigns with activity', () => {
  const serverSource = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const repoSource = fs.readFileSync(path.join(process.cwd(), 'src', 'repository.js'), 'utf8');
  assert.match(serverSource, /discoverAndSaveMarketplaceSites/);
  assert.match(serverSource, /updateMarketplaceSitePromotionStatus/);
  assert.match(repoSource, /marketplace_sites/);
  assert.match(repoSource, /listMarketplaceSites/);
  assert.match(repoSource, /last_promotion_status/);
});
