export const RESULT_CONTRACT_VERSION = 2;

export const CANCEL_RESULT_STATUS = Object.freeze({
  requestSuccess: 'request_success',
  liveVerifiedRemoved: 'live_verified_removed',
  liveStillStarted: 'live_still_started',
  confirmedRemoved: 'live_verified_removed',
  stillStarted: 'live_still_started',
  unverifiable: 'live_unverifiable',
  pendingVerification: 'pending_verification',
});

export const RELATION_TERMINAL_STATUS = Object.freeze({
  success: 'success',
  failed: 'failed',
  skipped: 'skipped',
  platformPending: 'platform_pending',
  unresolved: 'unresolved',
});

export const CANCEL_LIVE_READ_CLASSIFICATION = Object.freeze({
  confirmedRemoved: 'confirmed_removed',
  stillStarted: 'still_started',
  unverifiable: 'unverifiable',
});

const CONTRACT_FINAL_STATUSES = new Set([
  'success',
  'failed',
  'skipped',
  CANCEL_RESULT_STATUS.liveVerifiedRemoved,
  CANCEL_RESULT_STATUS.liveStillStarted,
  CANCEL_RESULT_STATUS.unverifiable,
  CANCEL_RESULT_STATUS.pendingVerification,
  CANCEL_RESULT_STATUS.requestSuccess,
]);

export function buildCancelResultContract({ plannedItemIds = [], outcomes = [], recheck = {} } = {}) {
  const planned = [...new Set(plannedItemIds.map(String).filter(Boolean))];
  const stateByItem = new Map(planned.map((itemId) => [itemId, { latest: 'skipped', requestSuccess: false }]));
  for (const outcome of outcomes || []) {
    const itemId = String(outcome?.item_id || outcome?.itemId || '');
    if (!itemId) continue;
    const state = stateByItem.get(itemId) || { latest: 'skipped', requestSuccess: false };
    const status = String(outcome?.status || '').toLowerCase();
    if (status === 'success' || status === CANCEL_RESULT_STATUS.requestSuccess) state.requestSuccess = true;
    if (status) state.latest = status;
    stateByItem.set(itemId, state);
  }

  const hasFinalLiveRead = recheck?.completed === true || Array.isArray(recheck?.remainingItemIds);
  const finalLiveReadIsComplete = hasFinalLiveRead
    && recheck?.truncated !== true
    && recheck?.is_full_fetch !== false
    && recheck?.isFullFetch !== false
    && recheck?.unverifiable !== true;
  const remaining = new Set((recheck?.remainingItemIds || []).map(String));
  const explicitlyUnverifiable = new Set((recheck?.unverifiableItemIds || []).map(String));
  const finalStatusByItem = {};
  const liveReadClassificationByItem = {};
  const counts = stableContractCounts({ relationCount: stateByItem.size, uniqueItemCount: stateByItem.size });
  for (const [itemId, state] of stateByItem) {
    if (state.requestSuccess) {
      counts.request_success_count += 1;
      if (!hasFinalLiveRead) {
        finalStatusByItem[itemId] = CANCEL_RESULT_STATUS.pendingVerification;
        counts.pending_verification_count += 1;
        counts.skipped += 1;
      } else if (remaining.has(itemId)) {
        finalStatusByItem[itemId] = CANCEL_RESULT_STATUS.liveStillStarted;
        liveReadClassificationByItem[itemId] = CANCEL_LIVE_READ_CLASSIFICATION.stillStarted;
        counts.failed += 1;
      } else if (!finalLiveReadIsComplete || explicitlyUnverifiable.has(itemId)) {
        finalStatusByItem[itemId] = CANCEL_RESULT_STATUS.unverifiable;
        liveReadClassificationByItem[itemId] = CANCEL_LIVE_READ_CLASSIFICATION.unverifiable;
        counts.pending_verification_count += 1;
        counts.skipped += 1;
      } else {
        finalStatusByItem[itemId] = CANCEL_RESULT_STATUS.liveVerifiedRemoved;
        liveReadClassificationByItem[itemId] = CANCEL_LIVE_READ_CLASSIFICATION.confirmedRemoved;
        counts.live_verified_removed_count += 1;
        counts.success += 1;
      }
      continue;
    }
    if (state.latest === 'failed' || state.latest === CANCEL_RESULT_STATUS.liveStillStarted) {
      finalStatusByItem[itemId] = 'failed';
      counts.failed += 1;
    } else {
      finalStatusByItem[itemId] = 'skipped';
      counts.skipped += 1;
    }
  }
  const terminalCounts = countMutuallyExclusiveRelationResults(
    Object.entries(finalStatusByItem).map(([itemId, status]) => ({ item_id: itemId, status })),
  );
  return {
    counts,
    terminal_counts: terminalCounts,
    final_status_by_item: finalStatusByItem,
    live_read_classification_by_item: liveReadClassificationByItem,
    live_read_complete: finalLiveReadIsComplete,
  };
}

export function classifyRelationTerminalStatus(row = {}) {
  const status = String(row?.status || '').toLowerCase();
  if (status === 'success' || status === CANCEL_RESULT_STATUS.liveVerifiedRemoved) {
    return RELATION_TERMINAL_STATUS.success;
  }
  if (status === 'failed' || status === CANCEL_RESULT_STATUS.liveStillStarted) {
    return RELATION_TERMINAL_STATUS.failed;
  }
  if (status === 'skipped') {
    return RELATION_TERMINAL_STATUS.skipped;
  }
  if (status === CANCEL_RESULT_STATUS.pendingVerification
    && (row?.platform_pending === true
      || String(row?.error_cn || '').startsWith('平台已明确返回 pending（待生效）'))) {
    return RELATION_TERMINAL_STATUS.platformPending;
  }
  return RELATION_TERMINAL_STATUS.unresolved;
}

export function countMutuallyExclusiveRelationResults(rows = []) {
  const finalByRelation = new Map();
  for (const row of rows || []) {
    const itemId = String(row?.item_id || row?.itemId || '').trim();
    if (!itemId) continue;
    finalByRelation.set(relationKey({ ...row, item_id: itemId }), row);
  }

  const counts = {
    relation_count: finalByRelation.size,
    success: 0,
    failed: 0,
    skipped: 0,
    platform_pending: 0,
    unresolved: 0,
    classified_count: 0,
    is_closed: false,
    is_resolved: false,
  };
  for (const row of finalByRelation.values()) {
    const terminalStatus = classifyRelationTerminalStatus(row);
    counts[terminalStatus] += 1;
  }
  counts.classified_count = counts.success
    + counts.failed
    + counts.skipped
    + counts.platform_pending
    + counts.unresolved;
  counts.is_closed = counts.classified_count === counts.relation_count;
  counts.is_resolved = counts.is_closed && counts.unresolved === 0;
  return counts;
}

export function summarizeResultContractRows(rows = []) {
  const finalByRelation = new Map();
  const requestSuccess = new Set();
  const activityFailures = [];
  for (const row of rows || []) {
    const itemId = String(row?.item_id || '').trim();
    if (!itemId) {
      if (['activity_failed', 'failed', 'skipped'].includes(String(row?.status || '').toLowerCase())) activityFailures.push(row);
      continue;
    }
    const key = relationKey(row);
    const status = String(row?.status || '').toLowerCase();
    if (status === CANCEL_RESULT_STATUS.requestSuccess) requestSuccess.add(key);
    if (CONTRACT_FINAL_STATUSES.has(status)) finalByRelation.set(key, row);
  }
  const uniqueItems = new Set();
  const counts = stableContractCounts({ relationCount: finalByRelation.size, uniqueItemCount: 0 });
  counts.activity_failure_count = activityFailures.length;
  counts.request_success_count = requestSuccess.size;
  for (const [key, row] of finalByRelation) {
    uniqueItems.add(`${row.account_id || ''}|${row.item_id || ''}`);
    const status = String(row.status || '').toLowerCase();
    if (status === 'success' || status === CANCEL_RESULT_STATUS.liveVerifiedRemoved) {
      counts.success += 1;
      if (status === CANCEL_RESULT_STATUS.liveVerifiedRemoved) counts.live_verified_removed_count += 1;
    } else if (status === CANCEL_RESULT_STATUS.pendingVerification
      && String(row.error_cn || '').startsWith('平台已明确返回 pending（待生效）')) {
      counts.platform_pending_count += 1;
    } else if (status === CANCEL_RESULT_STATUS.requestSuccess || status === CANCEL_RESULT_STATUS.pendingVerification) {
      counts.skipped += 1;
      counts.pending_verification_count += 1;
      counts.retryable_pending_count += 1;
    } else if (status === 'skipped') {
      counts.skipped += 1;
    } else {
      counts.failed += 1;
    }
  }
  counts.unique_item_count = uniqueItems.size;
  return counts;
}

export function stableContractCounts({ relationCount = 0, uniqueItemCount = 0, activityFailureCount = 0 } = {}) {
  return {
    relation_count: Number(relationCount || 0),
    unique_item_count: Number(uniqueItemCount || 0),
    activity_failure_count: Number(activityFailureCount || 0),
    request_success_count: 0,
    live_verified_removed_count: 0,
    pending_verification_count: 0,
    platform_pending_count: 0,
    retryable_pending_count: 0,
    success: 0,
    failed: 0,
    skipped: 0,
  };
}

export function summarizeLiveReadRows(rows = [], selectedCount = rows.length) {
  const normalized = (rows || []).map((row) => {
    const detailStatus = String(row?.detail_status || '').toLowerCase();
    const blocked = detailStatus === 'error' || detailStatus === 'unreadable' || row?.blocked === true || Boolean(row?.error);
    return {
      ...row,
      status: blocked ? 'blocked' : 'readable',
      reason: blocked ? '活动商品实时读取失败，本次禁止使用旧缓存执行' : null,
    };
  });
  const readableCount = normalized.filter((row) => row.status === 'readable').length;
  return {
    rows: normalized,
    readable_count: readableCount,
    blocked_count: normalized.length - readableCount,
    all_blocked: Number(selectedCount || 0) > 0 && readableCount === 0,
  };
}

export function relationKey(row = {}) {
  return [row.account_id || '', row.site_id || '', row.promotion_id || '', row.promotion_type || '', row.item_id || ''].join('|');
}
