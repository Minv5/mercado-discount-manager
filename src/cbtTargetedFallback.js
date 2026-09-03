const TARGETED_FALLBACK_CATEGORIES = new Set([
  'quarantined_unknown',
  'route_catalog_gap',
]);

function safeText(value, limit = 180) {
  return String(value || '').replace(/[\r\n]+/g, ' ').trim().slice(0, limit);
}

function safeRoute(route = {}) {
  return {
    account_id: safeText(route.account_id, 80),
    child_user_id: safeText(route.child_user_id, 80),
    site_id: safeText(route.site_id, 20).toUpperCase(),
  };
}

function isCbtResource(value) {
  return /^\/items\/CBT[A-Za-z0-9._:-]+(?:[?#].*)?$/.test(String(value || ''));
}

/**
 * Build a bounded, parent-account-scoped fallback plan.  This is deliberately
 * a plan-only boundary: it reuses the already verified items_cbt GET family,
 * never invents a second endpoint, and never expands to a full catalog scan.
 */
export function buildCbtTargetedFallbackPlan({
  event = {},
  classification = '',
  marketplaceSites = [],
} = {}) {
  const category = safeText(classification, 60).toLowerCase();
  const resource = safeText(event.resource, 180);
  const parentAccountId = safeText(event.remote_user_id, 80);
  if (!TARGETED_FALLBACK_CATEGORIES.has(category)) {
    return { status: 'not_needed', category, resource, parent_account_id: parentAccountId };
  }
  if (!isCbtResource(resource) || !parentAccountId) {
    return {
      status: 'blocked',
      category,
      resource,
      parent_account_id: parentAccountId,
      code: 'CBT_TARGETED_FALLBACK_INPUT_INVALID',
      reason_cn: 'CBT 商品通知缺少可验证的父账号或资源标识，未启动补查。',
    };
  }
  const routeCount = (Array.isArray(marketplaceSites) ? marketplaceSites : [])
    .map(safeRoute)
    .filter((route) => route.account_id === parentAccountId && route.child_user_id && route.site_id).length;
  return {
    status: 'planned',
    category,
    operation: 'cbt_targeted_fallback',
    endpoint_family: 'items_cbt',
    method: 'GET',
    resource,
    parent_account_id: parentAccountId,
    verified_route_count: routeCount,
    max_parent_gets: 1,
    full_catalog_refresh: false,
    stop_after_unknown: true,
    reason_cn: category === 'route_catalog_gap'
      ? '仅针对当前 CBT 与父账号经营路线做路线补查。'
      : '仅针对当前 CBT 与父账号经营路线做字段补查。',
  };
}

export function summarizeCbtTargetedFallbackOutcome({
  targets = [],
  diagnostics = {},
  resourceStatus = '',
  foreignSkipped = 0,
} = {}) {
  if (Array.isArray(targets) && targets.length) {
    return { category: 'exact_owned', retryable: false, write_allowed: true, target_count: targets.length };
  }
  const status = safeText(resourceStatus, 40).toLowerCase();
  if (['deleted', 'closed', 'inactive', 'unavailable'].includes(status)
    || Number(diagnostics.unusable_skipped || 0) > 0) {
    return { category: 'terminal_irrelevant', retryable: false, write_allowed: false, target_count: 0 };
  }
  if (Number(foreignSkipped || diagnostics.foreign_skipped || 0) > 0
    && Number(diagnostics.unmatched_skipped || 0) === 0
    && Number(diagnostics.ambiguous_skipped || 0) === 0) {
    return { category: 'terminal_foreign', retryable: false, write_allowed: false, target_count: 0 };
  }
  const codes = new Set((Array.isArray(diagnostics.codes) ? diagnostics.codes : []).map((row) => String(row?.code || '').toUpperCase()));
  if (codes.has('ROUTE_CATALOG_GAP')) {
    return { category: 'route_catalog_gap', retryable: false, write_allowed: false, target_count: 0 };
  }
  return { category: 'quarantined_unknown', retryable: false, write_allowed: false, target_count: 0 };
}

export function isCbtTargetedFallbackCategory(value) {
  return TARGETED_FALLBACK_CATEGORIES.has(String(value || '').trim().toLowerCase());
}
