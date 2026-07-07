import { promotionKey } from './planner.js';

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
