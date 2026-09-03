export const CBT_REPLAY_DEFAULT_WAVE_BUDGET = 1_000;
export const CBT_REPLAY_DEFAULT_MAX_WAVES = 12;
export const CBT_REPLAY_DEFAULT_COOLDOWN_MS = 5_000;
export const CBT_REPLAY_NO_PROGRESS_LIMIT = 3;

function number(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function add(target, source, key) {
  target[key] = number(target[key]) + number(source?.[key]);
}

function mergeWaveTotals(total, wave) {
  for (const key of [
    'considered', 'attempted', 'succeeded', 'unresolved', 'failed', 'retryable_failed',
    'retained_failure', 'replayed', 'partial', 'deduplicated', 'deduplicated_unresolved',
    'deduplicated_failed', 'unique_resource_attempted', 'unique_resource_succeeded',
    'unique_resource_unresolved', 'unique_resource_retryable_failed', 'parent_event_attempted',
    'unique_cbt_resource_attempted', 'route_target_attempted', 'route_target_matched',
    'child_get_count', 'cache_updated_count', 'physical_get_used', 'physical_get_reserved',
    'physical_get_issued', 'physical_get_completed', 'physical_get_released',
    'budget_remaining_route_targets',
    'targeted_fallback_planned',
  ]) add(total, wave, key);
  const waveClassificationCounts = wave?.classification_counts && typeof wave.classification_counts === 'object'
    && Object.keys(wave.classification_counts).length
    ? wave.classification_counts
    : null;
  const waveClassificationItemCounts = wave?.classification_item_counts && typeof wave.classification_item_counts === 'object'
    && Object.keys(wave.classification_item_counts).length
    ? wave.classification_item_counts
    : null;
  const waveClassificationAccountCounts = wave?.classification_account_counts && typeof wave.classification_account_counts === 'object'
    && Object.keys(wave.classification_account_counts).length
    ? wave.classification_account_counts
    : null;
  const waveClassificationItemAccountCounts = wave?.classification_item_account_counts && typeof wave.classification_item_account_counts === 'object'
    && Object.keys(wave.classification_item_account_counts).length
    ? wave.classification_item_account_counts
    : null;
  if (waveClassificationCounts || waveClassificationItemCounts) {
    // These are a snapshot of the remaining queue, not additive wave totals.
    // Replacing them prevents a quarantined resource that survives a cooldown
    // from being counted again as a new blocking resource.
    total.classification_counts = { ...(waveClassificationCounts || {}) };
    total.classification_item_counts = { ...(waveClassificationItemCounts || {}) };
    total.classification_account_counts = { ...(waveClassificationAccountCounts || {}) };
    total.classification_item_account_counts = { ...(waveClassificationItemAccountCounts || {}) };
    total.classification_snapshot = true;
    total.terminal_categories = {
      terminal_irrelevant: number(total.classification_item_counts.terminal_irrelevant),
      terminal_foreign: number(total.classification_item_counts.terminal_foreign),
      terminal_no_actionable_global_parent: number(total.classification_item_counts.terminal_no_actionable_global_parent),
      route_catalog_gap: number(total.classification_item_counts.route_catalog_gap),
      quarantined_unknown: number(total.classification_item_counts.quarantined_unknown),
    };
  } else {
    total.terminal_categories = { ...(wave?.terminal_categories || {}) };
  }
  total.remaining_events = wave?.remaining_events ?? wave?.remaining_eligible_events ?? null;
  total.remaining_unique_resources = wave?.remaining_unique_resources ?? wave?.remaining_eligible_resources ?? null;
  total.remaining_eligible_events = wave?.remaining_eligible_events ?? total.remaining_events;
  total.remaining_eligible_resources = wave?.remaining_eligible_resources ?? total.remaining_unique_resources;
  total.remaining_route_targets = wave?.remaining_route_targets ?? total.remaining_route_targets ?? null;
  total.route_catalog_gap_count = number(wave?.route_catalog_gap_count ?? total.terminal_categories.route_catalog_gap);
  total.quarantined_unknown_count = number(wave?.quarantined_unknown_count ?? total.terminal_categories.quarantined_unknown);
  total.terminal_foreign_count = number(wave?.terminal_foreign_count ?? total.terminal_categories.terminal_foreign);
  total.terminal_irrelevant_count = number(wave?.terminal_irrelevant_count ?? total.terminal_categories.terminal_irrelevant);
  total.terminal_no_actionable_global_parent_count = number(
    wave?.terminal_no_actionable_global_parent_count ?? total.terminal_categories.terminal_no_actionable_global_parent,
  );
  total.eligible_partial_count = number(wave?.eligible_partial_count ?? total.classification_item_counts?.eligible_partial);
  total.eligible_budget_remaining_count = number(wave?.eligible_budget_remaining_count ?? total.classification_item_counts?.eligible_budget_remaining);
  total.eligible_retryable_count = number(wave?.eligible_retryable_count
    ?? total.classification_item_counts?.eligible_retryable
    ?? total.classification_item_counts?.eligible_route_unresolved);
  total.unique_resource_retained_failure = total.classification_snapshot
    ? total.quarantined_unknown_count + total.route_catalog_gap_count
    : number(wave?.unique_resource_retained_failure);
  total.physical_budget = wave?.physical_budget ?? total.physical_budget;
  total.physical_budget_exhausted = wave?.physical_budget_exhausted === true;
  total.budget_exhausted = wave?.budget_exhausted === true;
}

export async function runCbtReplayWaves({
  replay,
  wavePhysicalBudget = CBT_REPLAY_DEFAULT_WAVE_BUDGET,
  waveTotalBudget = wavePhysicalBudget,
  maxWaves = CBT_REPLAY_DEFAULT_MAX_WAVES,
  cooldownMs = CBT_REPLAY_DEFAULT_COOLDOWN_MS,
  noProgressLimit = CBT_REPLAY_NO_PROGRESS_LIMIT,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  shouldStop = () => false,
  checkpoint = null,
  onProgress = null,
  replayOptions = {},
} = {}) {
  if (typeof replay !== 'function') throw new TypeError('CBT replay continuation requires a replay function');
  const safeWaveBudget = Math.max(0, Math.floor(number(wavePhysicalBudget, CBT_REPLAY_DEFAULT_WAVE_BUDGET)));
  const safeWaveTotal = Math.max(0, Math.floor(number(waveTotalBudget, safeWaveBudget)));
  const safeMaxWaves = Math.max(1, Math.floor(number(maxWaves, CBT_REPLAY_DEFAULT_MAX_WAVES)));
  const safeCooldown = Math.max(0, Math.floor(number(cooldownMs, CBT_REPLAY_DEFAULT_COOLDOWN_MS)));
  const safeNoProgress = Math.max(1, Math.floor(number(noProgressLimit, CBT_REPLAY_NO_PROGRESS_LIMIT)));
  const total = {
    waves: [],
    wave_count: 0,
    cooldown_count: 0,
    cooldown_ms_total: 0,
    continuation_status: 'pending',
    no_progress_waves: 0,
    terminal_categories: {},
    remaining_events: null,
    remaining_unique_resources: null,
    remaining_eligible_events: null,
    remaining_eligible_resources: null,
    remaining_route_targets: null,
    classification_counts: {},
    classification_item_counts: {},
    classification_account_counts: {},
    classification_item_account_counts: {},
    classification_snapshot: false,
    unique_resource_retained_failure: 0,
    physical_budget: safeWaveBudget,
    physical_budget_exhausted: false,
    budget_exhausted: false,
  };
  let firstWave = 1;
  if (checkpoint && typeof checkpoint.load === 'function') {
    const saved = await checkpoint.load();
    firstWave = Math.max(1, Math.min(safeMaxWaves, Math.floor(number(saved?.next_wave, 1))));
    total.resumed_from_wave = firstWave > 1 ? firstWave : null;
  }
  for (let wave = firstWave; wave <= safeMaxWaves; wave += 1) {
    if (shouldStop()) {
      total.continuation_status = 'stopped';
      break;
    }
    const waveStartedAt = Date.now();
    const waveProgress = async (value = {}) => {
      const snapshot = {
        ...value,
        wave,
        wave_count: wave,
        continuation_status: 'running',
        physical_get_used_total: number(total.physical_get_used) + number(value.physical_get_used),
      };
      if (typeof onProgress === 'function') await onProgress(snapshot);
    };
    const waveResult = await replay({
      ...replayOptions,
      totalBudget: safeWaveTotal,
      physicalBudget: safeWaveBudget,
      onProgress: waveProgress,
    });
    const summary = {
      wave,
      started_at: new Date(waveStartedAt).toISOString(),
      finished_at: new Date().toISOString(),
      status: waveResult?.physical_budget_exhausted ? 'budget_exhausted' : 'completed',
      cooldown_ms: 0,
      ...waveResult,
    };
    total.waves.push(summary);
    total.wave_count = wave;
    mergeWaveTotals(total, waveResult || {});
    const progress = number(waveResult?.unique_resource_succeeded) + number(waveResult?.cache_updated_count);
    if (progress <= 0) total.no_progress_waves += 1;
    else total.no_progress_waves = 0;
    if (checkpoint && typeof checkpoint.save === 'function') {
      await checkpoint.save({
        next_wave: wave + 1,
        wave,
        no_progress_waves: total.no_progress_waves,
        remaining_eligible_events: total.remaining_eligible_events,
        remaining_eligible_resources: total.remaining_eligible_resources,
      });
    }

    const remainingEligibleEvents = number(
      waveResult?.remaining_eligible_events ?? waveResult?.remaining_events,
      0,
    );
    const remainingEligibleResources = number(
      waveResult?.remaining_eligible_resources ?? waveResult?.remaining_unique_resources,
      0,
    );
    if (remainingEligibleEvents <= 0 && remainingEligibleResources <= 0) {
      total.continuation_status = 'complete';
      break;
    }
    if (total.no_progress_waves >= safeNoProgress) {
      total.continuation_status = 'no_progress_circuit_open';
      break;
    }
    if (wave >= safeMaxWaves) {
      total.continuation_status = 'max_waves_reached';
      break;
    }
    if (shouldStop()) {
      total.continuation_status = 'stopped';
      break;
    }
    total.cooldown_count += 1;
    total.cooldown_ms_total += safeCooldown;
    summary.cooldown_ms = safeCooldown;
    if (typeof onProgress === 'function') {
      await onProgress({
        ...waveResult,
        wave,
        wave_count: wave,
        continuation_status: 'cooldown',
        physical_get_used_total: total.physical_get_used,
        cooldown_ms: safeCooldown,
        cooldown_count: total.cooldown_count,
        no_progress_waves: total.no_progress_waves,
      });
    }
    if (safeCooldown > 0) await sleep(safeCooldown);
  }
  if (total.continuation_status === 'pending') total.continuation_status = 'max_waves_reached';
  return total;
}
