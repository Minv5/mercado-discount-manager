function nonNegative(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0, numeric) : 0;
}

function promotionKey(row = {}) {
  return [row.site_id, row.promotion_id, row.promotion_type]
    .map((value) => String(value || '').trim().toUpperCase())
    .join('|');
}

function mergeRecoveryCounts(previous = {}, current = {}) {
  const declaredRelationCount = nonNegative(previous.relation_count);
  const previousRelationCount = declaredRelationCount || (
    nonNegative(previous.success) + nonNegative(previous.failed) + nonNegative(previous.skipped)
      + Math.max(nonNegative(previous.pending), nonNegative(previous.pending_verification_count), nonNegative(previous.retryable_pending_count))
      + nonNegative(previous.platform_pending_count)
  );
  let previousRemaining = previousRelationCount;
  const previousPending = Math.min(previousRemaining, Math.max(
    nonNegative(previous.pending),
    nonNegative(previous.pending_verification_count),
    nonNegative(previous.retryable_pending_count),
  ));
  previousRemaining -= previousPending;
  const previousSuccess = Math.min(previousRemaining, nonNegative(previous.success));
  previousRemaining -= previousSuccess;
  const previousFailed = Math.min(previousRemaining, nonNegative(previous.failed));
  previousRemaining -= previousFailed;
  const previousPlatformPending = Math.min(previousRemaining, nonNegative(previous.platform_pending_count));
  previousRemaining -= previousPlatformPending;
  const previousSkipped = Math.min(previousRemaining, nonNegative(previous.skipped));
  const recoveryScope = previousPending || Math.min(
    previousRelationCount,
    Math.max(nonNegative(current.relation_count), nonNegative(current.total)),
  );
  let remaining = recoveryScope;
  const currentPending = Math.min(remaining, nonNegative(current.pending));
  remaining -= currentPending;
  const recoveredSuccess = Math.min(remaining, nonNegative(current.success));
  remaining -= recoveredSuccess;
  const recoveredFailed = Math.min(remaining, nonNegative(current.failed));
  remaining -= recoveredFailed;
  const recoveredPlatformPending = Math.min(remaining, nonNegative(current.platform_pending_count));
  remaining -= recoveredPlatformPending;
  // A recovery round may carry duplicate planning skips for the same pending
  // relations. Only the still-unclassified remainder can become a real skip.
  const recoveredSkipped = Math.min(remaining, nonNegative(current.skipped));
  remaining -= recoveredSkipped;
  const pending = currentPending + remaining;
  const success = previousSuccess + recoveredSuccess;
  const failed = previousFailed + recoveredFailed;
  const skipped = previousSkipped + recoveredSkipped;
  const platformPending = Math.min(
    Math.max(0, previousRelationCount - success - failed - skipped - pending),
    previousPlatformPending + recoveredPlatformPending,
  );
  return {
    relation_count: previousRelationCount,
    total: previousRelationCount,
    success,
    failed,
    skipped,
    pending,
    platform_pending_count: platformPending,
    retryable_pending_count: pending,
    pending_verification_count: Math.min(pending, nonNegative(current.pending_verification_count ?? current.pending)),
  };
}

export function mergeExecutionRecoveryRound(previous = {}, current = {}) {
  const promotions = new Map((previous.promotions || []).map((row) => [promotionKey(row), { ...row }]));
  for (const row of current.promotions || []) {
    const key = promotionKey(row);
    const prior = promotions.get(key);
    if (!prior) continue;
    promotions.set(key, { ...prior, ...row, ...mergeRecoveryCounts(prior, row) });
  }
  const counts = mergeRecoveryCounts(previous, current);
  return {
    ...previous,
    ...current,
    ...counts,
    promotions: [...promotions.values()],
    promotions_total: Math.max(nonNegative(previous.promotions_total), promotions.size),
    unique_item_count: nonNegative(previous.unique_item_count),
    blocked: Math.max(nonNegative(previous.blocked), nonNegative(current.blocked)),
    activity_failure_count: Math.max(nonNegative(previous.activity_failure_count), nonNegative(current.activity_failure_count)),
    request_success_count: Math.min(counts.relation_count, nonNegative(previous.request_success_count) + nonNegative(current.request_success_count)),
    live_verified_removed_count: Math.min(counts.success, nonNegative(previous.live_verified_removed_count) + nonNegative(current.live_verified_removed_count)),
    recovery_round: nonNegative(previous.recovery_round) + 1,
  };
}

export function combineCurrentRecoveryExecutions(left = null, right = null) {
  if (!left) return right;
  if (!right) return left;
  const promotionMap = new Map();
  for (const row of [...(left.promotions || []), ...(right.promotions || [])]) {
    promotionMap.set(promotionKey(row), row);
  }
  const relationCount = nonNegative(left.relation_count) + nonNegative(right.relation_count);
  return {
    ...left,
    ...right,
    total: relationCount,
    promotions_total: promotionMap.size,
    success: nonNegative(left.success) + nonNegative(right.success),
    failed: nonNegative(left.failed) + nonNegative(right.failed),
    skipped: nonNegative(left.skipped) + nonNegative(right.skipped),
    pending: nonNegative(left.pending) + nonNegative(right.pending),
    blocked: nonNegative(left.blocked) + nonNegative(right.blocked),
    relation_count: relationCount,
    unique_item_count: Math.max(nonNegative(left.unique_item_count), nonNegative(right.unique_item_count)),
    activity_failure_count: nonNegative(left.activity_failure_count) + nonNegative(right.activity_failure_count),
    request_success_count: nonNegative(left.request_success_count) + nonNegative(right.request_success_count),
    live_verified_removed_count: nonNegative(left.live_verified_removed_count) + nonNegative(right.live_verified_removed_count),
    pending_verification_count: nonNegative(left.pending_verification_count) + nonNegative(right.pending_verification_count),
    platform_pending_count: nonNegative(left.platform_pending_count) + nonNegative(right.platform_pending_count),
    retryable_pending_count: nonNegative(left.retryable_pending_count) + nonNegative(right.retryable_pending_count),
    promotions: [...promotionMap.values()],
  };
}

export function reconcileExecutionWithPendingQueue(execution = {}, queueState = {}) {
  const stateCounts = { success: 0, failed: 0, skipped: 0, pending: 0, platform_pending: 0 };
  for (const row of Object.values(queueState?.records || {})) {
    const state = String(row?.state || '');
    if (Object.hasOwn(stateCounts, state)) stateCounts[state] += 1;
  }
  const relationCount = Math.max(
    nonNegative(execution.relation_count),
    nonNegative(execution.total),
  );
  const failed = Math.max(nonNegative(execution.failed), stateCounts.failed);
  const skipped = Math.max(nonNegative(execution.skipped), stateCounts.skipped);
  const pending = stateCounts.pending;
  const platformPending = Math.max(nonNegative(execution.platform_pending_count), stateCounts.platform_pending);
  const successCapacity = Math.max(0, relationCount - failed - skipped - pending - platformPending);
  const success = Math.min(
    successCapacity,
    Math.max(nonNegative(execution.success), stateCounts.success),
  );
  const unresolved = Math.max(0, relationCount - success - failed - skipped - pending - platformPending);
  return {
    ...execution,
    total: relationCount,
    relation_count: relationCount,
    success,
    failed,
    skipped,
    pending,
    pending_count: pending,
    pending_verification_count: pending,
    platform_pending_count: platformPending,
    retryable_pending_count: 0,
    unresolved,
    queue_state_counts: stateCounts,
  };
}

export function recordCompletedPromotion(progress = {}, event = {}, hash = (value) => value) {
  const tokenMaterial = [event.promotion?.site_id, event.promotion_id, event.promotion_type]
    .map((value) => String(value || '').trim().toUpperCase())
    .join('|');
  const token = hash(tokenMaterial);
  const tokens = new Set(Array.isArray(progress.execute_completed_promotion_tokens)
    ? progress.execute_completed_promotion_tokens.map(String)
    : []);
  if (token) tokens.add(String(token));
  const total = Math.max(nonNegative(progress.total_promotions), nonNegative(event.total));
  return {
    tokens: [...tokens],
    completed: Math.min(total, Math.max(nonNegative(progress.execute_completed_promotions), tokens.size)),
    total,
  };
}
