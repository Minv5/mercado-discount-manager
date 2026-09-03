import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import {
  filterExecutionScopeByRequestedItemIds,
  filterItemsByConfirmedScope,
  isTargetedCancelAllActivitiesRequest,
  isTargetedItemActionRequest,
  requestedExecutionItemIds,
} from '../src/executionItemFilter.js';
import { promotionKey } from '../src/planner.js';

function activity(accountId, promotionId, itemIds, extra = {}) {
  return {
    account_id: accountId,
    child_user_id: `${accountId}-CHILD`,
    site_id: 'MLB',
    promotion_id: promotionId,
    promotion_type: extra.promotion_type || 'DEAL',
    item_ids: itemIds,
    ...extra,
  };
}

test('targeted cancellation parses unique item ids and requires explicit cancel mode', () => {
  const request = {
    action: 'cancel',
    targetedCancelAllActivities: true,
    itemIds: ['mlb100', 'MLB100', 'MLB200'],
  };
  assert.equal(isTargetedCancelAllActivitiesRequest(request), true);
  assert.deepEqual(requestedExecutionItemIds(request), ['mlb100', 'MLB200']);
  assert.equal(isTargetedCancelAllActivitiesRequest({ ...request, action: 'update' }), false);
  assert.equal(isTargetedItemActionRequest({ action: 'enroll', targetedItemAction: true, itemIds: ['MLB100'] }), true);
});

test('targeted enrollment preserves candidate relations across selected ordinary activities', () => {
  const scope = {
    action: 'enroll',
    activities: [
      activity('A1', 'P-SELLER', ['MLB100'], { promotion_type: 'SELLER_CAMPAIGN' }),
      activity('A1', 'P-DEAL', ['MLB100'], { promotion_type: 'DEAL' }),
    ],
  };
  const request = { action: 'enroll', targetedItemAction: true, itemIds: ['MLB100', 'MLB404'] };
  const filtered = filterExecutionScopeByRequestedItemIds({ scope, request });
  assert.equal(filtered.relationCount, 2);
  assert.deepEqual(filtered.matchedItemIds, ['MLB100']);
  assert.deepEqual(filtered.missingItemIds, ['MLB404']);
});

test('one requested item keeps every exact activity relation instead of the first match only', () => {
  const scope = {
    action: 'cancel',
    activities: [
      activity('A1', 'P-1', ['MLB100', 'MLB999']),
      activity('A1', 'P-2', ['MLB100'], { promotion_type: 'SMART' }),
      activity('A2', 'P-3', ['MLB100']),
    ],
  };
  const request = { action: 'cancel', targetedCancelAllActivities: true, itemIds: ['MLB100'] };
  const filtered = filterExecutionScopeByRequestedItemIds({ scope, request });

  assert.deepEqual(filtered.missingItemIds, []);
  assert.equal(filtered.matchedItemIds.length, 1);
  assert.equal(filtered.relationCount, 3);
  assert.equal(filtered.activityCount, 3);
  assert.equal(filtered.accountCount, 2);
  assert.deepEqual(filtered.scope.activities.map((row) => row.item_ids), [['MLB100'], ['MLB100'], ['MLB100']]);
  assert.deepEqual(filtered.accounts.map((row) => [row.account_id, row.unique_item_count, row.relation_count]), [
    ['A1', 1, 2],
    ['A2', 1, 1],
  ]);
});

test('targeted cancellation keeps missing ids for later review while matched relations proceed', () => {
  const filtered = filterExecutionScopeByRequestedItemIds({
    scope: { action: 'cancel', activities: [activity('A1', 'P-1', ['MLB100'])] },
    request: { action: 'cancel', targetedCancelAllActivities: true, itemIds: ['MLB100', 'MLB404'] },
  });
  assert.deepEqual(filtered.matchedItemIds, ['MLB100']);
  assert.deepEqual(filtered.missingItemIds, ['MLB404']);
  assert.equal(filtered.relationCount, 1);
  assert.equal(filtered.activityCount, 1);
});

test('confirmed targeted scope preserves the same item in multiple activities for execution', () => {
  const promotions = [
    activity('A1', 'P-1', []),
    activity('A1', 'P-2', [], { promotion_type: 'SMART' }),
  ];
  const itemsByPromotion = new Map(promotions.map((promotion, index) => [
    promotionKey(promotion),
    [{ item_id: 'MLB100', status: 'started', raw_json: index ? '{"offer_id":"OFFER-1"}' : '{}' }],
  ]));
  const request = {
    action: 'cancel',
    targetedCancelAllActivities: true,
    confirmedExecutionScope: {
      action: 'cancel',
      activities: promotions.map((promotion) => ({ ...promotion, item_ids: ['MLB100'] })),
    },
  };
  const filtered = filterItemsByConfirmedScope({ accountId: 'A1', promotions, itemsByPromotion, request });
  assert.equal(filtered.matchedRelationCount, 2);
  assert.equal([...filtered.itemsByPromotion.values()].flat().length, 2);
});

test('server targeted path supports enroll and cancel without full catalog refresh', () => {
  const source = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  const targetedStart = source.indexOf('async function buildTargetedItemSubmissionSnapshot');
  const targetedEnd = source.indexOf('async function buildExecutionSubmissionSnapshot', targetedStart);
  const targetedSource = source.slice(targetedStart, targetedEnd);
  assert.ok(targetedStart > 0 && targetedEnd > targetedStart);
  assert.match(targetedSource, /listPromotionRelationsByItemIds/);
  assert.doesNotMatch(targetedSource, /listItemRouteOwners/);
  assert.doesNotMatch(targetedSource, /configuredRoutes/);
  assert.doesNotMatch(targetedSource, /if \(!promotions\.length\)[\s\S]*?ensureUsableAccount/);
  assert.match(targetedSource, /getItemPromotions/);
  assert.match(targetedSource, /targetedRelationDecision/);
  assert.match(targetedSource, /cache_reused_item_count/);
  assert.match(targetedSource, /targeted_refresh_item_count/);
  assert.match(targetedSource, /for \(const relation of routeItem\.refresh_relations\)/);
  assert.doesNotMatch(targetedSource, /for \(const remote of remotePromotions\)/);
  assert.match(targetedSource, /webhook_cache_targeted_fallback/);
  assert.match(targetedSource, /if \(!remote\)[\s\S]*?removeGhostPromotionItem/);
  assert.match(targetedSource, /saveActivityCacheState\([\s\S]*?clearWebhookRouteCacheIfComplete/);
  assert.doesNotMatch(targetedSource, /prepareItemsForExecution/);
  assert.doesNotMatch(targetedSource, /refreshActivityCatalogForPrepare/);
  assert.doesNotMatch(targetedSource, /fetchAndSavePromotions/);
  assert.doesNotMatch(targetedSource, /fetchAllPromotionItems/);
  assert.match(targetedSource, /action === 'enroll' && !ordinaryPromotions\(filtered\)\.length/);
  assert.match(source, /isTargetedItemActionRequest\(input\)[\s\S]*?return buildTargetedItemSubmissionSnapshot/);
  assert.match(source, /filterExecutionScopeByRequestedItemIds/);
  assert.match(source, /targetedConfirmedAction[\s\S]*?指定商品精确活动关系/);
  assert.match(targetedSource, /promotionTypes: action === 'enroll' \? \['SELLER_CAMPAIGN', 'DEAL'\] : \[\]/);
  assert.match(source, /TARGETED_ITEM_ACTION_NO_MATCHES/);
  assert.match(source, /unmatched_item_count/);
  assert.doesNotMatch(source, /TARGETED_CANCEL_ITEMS_NOT_FOUND/);
  assert.match(source, /confirmText: 'REAL_SUBMIT'/);
});
