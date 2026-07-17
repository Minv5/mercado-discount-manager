import { activityItemsDecision } from './activityChangeCache.js';
import { activityIdentityKey } from './submissionScopeFreeze.js';

function routeKey(value = {}) {
  return [
    String(value.account_id ?? value.accountId ?? '').trim(),
    String(value.child_user_id ?? value.childUserId ?? '').trim(),
    String(value.site_id ?? value.siteId ?? '').trim().toUpperCase(),
  ].join('|');
}

function orderedSet(values = []) {
  return new Set([...new Set(values)].sort());
}

function addReason(reasons, key, reason) {
  if (!key || !reason) return;
  if (!reasons.has(key)) reasons.set(key, new Set());
  reasons.get(key).add(String(reason));
}

function catalogFacts(refreshes = []) {
  const changed = new Map();
  const blockedRoutes = new Set();
  for (const refresh of refreshes || []) {
    for (const route of refresh?.blocked_route_keys || []) blockedRoutes.add(String(route));
    for (const change of refresh?.catalog_identity_changes || []) {
      const key = activityIdentityKey(change?.key || change?.promotion || change);
      if (!key || key.split('|').some((part) => !part)) continue;
      if (!changed.has(key)) changed.set(key, new Set());
      changed.get(key).add(String(change?.reason || 'catalog_changed'));
    }
  }
  return { changed, blockedRoutes };
}

function targetStatuses(action) {
  return String(action || '').toLowerCase() === 'enroll' ? ['candidate', 'started'] : ['started'];
}

export function buildFinalRevalidationPlan({
  confirmedScope = {},
  currentPromotions = [],
  catalogRefreshes = [],
  action = '',
  explicitTargetKeys = [],
  getCacheState = () => null,
  getFetchState = () => null,
  getFallbackState = () => null,
  now = new Date(),
} = {}) {
  const confirmedActivities = Array.isArray(confirmedScope?.activities) ? confirmedScope.activities : [];
  const currentRows = Array.isArray(currentPromotions) ? currentPromotions : [];
  const confirmedByIdentity = new Map(confirmedActivities.map((row) => [activityIdentityKey(row), row]));
  const currentByIdentity = new Map(currentRows.map((row) => [activityIdentityKey(row), row]));
  const { changed, blockedRoutes } = catalogFacts(catalogRefreshes);
  const itemRead = new Set();
  const scopeReview = new Set();
  const blocked = new Set();
  const removed = new Set();
  const reasons = new Map();
  const acceptedNewIdentities = new Set();

  const markRead = (key, reason) => {
    itemRead.add(key);
    scopeReview.add(key);
    addReason(reasons, key, reason);
  };
  const markBlocked = (key, reason) => {
    blocked.add(key);
    scopeReview.add(key);
    addReason(reasons, key, reason);
  };
  const markRemoved = (key, reason) => {
    removed.add(key);
    scopeReview.add(key);
    addReason(reasons, key, reason);
  };

  for (const [key, baseline] of confirmedByIdentity) {
    if (blockedRoutes.has(routeKey(baseline))) {
      markBlocked(key, 'catalog:unreadable');
      continue;
    }
    const current = currentByIdentity.get(key);
    if (!current) {
      markRemoved(key, 'catalog:removed');
      continue;
    }
    for (const reason of changed.get(key) || []) markRead(key, `catalog:${reason}`);
    for (const status of targetStatuses(action || confirmedScope.action)) {
      const decision = activityItemsDecision({
        promotion: current,
        cacheState: getCacheState(current),
        fetchState: getFetchState(current, status),
        fallbackState: status === 'candidate' ? getFallbackState(current) : null,
        now,
      });
      if (decision.blocked) markRemoved(key, `${status}:${decision.reason}`);
      else if (decision.refresh) markRead(key, `${status}:${decision.reason}`);
    }
  }

  const explicit = [...new Set((explicitTargetKeys || []).map(String).filter(Boolean))];
  if (explicit.includes('__ACTION__')) {
    for (const key of confirmedByIdentity.keys()) {
      if (currentByIdentity.has(key)) markRead(key, 'explicit:action');
    }
  }
  if (explicit.includes('__SELLER__')) {
    const sellerRoutes = new Set((confirmedScope.seller_create_target_keys || []).map((value) => String(value).toUpperCase()));
    for (const [key, row] of currentByIdentity) {
      if (String(row.promotion_type || '').toUpperCase() !== 'SELLER_CAMPAIGN') continue;
      if (sellerRoutes.size && !sellerRoutes.has(routeKey(row).toUpperCase())) continue;
      acceptedNewIdentities.add(key);
      markRead(key, 'explicit:seller_created');
    }
  }
  for (const raw of explicit.filter((value) => !value.startsWith('__'))) {
    const key = activityIdentityKey(raw);
    if (currentByIdentity.has(key)) markRead(key, 'explicit:changed_target');
  }

  const excludedNewIdentities = [...currentByIdentity.keys()]
    .filter((key) => !confirmedByIdentity.has(key) && !acceptedNewIdentities.has(key));
  const reasonsByIdentity = Object.fromEntries([...reasons.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, values]) => [key, [...values].sort()]));

  return {
    total_activity_count: confirmedByIdentity.size,
    item_read_identity_keys: orderedSet(itemRead),
    scope_review_identity_keys: orderedSet(scopeReview),
    blocked_identity_keys: orderedSet(blocked),
    removed_identity_keys: orderedSet(removed),
    excluded_new_identity_keys: orderedSet(excludedNewIdentities),
    excluded_new_activity_count: excludedNewIdentities.length,
    reasons_by_identity: reasonsByIdentity,
  };
}

