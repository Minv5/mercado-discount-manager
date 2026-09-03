function count(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0, Math.floor(numeric)) : 0;
}

export function isExplicitCancelContinuationRequest(request = {}) {
  return request.resumePendingOnly === true
    || request.resume_pending_only === true
    || request.cancelContinuation === true
    || request.cancel_continuation === true;
}

export function isExplicitManualBatchRequest(request = {}, resolvedAction = '') {
  const requested = String(request.requested_action || request.requestedAction || '').toLowerCase();
  const resolved = String(resolvedAction || request.action || '').toLowerCase();
  return ['enroll', 'update', 'cancel'].includes(requested) && requested === resolved;
}

export function cancelContinuationBaseline({ groups = [], requestScopeKey = '', scopeKey = () => '', businessDate = () => null, now = new Date() } = {}) {
  const today = businessDate(now);
  const candidates = (groups || [])
    .filter((group) => ['completed', 'partial_or_failed', 'failed', 'cancelled', 'interrupted'].includes(String(group?.status || '').toLowerCase()))
    .filter((group) => String(group?.action || group?.request?.action || '').toLowerCase() === 'cancel')
    .filter((group) => businessDate(group.finished_at || group.updated_at) === today)
    .filter((group) => scopeKey(group.request || group) === requestScopeKey)
    .filter((group) => count(group?.result?.live_verified_removed_count) > 0)
    .sort((left, right) => String(right.finished_at || '').localeCompare(String(left.finished_at || '')));
  const group = candidates[0];
  if (!group) return null;
  const result = group.result || {};
  return {
    group_id: String(group.id || ''),
    expected_remaining_relations: count(result.failed) + Math.max(count(result.pending), count(result.retryable_pending_count)),
    confirmed_removed_relations: count(result.live_verified_removed_count),
  };
}

export function assertCancelContinuationScope({ baseline = null, liveRows = [], observedRelationCount = 0 } = {}) {
  if (!baseline) return { continuation: false, observed_relation_count: count(observedRelationCount) };
  const incompleteRows = (liveRows || []).filter((row) => (
    row?.blocked === true
    || row?.cache_reused === true
    || row?.fetch_mode === 'cache'
    || row?.is_full_fetch !== true
  ));
  if (incompleteRows.length) {
    const error = new Error('同日取消续跑必须实时读取全部活动商品状态，禁止复用历史 started 缓存。');
    error.code = 'CANCEL_CONTINUATION_LIVE_READ_REQUIRED';
    error.status = 409;
    error.details = { incomplete_activity_count: incompleteRows.length };
    throw error;
  }
  const expected = count(baseline.expected_remaining_relations);
  const observed = count(observedRelationCount);
  const ceiling = Math.max(expected + 1000, expected * 5);
  if (observed > ceiling) {
    const error = new Error(`取消续跑范围从预计 ${expected} 条扩大到 ${observed} 条，已阻断以避免历史缓存造成大范围重复写入。`);
    error.code = 'CANCEL_CONTINUATION_SCOPE_EXPANDED';
    error.status = 409;
    error.details = { expected_relation_count: expected, observed_relation_count: observed, hard_ceiling: ceiling };
    throw error;
  }
  return {
    continuation: true,
    expected_relation_count: expected,
    observed_relation_count: observed,
    hard_ceiling: ceiling,
  };
}

export function selectTerminalCancelRecoveryGroups({ groups = [], businessDate = () => null, now = new Date(), limit = 2 } = {}) {
  const today = businessDate(now);
  return (groups || [])
    .filter((group) => ['cancelled', 'failed'].includes(String(group?.status || '').toLowerCase()))
    .filter((group) => String(group?.action || group?.request?.action || '').toLowerCase() === 'cancel')
    .filter((group) => group?.cancel_requested === true)
    .filter((group) => businessDate(group.finished_at || group.updated_at) === today)
    .sort((left, right) => String(right.finished_at || right.updated_at || '').localeCompare(String(left.finished_at || left.updated_at || '')))
    .slice(0, Math.max(0, Math.floor(Number(limit) || 0)));
}

export function selectTerminalWriteRecoveryGroups({ groups = [], businessDate = () => null, now = new Date(), limit = 1 } = {}) {
  const today = businessDate(now);
  return (groups || [])
    .filter((group) => ['cancelled', 'failed', 'interrupted'].includes(String(group?.status || '').toLowerCase()))
    .filter((group) => ['enroll', 'update'].includes(String(group?.action || group?.request?.action || '').toLowerCase()))
    .filter((group) => group?.cancel_requested === true)
    .filter((group) => businessDate(group.finished_at || group.updated_at) === today)
    .sort((left, right) => String(right.finished_at || right.updated_at || '').localeCompare(String(left.finished_at || left.updated_at || '')))
    .slice(0, Math.max(0, Math.floor(Number(limit) || 0)));
}

function pendingTimestamp(record = {}) {
  const value = Date.parse(String(record?.first_pending_at || record?.updated_at || ''));
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function cancelRecoveryFreshAfter(records = [], { graceMs = 0, nowMs = Date.now() } = {}) {
  const timestamps = (records || []).map(pendingTimestamp).filter((value) => value !== null);
  if (!timestamps.length) return '';
  const latestPending = Math.max(...timestamps);
  const latestGraceDeadline = latestPending + Math.max(0, Number(graceMs) || 0);
  // Before the grace deadline, any complete read newer than the request is a
  // valid interim observation.  Once the deadline has passed, require a read
  // taken after the deadline so an older startup snapshot cannot falsely mark
  // a cancellation as still active.
  return new Date(Number(nowMs) >= latestGraceDeadline ? latestGraceDeadline : latestPending).toISOString();
}

export function cancelRecoveryFinalDelayMs(records = [], { graceMs = 0, nowMs = Date.now() } = {}) {
  const timestamps = (records || []).map(pendingTimestamp).filter((value) => value !== null);
  if (!timestamps.length) return null;
  const latestGraceDeadline = Math.max(...timestamps) + Math.max(0, Number(graceMs) || 0);
  return Math.max(0, latestGraceDeadline - Number(nowMs || 0));
}
