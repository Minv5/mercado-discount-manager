import { promotionKey } from './planner.js';
import { activityIdentityKey } from './submissionScopeFreeze.js';

function confirmedScopeFromRequest(request = {}) {
  return request.confirmedExecutionScope ?? request.confirmed_execution_scope ?? null;
}

export function hasConfirmedExecutionScope(request = {}) {
  return Boolean(confirmedScopeFromRequest(request));
}

function confirmedActivitiesForAccount(accountId, request = {}) {
  const scope = confirmedScopeFromRequest(request);
  if (!scope) return [];
  return (Array.isArray(scope.activities) ? scope.activities : [])
    .filter((activity) => String(activity.account_id ?? activity.accountId ?? '') === String(accountId || ''));
}

export function filterPromotionsByConfirmedScope({ accountId = '', promotions = [], request = {} } = {}) {
  if (!hasConfirmedExecutionScope(request)) {
    return { hasFilter: false, promotions, missingActivityKeys: [] };
  }
  const activities = confirmedActivitiesForAccount(accountId, request);
  const wanted = new Set(activities
    .filter((activity) => (activity.item_ids ?? activity.itemIds ?? []).length > 0)
    .map(activityIdentityKey));
  const matched = new Set();
  const filtered = (promotions || []).filter((promotion) => {
    const key = activityIdentityKey({ ...promotion, account_id: promotion.account_id || accountId });
    if (!wanted.has(key)) return false;
    matched.add(key);
    return true;
  });
  return {
    hasFilter: true,
    promotions: filtered,
    missingActivityKeys: [...wanted].filter((key) => !matched.has(key)).sort(),
  };
}

export function requestedExecutionItemIds(request = {}) {
  const values = [];
  collectItemIdValues(values, request.itemIds);
  collectItemIdValues(values, request.itemId);
  if (Array.isArray(request.items)) {
    for (const item of request.items) {
      if (typeof item === 'string') collectItemIdValues(values, item);
      else collectItemIdValues(values, item?.item_id ?? item?.itemId ?? item?.id);
    }
  }

  const seen = new Set();
  const result = [];
  for (const value of values) {
    const id = String(value || '').trim();
    if (!id) continue;
    const key = normalizeItemId(id);
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(id);
  }
  return result;
}

export function filterItemsByRequestedIds({ promotions = [], itemsByPromotion = new Map(), request = {} } = {}) {
  const requestedItemIds = requestedExecutionItemIds(request);
  if (!requestedItemIds.length) {
    return {
      hasFilter: false,
      requestedItemIds,
      matchedItemIds: [],
      missingItemIds: [],
      itemsByPromotion
    };
  }

  const wanted = new Set(requestedItemIds.map(normalizeItemId));
  const matched = new Set();
  const assigned = new Set();
  const filtered = new Map();

  for (const promotion of promotions) {
    const key = promotionKey(promotion);
    const items = itemsByPromotion.get(key) || [];
    const selected = items.filter((item) => {
      const itemId = itemIdOf(item);
      const normalized = normalizeItemId(itemId);
      if (!itemId || !wanted.has(normalized) || assigned.has(normalized)) return false;
      matched.add(normalized);
      assigned.add(normalized);
      return true;
    });
    filtered.set(key, selected);
  }

  const missingItemIds = requestedItemIds.filter((itemId) => !matched.has(normalizeItemId(itemId)));
  return {
    hasFilter: true,
    requestedItemIds,
    matchedItemIds: requestedItemIds.filter((itemId) => matched.has(normalizeItemId(itemId))),
    missingItemIds,
    itemsByPromotion: filtered
  };
}

export function filterItemsByConfirmedScope({ accountId = '', promotions = [], itemsByPromotion = new Map(), request = {} } = {}) {
  if (!hasConfirmedExecutionScope(request)) {
    return {
      hasFilter: false,
      requestedRelationCount: 0,
      matchedRelationCount: 0,
      missingRelations: [],
      itemsByPromotion,
    };
  }
  const activities = confirmedActivitiesForAccount(accountId, request);
  const wantedByActivity = new Map();
  for (const activity of activities) {
    wantedByActivity.set(
      activityIdentityKey(activity),
      new Set((activity.item_ids ?? activity.itemIds ?? []).map(normalizeItemId).filter(Boolean)),
    );
  }
  const filtered = new Map();
  const missingRelations = [];
  const matchedActivityKeys = new Set();
  const requestedRelationCount = [...wantedByActivity.values()].reduce((sum, itemIds) => sum + itemIds.size, 0);
  let matchedRelationCount = 0;
  for (const promotion of promotions) {
    const key = promotionKey(promotion);
    const identityKey = activityIdentityKey({ ...promotion, account_id: promotion.account_id || accountId });
    const wanted = wantedByActivity.get(identityKey) || new Set();
    if (wantedByActivity.has(identityKey)) matchedActivityKeys.add(identityKey);
    const matched = new Set();
    const selected = (itemsByPromotion.get(key) || []).filter((item) => {
      const itemId = normalizeItemId(itemIdOf(item));
      if (!wanted.has(itemId)) return false;
      matched.add(itemId);
      return true;
    });
    matchedRelationCount += matched.size;
    for (const itemId of wanted) {
      if (!matched.has(itemId)) missingRelations.push(`${key}|${itemId}`);
    }
    filtered.set(key, selected);
  }
  for (const [activityKey, wanted] of wantedByActivity) {
    if (matchedActivityKeys.has(activityKey)) continue;
    for (const itemId of wanted) missingRelations.push(`${activityKey}|${itemId}`);
  }
  return {
    hasFilter: true,
    requestedRelationCount,
    matchedRelationCount,
    missingRelations,
    itemsByPromotion: filtered,
  };
}

export function requestedItemFilterErrorMessage(result, itemStatus = '') {
  const ids = (result?.missingItemIds || []).join(', ');
  const statusText = itemStatus ? `${itemStatus} ` : '';
  return `指定商品未在本次 ${statusText}商品列表中找到，已停止执行，未改为处理同活动其它商品：${ids}`;
}

function collectItemIdValues(target, value) {
  if (Array.isArray(value)) {
    for (const item of value) collectItemIdValues(target, item);
    return;
  }
  if (typeof value === 'string') {
    for (const part of value.split(/[\s,，;；]+/)) {
      const text = part.trim();
      if (text) target.push(text);
    }
    return;
  }
  if (value !== null && value !== undefined) target.push(value);
}

function itemIdOf(item = {}) {
  return String(item.item_id ?? item.itemId ?? item.id ?? '').trim();
}

function normalizeItemId(value) {
  return String(value || '').trim().toUpperCase();
}
