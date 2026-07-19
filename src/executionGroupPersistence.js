import fs from 'node:fs';
import path from 'node:path';

export const ACTIVE_EXECUTION_GROUP_STATUSES = new Set(['queued', 'running', 'stopping', 'paused']);
export const TERMINAL_EXECUTION_GROUP_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted']);

function safeId(value) {
  return String(value || '').replace(/[^A-Za-z0-9_.-]/g, '');
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function childCounts(child = {}) {
  const execution = child?.result?.execution || child?.result?.result?.execution || {};
  const success = Math.max(0, Number(execution.success || 0));
  const failed = Math.max(0, Number(execution.failed || 0));
  const skipped = Math.max(0, Number(execution.skipped || 0));
  const pending = Math.max(0, Number(execution.pending || 0));
  const total = Math.max(Number(execution.total || 0), success + failed + skipped + pending);
  return {
    total, success, failed, skipped, pending,
    relation_count: execution.relation_count ?? null,
    unique_item_count: execution.unique_item_count ?? null,
    activity_failure_count: execution.activity_failure_count ?? null,
    request_success_count: execution.request_success_count ?? null,
    live_verified_removed_count: execution.live_verified_removed_count ?? null,
    pending_verification_count: execution.pending_verification_count ?? null,
  };
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
  return stores.reduce((summary, store) => {
    summary.total += store.total;
    summary.success += store.success;
    summary.failed += store.failed;
    summary.skipped += store.skipped;
    summary.pending += store.pending;
    for (const field of ['relation_count', 'unique_item_count', 'activity_failure_count', 'request_success_count', 'live_verified_removed_count', 'pending_verification_count']) {
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
    stores,
  });
}

export function createExecutionGroupPersistence({ stateDir, currentPid = process.pid, now = () => new Date().toISOString() }) {
  const groups = new Map();

  function statePath(groupId) {
    return path.join(stateDir, `${safeId(groupId)}.json`);
  }

  function persist(group) {
    if (!group?.id) throw new Error('execution group id is required');
    fs.mkdirSync(stateDir, { recursive: true });
    group.updated_at = now();
    const target = statePath(group.id);
    const temporary = `${target}.${currentPid}.tmp`;
    const snapshot = { ...clone(group), process_pid: currentPid, persisted_at: now() };
    fs.writeFileSync(temporary, JSON.stringify(snapshot), 'utf8');
    fs.renameSync(temporary, target);
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

  function load(groupId) {
    const target = statePath(groupId);
    if (!fs.existsSync(target)) return null;
    try {
      const snapshot = JSON.parse(fs.readFileSync(target, 'utf8'));
      if (!snapshot || String(snapshot.id || '') !== String(groupId || '')) return null;
      const group = recover(snapshot);
      groups.set(String(group.id), group);
      return group;
    } catch {
      return null;
    }
  }

  function loadAll() {
    fs.mkdirSync(stateDir, { recursive: true });
    const loaded = [];
    for (const name of fs.readdirSync(stateDir).filter((value) => value.endsWith('.json'))) {
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

  loadAll();
  return { active, create, findBySubmissionId, load, loadAll, persist, statePath, updateChild };
}
