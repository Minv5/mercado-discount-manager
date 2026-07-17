import crypto from 'node:crypto';

function text(value) {
  return String(value ?? '').trim();
}

function upper(value) {
  return text(value).toUpperCase();
}

function lower(value) {
  return text(value).toLowerCase();
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function collectionValues(values, label = '执行范围集合') {
  if (values === null || values === undefined) return [];
  if (Array.isArray(values)) return values;
  if (values instanceof Set) return [...values];
  const error = new TypeError(`${label}格式异常，已停止准备且未执行商品操作。`);
  error.code = 'EXECUTION_SCOPE_COLLECTION_INVALID';
  error.status = 422;
  throw error;
}

function uniqueSorted(values = [], label = '执行范围集合') {
  return [...new Set(collectionValues(values, label).map(upper).filter(Boolean))].sort();
}

export function activityIdentityKey(value = {}) {
  if (typeof value === 'string') {
    const parts = text(value).split('|');
    if (parts.length >= 5) return [parts[0], parts[1], upper(parts[2]), parts[3], upper(parts[4])].join('|');
    if (parts.length === 4) return [parts[0], '', upper(parts[1]), parts[2], upper(parts[3])].join('|');
  }
  return [
    text(value.account_id ?? value.accountId),
    text(value.child_user_id ?? value.childUserId),
    upper(value.site_id ?? value.siteId),
    text(value.promotion_id ?? value.promotionId),
    upper(value.promotion_type ?? value.promotionType),
  ].join('|');
}

export const confirmedActivityKey = activityIdentityKey;

export function activityReadKey(value = {}, status = '') {
  const identity = activityIdentityKey(value);
  const normalizedStatus = lower(status || (typeof value === 'object' ? value.item_status ?? value.itemStatus : ''));
  return normalizedStatus ? `${identity}|${normalizedStatus}` : identity;
}

export function forceReadKeysForStatus(values = [], status = '') {
  return new Set([...new Set(collectionValues(values, '执行范围活动键集合')
    .map((value) => activityReadKey(value, status))
    .filter((value) => value && !value.startsWith('__')))].sort());
}

function normalizeActivity(value = {}) {
  return {
    account_id: text(value.account_id ?? value.accountId),
    child_user_id: text(value.child_user_id ?? value.childUserId),
    site_id: upper(value.site_id ?? value.siteId),
    promotion_id: text(value.promotion_id ?? value.promotionId),
    promotion_type: upper(value.promotion_type ?? value.promotionType),
    item_status: lower(value.item_status ?? value.itemStatus),
    item_ids: uniqueSorted(value.item_ids ?? value.itemIds, '执行范围商品集合'),
    platform_total: numberOrNull(value.platform_total ?? value.platformTotal),
    saved_count: numberOrNull(value.saved_count ?? value.savedCount),
    detail_status: lower(value.detail_status ?? value.detailStatus),
    blocked: Boolean(value.blocked),
  };
}

function mergeActivity(left, right) {
  return {
    ...left,
    ...right,
    item_status: right.item_status || left.item_status,
    item_ids: uniqueSorted([...(left.item_ids || []), ...(right.item_ids || [])]),
    platform_total: right.platform_total ?? left.platform_total ?? null,
    saved_count: right.saved_count ?? left.saved_count ?? null,
    detail_status: right.detail_status || left.detail_status,
    blocked: Boolean(left.blocked || right.blocked),
  };
}

function normalizedSellerStates(input = {}) {
  const output = {};
  for (const [key, value] of Object.entries(input || {})) {
    const normalizedKey = upper(key);
    if (normalizedKey) output[normalizedKey] = lower(value);
  }
  return output;
}

export function createConfirmedExecutionScope({
  action = '',
  activities = [],
  sellerCreateTargetKeys = [],
  sellerTargetStates = {},
} = {}) {
  const byActivity = new Map();
  for (const value of activities || []) {
    const activity = normalizeActivity(value);
    const key = activityIdentityKey(activity);
    if (!activity.account_id || !activity.site_id || !activity.promotion_id || !activity.promotion_type) continue;
    byActivity.set(key, byActivity.has(key) ? mergeActivity(byActivity.get(key), activity) : activity);
  }
  return {
    version: 2,
    action: lower(action),
    activities: [...byActivity.values()].sort((left, right) => activityIdentityKey(left).localeCompare(activityIdentityKey(right))),
    seller_create_target_keys: uniqueSorted(sellerCreateTargetKeys, '自建活动创建目标集合'),
    seller_target_states: normalizedSellerStates(sellerTargetStates),
  };
}

export function confirmedExecutionScopeFacts(scope = {}) {
  const normalized = createConfirmedExecutionScope({
    action: scope.action,
    activities: scope.activities,
    sellerCreateTargetKeys: scope.seller_create_target_keys,
    sellerTargetStates: scope.seller_target_states,
  });
  return {
    version: normalized.version,
    action: normalized.action,
    activities: normalized.activities.map((activity) => ({
      account_id: activity.account_id,
      child_user_id: activity.child_user_id,
      site_id: activity.site_id,
      promotion_id: activity.promotion_id,
      promotion_type: activity.promotion_type,
      item_status: activity.item_status,
      item_ids: activity.item_ids,
    })),
    seller_create_target_keys: normalized.seller_create_target_keys,
  };
}

function itemDigest(itemIds = []) {
  return crypto.createHash('sha256').update(uniqueSorted(itemIds).join('\n'), 'utf8').digest('hex').toUpperCase();
}

function activitySummary(activity = null) {
  if (!activity) return null;
  return {
    activity_key: activityIdentityKey(activity),
    item_status: activity.item_status || '',
    item_count: activity.item_ids.length,
    item_digest: itemDigest(activity.item_ids),
    platform_total: activity.platform_total,
    saved_count: activity.saved_count,
    detail_status: activity.detail_status || null,
    blocked: Boolean(activity.blocked),
  };
}

function logicalActivityKey(activity = {}) {
  return [activity.account_id, activity.child_user_id, activity.promotion_id].join('|');
}

function sellerActivitySites(scope = {}) {
  return new Set((scope.activities || [])
    .filter((activity) => activity.promotion_type === 'SELLER_CAMPAIGN')
    .map((activity) => upper(`${activity.account_id}|${activity.child_user_id}|${activity.site_id}`)));
}

function adjustmentMessages({ autoRemovedItems, excludedNewItems, removedActivities, blockedActivities, structuralChanges }) {
  const messages = [];
  if (autoRemovedItems) messages.push(`已自动剔除 ${autoRemovedItems} 个失效或不再可处理的商品`);
  if (excludedNewItems) messages.push(`发现新增 ${excludedNewItems} 个商品，本次不纳入`);
  if (removedActivities) messages.push(`有 ${removedActivities} 个活动已失效或不再匹配，本次自动剔除`);
  if (blockedActivities) messages.push(`有 ${blockedActivities} 个活动实时读取失败，本次不使用旧缓存`);
  if (structuralChanges.length) messages.push('执行动作或活动结构发生实质变化，需要重新确认');
  return messages;
}

export function reconcileConfirmedExecutionScope({ confirmedScope = {}, observedScope = {} } = {}) {
  const confirmed = createConfirmedExecutionScope({
    action: confirmedScope.action,
    activities: confirmedScope.activities,
    sellerCreateTargetKeys: confirmedScope.seller_create_target_keys,
    sellerTargetStates: confirmedScope.seller_target_states,
  });
  const observed = createConfirmedExecutionScope({
    action: observedScope.action,
    activities: observedScope.activities,
    sellerCreateTargetKeys: observedScope.seller_create_target_keys,
    sellerTargetStates: observedScope.seller_target_states,
  });
  const structuralChanges = [];
  const structuralTargetKeys = new Set();
  if (confirmed.action !== observed.action) {
    structuralChanges.push('action_changed');
    structuralTargetKeys.add('__ACTION__');
  }

  const before = new Map(confirmed.activities.map((row) => [activityIdentityKey(row), row]));
  const after = new Map(observed.activities.map((row) => [activityIdentityKey(row), row]));
  const observedByLogicalKey = new Map(observed.activities.map((row) => [logicalActivityKey(row), row]));
  const executableActivities = [];
  const activityDiffs = [];
  let autoRemovedItems = 0;
  let excludedNewItems = 0;
  let removedActivities = 0;
  let blockedActivities = 0;

  for (const [key, current] of after) {
    if (before.has(key)) continue;
    excludedNewItems += current.item_ids.length;
    activityDiffs.push({
      activity_key: key,
      before: null,
      after: activitySummary(current),
      added_item_ids: [...current.item_ids],
      removed_item_ids: [],
    });
  }

  for (const [key, baseline] of before) {
    const current = after.get(key);
    if (!current) {
      const sameLogical = observedByLogicalKey.get(logicalActivityKey(baseline));
      if (sameLogical) {
        structuralChanges.push(`activity_definition_changed:${key}`);
        structuralTargetKeys.add(key);
        structuralTargetKeys.add(activityIdentityKey(sameLogical));
      }
      removedActivities += 1;
      autoRemovedItems += baseline.item_ids.length;
      activityDiffs.push({
        activity_key: key,
        before: activitySummary(baseline),
        after: null,
        added_item_ids: [],
        removed_item_ids: [...baseline.item_ids],
      });
      continue;
    }

    const baselineItems = new Set(baseline.item_ids);
    const currentItems = new Set(current.item_ids);
    const added = current.item_ids.filter((itemId) => !baselineItems.has(itemId));
    const removed = baseline.item_ids.filter((itemId) => !currentItems.has(itemId));
    activityDiffs.push({
      activity_key: key,
      before: activitySummary(baseline),
      after: activitySummary(current),
      added_item_ids: added,
      removed_item_ids: removed,
    });

    if (baseline.item_status !== current.item_status) {
      structuralChanges.push(`item_status_changed:${key}`);
      structuralTargetKeys.add(key);
      autoRemovedItems += baseline.item_ids.length;
      continue;
    }
    if (current.blocked) {
      blockedActivities += 1;
      autoRemovedItems += baseline.item_ids.length;
      continue;
    }
    const kept = baseline.item_ids.filter((itemId) => currentItems.has(itemId));
    autoRemovedItems += removed.length;
    excludedNewItems += added.length;
    if (kept.length) executableActivities.push({ ...current, blocked: false, item_ids: kept });
  }

  const observedSellerStates = observed.seller_target_states || {};
  const sellerCreateTargetKeys = [];
  const beforeSellerSites = sellerActivitySites(confirmed);
  const afterSellerSites = sellerActivitySites(observed);
  let sellerExistingCount = 0;
  for (const key of confirmed.seller_create_target_keys) {
    const state = lower(observedSellerStates[key]);
    if (state === 'existing') {
      sellerExistingCount += 1;
      if (!beforeSellerSites.has(key) && afterSellerSites.has(key)) {
        structuralChanges.push(`seller_activity_created:${key}`);
        structuralTargetKeys.add('__SELLER__');
      }
      continue;
    }
    if (state === 'confirmed_absent') {
      sellerCreateTargetKeys.push(key);
      continue;
    }
    structuralChanges.push(`seller_target_${state || 'missing'}:${key}`);
    structuralTargetKeys.add('__SELLER__');
  }

  const uniqueStructuralChanges = [...new Set(structuralChanges)].sort();
  const executionScope = createConfirmedExecutionScope({
    action: confirmed.action,
    activities: executableActivities,
    sellerCreateTargetKeys,
    sellerTargetStates: observedSellerStates,
  });
  const reconfirmationActivities = [...executionScope.activities];
  const reconfirmationKeys = new Set(reconfirmationActivities.map(activityIdentityKey));
  for (const baseline of confirmed.activities) {
    const current = after.get(activityIdentityKey(baseline)) || observedByLogicalKey.get(logicalActivityKey(baseline));
    if (!current || current.blocked) continue;
    const currentItems = new Set(current.item_ids);
    const kept = baseline.item_ids.filter((itemId) => currentItems.has(itemId));
    const key = activityIdentityKey(current);
    if (!kept.length || reconfirmationKeys.has(key)) continue;
    reconfirmationActivities.push({ ...current, blocked: false, item_ids: kept });
    reconfirmationKeys.add(key);
  }
  const confirmedSellerSites = new Set(confirmed.seller_create_target_keys.map(upper));
  if (sellerExistingCount > 0) {
    for (const current of observed.activities) {
      const siteKey = upper(`${current.account_id}|${current.child_user_id}|${current.site_id}`);
      const key = activityIdentityKey(current);
      if (current.promotion_type !== 'SELLER_CAMPAIGN'
          || !confirmedSellerSites.has(siteKey)
          || before.has(key)
          || current.blocked
          || !current.item_ids.length
          || reconfirmationKeys.has(key)) continue;
      reconfirmationActivities.push(current);
      reconfirmationKeys.add(key);
    }
  }
  const reconfirmationScope = createConfirmedExecutionScope({
    action: observed.action || confirmed.action,
    activities: reconfirmationActivities,
    sellerCreateTargetKeys,
    sellerTargetStates: observedSellerStates,
  });
  const changes = [];
  if (autoRemovedItems) changes.push({ kind: 'auto_removed', count: autoRemovedItems });
  if (excludedNewItems) changes.push({ kind: 'new_excluded', count: excludedNewItems });
  if (removedActivities) changes.push({ kind: 'activity_removed', count: removedActivities });
  if (blockedActivities) changes.push({ kind: 'activity_blocked', count: blockedActivities });
  if (sellerExistingCount) changes.push({ kind: 'seller_existing', count: sellerExistingCount });
  if (uniqueStructuralChanges.length) changes.push({ kind: 'requires_reprepare', values: uniqueStructuralChanges });
  const messages = adjustmentMessages({
    autoRemovedItems,
    excludedNewItems,
    removedActivities,
    blockedActivities,
    structuralChanges: uniqueStructuralChanges,
  });

  return {
    execution_scope: executionScope,
    reconfirmation_scope: reconfirmationScope,
    observed_scope: observed,
    requires_reconfirm: uniqueStructuralChanges.length > 0,
    requires_reprepare: uniqueStructuralChanges.length > 0,
    structural_changes: uniqueStructuralChanges,
    structural_target_keys: [...structuralTargetKeys].sort(),
    auto_removed_item_count: autoRemovedItems,
    excluded_new_item_count: excludedNewItems,
    removed_activity_count: removedActivities,
    blocked_activity_count: blockedActivities,
    seller_existing_count: sellerExistingCount,
    execution_relation_count: executionScope.activities.reduce((sum, activity) => sum + activity.item_ids.length, 0),
    before_summaries: confirmed.activities.map(activitySummary),
    after_summaries: observed.activities.map(activitySummary),
    activity_diffs: activityDiffs.sort((left, right) => left.activity_key.localeCompare(right.activity_key)),
    changes,
    messages,
  };
}
