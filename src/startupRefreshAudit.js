const READY_STAGE_STATUSES = new Set(['ok', 'reused']);

function safeText(value) {
  return String(value || '').trim().slice(0, 500);
}

function routeKey(route = {}) {
  return [route.account_id, route.child_user_id, route.site_id]
    .map((value) => String(value || '').trim())
    .join('|');
}

function normalizeRoute(route = {}) {
  return {
    account_id: String(route.account_id || '').trim(),
    child_user_id: String(route.child_user_id || '').trim(),
    site_id: String(route.site_id || '').trim().toUpperCase(),
  };
}

function shanghaiDate(value = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value instanceof Date ? value : new Date(value));
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${map.year}-${map.month}-${map.day}`;
}

export function classifyStartupPromotionWindow(promotion = {}, now = new Date()) {
  const rawFinish = String(promotion.finish_date || promotion.finishDate || '').trim();
  const exactFinish = /\d{2}:\d{2}/.test(rawFinish) ? Date.parse(rawFinish) : NaN;
  if (Number.isFinite(exactFinish)) {
    if (exactFinish <= (now instanceof Date ? now : new Date(now)).getTime()) {
      return { status: 'expired', reason: 'finish_time_elapsed', time_complete: true };
    }
    return { status: 'active', reason: 'finish_time_future', time_complete: true };
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(rawFinish)) {
    if (rawFinish < shanghaiDate(now)) return { status: 'expired', reason: 'finish_date_elapsed', time_complete: false };
    return { status: 'active', reason: 'finish_time_unknown', time_complete: false };
  }
  return { status: 'active', reason: 'finish_time_missing', time_complete: false };
}

export function partitionStartupPromotions(promotions = [], { now = new Date() } = {}) {
  const unique = new Map();
  for (const promotion of Array.isArray(promotions) ? promotions : []) {
    const route = normalizeRoute(promotion);
    const key = [
      route.account_id,
      route.child_user_id,
      route.site_id,
      String(promotion.promotion_id || promotion.id || '').trim(),
      String(promotion.promotion_type || promotion.type || '').trim().toUpperCase(),
    ].join('|');
    if (key !== '||||' && !unique.has(key)) unique.set(key, promotion);
  }
  const active = [];
  const expired = [];
  const uncertain = [];
  for (const promotion of unique.values()) {
    const window = classifyStartupPromotionWindow(promotion, now);
    if (window.status === 'expired') expired.push({ promotion, ...window });
    else {
      active.push(promotion);
      if (!window.time_complete) uncertain.push({ promotion, ...window });
    }
  }
  return { active, expired, uncertain };
}

export function createStartupAccountAudit({ accountId, storeName, routes = [], startedAt = new Date().toISOString() } = {}) {
  const normalizedRoutes = [...new Map((Array.isArray(routes) ? routes : [])
    .map(normalizeRoute)
    .filter((route) => routeKey(route))
    .map((route) => [routeKey(route), route]))
    .values()];
  return {
    account_id: String(accountId || '').trim(),
    store_name: safeText(storeName) || '当前店铺',
    status: 'running',
    started_at: String(startedAt || new Date().toISOString()),
    finished_at: null,
    stages: [],
    routes: normalizedRoutes.map((route) => ({ ...route, status: 'pending', stages: [] })),
  };
}

export function recordStartupAuditStage(audit, {
  stage,
  status = 'unknown',
  mode = 'executed',
  safe = false,
  routes = [],
  counts = null,
  sub_stages = null,
  expired_skipped = null,
  reason_cn = '',
  error_cn = '',
  started_at = new Date().toISOString(),
  finished_at = new Date().toISOString(),
} = {}) {
  const normalizedStatus = ['ok', 'reused', 'skipped', 'failed', 'unknown'].includes(String(status))
    ? String(status)
    : 'unknown';
  const normalizedMode = ['executed', 'reused', 'skipped'].includes(String(mode))
    ? String(mode)
    : 'executed';
  const stageReady = normalizedStatus === 'skipped' ? safe === true : READY_STAGE_STATUSES.has(normalizedStatus);
  const stageRecord = {
    stage: String(stage || '').trim(),
    status: normalizedStatus,
    mode: normalizedMode,
    ready: stageReady,
    safe: Boolean(safe),
    started_at: String(started_at || new Date().toISOString()),
    finished_at: String(finished_at || new Date().toISOString()),
    ...(counts && typeof counts === 'object' ? { counts: { ...counts } } : {}),
    ...(Array.isArray(sub_stages) ? { sub_stages: sub_stages.map((subStage) => ({ ...subStage })) } : {}),
    ...(Array.isArray(expired_skipped) ? { expired_skipped: expired_skipped.map((row) => ({ ...row })) } : {}),
    ...(safeText(reason_cn) ? { reason_cn: safeText(reason_cn) } : {}),
    ...(safeText(error_cn) ? { error_cn: safeText(error_cn) } : {}),
    routes: (Array.isArray(routes) ? routes : []).map((route) => ({
      ...normalizeRoute(route),
      status: String(route.status || (stageReady ? normalizedStatus : 'unknown')),
      mode: String(route.mode || normalizedMode),
      ready: route.ready === undefined ? stageReady : Boolean(route.ready),
      ...(safeText(route.error_cn) ? { error_cn: safeText(route.error_cn) } : {}),
    })),
  };
  const existingRoutes = new Map((audit?.routes || []).map((route) => [routeKey(route), route]));
  for (const route of stageRecord.routes) {
    const existing = existingRoutes.get(routeKey(route)) || { ...normalizeRoute(route), status: 'pending', stages: [] };
    existingRoutes.set(routeKey(route), {
      ...existing,
      status: route.ready ? route.status : 'failed',
      stages: [...(existing.stages || []), {
        stage: stageRecord.stage,
        status: route.status,
        mode: route.mode,
        ready: route.ready,
        ...(route.error_cn ? { error_cn: route.error_cn } : {}),
      }],
    });
  }
  return {
    ...(audit || {}),
    stages: [...(audit?.stages || []), stageRecord],
    routes: [...existingRoutes.values()],
  };
}

export function finishStartupAccountAudit(audit, {
  status = null,
  error_cn = '',
  finishedAt = new Date().toISOString(),
} = {}) {
  const stages = Array.isArray(audit?.stages) ? audit.stages : [];
  const inferredReady = stages.length > 0 && stages.every((stage) => stage.ready === true);
  const normalizedStatus = status || (inferredReady ? 'ok' : 'failed');
  return {
    ...(audit || {}),
    status: ['ok', 'failed', 'unknown'].includes(normalizedStatus) ? normalizedStatus : 'unknown',
    finished_at: String(finishedAt || new Date().toISOString()),
    ...(safeText(error_cn) ? { error_cn: safeText(error_cn) } : {}),
  };
}

function replayNumber(replay, key) {
  const direct = replay?.[key];
  if (direct !== null && direct !== undefined && Number.isFinite(Number(direct))) return Number(direct);
  const nested = replay?.remaining?.[key === 'remaining_events' ? 'event_count' : 'item_count'];
  return nested !== null && nested !== undefined && Number.isFinite(Number(nested)) ? Number(nested) : null;
}

export function assessStartupReadiness({ accountAudits = [], requiredAccounts = [], cbtReplay = null, historicalOutsideRecent = null, scopeAccountIds = null } = {}) {
  const audits = Array.isArray(accountAudits) ? accountAudits : [];
  const required = [...new Map((Array.isArray(requiredAccounts) ? requiredAccounts : audits)
    .map((row) => [String(row.account_id || row.accountId || '').trim(), row])
    .filter(([accountId]) => accountId))
    .values()];
  const scopeSet = Array.isArray(scopeAccountIds)
    ? new Set(scopeAccountIds.map((value) => String(value || '').trim()).filter(Boolean))
    : null;
  const scopedRequired = scopeSet ? required.filter((row) => scopeSet.has(String(row.account_id || row.accountId || '').trim())) : required;
  const byAccount = new Map(audits.map((audit) => [String(audit.account_id || '').trim(), audit]));
  const reasons = [];
  const readyAccounts = [];
  const failedAccounts = [];
  const unknownAccounts = [];
  for (const requiredRow of scopedRequired) {
    const accountId = String(requiredRow.account_id || requiredRow.accountId || '').trim();
    const audit = byAccount.get(accountId);
    if (!audit) {
      unknownAccounts.push(accountId);
      reasons.push({ code: 'startup_account_audit_missing', account_id: accountId, reason_cn: '店铺启动审计缺失，无法确认缓存状态。' });
      continue;
    }
    const badStage = (audit.stages || []).find((stage) => stage.ready !== true);
    if (audit.status === 'ok' && !badStage) {
      readyAccounts.push(accountId);
      continue;
    }
    const target = audit.status === 'unknown' || !badStage ? unknownAccounts : failedAccounts;
    target.push(accountId);
    reasons.push({
      code: badStage?.status === 'unknown' || audit.status === 'unknown' ? 'startup_stage_unknown' : 'startup_stage_failed',
      account_id: accountId,
      store_name: audit.store_name || '',
      stage: badStage?.stage || null,
      reason_cn: badStage?.error_cn || badStage?.reason_cn || audit.error_cn || '店铺启动缓存未完成可信校准，执行已阻断。',
    });
  }
  if (!scopedRequired.length) reasons.push({ code: 'startup_accounts_missing', reason_cn: '没有可确认的经营店铺，无法放行执行。' });

  const replayRemainingEvents = replayNumber(cbtReplay, 'remaining_events');
  const replayRemainingItems = replayNumber(cbtReplay, 'remaining_unique_resources');
  const replayRemainingEligibleEvents = replayNumber(cbtReplay, 'remaining_eligible_events');
  const replayRemainingEligibleItems = replayNumber(cbtReplay, 'remaining_eligible_resources');
  const classificationCounts = cbtReplay?.classification_counts && typeof cbtReplay.classification_counts === 'object'
    ? cbtReplay.classification_counts
    : {};
  const classificationItemCounts = cbtReplay?.classification_item_counts && typeof cbtReplay.classification_item_counts === 'object'
    ? cbtReplay.classification_item_counts
    : {};
  const classificationAccountCounts = cbtReplay?.classification_account_counts && typeof cbtReplay.classification_account_counts === 'object'
    ? cbtReplay.classification_account_counts
    : {};
  const classificationItemAccountCounts = cbtReplay?.classification_item_account_counts && typeof cbtReplay.classification_item_account_counts === 'object'
    ? cbtReplay.classification_item_account_counts
    : {};
  const nestedCount = (map, category) => {
    const row = map?.[category];
    if (!row || typeof row !== 'object') return null;
    if (!scopeSet) return Object.values(row).reduce((sum, value) => sum + Number(value || 0), 0);
    return [...scopeSet].reduce((sum, accountId) => sum + Number(row[accountId] || 0), 0);
  };
  const accountCategoryCount = (map, category, accountId) => Number(map?.[category]?.[accountId] || 0);
  const hasClassificationSnapshot = Boolean(
    cbtReplay?.classification_snapshot === true
      || Object.keys(classificationCounts).length
      || Object.keys(classificationItemCounts).length,
  );
  const categoryCount = (category, unique = true) => Number(
    (scopeSet && (unique ? classificationItemAccountCounts : classificationAccountCounts)[category] !== undefined
      ? nestedCount(unique ? classificationItemAccountCounts : classificationAccountCounts, category)
      : (unique ? classificationItemCounts : classificationCounts)[category])
      ?? cbtReplay?.[`${category}_count`]
      ?? 0,
  );
  const routeCatalogGap = categoryCount('route_catalog_gap');
  const quarantinedUnknown = categoryCount('quarantined_unknown');
  const terminalForeign = categoryCount('terminal_foreign');
  const terminalIrrelevant = categoryCount('terminal_irrelevant');
  const terminalNoActionableGlobalParent = categoryCount('terminal_no_actionable_global_parent');
  const eligiblePartial = categoryCount('eligible_partial');
  const eligibleBudgetRemaining = categoryCount('eligible_budget_remaining');
  const eligibleRetryable = categoryCount('eligible_retryable') + categoryCount('eligible_route_unresolved');
  const scopedEligibleEventCount = scopeSet ? (
    ['eligible_partial', 'eligible_budget_remaining', 'eligible_retryable', 'eligible_route_unresolved']
      .reduce((sum, category) => sum + Number(nestedCount(classificationAccountCounts, category) || 0), 0)
  ) : null;
  const scopedEligibleItemCount = scopeSet ? (
    ['eligible_partial', 'eligible_budget_remaining', 'eligible_retryable', 'eligible_route_unresolved']
      .reduce((sum, category) => sum + Number(nestedCount(classificationItemAccountCounts, category) || 0), 0)
  ) : null;
  const eligibleEventCount = Number(
    scopedEligibleEventCount !== null && Object.keys(classificationAccountCounts).length
      ? scopedEligibleEventCount
      : replayRemainingEligibleEvents
      ?? cbtReplay?.eligible_event_count
      ?? (hasClassificationSnapshot
        ? Number(classificationCounts.eligible_partial || 0)
          + Number(classificationCounts.eligible_budget_remaining || 0)
          + Number(classificationCounts.eligible_retryable || 0)
          + Number(classificationCounts.eligible_route_unresolved || 0)
        : replayRemainingEvents ?? 0),
  );
  const eligibleItemCount = Number(
    scopedEligibleItemCount !== null && Object.keys(classificationItemAccountCounts).length
      ? scopedEligibleItemCount
      : replayRemainingEligibleItems
      ?? cbtReplay?.eligible_item_count
      ?? (hasClassificationSnapshot
        ? eligiblePartial + eligibleBudgetRemaining + eligibleRetryable
        : replayRemainingItems ?? 0),
  );
  if (!cbtReplay) {
    reasons.push({ code: 'cbt_replay_unknown', reason_cn: '最近48小时商品通知补偿尚未完成，无法确认路由队列。' });
  } else if (cbtReplay.error_cn) {
    reasons.push({ code: 'cbt_replay_failed', reason_cn: safeText(cbtReplay.error_cn) || '最近48小时商品通知补偿未完成，执行已阻断。' });
  } else if (replayRemainingEvents === null) {
    reasons.push({ code: 'cbt_replay_remaining_unknown', reason_cn: '最近48小时商品通知剩余量未知，执行已阻断。' });
  } else if (eligibleEventCount > 0 || eligibleItemCount > 0) {
    reasons.push({
      code: 'cbt_replay_remaining',
      reason_cn: `最近48小时仍有 ${eligibleEventCount} 条可继续处理的商品通知待确认。`,
      remaining_events: replayRemainingEvents,
      remaining_unique_resources: replayRemainingItems,
      remaining_eligible_events: eligibleEventCount,
      remaining_eligible_resources: eligibleItemCount,
    });
  }
  if (routeCatalogGap > 0) reasons.push({
    code: 'cbt_route_catalog_gap',
    reason_cn: `有 ${routeCatalogGap} 个商品已确认账号身份但缺少经营路线，需先完成路线只读校准。`,
    count: routeCatalogGap,
  });
  if (quarantinedUnknown > 0) reasons.push({
    code: 'cbt_quarantined_unknown',
    reason_cn: `有 ${quarantinedUnknown} 个商品字段不足或多候选，已隔离等待人工核对。`,
    count: quarantinedUnknown,
  });
  if (terminalNoActionableGlobalParent > 0) reasons.push({
    code: 'cbt_terminal_no_actionable_global_parent',
    blocking: false,
    reason_cn: `全球父商品${terminalNoActionableGlobalParent}个暂无站点子商品，已隔离，不影响当前活动。`,
    count: terminalNoActionableGlobalParent,
  });
  const failed = Number(cbtReplay?.failed || 0) + Number(cbtReplay?.unresolved || 0);
  const uniqueUnresolved = Number(cbtReplay?.unique_resource_unresolved ?? cbtReplay?.unresolved ?? 0);
  const uniqueRetryableFailed = Number(cbtReplay?.unique_resource_retryable_failed ?? cbtReplay?.failed ?? 0);
  const uniqueRetainedFailure = hasClassificationSnapshot
    ? routeCatalogGap + quarantinedUnknown
    : Number(cbtReplay?.unique_resource_retained_failure ?? (uniqueUnresolved + uniqueRetryableFailed));
  if (uniqueRetainedFailure > 0) reasons.push({
    code: 'cbt_replay_retained_failure',
    reason_cn: hasClassificationSnapshot
      ? `最近48小时有 ${uniqueRetainedFailure} 个商品资源因字段不足或路线缺失被保留（事件 ${failed} 条）。`
      : `最近48小时有 ${uniqueRetainedFailure} 个商品资源未确认（事件 ${failed} 条）。`,
    failed,
    unique_retained_failure: uniqueRetainedFailure,
  });
  if (cbtReplay?.budget_exhausted === true && (!hasClassificationSnapshot || eligibleEventCount > 0 || eligibleItemCount > 0)) reasons.push({ code: 'cbt_replay_budget_exhausted', reason_cn: '最近48小时商品通知补偿已达到本次预算，仍有可继续处理项，执行已阻断。' });
  if (cbtReplay?.continuation_status === 'no_progress_circuit_open') reasons.push({
    code: 'cbt_replay_no_progress_circuit_open',
    reason_cn: '连续多波补偿没有新的商品或缓存进展，已触发安全熔断，执行已阻断。',
  });
  if (cbtReplay?.continuation_status === 'max_waves_reached') reasons.push({
    code: 'cbt_replay_max_waves_reached',
    reason_cn: '补偿已达到本次最大波次数，仍有 eligible 商品待处理，执行已阻断。',
  });

  const blockedAccountIds = scopeSet
    ? [...scopeSet].filter((accountId) => (
      accountCategoryCount(classificationItemAccountCounts, 'quarantined_unknown', accountId)
      + accountCategoryCount(classificationItemAccountCounts, 'route_catalog_gap', accountId) > 0
    ))
    : [...new Set([
      ...Object.keys(classificationItemAccountCounts.quarantined_unknown || {}),
      ...Object.keys(classificationItemAccountCounts.route_catalog_gap || {}),
    ])];
  const blockingReasons = reasons.filter((reason) => reason?.blocking !== false);
  return {
    ready: blockingReasons.length === 0,
    reasons,
    required_accounts: scopedRequired.map((row) => String(row.account_id || row.accountId || '').trim()),
    ready_accounts: readyAccounts,
    failed_accounts: failedAccounts,
    unknown_accounts: unknownAccounts,
    replay_remaining_events: replayRemainingEvents,
    replay_remaining_unique_resources: replayRemainingItems,
    replay_remaining_eligible_events: eligibleEventCount,
    replay_remaining_eligible_resources: eligibleItemCount,
    replay_unique_retained_failure: uniqueRetainedFailure,
    replay_route_catalog_gap: routeCatalogGap,
    replay_quarantined_unknown: quarantinedUnknown,
    replay_terminal_foreign: terminalForeign,
    replay_terminal_irrelevant: terminalIrrelevant,
    replay_terminal_no_actionable_global_parent: terminalNoActionableGlobalParent,
    replay_eligible_partial: eligiblePartial,
    replay_eligible_budget_remaining: eligibleBudgetRemaining,
    replay_eligible_retryable: eligibleRetryable,
    replay_classification_counts: classificationCounts,
    replay_classification_item_counts: classificationItemCounts,
    scope_account_ids: scopeSet ? [...scopeSet] : null,
    blocked_account_ids: blockedAccountIds,
    history_outside_recent: historicalOutsideRecent || null,
  };
}
