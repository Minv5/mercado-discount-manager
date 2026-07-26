import crypto from 'node:crypto';
import { activityItemsDecision } from './activityChangeCache.js';
import { activityIdentityKey } from './submissionScopeFreeze.js';

const FINAL_VERIFICATION_MAX_AGE_MS = 5 * 60 * 1000;

function routeKey(value = {}) {
  return [
    String(value.account_id ?? value.accountId ?? '').trim(),
    String(value.child_user_id ?? value.childUserId ?? '').trim(),
    String(value.site_id ?? value.siteId ?? '').trim().toUpperCase(),
  ].join('|');
}

function shanghaiBusinessDate(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date);
}

function routeStateFacts(route = {}, state = null) {
  const key = routeKey(route);
  const facts = {
    route_key: key,
    account_id: String(route.account_id ?? route.accountId ?? '').trim(),
    child_user_id: String(route.child_user_id ?? route.childUserId ?? '').trim(),
    site_id: String(route.site_id ?? route.siteId ?? '').trim().toUpperCase(),
    catalog_checked_at: state?.catalog_checked_at || null,
    updated_at: state?.updated_at || null,
    dirty: Number(state?.dirty || 0),
    continuity: String(state?.continuity || 'continuous'),
    event_cursor: state?.event_cursor == null ? null : String(state.event_cursor),
    last_error: state?.last_error == null ? null : String(state.last_error),
  };
  return {
    ...facts,
    digest: crypto.createHash('sha256').update(JSON.stringify(facts), 'utf8').digest('hex').toUpperCase(),
  };
}

function validPreparedRouteFacts(facts = {}, businessDate = null) {
  return Boolean(
    facts.route_key
    && !facts.route_key.split('|').some((part) => !part)
    && facts.catalog_checked_at
    && shanghaiBusinessDate(facts.catalog_checked_at) === businessDate
    && Number(facts.dirty || 0) === 0
    && String(facts.continuity || '') === 'continuous'
    && !facts.last_error
  );
}

function preparedRouteSnapshotPayload(snapshot = {}) {
  return {
    version: Number(snapshot.version || 0),
    source: String(snapshot.source || ''),
    captured_at: snapshot.captured_at || null,
    business_date: snapshot.business_date || null,
    routes: Array.isArray(snapshot.routes) ? snapshot.routes : [],
  };
}

function preparedRouteSnapshotDigest(snapshot = {}) {
  return crypto.createHash('sha256')
    .update(JSON.stringify(preparedRouteSnapshotPayload(snapshot)), 'utf8')
    .digest('hex')
    .toUpperCase();
}

function canonicalIso(value) {
  if (typeof value !== 'string' || !value) return false;
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString() === value;
}

function nullableCanonicalIso(value) {
  return value === null || canonicalIso(value);
}

function validPreparedRouteRowSchema(row = null) {
  if (!row || typeof row !== 'object' || Array.isArray(row)) return false;
  const requiredFields = [
    'route_key', 'account_id', 'child_user_id', 'site_id',
    'catalog_checked_at', 'updated_at', 'dirty', 'continuity',
    'event_cursor', 'last_error', 'digest',
  ];
  if (requiredFields.some((field) => !Object.hasOwn(row, field))) return false;
  const key = routeKey(row);
  if (!key || key.split('|').length !== 3 || key.split('|').some((part) => !part)) return false;
  if (String(row.route_key) !== key) return false;
  if (String(row.account_id) !== String(row.account_id).trim()) return false;
  if (String(row.child_user_id) !== String(row.child_user_id).trim()) return false;
  if (String(row.site_id) !== String(row.site_id).trim().toUpperCase()) return false;
  if (!nullableCanonicalIso(row.catalog_checked_at) || !nullableCanonicalIso(row.updated_at)) return false;
  if (![0, 1].includes(row.dirty)) return false;
  if (!['continuous', 'gap'].includes(row.continuity)) return false;
  if (!(row.event_cursor === null || typeof row.event_cursor === 'string')) return false;
  if (!(row.last_error === null || typeof row.last_error === 'string')) return false;
  return true;
}

function validPreparedRouteSnapshotSchema(snapshot = null) {
  if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)) return false;
  if (snapshot.version !== 1 || snapshot.source !== 'prepared_route_snapshot') return false;
  if (!canonicalIso(snapshot.captured_at)) return false;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(snapshot.business_date || ''))) return false;
  if (snapshot.business_date !== shanghaiBusinessDate(snapshot.captured_at)) return false;
  if (!Array.isArray(snapshot.routes)) return false;
  const routeKeys = new Set();
  for (const row of snapshot.routes) {
    if (!validPreparedRouteRowSchema(row) || routeKeys.has(row.route_key)) return false;
    routeKeys.add(row.route_key);
  }
  return true;
}

function validPreparedRouteSnapshot(snapshot = null) {
  if (!validPreparedRouteSnapshotSchema(snapshot)) return false;
  if (!snapshot || !/^[A-F0-9]{64}$/.test(String(snapshot.digest || '').toUpperCase())) return false;
  if (String(snapshot.digest).toUpperCase() !== preparedRouteSnapshotDigest(snapshot)) return false;
  return (snapshot.routes || []).every((row) => (
    /^[A-F0-9]{64}$/.test(String(row?.digest || '').toUpperCase())
    && String(row.digest).toUpperCase() === routeStateFacts(row, row).digest
  ));
}

export function buildPreparedRouteSnapshot({
  routes = [],
  getRouteState = () => null,
  capturedAt = new Date(),
} = {}) {
  const businessDate = shanghaiBusinessDate(capturedAt);
  const rows = [...new Map((routes || [])
    .map((route) => [routeKey(route), route])
    .filter(([key]) => key && !key.split('|').some((part) => !part))).values()]
    .map((route) => routeStateFacts(route, getRouteState(route)))
    .sort((left, right) => left.route_key.localeCompare(right.route_key));
  const snapshot = {
    version: 1,
    source: 'prepared_route_snapshot',
    captured_at: (capturedAt instanceof Date ? capturedAt : new Date(capturedAt)).toISOString(),
    business_date: businessDate,
    routes: rows,
  };
  return {
    ...snapshot,
    digest: preparedRouteSnapshotDigest(snapshot),
  };
}

export function extendPreparedRouteSnapshot({
  preparedSnapshot = null,
  routes = [],
  getRouteState = () => null,
} = {}) {
  if (!validPreparedRouteSnapshot(preparedSnapshot)) return preparedSnapshot;
  const existingRows = new Map((preparedSnapshot.routes || []).map((row) => [String(row.route_key || ''), row]));
  const missingRoutes = (routes || []).filter((route) => !existingRows.has(routeKey(route)));
  if (!missingRoutes.length) return preparedSnapshot;
  const additions = buildPreparedRouteSnapshot({
    routes: missingRoutes,
    getRouteState,
    capturedAt: preparedSnapshot.captured_at,
  });
  const snapshot = {
    ...preparedRouteSnapshotPayload(preparedSnapshot),
    routes: [...existingRows.values(), ...(additions.routes || [])]
      .sort((left, right) => String(left.route_key || '').localeCompare(String(right.route_key || ''))),
  };
  return {
    ...snapshot,
    digest: preparedRouteSnapshotDigest(snapshot),
  };
}

export function comparePreparedRouteSnapshot({
  preparedSnapshot = null,
  routes = [],
  getRouteState = () => null,
  liveRouteKeys = [],
  attemptedRouteKeys = [],
  now = new Date(),
} = {}) {
  const businessDate = shanghaiBusinessDate(now);
  const snapshotValid = validPreparedRouteSnapshot(preparedSnapshot);
  const preparedRows = new Map((snapshotValid ? preparedSnapshot.routes : [])
    .map((row) => [String(row?.route_key || ''), row]));
  const requiredRoutes = [...new Set((routes || []).map(routeKey).filter((key) => key && !key.split('|').some((part) => !part)))].sort();
  const live = new Set([...(liveRouteKeys || [])].map(String));
  const attempted = new Set([...(attemptedRouteKeys || [])].map(String));
  const verified = new Set();
  const localVerified = new Set();
  const invalid = new Set();
  for (const route of routes || []) {
    const key = routeKey(route);
    if (!requiredRoutes.includes(key)) continue;
    if (live.has(key)) {
      verified.add(key);
      continue;
    }
    if (attempted.has(key)) {
      invalid.add(key);
      continue;
    }
    const prepared = preparedRows.get(key);
    const current = routeStateFacts(route, getRouteState(route));
    if (
      snapshotValid
      && preparedSnapshot?.business_date === businessDate
      && prepared
      && prepared.digest === current.digest
      && validPreparedRouteFacts(current, businessDate)
    ) {
      verified.add(key);
      localVerified.add(key);
    } else {
      invalid.add(key);
    }
  }
  const evidencePayload = {
    source: 'prepared_route_snapshot',
    production: true,
    business_date: businessDate,
    prepared_snapshot_digest: preparedSnapshot?.digest || null,
    prepared_snapshot_valid: snapshotValid,
    verified_route_keys: [...verified].sort(),
    local_verified_route_keys: [...localVerified].sort(),
    live_route_keys: [...live].filter((key) => requiredRoutes.includes(key)).sort(),
    attempted_route_keys: [...attempted].filter((key) => requiredRoutes.includes(key)).sort(),
    invalid_route_keys: [...invalid].sort(),
    required_route_keys: requiredRoutes,
  };
  return {
    ...evidencePayload,
    verified_at: (now instanceof Date ? now : new Date(now)).toISOString(),
    evidence_id: crypto.createHash('sha256').update(JSON.stringify(evidencePayload), 'utf8').digest('hex').toUpperCase(),
  };
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

function normalizeScopeSignatures(activities = [], sellerCreateTargetKeys = []) {
  const activityKeys = new Set();
  for (const row of activities || []) {
    const key = activityIdentityKey(row);
    if (key) activityKeys.add(key);
  }
  const sellerKeys = new Set(
    (sellerCreateTargetKeys || [])
      .map((value) => String(value || '').trim().toUpperCase())
      .filter(Boolean),
  );
  return { activityKeys, sellerKeys, count: activityKeys.size };
}

function isSameFinalScope(signature, previousSignature) {
  if (!previousSignature || !signature) return false;
  if (signature.action !== previousSignature.action) return false;
  if (signature.count !== previousSignature.count) return false;
  if (signature.sellerKeys.size !== previousSignature.sellerKeys.size) return false;
  for (const key of signature.sellerKeys) {
    if (!previousSignature.sellerKeys.has(key)) return false;
  }
  for (const key of previousSignature.sellerKeys) {
    if (!signature.sellerKeys.has(key)) return false;
  }
  for (const key of signature.activityKeys) {
    if (!previousSignature.activityKeys.has(key)) return false;
  }
  for (const key of previousSignature.activityKeys) {
    if (!signature.activityKeys.has(key)) return false;
  }
  return true;
}

export function assessFinalVerificationEvidence({
  evidence = null,
  preparedAt = null,
  now = new Date(),
  maxAgeMs = FINAL_VERIFICATION_MAX_AGE_MS,
} = {}) {
  const source = String(evidence?.source || 'local_cache').toLowerCase();
  const verifiedAt = Date.parse(String(evidence?.verified_at || ''));
  const preparedTime = Date.parse(String(preparedAt || ''));
  const nowTime = (now instanceof Date ? now : new Date(now)).getTime();
  const fresh = Number.isFinite(verifiedAt)
    && verifiedAt <= nowTime
    && nowTime - verifiedAt <= Math.max(0, Number(maxAgeMs) || 0);
  const afterPrepared = Number.isFinite(preparedTime)
    && Number.isFinite(verifiedAt)
    && verifiedAt >= preparedTime;
  const hasEvidenceId = Boolean(String(evidence?.evidence_id || '').trim());

  if (source === 'live_catalog' && fresh && afterPrepared && hasEvidenceId) {
    return {
      source,
      allows_local_zero_read: true,
      reason: 'live_catalog_verified_after_prepare',
      verified_at: new Date(verifiedAt).toISOString(),
      evidence_id: String(evidence.evidence_id),
    };
  }

  if (source === 'prepared_route_snapshot'
      && evidence?.production === true
      && evidence?.business_date === shanghaiBusinessDate(now)
      && hasEvidenceId) {
    return {
      source,
      allows_local_zero_read: true,
      reason: 'prepared_route_snapshot_compared',
      verified_at: Number.isFinite(verifiedAt) ? new Date(verifiedAt).toISOString() : null,
      evidence_id: String(evidence.evidence_id),
      verified_route_keys: orderedSet(evidence.verified_route_keys || []),
      attempted_route_keys: orderedSet(evidence.attempted_route_keys || []),
    };
  }

  return {
    source,
    allows_local_zero_read: false,
    reason: 'authoritative_evidence_missing',
    verified_at: Number.isFinite(verifiedAt) ? new Date(verifiedAt).toISOString() : null,
    evidence_id: hasEvidenceId ? String(evidence.evidence_id) : null,
  };
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
  preparedAt = null,
  verificationEvidence = null,
  previousRevalidationRecord = null,
  previousConfirmedScope = null,
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
  const verificationContract = assessFinalVerificationEvidence({
    evidence: verificationEvidence,
    preparedAt,
    now,
  });
  const confirmedRouteKeys = orderedSet([
    ...confirmedActivities.map(routeKey),
    ...(confirmedScope?.seller_create_target_keys || []).map((value) => String(value || '').trim()),
  ].filter((key) => key && key.split('|').length === 3 && !key.split('|').some((part) => !part)));
  const verifiedRouteKeys = verificationContract.verified_route_keys || new Set();
  const attemptedRouteKeys = verificationContract.attempted_route_keys || new Set();
  const requiredLiveRoutes = verificationContract.allows_local_zero_read
    ? verificationContract.verified_route_keys
      ? orderedSet([...confirmedRouteKeys].filter((key) => !verifiedRouteKeys.has(key)))
      : new Set()
    : confirmedRouteKeys;
  const explicit = [...new Set((explicitTargetKeys || []).map(String).filter(Boolean))];
  const scopeSignature = {
    action: String(action || confirmedScope.action || '').toLowerCase(),
    ...normalizeScopeSignatures(
      confirmedActivities,
      confirmedScope?.seller_create_target_keys || [],
    ),
  };
  const previousScopeSignature = normalizeScopeSignatures(
    Array.isArray(previousConfirmedScope?.activities) ? previousConfirmedScope.activities : [],
    previousConfirmedScope?.seller_create_target_keys || [],
  );
  previousScopeSignature.action = String(previousConfirmedScope?.action || '').toLowerCase();

  const previousReasons = previousRevalidationRecord?.revalidation_reasons;
  const hasPreviousReasonFlags = previousReasons && Object.keys(previousReasons).length > 0;
  const shouldSkipByPreviousRevalidation = isSameFinalScope(scopeSignature, previousScopeSignature)
    && explicit.length === 0
    && !hasPreviousReasonFlags
    && Number(previousRevalidationRecord?.total_activity_count || 0) === scopeSignature.count
    && Number(previousRevalidationRecord?.item_read_activity_count || 0) === 0
    && Number(previousRevalidationRecord?.scope_review_activity_count || 0) === 0;

  if (shouldSkipByPreviousRevalidation) {
    return {
      total_activity_count: confirmedByIdentity.size,
      item_read_identity_keys: orderedSet([]),
      scope_review_identity_keys: orderedSet([]),
      blocked_identity_keys: orderedSet([]),
      removed_identity_keys: orderedSet([]),
      excluded_new_identity_keys: orderedSet([]),
      excluded_new_activity_count: 0,
      reasons_by_identity: {},
      platform_read_required: requiredLiveRoutes.size > 0,
      required_live_route_keys: requiredLiveRoutes,
      verification_contract: verificationContract,
    };
  }

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
        itemStatus: status,
        now,
      });
      if (decision.blocked && decision.reason === 'expired') markRemoved(key, `${status}:${decision.reason}`);
      else if (decision.blocked) markBlocked(key, `${status}:${decision.reason}`);
      else if (decision.refresh) markRead(key, `${status}:${decision.reason}`);
    }
  }

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
    platform_read_required: requiredLiveRoutes.size > 0,
    required_live_route_keys: requiredLiveRoutes,
    verification_contract: verificationContract,
  };
}
