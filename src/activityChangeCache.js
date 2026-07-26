import crypto from 'node:crypto';

export const ACTIVITY_CATALOG_TTL_MS = 24 * 60 * 60 * 1000;
export const ACTIVITY_ITEMS_TTL_MS = 3 * 24 * 60 * 60 * 1000;
export const ACTIVITY_PARTIAL_ITEMS_TTL_MS = 24 * 60 * 60 * 1000;

export function activityCacheKey(value = {}) {
  const accountId = String(value.account_id || value.accountId || '');
  const childUserId = String(value.child_user_id || value.childUserId || '');
  const tail = [
    String(value.site_id || value.siteId || '').toUpperCase(),
    String(value.promotion_id || value.promotionId || ''),
    String(value.promotion_type || value.promotionType || '').toUpperCase(),
  ];
  return [
    accountId,
    ...(childUserId ? [childUserId] : []),
    ...tail,
  ].join('|');
}

export function isActivityExpired(promotion = {}, now = new Date()) {
  const finish = String(promotion.finish_date || promotion.finishDate || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(finish)) return false;
  return finish < shanghaiDate(now);
}

export function activityCatalogDecision(state = null, now = new Date()) {
  if (!state) return { refresh: true, reason: 'unverified' };
  if (state.last_error && isSameShanghaiDay(state.updated_at, now)) {
    return { refresh: false, blocked: true, reason: 'same_day_failed' };
  }
  if (Number(state.dirty || 0) === 1) return { refresh: true, reason: 'dirty' };
  if (String(state.continuity || 'continuous') !== 'continuous') return { refresh: true, reason: 'event_gap' };
  if (isSameShanghaiDay(state.catalog_checked_at, now)) return { refresh: false, reason: 'same_day_cache' };
  return { refresh: true, reason: 'daily_due' };
}

export function planActivityCatalogRoutes(routes = [], getState = () => null, now = new Date()) {
  const refresh = [];
  const cached = [];
  const blocked = [];
  const reasons = {};
  for (const route of routes || []) {
    const decision = activityCatalogDecision(getState(route), now);
    const key = [
      String(route.account_id || route.accountId || ''),
      String(route.child_user_id || route.childUserId || ''),
      String(route.site_id || route.siteId || '').toUpperCase(),
    ].join('|');
    if (decision.blocked) {
      blocked.push(route);
      reasons[key] = decision.reason;
    } else if (decision.refresh) {
      refresh.push(route);
      reasons[key] = decision.reason;
    } else cached.push(route);
  }
  return { refresh, cached, blocked, reasons };
}

export function activityItemsDecision({
  promotion = {},
  cacheState = null,
  fetchState = null,
  fallbackState = null,
  itemStatus = 'candidate',
  now = new Date(),
} = {}) {
  const normalizedStatus = String(itemStatus || 'candidate').toLowerCase();
  if (isActivityExpired(promotion, now)) return { refresh: false, blocked: true, reason: 'expired' };
  if (!cacheState) return { refresh: true, reason: 'unverified' };
  const attemptedToday = isSameShanghaiDay(fetchState?.updated_at, now);
  const cacheChangedAfterAttempt = isNewer(cacheState?.updated_at, fetchState?.updated_at);
  const detailStatus = String(fetchState?.detail_status || '').toLowerCase();
  if (attemptedToday && !cacheChangedAfterAttempt && detailStatus === 'error') {
    return { refresh: false, blocked: true, reason: 'same_day_failed' };
  }
  if (attemptedToday && !cacheChangedAfterAttempt) {
    if (isCompositeCandidateState(fetchState, fallbackState)
        && isFresh(fallbackState.updated_at, ACTIVITY_ITEMS_TTL_MS, now)) {
      return { refresh: false, reason: 'verified_composite_cache', effective_state: 'candidate_plus_inventory_fallback' };
    }
    if (normalizedStatus === 'candidate' && detailStatus === 'partial_api_sparse_marketplace_candidate') {
      return {
        refresh: false,
        reason: 'same_day_partial_cache',
        effective_state: 'partial_api_sparse_marketplace_candidate',
      };
    }
    if (!isFullFetchState(fetchState)) {
      return { refresh: false, blocked: true, reason: 'same_day_incomplete' };
    }
    if (Number(cacheState.dirty || 0) === 1 || String(cacheState.continuity || 'continuous') !== 'continuous') {
      return { refresh: false, reason: 'same_day_cache' };
    }
  }
  if (Number(cacheState.dirty || 0) === 1) return { refresh: true, reason: 'dirty' };
  if (String(cacheState.continuity || 'continuous') !== 'continuous') return { refresh: true, reason: 'event_gap' };
  if (!fetchState || String(fetchState.detail_status || '').toLowerCase() === 'error') return { refresh: true, reason: 'unreadable' };
  if (!isFullFetchState(fetchState)) {
    if (isCompositeCandidateState(fetchState, fallbackState)
        && isFresh(fetchState.updated_at, ACTIVITY_ITEMS_TTL_MS, now)
        && isFresh(fallbackState.updated_at, ACTIVITY_ITEMS_TTL_MS, now)
        && isFresh(cacheState.items_full_checked_at, ACTIVITY_ITEMS_TTL_MS, now)) {
      return { refresh: false, reason: 'verified_composite_cache', effective_state: 'candidate_plus_inventory_fallback' };
    }
    if (
      normalizedStatus === 'candidate'
      && String(fetchState.detail_status || '').toLowerCase() === 'partial_api_sparse_marketplace_candidate'
      && isFresh(fetchState.updated_at, ACTIVITY_PARTIAL_ITEMS_TTL_MS, now)
    ) {
      return {
        refresh: false,
        reason: 'verified_sparse_window',
        effective_state: 'partial_api_sparse_marketplace_candidate',
      };
    }
    if (attemptedToday && !cacheChangedAfterAttempt) {
      return { refresh: false, blocked: true, reason: 'same_day_incomplete' };
    }
    return { refresh: true, reason: 'not_full' };
  }
  if (isSameShanghaiDay(cacheState.items_full_checked_at, now)) {
    return { refresh: false, reason: 'same_day_cache' };
  }
  if (!isFresh(cacheState.items_full_checked_at, ACTIVITY_ITEMS_TTL_MS, now)) return { refresh: true, reason: 'three_day_due' };
  return { refresh: false, reason: 'verified_cache' };
}

export function shouldProbeFreshPreparationItems({
  action = '',
  itemStatus = '',
  finalRevalidation = false,
  cacheDecision = null,
  fetchState = null,
  now = new Date(),
} = {}) {
  return candidatePreparationReadDecision({
    action,
    itemStatus,
    finalRevalidation,
    cacheDecision,
    fetchState,
    now,
  }).probe;
}

export function candidatePreparationReadDecision({
  action = '',
  itemStatus = '',
  finalRevalidation = false,
  cacheDecision = null,
  fetchState = null,
  now = new Date(),
} = {}) {
  const normalizedAction = String(action || '').toLowerCase();
  const normalizedStatus = String(itemStatus || '').toLowerCase();
  if (finalRevalidation || normalizedAction !== 'enroll' || normalizedStatus !== 'candidate') {
    return { probe: false, reason: 'candidate_probe_not_applicable' };
  }
  if (!cacheDecision) {
    return { probe: true, reason: 'candidate_probe_legacy_call' };
  }
  if (cacheDecision.refresh || cacheDecision.blocked) {
    return { probe: false, reason: String(cacheDecision.reason || 'candidate_cache_not_reusable') };
  }
  const sameDayCache = isSameShanghaiDay(fetchState?.updated_at, now)
    || ['same_day_cache', 'same_day_partial_cache'].includes(String(cacheDecision.reason || ''));
  if (sameDayCache) {
    return { probe: false, reason: 'same_day_candidate_cache' };
  }
  return { probe: true, reason: 'candidate_daily_probe_due' };
}

export function candidateTotalProbeDecision({
  fetchState = null,
  probe = null,
  now = new Date(),
} = {}) {
  const baselineSummary = fetchStateRaw(fetchState)?.identity_summary;
  if (baselineSummary || probe?.identity_summary) {
    return candidateIdentityProbeDecision({ fetchState, probe, now });
  }
  if (!fetchState) return { refresh: true, reason: 'candidate_baseline_missing' };
  const previousTotal = finiteNumberOrNull(fetchState.platform_total);
  const currentTotal = finiteNumberOrNull(probe?.platform_total);
  if (previousTotal === null || currentTotal === null) {
    return { refresh: true, reason: 'candidate_total_unavailable' };
  }
  if (previousTotal !== currentTotal) {
    return { refresh: true, reason: 'candidate_total_changed' };
  }
  if (!isSameShanghaiDay(fetchState.updated_at, now)) {
    return { refresh: true, reason: 'candidate_daily_identity_due' };
  }
  const previousFirstPageIds = fetchStateFirstPageItemIds(fetchState);
  if (!previousFirstPageIds) {
    return { refresh: true, reason: 'candidate_identity_baseline_missing' };
  }
  const currentFirstPageIds = normalizedItemIds(probe?.first_page_item_ids);
  if (!sameStringArray(previousFirstPageIds, currentFirstPageIds)) {
    return { refresh: true, reason: 'candidate_first_page_changed' };
  }
  return { refresh: false, reason: 'candidate_total_unchanged' };
}

export function candidateIdentityProbeDecision({
  fetchState = null,
  probe = null,
  now = new Date(),
} = {}) {
  if (!fetchState) return { refresh: true, reason: 'candidate_baseline_missing' };
  const previousTotal = finiteNumberOrNull(fetchState.platform_total);
  const currentTotal = finiteNumberOrNull(probe?.platform_total);
  if (previousTotal === null || currentTotal === null) {
    return { refresh: true, reason: 'candidate_total_unavailable' };
  }
  if (previousTotal !== currentTotal) {
    return { refresh: true, reason: 'candidate_total_changed' };
  }
  const sameDay = isSameShanghaiDay(fetchState.updated_at, now);
  const baseline = normalizeIdentitySummary(fetchStateRaw(fetchState)?.identity_summary);
  const current = normalizeIdentitySummary(probe?.identity_summary);
  if (!baseline) return { refresh: true, reason: 'candidate_identity_baseline_missing' };
  if (!current || !current.complete) {
    if (sameDay) {
      return {
        refresh: false,
        reason: 'same_day_candidate_cache',
        verification_source: 'same_day_cached_identity',
      };
    }
    return { refresh: true, reason: 'candidate_probe_not_authoritative' };
  }
  if (!baseline.complete) {
    return { refresh: true, reason: 'candidate_probe_not_authoritative' };
  }
  if (baseline.algorithm !== current.algorithm
    || baseline.item_count !== current.item_count
    || baseline.digest !== current.digest) {
    return { refresh: true, reason: 'candidate_identity_changed' };
  }
  return {
    refresh: false,
    reason: 'candidate_identity_verified',
    verification_source: 'complete_identity_digest',
  };
}

export function inventoryTotalProbeDecision({
  fallbackState = null,
  probe = null,
  now = new Date(),
} = {}) {
  if (!fallbackState) return { refresh: true, reason: 'inventory_baseline_missing' };
  const raw = fetchStateRaw(fallbackState);
  const previousTotal = finiteNumberOrNull(raw?.scan_total);
  const currentTotal = finiteNumberOrNull(probe?.platform_total);
  if (previousTotal === null || currentTotal === null) {
    return { refresh: true, reason: 'inventory_total_unavailable' };
  }
  if (previousTotal !== currentTotal) {
    return { refresh: true, reason: 'inventory_total_changed' };
  }
  if (!isSameShanghaiDay(fallbackState.updated_at, now)) {
    return { refresh: true, reason: 'inventory_daily_identity_due' };
  }
  const baselineIds = raw?.inventory_first_page_item_ids ?? raw?.scan?.inventory_first_page_item_ids;
  if (!Array.isArray(baselineIds)) {
    return { refresh: true, reason: 'inventory_identity_baseline_missing' };
  }
  const previousFirstPageIds = normalizedItemIds(baselineIds);
  const currentFirstPageIds = normalizedItemIds(probe?.first_page_item_ids);
  if (!sameStringArray(previousFirstPageIds, currentFirstPageIds)) {
    return { refresh: true, reason: 'inventory_first_page_changed' };
  }
  return { refresh: false, reason: 'inventory_total_unchanged' };
}

export function buildItemIdentitySummary(values = [], {
  complete = false,
  cursor = null,
} = {}) {
  const itemIds = itemIdsFromRows(values);
  return {
    algorithm: 'sha256-sorted-item-ids-v1',
    item_count: itemIds.length,
    digest: crypto.createHash('sha256').update(itemIds.join('\n'), 'utf8').digest('hex').toUpperCase(),
    complete: Boolean(complete),
    cursor: cursor == null ? null : String(cursor),
  };
}

export function itemIdentityDelta(previousRows = [], nextRows = [], { complete = false } = {}) {
  const previous = new Set(itemIdsFromRows(previousRows));
  const next = new Set(itemIdsFromRows(nextRows));
  let addedCount = 0;
  let missingCount = 0;
  for (const itemId of next) {
    if (!previous.has(itemId)) addedCount += 1;
  }
  for (const itemId of previous) {
    if (!next.has(itemId)) missingCount += 1;
  }
  return {
    previous_count: previous.size,
    current_count: next.size,
    added_count: addedCount,
    removed_count: complete ? missingCount : 0,
    unreturned_count: complete ? 0 : missingCount,
  };
}

function isCompositeCandidateState(fetchState = {}, fallbackState = {}) {
  const primary = String(fetchState.detail_status || '').toLowerCase();
  const fallback = String(fallbackState?.detail_status || '').toLowerCase();
  if (!/(partial|sample|candidate)/.test(primary)) return false;
  if (!/(candidate_inventory_fallback|inventory_scan_fallback_ready|inventory_fallback|full|ok)/.test(fallback)) return false;
  return !/(error|partial|sample|blocked|unknown)/.test(fallback);
}

export function applyActivityChangeEvent(states, event = {}) {
  const key = activityCacheKey(event);
  const current = states.get(key) || {};
  states.set(key, {
    ...current,
    account_id: String(event.account_id || event.accountId || ''),
    site_id: String(event.site_id || event.siteId || '').toUpperCase(),
    promotion_id: String(event.promotion_id || event.promotionId || ''),
    promotion_type: String(event.promotion_type || event.promotionType || '').toUpperCase(),
    dirty: 1,
    continuity: event.gap === true ? 'gap' : String(current.continuity || 'continuous'),
    event_cursor: event.cursor == null ? current.event_cursor ?? null : String(event.cursor),
  });
  return states.get(key);
}

export function nextNonPeakCalibrationAt(now = new Date(), hour = 2, minute = 30) {
  const target = new Date(now);
  target.setHours(hour, minute, 0, 0);
  if (target.getTime() <= now.getTime()) target.setDate(target.getDate() + 1);
  return target;
}

function isFresh(value, ttlMs, now) {
  const timestamp = Date.parse(String(value || ''));
  return Number.isFinite(timestamp) && now.getTime() - timestamp >= 0 && now.getTime() - timestamp < ttlMs;
}

function isFullFetchState(state = {}) {
  const detail = String(state.detail_status || '').toLowerCase();
  if (/(error|partial|sample|blocked|unknown)/.test(detail)) return false;
  const saved = Number(state.saved_count || 0);
  const total = state.platform_total == null ? null : Number(state.platform_total);
  return detail === 'empty' || detail === 'ok' || detail === 'full' || (Number.isFinite(total) && saved >= total);
}

function shanghaiDate(now) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now);
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${map.year}-${map.month}-${map.day}`;
}

function isSameShanghaiDay(timestamp, now) {
  if (!timestamp) return false;
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return false;
  return shanghaiDate(parsed) === shanghaiDate(now);
}

function isNewer(left, right) {
  const leftTime = Date.parse(String(left || ''));
  const rightTime = Date.parse(String(right || ''));
  return Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime > rightTime;
}

function finiteNumberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function fetchStateFirstPageItemIds(fetchState = {}) {
  const raw = fetchStateRaw(fetchState);
  if (!raw || !Array.isArray(raw.first_page_item_ids)) return null;
  return normalizedItemIds(raw.first_page_item_ids);
}

function fetchStateRaw(fetchState = {}) {
  if (!fetchState || typeof fetchState !== 'object') return null;
  let raw = fetchState.raw_json;
  if (typeof raw !== 'string') return raw && typeof raw === 'object' ? raw : null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function normalizeIdentitySummary(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const algorithm = String(value.algorithm || '');
  const itemCount = finiteNumberOrNull(value.item_count);
  const digest = String(value.digest || '').trim().toUpperCase();
  if (algorithm !== 'sha256-sorted-item-ids-v1'
    || itemCount === null
    || itemCount < 0
    || !/^[A-F0-9]{64}$/.test(digest)) {
    return null;
  }
  return {
    algorithm,
    item_count: itemCount,
    digest,
    complete: value.complete === true,
    cursor: value.cursor == null ? null : String(value.cursor),
  };
}

function normalizedItemIds(values = []) {
  return [...new Set((Array.isArray(values) ? values : [])
    .map((value) => String(value || '').trim())
    .filter(Boolean))]
    .sort();
}

function itemIdsFromRows(rows = []) {
  return normalizedItemIds((Array.isArray(rows) ? rows : []).map((row) => (
    typeof row === 'string' ? row : row?.item_id ?? row?.itemId ?? row?.id
  )));
}

function sameStringArray(left = [], right = []) {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}
