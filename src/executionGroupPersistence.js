import fs from 'node:fs';
import path from 'node:path';
import { writeJsonFileAtomicallySync } from './processInstanceLock.js';

export const ACTIVE_EXECUTION_GROUP_STATUSES = new Set(['queued', 'running', 'stopping', 'paused']);
export const TERMINAL_EXECUTION_GROUP_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted']);

function safeId(value) {
  return String(value || '').replace(/[^A-Za-z0-9_.-]/g, '');
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function childCounts(child = {}) {
  const result = child?.result?.execution
    ? child.result
    : child?.result?.result?.execution
      ? child.result.result
      : child?.result || null;
  const execution = result?.execution || null;
  const resultPresent = Boolean(execution && typeof execution === 'object');
  const counts = execution || {};
  const success = Math.max(0, Number(counts.success || 0));
  const failed = Math.max(0, Number(counts.failed || 0));
  const skipped = Math.max(0, Number(counts.skipped || 0));
  const pending = Math.max(0, Number(counts.pending || 0));
  const total = Math.max(Number(counts.total || 0), success + failed + skipped + pending);
  const persistenceState = String(child.persistence_state || '');
  const missingPersistence = ['missing', 'corrupt', 'unreadable', 'identity_mismatch'].includes(persistenceState);
  const incompleteReasons = [];
  const addReason = (reason) => {
    const normalized = String(reason || '').trim();
    if (normalized && !incompleteReasons.includes(normalized)) incompleteReasons.push(normalized);
  };
  for (const reason of Array.isArray(child.incomplete_reasons) ? child.incomplete_reasons : []) addReason(reason);
  addReason(child.incomplete_reason);
  if (missingPersistence) addReason(`${persistenceState}_job_state`);
  if (!resultPresent) {
    addReason('missing_child_result');
  } else {
    const accountingValues = [result?.accounting_complete, execution?.accounting_complete];
    if (accountingValues.some((value) => value === false)) addReason('accounting_incomplete');
    else if (accountingValues.some((value) => value !== true)) addReason('accounting_not_proven');

    const terminalSources = [result?.terminal_counts, execution?.terminal_counts];
    const terminalContracts = terminalSources.map(normalizeTerminalCounts);
    if (terminalContracts.some((value) => value === null)) {
      addReason('terminal_counts_not_proven');
    }
    const provenTerminalContracts = terminalContracts.filter(Boolean);
    if (provenTerminalContracts.length) {
      if (provenTerminalContracts.some((value) => value.is_closed !== true)) addReason('terminal_counts_not_closed');
      if (provenTerminalContracts.some((value) => !terminalCountsAreClosed(value))) {
        addReason('relation_count_gap');
      }
      if (
        provenTerminalContracts.length === terminalSources.length
        && !terminalCountsMatch(provenTerminalContracts[0], provenTerminalContracts[1])
      ) {
        addReason('terminal_counts_mismatch');
      }
      const terminal = provenTerminalContracts[0];
      const relationCount = terminal.relation_count;
      const executionRelationCount = nonNegativeFiniteNumber(execution.relation_count);
      if (
        executionRelationCount !== null
        && executionRelationCount !== relationCount
      ) {
        addReason('relation_count_gap');
      }
    }

    if (hasPositiveCount([
      result?.pending,
      result?.pending_count,
      result?.pending_verification_count,
      result?.retryable_pending_count,
      execution?.pending,
      execution?.pending_count,
      execution?.pending_verification_count,
      execution?.retryable_pending_count,
    ])) addReason('pending_relations_present');
    if (hasPositiveCount([
      result?.unresolved,
      result?.unresolved_count,
      result?.terminal_counts?.unresolved,
      execution?.unresolved,
      execution?.unresolved_count,
      execution?.terminal_counts?.unresolved,
    ])) addReason('unresolved_relations_present');
    if (hasPositiveCount([
      result?.platform_pending,
      result?.platform_pending_count,
      result?.terminal_counts?.platform_pending,
      execution?.platform_pending,
      execution?.platform_pending_count,
      execution?.terminal_counts?.platform_pending,
    ])) addReason('platform_pending_present');
  }
  const incomplete = Boolean(child.incomplete) || incompleteReasons.length > 0;
  return {
    total, success, failed, skipped, pending,
    relation_count: counts.relation_count ?? null,
    unique_item_count: counts.unique_item_count ?? null,
    activity_failure_count: counts.activity_failure_count ?? null,
    request_success_count: counts.request_success_count ?? null,
    live_verified_removed_count: counts.live_verified_removed_count ?? null,
    pending_verification_count: counts.pending_verification_count ?? null,
    platform_pending_count: counts.platform_pending_count ?? null,
    retryable_pending_count: counts.retryable_pending_count ?? null,
    result_present: resultPresent,
    persistence_state: persistenceState || null,
    incomplete,
    incomplete_reason: incompleteReasons[0] || null,
    incomplete_reasons: incompleteReasons,
  };
}

function nonNegativeFiniteNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : null;
}

function hasPositiveCount(values = []) {
  return values.some((value) => {
    const numeric = nonNegativeFiniteNumber(value);
    return numeric !== null && numeric > 0;
  });
}

const TERMINAL_COUNT_FIELDS = Object.freeze([
  'relation_count',
  'success',
  'failed',
  'skipped',
  'platform_pending',
  'unresolved',
  'classified_count',
]);

function normalizeTerminalCounts(value) {
  if (!value || typeof value !== 'object') return null;
  const normalized = { is_closed: value.is_closed };
  for (const field of TERMINAL_COUNT_FIELDS) {
    const numeric = nonNegativeFiniteNumber(value[field]);
    if (numeric === null) return null;
    normalized[field] = numeric;
  }
  return normalized;
}

function terminalCountsAreClosed(counts) {
  const calculated = counts.success
    + counts.failed
    + counts.skipped
    + counts.platform_pending
    + counts.unresolved;
  return counts.is_closed === true
    && calculated === counts.relation_count
    && counts.classified_count === calculated;
}

function terminalCountsMatch(left, right) {
  return TERMINAL_COUNT_FIELDS.every((field) => left[field] === right[field]);
}

function normalizedTextList(values) {
  return [...new Set((Array.isArray(values) ? values : [])
    .map((value) => String(value || '').trim())
    .filter(Boolean))].sort((left, right) => left.localeCompare(right));
}

export function executionGroupBusinessScope(group = {}) {
  const request = group.request || {};
  const filters = request.filters || {};
  const accountIds = Array.isArray(request.accountIds) && request.accountIds.length
    ? request.accountIds
    : (group.children || []).map((child) => child.account_id || child.request_summary?.accountId);
  const numberOrNull = (value) => value === undefined || value === null || value === ''
    ? null
    : Number(value);
  return {
    account_ids: normalizedTextList(accountIds),
    site_id: String(filters.siteId || '').trim().toUpperCase(),
    selected_site_name: String(request.selectedSiteName || '').trim(),
    seller_activity_names: normalizedTextList(filters.sellerActivityNames),
    official_activity_names: normalizedTextList(filters.officialActivityNames),
    exclude_seller: Boolean(filters.excludeSeller),
    exclude_official: Boolean(filters.excludeOfficial),
    seller_discount_percent: numberOrNull(request.sellerDiscountPercent),
    official_discount_percent: numberOrNull(request.officialDiscountPercent),
  };
}

export function summarizeExecutionGroup(group = {}) {
  const stores = (group.children || []).map((child) => ({
    job_id: String(child.job_id || child.id || ''),
    account_id: String(child.account_id || child.request_summary?.accountId || ''),
    store_name: child.store_name || child.request_summary?.storeName || '',
    site_name: child.site_name || child.request_summary?.selectedSiteName || '',
    status: String(child.status || 'queued'),
    error: child.error || null,
    ...childCounts(child),
  }));
  const summary = stores.reduce((summary, store) => {
    summary.total += store.total;
    summary.success += store.success;
    summary.failed += store.failed;
    summary.skipped += store.skipped;
    summary.pending += store.pending;
    for (const field of ['relation_count', 'unique_item_count', 'activity_failure_count', 'request_success_count', 'live_verified_removed_count', 'pending_verification_count', 'platform_pending_count', 'retryable_pending_count']) {
      if (store[field] !== null && store[field] !== undefined) summary[field] += Number(store[field] || 0);
    }
    return summary;
  }, {
    action: String(group.action || group.request?.action || ''),
    store_count: stores.length,
    total: 0,
    success: 0,
    failed: 0,
    skipped: 0,
    pending: 0,
    relation_count: 0,
    unique_item_count: 0,
    activity_failure_count: 0,
    request_success_count: 0,
    live_verified_removed_count: 0,
    pending_verification_count: 0,
    platform_pending_count: 0,
    retryable_pending_count: 0,
    stores,
  });
  const incompleteStores = stores.filter((store) => store.incomplete);
  summary.accounting_complete = incompleteStores.length === 0;
  summary.incomplete_child_count = incompleteStores.length;
  summary.incomplete_job_ids = incompleteStores
    .map((store) => store.job_id)
    .filter(Boolean);
  summary.incomplete_reasons = [...new Set(incompleteStores
    .flatMap((store) => store.incomplete_reasons || [store.incomplete_reason])
    .filter(Boolean))];
  summary.incomplete_details = incompleteStores.map((store) => ({
    job_id: store.job_id,
    reasons: [...(store.incomplete_reasons || [store.incomplete_reason]).filter(Boolean)],
  }));
  return summary;
}

export function projectLiveExecutionGroupChildren(group = {}, resolveJob = () => null, projectJob = (job) => job) {
  return (group.children || []).map((child) => {
    const jobId = String(child.job_id || child.id || '');
    const job = jobId ? resolveJob(jobId) : null;
    if (!job) {
      const hasPersistedResult = Boolean(child?.result?.execution || child?.result?.result?.execution);
      if (hasPersistedResult) return { ...child, persistence_state: 'snapshot_only' };
      return {
        ...child,
        persistence_state: 'missing',
        incomplete: true,
        incomplete_reason: 'missing_job_state',
      };
    }
    return {
      ...child,
      ...projectJob(job),
      job_id: jobId,
    };
  });
}

function persistenceReadError(kind, groupId, target, cause = null) {
  const code = `EXECUTION_GROUP_STATE_${String(kind || 'UNKNOWN').toUpperCase()}`;
  const error = new Error(`执行组状态${kind === 'corrupt' ? '已损坏' : kind === 'unreadable' ? '无法读取' : '身份不匹配'}，已停止以保护任务状态。`, cause ? { cause } : undefined);
  error.code = code;
  error.state_kind = 'execution_group';
  error.state_id = String(groupId || '');
  error.state_path = target;
  error.read_status = kind;
  return error;
}

export function createExecutionGroupPersistence({
  stateDir,
  currentPid = process.pid,
  now = () => new Date().toISOString(),
  fsImpl = fs,
  retryDelaysMs,
  sleepSync,
  loadOnCreate = true,
}) {
  const groups = new Map();

  function statePath(groupId) {
    return path.join(stateDir, `${safeId(groupId)}.json`);
  }

  function persist(group) {
    if (!group?.id) throw new Error('execution group id is required');
    group.updated_at = now();
    const target = statePath(group.id);
    const snapshot = { ...clone(group), process_pid: currentPid, persisted_at: now() };
    writeJsonFileAtomicallySync({
      target,
      value: snapshot,
      currentPid,
      fsImpl,
      retryDelaysMs,
      sleepSync,
    });
    groups.set(String(group.id), group);
    return group;
  }

  function recover(snapshot) {
    const status = String(snapshot?.status || '');
    if (!ACTIVE_EXECUTION_GROUP_STATUSES.has(status) || Number(snapshot.process_pid || 0) === currentPid) return snapshot;
    if (status === 'paused') {
      snapshot.status = 'queued';
      snapshot.children = (snapshot.children || []).map((child) => String(child.status || '') === 'paused'
        ? { ...child, status: 'queued', progress: { ...(child.progress || {}), recovered_pending_after_restart: true } }
        : child);
      snapshot.recovered_pending_after_restart = true;
      return persist(snapshot);
    }
    const recoveredAt = now();
    snapshot.status = 'interrupted';
    snapshot.finished_at = snapshot.finished_at || recoveredAt;
    snapshot.error = '程序组件重启，执行组已中断；已完成结果保留，请查看历史详情。';
    snapshot.children = (snapshot.children || []).map((child) => ACTIVE_EXECUTION_GROUP_STATUSES.has(String(child.status || ''))
      ? { ...child, status: 'interrupted', finished_at: child.finished_at || recoveredAt, error: child.error || snapshot.error }
      : child);
    snapshot.result = summarizeExecutionGroup(snapshot);
    return persist(snapshot);
  }

  function inspect(groupId) {
    const target = statePath(groupId);
    let text;
    try {
      text = fsImpl.readFileSync(target, 'utf8');
    } catch (error) {
      if (String(error?.code || '') === 'ENOENT') return { status: 'missing', value: null, path: target };
      return { status: 'unreadable', value: null, path: target, error };
    }
    let snapshot;
    try {
      snapshot = JSON.parse(text);
    } catch (error) {
      return { status: 'corrupt', value: null, path: target, error };
    }
    if (!snapshot || typeof snapshot !== 'object') return { status: 'corrupt', value: null, path: target };
    if (String(snapshot.id || '') !== String(groupId || '')) {
      return { status: 'identity_mismatch', value: null, path: target };
    }
    return { status: 'ok', value: snapshot, path: target };
  }

  function load(groupId) {
    const inspected = inspect(groupId);
    if (inspected.status === 'missing') return null;
    if (inspected.status !== 'ok') {
      throw persistenceReadError(inspected.status, groupId, inspected.path, inspected.error);
    }
    const group = recover(inspected.value);
    groups.set(String(group.id), group);
    return group;
  }

  function loadAll() {
    fsImpl.mkdirSync(stateDir, { recursive: true });
    const loaded = [];
    for (const name of fsImpl.readdirSync(stateDir).filter((value) => value.endsWith('.json'))) {
      const group = load(name.slice(0, -5));
      if (group) loaded.push(group);
    }
    return loaded.sort((left, right) => String(left.created_at || '').localeCompare(String(right.created_at || '')));
  }

  function findBySubmissionId(submissionId) {
    const wanted = String(submissionId || '');
    if (!wanted) return null;
    for (const group of groups.values()) {
      if (String(group.client_submission_id || '') === wanted) return group;
    }
    return null;
  }

  function active() {
    return [...groups.values()]
      .filter((group) => ACTIVE_EXECUTION_GROUP_STATUSES.has(String(group.status || '')))
      .sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')))[0] || null;
  }

  function create(group) {
    if (!groups.size) loadAll();
    const prior = findBySubmissionId(group.client_submission_id);
    if (prior) return { group: prior, reused: true };
    const current = active();
    if (current) {
      const error = new Error('已有真实执行组正在运行，本次未重复提交。');
      error.code = 'ACTIVE_EXECUTION_GROUP';
      error.group = current;
      throw error;
    }
    persist(group);
    return { group, reused: false };
  }

  function updateChild(groupId, job) {
    const group = groups.get(String(groupId)) || load(groupId);
    if (!group) return null;
    const jobId = String(job?.id || job?.job_id || '');
    const index = (group.children || []).findIndex((child) => String(child.job_id || child.id || '') === jobId);
    if (index < 0) return group;
    group.children[index] = {
      ...group.children[index],
      job_id: jobId,
      status: job.status,
      started_at: job.started_at || group.children[index].started_at || null,
      finished_at: job.finished_at || null,
      progress: job.progress || null,
      request_summary: job.request_summary || null,
      logs: job.logs || [],
      userLogs: job.userLogs || [],
      result: job.result || null,
      error: job.error || null,
    };
    group.global_peak_in_flight = Math.max(
      Number(group.global_peak_in_flight || 0),
      Number(job.progress?.global_peak_in_flight || 0),
    );
    persist(group);
    return group;
  }

  if (loadOnCreate) loadAll();
  return { active, create, findBySubmissionId, inspect, load, loadAll, persist, statePath, updateChild };
}
