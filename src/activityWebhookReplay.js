export const CBT_WEBHOOK_REPLAY_MAX = 100;
export const CBT_WEBHOOK_REPLAY_DEFAULT_TOTAL_BUDGET = 300;
export const CBT_WEBHOOK_REPLAY_MAX_TOTAL_BUDGET = 1_000;

import { PHYSICAL_GET_BUDGET_EXHAUSTED, createPhysicalGetBudget } from './physicalGetBudget.js';
import { classifyCbtUnresolved, isEligibleCbtContinuation } from './cbtUnresolvedClassification.js';
import { buildCbtTargetedFallbackPlan } from './cbtTargetedFallback.js';

export function buildCbtWebhookReplayPlan(events = [], { limit = CBT_WEBHOOK_REPLAY_MAX } = {}) {
  const safeLimit = Math.max(1, Math.min(CBT_WEBHOOK_REPLAY_MAX, Number(limit) || CBT_WEBHOOK_REPLAY_MAX));
  const seen = new Set();
  return (Array.isArray(events) ? events : [])
    .filter((event) => {
      const resource = String(event?.resource || '');
      return ['route_unresolved_skipped', 'compensation_failed'].includes(String(event?.outcome || ''))
        && ['items', 'marketplace_items'].includes(String(event?.topic || '').toLowerCase())
        && /^\/items\/CBT[A-Za-z0-9._:-]+(?:[?#].*)?$/.test(resource)
        && !String(event?.child_user_id || '').trim();
    })
    .sort((left, right) => String(left.received_at || '').localeCompare(String(right.received_at || ''))
      || String(left.event_id || '').localeCompare(String(right.event_id || '')))
    .filter((event) => {
      const eventId = String(event?.event_id || '').trim();
      if (!eventId || seen.has(eventId)) return false;
      seen.add(eventId);
      return true;
    })
    .slice(0, safeLimit);
}

function safeReplayCode(value) {
  const text = String(value || '').trim().toUpperCase();
  return /^[A-Z0-9_.-]{1,80}$/.test(text) ? text : null;
}

function replayErrorKind(error = {}) {
  const provided = String(error?.error_kind || '').trim().toLowerCase();
  if (['network', 'timeout', 'rate_limit', 'service', 'unauthorized', 'forbidden', 'business', 'local_contract', 'unknown'].includes(provided)) return provided;
  const code = String(error?.code || error?.cause?.code || '').toUpperCase();
  const status = Number(error?.http_status || error?.status || error?.statusCode || 0);
  const message = String(error?.message || error || '');
  if (status === 429) return 'rate_limit';
  if (/timeout|timed out/i.test(message) || /TIME(?:D)?OUT/.test(code)) return 'timeout';
  if (/ECONN|ENET|EAI_|UND_ERR|fetch failed|network/i.test(`${code} ${message}`)) return 'network';
  if (status >= 500) return 'service';
  if (/not defined|ReferenceError|TypeError|contract|schema/i.test(`${code} ${message}`)) return 'local_contract';
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status >= 400 && status < 500) return 'business';
  return 'unknown';
}

export function buildCbtReplayErrorDiagnostic(error, { attemptCount = 0 } = {}) {
  const status = Number(error?.http_status || error?.status || error?.statusCode || 0);
  const message = String(error?.reason_cn || error?.message || error || '活动通知处理失败。')
    .replace(/[\r\n]+/g, ' ')
    .replace(/Bearer\s+[A-Za-z0-9._~-]+/gi, 'Bearer [REDACTED]')
    .slice(0, 180);
  const classification = error?.classification || error?.resolver_diagnostic?.classification;
  const result = {
    operation: error?.resolver_diagnostic?.operation || error?.operation || 'cbt_webhook_replay',
    endpoint_family: error?.resolver_diagnostic?.endpoint_family || error?.endpoint_family || 'marketplace_item_resource',
    error_kind: replayErrorKind(error),
    http_status: Number.isInteger(status) && status >= 100 && status <= 599 ? status : null,
    code: safeReplayCode(error?.diagnostic_code || error?.resolver_diagnostic?.code || error?.code),
    cause_code: safeReplayCode(error?.cause_code || error?.cause?.code),
    attempt_count: Math.max(0, Math.floor(Number(error?.attempt_count ?? attemptCount) || 0)),
    reason_cn: message,
    ...((classification || ['OFFICIAL_SELLER_MISSING', 'OFFICIAL_SELLER_AMBIGUOUS', 'OFFICIAL_SITE_MISSING', 'OFFICIAL_SITE_AMBIGUOUS', 'ROUTE_AMBIGUOUS', 'ROUTE_NOT_OWNED', 'RESOURCE_STATUS_UNUSABLE', PHYSICAL_GET_BUDGET_EXHAUSTED].includes(String(error?.diagnostic_code || error?.code || '').toUpperCase()))
      ? { classification: classification || classifyCbtUnresolved(error).category }
      : {}),
    ...(error?.budget_remaining === true ? { budget_remaining: true } : {}),
    ...((error?.resolver_diagnostic?.signal_presence || error?.signal_presence) ? {
      signal_presence: {
        seller_signal_count: Math.max(0, Number((error.resolver_diagnostic?.signal_presence || error.signal_presence).seller_signal_count || 0)),
        site_signal_count: Math.max(0, Number((error.resolver_diagnostic?.signal_presence || error.signal_presence).site_signal_count || 0)),
        status_present: Boolean((error.resolver_diagnostic?.signal_presence || error.signal_presence).status_present),
        route_candidate_count: Math.max(0, Number((error.resolver_diagnostic?.signal_presence || error.signal_presence).route_candidate_count || 0)),
        ...(Object.hasOwn((error.resolver_diagnostic?.signal_presence || error.signal_presence), 'owned_identity_proven')
          ? { owned_identity_proven: Boolean((error.resolver_diagnostic?.signal_presence || error.signal_presence).owned_identity_proven) }
          : {}),
        ...(Object.hasOwn((error.resolver_diagnostic?.signal_presence || error.signal_presence), 'foreign_route_candidate_count')
          ? { foreign_route_candidate_count: Math.max(0, Number((error.resolver_diagnostic?.signal_presence || error.signal_presence).foreign_route_candidate_count || 0)) }
          : {}),
        ...(Object.hasOwn((error.resolver_diagnostic?.signal_presence || error.signal_presence), 'root_site_id')
          ? { root_site_id: String((error.resolver_diagnostic?.signal_presence || error.signal_presence).root_site_id || '').trim().toUpperCase() }
          : {}),
        ...(Object.hasOwn((error.resolver_diagnostic?.signal_presence || error.signal_presence), 'child_item_count')
          ? { child_item_count: Math.max(0, Number((error.resolver_diagnostic?.signal_presence || error.signal_presence).child_item_count || 0)) }
          : {}),
      },
    } : {}),
  };
  return result;
}

export function storedActivityCallbackEvent(row = {}) {
  let stored = null;
  try { stored = typeof row.raw_json === 'string' ? JSON.parse(row.raw_json) : row.raw_json; } catch { stored = null; }
  const event = stored?.event && typeof stored.event === 'object' ? stored.event : stored;
  const storedOutcome = stored?.outcome && typeof stored.outcome === 'object' ? stored.outcome : {};
  return {
    ...(event && typeof event === 'object' && !Array.isArray(event) ? event : {}),
    ...storedOutcome,
    schema_version: String(row.schema_version || event?.schema_version || '2'),
    event_id: String(row.event_id || event?.event_id || ''),
    topic: String(row.topic || event?.topic || '').toLowerCase(),
    resource: String(row.resource || event?.resource || ''),
    remote_user_id: String(row.remote_user_id || event?.remote_user_id || ''),
    application_id: String(row.application_id || event?.application_id || ''),
    received_at: String(row.received_at || event?.received_at || ''),
    last_error: row.last_error || event?.last_error || null,
  };
}

export function createCbtWebhookReplay({
  listEvents,
  countEvents = null,
  claimEvent,
  consumeEvent,
  finalizeEvent,
  onProgress = null,
  shouldRetryEvent = null,
  now = () => new Date(),
} = {}) {
  if (typeof listEvents !== 'function' || typeof claimEvent !== 'function'
    || typeof consumeEvent !== 'function' || typeof finalizeEvent !== 'function') {
    throw new TypeError('CBT webhook replay requires list, claim, consume and finalize functions');
  }
  return async ({
    pageSize = CBT_WEBHOOK_REPLAY_MAX,
    totalBudget = CBT_WEBHOOK_REPLAY_DEFAULT_TOTAL_BUDGET,
    physicalBudget = null,
    since = null,
    onProgress: progressCallback = onProgress,
    shouldRetryEvent: retryEvent = shouldRetryEvent,
  } = {}) => {
    const safePageSize = Math.max(1, Math.min(CBT_WEBHOOK_REPLAY_MAX, Number(pageSize) || CBT_WEBHOOK_REPLAY_MAX));
    const normalizedTotalBudget = Number(totalBudget);
    const safeTotalBudget = Math.max(0, Math.min(
      CBT_WEBHOOK_REPLAY_MAX_TOTAL_BUDGET,
      Number.isFinite(normalizedTotalBudget) ? Math.floor(normalizedTotalBudget) : CBT_WEBHOOK_REPLAY_DEFAULT_TOTAL_BUDGET,
    ));
    const normalizedPhysicalBudget = physicalBudget === null || physicalBudget === undefined
      ? safeTotalBudget
      : Number(physicalBudget);
    const safePhysicalBudget = Math.max(0, Math.min(
      CBT_WEBHOOK_REPLAY_MAX_TOTAL_BUDGET,
      Number.isFinite(normalizedPhysicalBudget) ? Math.floor(normalizedPhysicalBudget) : safeTotalBudget,
    ));
    const physicalGetBudget = createPhysicalGetBudget(safePhysicalBudget);
    const result = {
      page_size: safePageSize,
      total_budget: safeTotalBudget,
      physical_budget: safePhysicalBudget,
      pages: 0,
      budget_used: 0,
      budget_exhausted: false,
      physical_budget_exhausted: false,
      considered: 0,
      replayed: 0,
      in_progress: 0,
      already_processed: 0,
      failed: 0,
      unresolved: 0,
      attempted: 0,
      succeeded: 0,
      retained_failure: 0,
      retryable_failed: 0,
      partial: 0,
      deduplicated: 0,
      deduplicated_unresolved: 0,
      deduplicated_failed: 0,
      unique_resource_attempted: 0,
      unique_resource_succeeded: 0,
      unique_resource_unresolved: 0,
      unique_resource_retryable_failed: 0,
      parent_event_attempted: 0,
      unique_cbt_resource_attempted: 0,
      route_target_attempted: 0,
      route_target_matched: 0,
      child_get_count: 0,
      cache_updated_count: 0,
      physical_get_used: 0,
      physical_get_reserved: 0,
      physical_get_issued: 0,
      physical_get_completed: 0,
      physical_get_released: 0,
      remaining_route_targets: null,
      budget_remaining_route_targets: 0,
      terminal_categories: {},
      classification_counts: {},
      classification_item_counts: {},
      classification_account_counts: {},
      classification_item_account_counts: {},
      classification_snapshot: false,
      targeted_fallback_planned: 0,
      targeted_fallback_status: 'none',
      event_ids: [],
    };
    let afterReceivedAt = null;
    let afterEventId = null;
    const resourceOutcomes = new Map();
    const syncPhysicalBudgetMetrics = () => {
      const stats = physicalGetBudget.stats();
      result.physical_get_used = stats.completed;
      result.physical_get_reserved = stats.reserved;
      result.physical_get_issued = stats.issued;
      result.physical_get_completed = stats.completed;
      result.physical_get_released = stats.released;
      result.physical_get_available = stats.available;
      result.physical_budget_exhausted = stats.available <= 0;
    };
    const addOutcomeMetrics = (outcome = {}) => {
      result.route_target_attempted += Math.max(0, Number(outcome.route_target_attempted_count ?? outcome.route_target_count ?? 0));
      result.route_target_matched += Math.max(0, Number(outcome.route_target_count || 0));
      result.child_get_count += Math.max(0, Number(outcome.child_get_count || 0));
      result.cache_updated_count += Math.max(0, Number(outcome.cache_updated_count || 0));
      if (!outcome.physical_get_stats) {
        const legacyCount = Math.max(0, Number(outcome.physical_get_count || 0));
        for (let index = 0; index < legacyCount; index += 1) {
          const permit = physicalGetBudget.reserve('legacy_consume_event');
          if (!permit) break;
          physicalGetBudget.issue(permit);
          physicalGetBudget.complete(permit);
        }
      }
      syncPhysicalBudgetMetrics();
    };
    const refreshRemaining = async () => {
      if (typeof countEvents !== 'function') {
        result.remaining = { event_count: null, item_count: null, retryable_failed_count: null };
      } else {
        result.remaining = await countEvents({ since, afterReceivedAt, afterEventId });
      }
      result.remaining_events = result.remaining.event_count;
      result.remaining_unique_resources = result.remaining.item_count;
      result.remaining_retryable_failed = result.remaining.retryable_failed_count ?? null;
      result.remaining_route_targets = result.remaining.route_target_count
        ?? result.remaining.route_target_remaining
        ?? result.remaining.route_targets
        ?? result.remaining_route_targets;
      result.remaining_eligible_events = result.remaining.eligible_event_count ?? result.remaining_events;
      result.remaining_eligible_resources = result.remaining.eligible_item_count ?? result.remaining_unique_resources;
      if (result.remaining && typeof result.remaining === 'object') {
        const eventCounts = result.remaining.classification_counts;
        const itemCounts = result.remaining.classification_item_counts;
        const eventAccountCounts = result.remaining.classification_account_counts;
        const itemAccountCounts = result.remaining.classification_item_account_counts;
        if (eventCounts && typeof eventCounts === 'object') result.classification_counts = { ...eventCounts };
        if (itemCounts && typeof itemCounts === 'object') result.classification_item_counts = { ...itemCounts };
        if (eventAccountCounts && typeof eventAccountCounts === 'object') result.classification_account_counts = { ...eventAccountCounts };
        if (itemAccountCounts && typeof itemAccountCounts === 'object') result.classification_item_account_counts = { ...itemAccountCounts };
        result.classification_snapshot = Boolean(
          (eventCounts && Object.keys(eventCounts).length)
          || (itemCounts && Object.keys(itemCounts).length),
        );
        if (result.classification_snapshot) {
          result.terminal_categories = {
            terminal_irrelevant: Number(result.classification_item_counts.terminal_irrelevant || 0),
            terminal_foreign: Number(result.classification_item_counts.terminal_foreign || 0),
            terminal_no_actionable_global_parent: Number(result.classification_item_counts.terminal_no_actionable_global_parent || 0),
            route_catalog_gap: Number(result.classification_item_counts.route_catalog_gap || 0),
            quarantined_unknown: Number(result.classification_item_counts.quarantined_unknown || 0),
          };
          result.route_catalog_gap_count = Number(result.remaining.route_catalog_gap_count
            ?? result.classification_item_counts.route_catalog_gap ?? 0);
          result.quarantined_unknown_count = Number(result.remaining.quarantined_unknown_count
            ?? result.classification_item_counts.quarantined_unknown ?? 0);
          result.terminal_foreign_count = Number(result.remaining.terminal_foreign_count
            ?? result.classification_item_counts.terminal_foreign ?? 0);
          result.terminal_irrelevant_count = Number(result.remaining.terminal_irrelevant_count
            ?? result.classification_item_counts.terminal_irrelevant ?? 0);
          result.terminal_no_actionable_global_parent_count = Number(result.remaining.terminal_no_actionable_global_parent_count
            ?? result.classification_item_counts.terminal_no_actionable_global_parent ?? 0);
          result.eligible_partial_count = Number(result.remaining.eligible_partial_count
            ?? result.classification_item_counts.eligible_partial ?? 0);
          result.eligible_budget_remaining_count = Number(result.remaining.eligible_budget_remaining_count
            ?? result.classification_item_counts.eligible_budget_remaining ?? 0);
          result.eligible_retryable_count = Number(result.remaining.eligible_retryable_count
            ?? result.classification_item_counts.eligible_retryable
            ?? result.classification_item_counts.eligible_route_unresolved
            ?? 0);
        }
      }
    };
    const publishProgress = async (phase) => {
      await refreshRemaining();
      syncPhysicalBudgetMetrics();
      if (typeof progressCallback === 'function') await progressCallback({ ...result, phase });
    };
    while (result.budget_used < safeTotalBudget) {
      syncPhysicalBudgetMetrics();
      if (safePhysicalBudget <= 0 || result.physical_get_used >= safePhysicalBudget) {
        result.physical_budget_exhausted = true;
        break;
      }
      const remainingBudget = safeTotalBudget - result.budget_used;
      const rows = await listEvents({
        since,
        limit: Math.min(safePageSize, remainingBudget),
        afterReceivedAt,
        afterEventId,
      });
      const page = buildCbtWebhookReplayPlan(rows, { limit: Math.min(safePageSize, remainingBudget) });
      if (!Array.isArray(rows) || !rows.length) break;
      if (page.length) {
        result.pages += 1;
        result.budget_used += page.length;
        result.considered += page.length;
      }
      let lastProcessedRow = null;
      let physicalBudgetHit = false;
      for (const row of page) {
        syncPhysicalBudgetMetrics();
        if (result.physical_get_used >= safePhysicalBudget) {
          physicalBudgetHit = true;
          break;
        }
        lastProcessedRow = row;
        const event = storedActivityCallbackEvent(row);
        const classification = classifyCbtUnresolved(event);
        if (typeof retryEvent === 'function' && !retryEvent(event, classification)) {
          result.terminal_categories[classification.category] = Number(result.terminal_categories[classification.category] || 0) + 1;
          const fallbackPlan = buildCbtTargetedFallbackPlan({
            event,
            classification: classification.category,
          });
          if (fallbackPlan.status === 'planned') {
            result.targeted_fallback_planned += 1;
            result.targeted_fallback_status = 'plan_only';
          }
          continue;
        }
        result.attempted += 1;
        result.parent_event_attempted += 1;
        const claim = await claimEvent(row.event_id, { now: now() });
        if (claim?.status === 'in_progress') {
          result.in_progress += 1;
          continue;
        }
        if (claim?.status !== 'claimed' || !claim?.claim_token) {
          result.already_processed += 1;
          continue;
        }
        const resourceKey = String(event.resource || row.resource || '').trim();
        const prior = resourceKey ? resourceOutcomes.get(resourceKey) : null;
        if (prior) {
          result.deduplicated += 1;
          try {
            if (prior.status === 'succeeded') {
              await finalizeEvent({
                eventId: row.event_id,
                claimToken: claim.claim_token,
                status: 'completed',
                event: { ...event, ...prior.outcome, replay_deduplicated: true, raw_json: JSON.stringify({ event, outcome: prior.outcome }) },
              });
              result.replayed += 1;
              result.succeeded += 1;
              result.event_ids.push(row.event_id);
            } else if (prior.status === 'unresolved') {
              await finalizeEvent({
                eventId: row.event_id,
                claimToken: claim.claim_token,
                status: 'completed',
                event: { ...event, outcome: 'route_unresolved_skipped', replay_deduplicated: true, raw_json: JSON.stringify({ event, diagnostic: prior.diagnostic }) },
                error: prior.diagnostic,
              });
              result.unresolved += 1;
              result.retained_failure += 1;
              result.deduplicated_unresolved += 1;
            } else {
              await finalizeEvent({
                eventId: row.event_id,
                claimToken: claim.claim_token,
                status: 'failed',
                event: { ...event, outcome: 'compensation_failed', raw_json: JSON.stringify({ event, diagnostic: prior.diagnostic }) },
                error: prior.diagnostic,
              });
              result.failed += 1;
              result.retained_failure += 1;
              result.retryable_failed += 1;
              result.deduplicated_failed += 1;
            }
          } catch {}
          continue;
        }
        if (resourceKey) {
          result.unique_resource_attempted += 1;
          result.unique_cbt_resource_attempted += 1;
        }
        try {
          const outcome = await consumeEvent(event, { physicalGetBudget });
          addOutcomeMetrics(outcome);
          if (outcome?.outcome === 'partial') {
            const budgetRemaining = outcome?.budget_remaining === true;
            const diagnostic = buildCbtReplayErrorDiagnostic({
              diagnostic_code: budgetRemaining ? PHYSICAL_GET_BUDGET_EXHAUSTED : 'CBT_FANOUT_PARTIAL',
              code: budgetRemaining ? PHYSICAL_GET_BUDGET_EXHAUSTED : 'CBT_FANOUT_PARTIAL',
              error_kind: budgetRemaining ? 'budget' : 'business',
              status: budgetRemaining ? 429 : 422,
              reason_cn: budgetRemaining
                ? '本次物理读取预算已用尽，未处理的 CBT 子商品保留到下一批。'
                : 'CBT 子商品多路由处理部分失败，保留失败目标等待重试。',
              attempt_count: claim.attempt_count,
            });
            if (resourceKey) resourceOutcomes.set(resourceKey, { status: budgetRemaining ? 'unresolved' : 'failed', diagnostic });
            await finalizeEvent({
              eventId: row.event_id,
              claimToken: claim.claim_token,
              status: budgetRemaining ? 'completed' : 'failed',
              event: {
                ...event,
                ...outcome,
                outcome: budgetRemaining ? 'route_unresolved_skipped' : 'compensation_failed',
                budget_remaining: budgetRemaining,
                raw_json: JSON.stringify({ event, outcome, diagnostic }),
              },
              error: diagnostic,
            });
            result.partial += 1;
            if (budgetRemaining) result.unresolved += 1;
            else result.failed += 1;
            result.retained_failure += 1;
            if (budgetRemaining) {
              result.unique_resource_unresolved += resourceKey ? 1 : 0;
              result.physical_budget_exhausted = true;
              result.budget_remaining_route_targets += Math.max(0, Number(outcome.fanout_failed_count || 0));
            } else {
              result.retryable_failed += 1;
              result.unique_resource_retryable_failed += resourceKey ? 1 : 0;
            }
            continue;
          }
          if (resourceKey) resourceOutcomes.set(resourceKey, { status: 'succeeded', outcome });
          await finalizeEvent({
            eventId: row.event_id,
            claimToken: claim.claim_token,
            status: 'completed',
            event: { ...event, ...outcome, raw_json: JSON.stringify({ event, outcome }) },
          });
          result.replayed += 1;
          result.succeeded += 1;
          result.unique_resource_succeeded += resourceKey ? 1 : 0;
          result.event_ids.push(row.event_id);
        } catch (error) {
          const code = String(error?.diagnostic_code || error?.code || '');
          const budgetRemaining = code === PHYSICAL_GET_BUDGET_EXHAUSTED || error?.budget_remaining === true;
          const unresolved = budgetRemaining || [
            'ACTIVITY_CALLBACK_ROUTE_UNRESOLVED',
            'ACTIVITY_CALLBACK_ROUTE_AMBIGUOUS',
            'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED',
            'ACTIVITY_CALLBACK_ROUTE_MISMATCH',
            'OFFICIAL_SELLER_MISSING',
            'OFFICIAL_SELLER_AMBIGUOUS',
            'OFFICIAL_SITE_MISSING',
            'OFFICIAL_SITE_AMBIGUOUS',
            'ROUTE_NOT_OWNED',
            'ROUTE_AMBIGUOUS',
            'RESOURCE_STATUS_UNUSABLE',
          ].includes(code);
          const diagnostic = buildCbtReplayErrorDiagnostic(error, { attemptCount: claim.attempt_count });
          if (resourceKey) resourceOutcomes.set(resourceKey, { status: unresolved ? 'unresolved' : 'failed', diagnostic });
          try {
            await finalizeEvent({
              eventId: row.event_id,
              claimToken: claim.claim_token,
              status: unresolved ? 'completed' : 'failed',
              event: {
                ...event,
                outcome: unresolved ? 'route_unresolved_skipped' : 'compensation_failed',
                budget_remaining: budgetRemaining,
                raw_json: JSON.stringify({ event, diagnostic }),
              },
              error: diagnostic,
            });
          } catch {}
          if (unresolved) {
            result.unresolved += 1;
            result.retained_failure += 1;
            result.unique_resource_unresolved += resourceKey ? 1 : 0;
            if (budgetRemaining) {
              result.physical_budget_exhausted = true;
              result.budget_remaining_route_targets += Math.max(0, Number(error?.fanout?.fanout_failed_count || 0));
            }
          } else {
            result.failed += 1;
            result.retained_failure += 1;
            result.retryable_failed += 1;
            result.unique_resource_retryable_failed += resourceKey ? 1 : 0;
          }
        }
      }
      // Advance from the raw page tail, not the deduped work list. Otherwise
      // duplicate event ids near a page boundary can make the cursor repeat
      // rows or silently truncate the remaining batch.
      const last = physicalBudgetHit ? lastProcessedRow : rows.at(-1);
      afterReceivedAt = String(last.received_at || '');
      afterEventId = String(last.event_id || '');
      if (!afterReceivedAt || !afterEventId) break;
      await publishProgress('page');
      if (physicalBudgetHit) {
        result.physical_budget_exhausted = true;
        break;
      }
      if (rows.length < safePageSize) break;
    }
    result.cursor = afterReceivedAt && afterEventId ? { received_at: afterReceivedAt, event_id: afterEventId } : null;
    await refreshRemaining();
    syncPhysicalBudgetMetrics();
    result.remaining_parent_events = result.remaining_events;
    result.remaining_unique_cbt_resources = result.remaining_unique_resources;
    result.unique_resource_retained_failure = result.classification_snapshot
      ? Number(result.quarantined_unknown_count || 0) + Number(result.route_catalog_gap_count || 0)
      : result.unique_resource_unresolved + result.unique_resource_retryable_failed;
    result.budget_exhausted = (result.budget_used >= safeTotalBudget || result.physical_budget_exhausted)
      && Number(result.remaining_eligible_events ?? result.remaining_events ?? 0) > 0;
    return result;
  };
}
