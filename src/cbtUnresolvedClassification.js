const QUARANTINE_CODES = new Set([
  'OFFICIAL_SELLER_MISSING',
  'OFFICIAL_SELLER_AMBIGUOUS',
  'OFFICIAL_SITE_MISSING',
  'OFFICIAL_SITE_AMBIGUOUS',
  'MARKETPLACE_ITEM_MISSING',
  'ROUTE_AMBIGUOUS',
]);

const KNOWN_CATEGORIES = new Set([
  'terminal_irrelevant',
  'terminal_foreign',
  'terminal_no_actionable_global_parent',
  'route_catalog_gap',
  'quarantined_unknown',
  'eligible_route_unresolved',
  'eligible_retryable',
  'eligible_partial',
  'eligible_budget_remaining',
]);

function safeCode(value) {
  return String(value || '').trim().toUpperCase();
}

function diagnosticFor(input = {}) {
  const direct = input?.diagnostic && typeof input.diagnostic === 'object' ? input.diagnostic : input;
  let merged = direct || {};
  if (direct?.last_error && typeof direct.last_error === 'object' && !Array.isArray(direct.last_error)) {
    merged = { ...merged, ...direct.last_error };
  }
  if (typeof direct?.last_error === 'string' && direct.last_error.trim()) {
    try { merged = { ...merged, ...JSON.parse(direct.last_error) }; } catch {
      // Keep the stable code fallback below without exposing or interpreting raw text.
    }
  }
  return merged?.resolver_diagnostic && typeof merged.resolver_diagnostic === 'object'
    ? { ...merged, ...merged.resolver_diagnostic }
    : merged;
}

export function isNonActionableGlobalParent(input = {}) {
  const diagnostic = diagnosticFor(input);
  const resource = String(input.resource || diagnostic.resource || '').trim();
  const code = safeCode(diagnostic.classification_code || diagnostic.diagnostic_code || diagnostic.code || diagnostic.cause_code);
  const endpointFamily = String(diagnostic.endpoint_family || '').trim().toLowerCase();
  const signal = diagnostic.signal_presence && typeof diagnostic.signal_presence === 'object'
    ? diagnostic.signal_presence
    : {};
  const rootFieldPresent = Object.hasOwn(diagnostic, 'root_site_id') || Object.hasOwn(diagnostic, 'parent_site_id');
  const rootSite = String(diagnostic.root_site_id || diagnostic.parent_site_id || '').trim().toUpperCase();
  const parentEvidence = rootSite === 'CBT'
    || diagnostic.global_parent_proven === true
    || (!rootFieldPresent && endpointFamily === 'items_cbt' && code === 'OFFICIAL_SITE_MISSING'
      && /^\/items\/CBT[A-Za-z0-9._:-]+(?:[?#].*)?$/.test(resource));
  const sellerSignals = Number(signal.seller_signal_count ?? diagnostic.seller_signal_count ?? 0);
  const siteSignals = Number(signal.site_signal_count ?? diagnostic.site_signal_count ?? 0);
  const routeCandidates = Number(signal.route_candidate_count ?? diagnostic.route_candidate_count ?? 0);
  const childCount = Number(diagnostic.child_item_count ?? diagnostic.local_child_id_count ?? 0);
  const eventGapKnown = input.event_gap !== undefined && input.event_gap !== null;
  const responseStatus = input.response_http_status ?? diagnostic.response_http_status;
  const responseComplete = responseStatus === undefined || responseStatus === null || Number(responseStatus) === 200;
  return parentEvidence
    && responseComplete
    && sellerSignals === 0
    && siteSignals === 0
    && routeCandidates === 0
    && childCount === 0
    && !String(input.child_user_id || diagnostic.child_user_id || '').trim()
    && !String(input.site_id || diagnostic.site_id || '').trim()
    && Number(input.local_relation_count || 0) === 0
    && input.route_catalog_ready === true
    && eventGapKnown
    && Number(input.event_gap) === 0;
}

function categoryResult(category) {
  switch (category) {
    case 'terminal_irrelevant':
      return { category, retryable: false, terminal: true, reason_cn: '子商品已删除或不可用，可排除本地缓存，不再自动读取。' };
    case 'terminal_foreign':
      return { category, retryable: false, terminal: true, reason_cn: '官方资源属于其它账号或经营路由，不写入当前账号。' };
    case 'terminal_no_actionable_global_parent':
      return { category, retryable: false, terminal: true, reason_cn: '全球父商品暂无可操作的站点子商品，已隔离，不影响当前活动。' };
    case 'route_catalog_gap':
      return { category, retryable: false, terminal: false, reason_cn: '已确认账号身份，但本地经营路线缺失，需先做路线只读校准。' };
    case 'quarantined_unknown':
      return { category, retryable: false, terminal: false, reason_cn: '官方卖家/站点字段不足或存在多候选，已隔离等待人工或路线核对。' };
    case 'eligible_partial':
      return { category, retryable: true, terminal: false, reason_cn: '部分子商品已处理，失败目标保留到下一波重试。' };
    case 'eligible_budget_remaining':
      return { category, retryable: true, terminal: false, reason_cn: '本波物理读取预算已用尽，保留到下一波继续处理。' };
    case 'eligible_route_unresolved':
    case 'eligible_retryable':
      return { category, retryable: true, terminal: false, reason_cn: '读取暂未完成，保留到下一波有限重试。' };
    default:
      return null;
  }
}

export function classifyCbtUnresolved(input = {}) {
  const diagnostic = diagnosticFor(input);
  if (diagnostic.reopen_on_child_mapping === true
    || Number(diagnostic.child_item_count || 0) > 0
    || Number(diagnostic.route_target_count || diagnostic.target_count || 0) > 0) {
    return categoryResult('eligible_route_unresolved');
  }
  const storedCategory = safeCode(
    diagnostic.classification
      || diagnostic.terminal_category
      || diagnostic.category,
  ).toLowerCase();
  const hasGlobalParentContext = Object.hasOwn(input, 'local_relation_count')
    || Object.hasOwn(input, 'route_catalog_ready')
    || Object.hasOwn(input, 'event_gap');
  const globalParentQualifies = hasGlobalParentContext && isNonActionableGlobalParent(input);
  if (globalParentQualifies) return categoryResult('terminal_no_actionable_global_parent');
  if (storedCategory === 'terminal_no_actionable_global_parent') {
    return categoryResult('quarantined_unknown');
  }
  if (KNOWN_CATEGORIES.has(storedCategory)) return categoryResult(storedCategory);
  if ((diagnostic.non_actionable_global_parent === true
    || diagnostic.global_parent_no_actionable_local_child === true)
    && (!hasGlobalParentContext || globalParentQualifies)
    || (!hasGlobalParentContext && isNonActionableGlobalParent(input))) {
    return categoryResult('terminal_no_actionable_global_parent');
  }
  const code = safeCode(diagnostic.classification_code || diagnostic.diagnostic_code || diagnostic.code || diagnostic.cause_code);
  const signal = diagnostic.signal_presence && typeof diagnostic.signal_presence === 'object'
    ? diagnostic.signal_presence
    : {};
  const foreign = Number(diagnostic.foreign_skipped || signal.foreign_route_candidate_count || 0) > 0
    || (code === 'ROUTE_NOT_OWNED' && diagnostic.foreign_identity_proven === true);
  const ownedIdentityProven = diagnostic.owned_identity_proven === true || signal.owned_identity_proven === true;

  if (!code) {
    return { category: 'quarantined_unknown', retryable: false, terminal: false, reason_cn: '缺少稳定未匹配诊断字段，已隔离等待人工核对。' };
  }

  if (code === 'RESOURCE_STATUS_UNUSABLE' || diagnostic.terminal_status === true) {
    return categoryResult('terminal_irrelevant');
  }
  if (foreign) {
    return categoryResult('terminal_foreign');
  }
  if (code === 'ROUTE_CATALOG_GAP' || (code === 'ROUTE_NOT_OWNED' && ownedIdentityProven)) {
    return categoryResult('route_catalog_gap');
  }
  if (QUARANTINE_CODES.has(code) || code === 'ROUTE_NOT_OWNED' || code === 'CBT_ROUTE_UNRESOLVED') {
    return categoryResult('quarantined_unknown');
  }
  if (code === 'PHYSICAL_GET_BUDGET_EXHAUSTED' || diagnostic.budget_remaining === true) {
    return categoryResult('eligible_budget_remaining');
  }
  if (code === 'CBT_FANOUT_PARTIAL' || diagnostic.partial === true) {
    return categoryResult('eligible_partial');
  }
  return categoryResult('eligible_retryable');
}

export function isEligibleCbtContinuation(input = {}) {
  return classifyCbtUnresolved(input).retryable === true;
}
