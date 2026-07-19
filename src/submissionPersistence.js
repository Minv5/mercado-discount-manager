import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export const SUBMISSION_STATES = new Set([
  'preparing', 'prepared', 'reconfirm_required', 'paused', 'cancelled', 'expired', 'committing', 'creating', 'created',
  'starting', 'executing', 'terminal', 'failed',
]);
export const ACTIVE_SUBMISSION_STATES = new Set(['preparing', 'prepared', 'reconfirm_required', 'committing', 'creating', 'created', 'starting', 'executing']);
export const SUBMISSION_DECISION_CONTRACT_VERSION = 2;
export const DEFAULT_COMMIT_LEASE_MS = 30 * 60 * 1000;
const PRE_GROUP_COMMIT_STATES = new Set(['committing', 'creating', 'created', 'starting']);
const PREPARED_EXPIRY_PROTECTED_STATES = new Set(['committing', 'creating', 'created', 'starting', 'executing']);
const PREPARED_EXPIRY_PROTECTED_AUDITS = new Set([
  'commit_started', 'lease_acquired', 'seller_creation_started', 'seller_creation_verified', 'group_starting', 'group_started',
]);
const MAX_AUDIT_EVENTS = 200;
const MAX_REVALIDATION_HISTORY = 20;

function safeId(value) {
  return String(value || '').replace(/[^A-Za-z0-9_.-]/g, '');
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]));
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function submissionScopeHash(value) {
  return crypto.createHash('sha256').update(JSON.stringify(stable(value ?? null))).digest('hex').toUpperCase();
}

export function submissionRequestFingerprint(request = {}) {
  const value = clone(request || {});
  if (value.decision_contract_version == null) value.decision_contract_version = SUBMISSION_DECISION_CONTRACT_VERSION;
  for (const key of [
    'client_submission_id',
    'confirmText', 'confirm_text',
    'createConfirmText', 'create_confirm_text',
    'same_day_confirmation_token', 'sameDayConfirmationToken',
  ]) delete value[key];
  for (const key of ['accountIds', 'account_ids']) {
    if (Array.isArray(value[key])) value[key] = [...new Set(value[key].map(String))].sort();
  }
  if (value.filters && typeof value.filters === 'object') {
    for (const key of ['siteIds', 'promotionTypes', 'sellerActivityNames', 'officialActivityNames']) {
      if (Array.isArray(value.filters[key])) value.filters[key] = [...new Set(value.filters[key].map(String))].sort();
    }
  }
  return submissionScopeHash(value);
}

export function createSubmissionPersistence({ stateDir, now = () => new Date().toISOString() }) {
  const records = new Map();

  function statePath(id) {
    return path.join(stateDir, `${safeId(id)}.json`);
  }

  function auditPath(id) {
    return path.join(stateDir, `${safeId(id)}.audit.jsonl`);
  }

  function appendAudit(id, event = {}) {
    const current = load(id);
    if (!current) return false;
    fs.mkdirSync(stateDir, { recursive: true });
    fs.appendFileSync(auditPath(id), `${JSON.stringify({
      at: now(),
      type: String(event.type || ''),
      reason: event.reason ? String(event.reason) : undefined,
      state: event.state ? String(event.state) : undefined,
    })}\n`, 'utf8');
    return true;
  }

  function persist(prepare) {
    if (!prepare?.id || !prepare?.client_submission_id) throw new Error('prepare id and client submission id are required');
    if (!SUBMISSION_STATES.has(String(prepare.state || ''))) throw new Error('invalid submission state');
    fs.mkdirSync(stateDir, { recursive: true });
    const saved = clone(prepare);
    saved.version = Math.max(1, Number(saved.version || 1));
    saved.updated_at = now();
    const target = statePath(saved.id);
    const temporary = `${target}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(saved), 'utf8');
    fs.renameSync(temporary, target);
    records.set(String(saved.id), saved);
    return saved;
  }

  function load(id) {
    const key = String(id || '');
    if (records.has(key)) return records.get(key);
    const target = statePath(key);
    if (!fs.existsSync(target)) return null;
    try {
      const value = JSON.parse(fs.readFileSync(target, 'utf8'));
      if (!value || String(value.id || '') !== key) return null;
      value.version = Math.max(1, Number(value.version || 1));
      records.set(key, value);
      return value;
    } catch {
      return null;
    }
  }

  function loadAll() {
    fs.mkdirSync(stateDir, { recursive: true });
    for (const file of fs.readdirSync(stateDir).filter((name) => name.endsWith('.json'))) load(file.slice(0, -5));
    return [...records.values()];
  }

  function findBySubmissionId(submissionId) {
    const wanted = String(submissionId || '');
    if (!records.size) loadAll();
    return [...records.values()].find((row) => String(row.client_submission_id || '') === wanted) || null;
  }

  function create(input) {
    const prior = findBySubmissionId(input?.client_submission_id);
    if (prior) return { prepare: prior, reused: true };
    const prepared = { ...clone(input), version: Math.max(1, Number(input.version || 1)), created_at: input.created_at || now(), updated_at: now() };
    persist(prepared);
    return { prepare: load(prepared.id), reused: false };
  }

  function update(id, patch) {
    const current = load(id);
    if (!current) return null;
    return persist({ ...clone(current), ...clone(patch || {}), version: Number(current.version || 1) + 1 });
  }

  function compareAndSwap(id, expected = {}, patch = {}) {
    const current = load(id);
    if (!current) return { ok: false, prepare: null };
    const expectedStates = Array.isArray(expected.state) ? expected.state.map(String) : null;
    const matches = (expected.version == null || Number(current.version || 1) === Number(expected.version))
      && (expected.state == null || (expectedStates ? expectedStates.includes(String(current.state || '')) : String(current.state || '') === String(expected.state)))
      && (expected.commit_lease_id == null || String(current.commit_lease_id || '') === String(expected.commit_lease_id || ''));
    if (!matches) return { ok: false, prepare: current };
    const prepared = persist({
      ...clone(current),
      ...clone(patch || {}),
      version: Number(current.version || 1) + 1,
    });
    return { ok: true, prepare: prepared };
  }

  loadAll();
  return { appendAudit, auditPath, compareAndSwap, create, findBySubmissionId, load, loadAll, persist, statePath, update };
}

export async function runSubmissionPreparation({
  store,
  prepareId,
  buildSnapshot,
  preparedPatch,
  formatError = (error) => String(error?.message || error || '准备执行范围失败'),
}) {
  let prepare = store.load(prepareId);
  if (!prepare || prepare.state !== 'preparing') return prepare;
  const reportProgress = (progress = {}) => {
    const current = store.load(prepareId);
    if (!current || current.state !== 'preparing') {
      const stopped = new Error('submission preparation is no longer active');
      stopped.code = 'PREPARATION_STOPPED';
      throw stopped;
    }
    return store.update(prepareId, {
      progress: {
        stage: String(progress.stage || current.progress?.stage || 'preparing'),
        percent: Math.max(0, Math.min(99, Number(progress.percent || 0))),
        completed: Math.max(0, Number(progress.completed || 0)),
        total: Math.max(0, Number(progress.total || 0)),
        message: String(progress.message || current.progress?.message || '正在核对执行范围'),
        current_store: String(progress.current_store || ''),
        current_site: String(progress.current_site || ''),
        current_activity: String(progress.current_activity || ''),
      },
    });
  };
  try {
    const snapshot = await buildSnapshot(prepare.request || {}, reportProgress);
    prepare = store.load(prepareId);
    if (!prepare || prepare.state !== 'preparing') return prepare;
    return store.update(prepareId, {
      state: 'prepared',
      ...(preparedPatch?.(snapshot, prepare) || {}),
      progress: {
        stage: 'prepared', percent: 100, completed: 1, total: 1,
        message: '执行范围准备完成', current_store: '', current_site: '', current_activity: '',
      },
      error: null,
    });
  } catch (error) {
    const current = store.load(prepareId);
    if (!current || current.state !== 'preparing') return current;
    return store.update(prepareId, {
      state: 'failed',
      error: formatError(error),
      progress: {
        stage: 'failed', percent: Number(current.progress?.percent || 0),
        completed: Number(current.progress?.completed || 0), total: Number(current.progress?.total || 0),
        message: '执行范围准备失败', current_store: '', current_site: '', current_activity: '',
      },
    });
  }
}

export function claimSubmissionPreparation({ store, clientSubmissionId, createRecord, now = () => new Date().toISOString() }) {
  const rawPrior = store.findBySubmissionId(clientSubmissionId);
  const prior = rawPrior ? loadEffectiveSubmission({ store, prepareId: rawPrior.id, now }) : null;
  if (prior) return { prepare: prior, reused: true };
  const active = loadAllEffectiveSubmissions({ store, now })
    .find((row) => ACTIVE_SUBMISSION_STATES.has(String(row?.state || '')));
  if (active) {
    throw submissionError('已有一次提交正在准备或等待确认，请先完成当前提交。', 'ACTIVE_SUBMISSION_EXISTS', 409, {
      prepare_id: active.id,
      state: active.state,
    });
  }
  return store.create(createRecord());
}

export function resumePausedSubmission({ store, request, clientSubmissionId, now = () => new Date().toISOString() }) {
  const fingerprint = submissionRequestFingerprint(request);
  const currentTime = new Date(now()).getTime();
  const paused = store.loadAll()
    .filter((row) => row.state === 'paused'
      && row.request_fingerprint === fingerprint
      && row.expires_at
      && new Date(row.expires_at).getTime() > currentTime)
    .sort((left, right) => String(right.updated_at || '').localeCompare(String(left.updated_at || '')))[0] || null;
  if (!paused) return null;
  const prepare = store.update(paused.id, {
    state: 'prepared',
    client_submission_id: String(clientSubmissionId),
    error: null,
    progress: {
      ...(paused.progress || {}), stage: 'prepared', percent: 100,
      message: '已恢复尚未过期的执行范围', current_store: '', current_site: '', current_activity: '',
    },
  });
  return { prepare, reused: true, resumed: true };
}

function submissionError(message, code, status = 409, details = null) {
  const error = new Error(message);
  error.code = code;
  error.status = status;
  error.details = details;
  return error;
}

function auditPatch(record, at, entries = []) {
  const prior = Array.isArray(record?.audit_events) ? record.audit_events : [];
  const next = entries.filter(Boolean).map((entry) => ({
    at,
    type: String(entry.type || ''),
    reason: entry.reason ? String(entry.reason) : undefined,
    from_state: entry.from_state ? String(entry.from_state) : undefined,
    to_state: entry.to_state ? String(entry.to_state) : undefined,
  }));
  return [...prior, ...next].slice(-MAX_AUDIT_EVENTS);
}

function stateChangedAudit(fromState, toState, reason = null) {
  return { type: 'state_changed', from_state: fromState, to_state: toState, reason };
}

function revalidationHistoryPatch(record, checked, at) {
  const prior = Array.isArray(record?.revalidation_history) ? record.revalidation_history : [];
  if (!checked?.revalidation_record) return prior;
  return [...prior, {
    at,
    round: prior.length + 1,
    ...clone(checked.revalidation_record),
  }].slice(-MAX_REVALIDATION_HISTORY);
}

function stateError(record, fallbackCode = 'COMMIT_STATE_CHANGED') {
  const state = String(record?.state || '');
  if (state === 'expired') return submissionError(record?.error || '准备结果已过期，请重新检查范围。', 'PREPARE_EXPIRED');
  if (state === 'cancelled') return submissionError('本次提交已停止，未启动商品执行。', 'SUBMISSION_CANCELLED');
  if (state === 'paused') return submissionError('本次准备已暂停，未启动商品执行。', 'SUBMISSION_PAUSED');
  if (state === 'failed') return submissionError(record?.error || '本次提交未完成。', record?.failure_code || 'COMMIT_FAILED');
  return submissionError('本次提交状态已变化，后台已阻止旧流程继续。', fallbackCode);
}

function abortError() {
  return submissionError('本次提交已停止，后台已阻止旧流程继续。', 'COMMIT_ABORTED');
}

async function awaitAbortable(operation, signal) {
  if (signal?.aborted) throw abortError();
  const pending = Promise.resolve().then(operation);
  if (!signal) return pending;
  return new Promise((resolve, reject) => {
    let settled = false;
    const abort = () => {
      if (settled) return;
      settled = true;
      reject(abortError());
    };
    signal.addEventListener('abort', abort, { once: true });
    pending.then((value) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener('abort', abort);
      resolve(value);
    }, (error) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener('abort', abort);
      reject(error);
    });
  });
}

export function refreshSubmissionDeadline({ store, prepareId, now = () => new Date().toISOString(), onAbort = null }) {
  let current = store.load(prepareId);
  if (!current) return null;
  const nowText = now();
  const nowMs = new Date(nowText).getTime();
  current = normalizeExpiredPreparedSubmission({ store, prepare: current, nowText, nowMs });
  if (String(current.state || '') === 'reconfirm_required'
      && current.expires_at && new Date(current.expires_at).getTime() <= nowMs) {
    const changed = store.compareAndSwap(current.id, { version: current.version, state: current.state }, {
      state: 'expired',
      error: '准备结果已过期，请重新检查范围。',
      failure_code: 'PREPARE_EXPIRED',
      progress: { ...(current.progress || {}), stage: 'expired', message: '准备结果已过期，请重新检查范围' },
      audit_events: auditPatch(current, nowText, [stateChangedAudit(current.state, 'expired', 'prepare_ttl_expired')]),
    });
    return changed.prepare || current;
  }
  if (PRE_GROUP_COMMIT_STATES.has(String(current.state || ''))
      && current.commit_lease_expires_at
      && new Date(current.commit_lease_expires_at).getTime() <= nowMs
      && !current.group_id) {
    const changed = store.compareAndSwap(current.id, {
      version: current.version,
      state: current.state,
      commit_lease_id: current.commit_lease_id || '',
    }, {
      state: 'failed',
      error: '提交处理超过安全时限，已停止且未继续启动商品执行。',
      failure_code: 'COMMIT_LEASE_EXPIRED',
      commit_lease_id: null,
      commit_lease_owner: null,
      commit_lease_expires_at: null,
      progress: { ...(current.progress || {}), stage: 'failed', message: '提交安全时限已到，已停止继续执行' },
      audit_events: auditPatch(current, nowText, [
        { type: 'abort_requested', reason: 'commit_lease_expired' },
        { type: 'aborted', reason: 'commit_lease_expired' },
        stateChangedAudit(current.state, 'failed', 'commit_lease_expired'),
      ]),
    });
    if (changed.ok) onAbort?.(changed.prepare, 'commit_lease_expired');
    return changed.prepare || current;
  }
  return current;
}

function hasPersistedWriteEvidence(value) {
  if (Array.isArray(value)) return value.length > 0;
  if (value && typeof value === 'object') return Object.keys(value).length > 0;
  if (typeof value === 'number') return value > 0;
  return Boolean(value);
}

function preparedExpiryHasUnsafeEvidence(prepare) {
  if (!prepare || String(prepare.state || '') !== 'prepared') return true;
  if ([
    'group_id', 'execution_group_id', 'execution_job_id', 'commit_lease_id', 'commit_lease_owner',
    'commit_lease_expires_at', 'commit_started_at', 'creating_started_at', 'group_started_at',
  ].some((field) => Boolean(prepare[field]))) return true;
  if (prepare.commit_confirmed === true || prepare.create_confirmed === true) return true;
  if ([
    'write_attempt_count', 'request_success_count', 'live_verified_removed_count', 'pending_verification_count',
    'creation_progress', 'creation_result', 'seller_campaign_create_results', 'execution_job_ids', 'child_jobs',
  ].some((field) => hasPersistedWriteEvidence(prepare[field]))) return true;
  return (Array.isArray(prepare.audit_events) ? prepare.audit_events : []).some((event) => (
    PREPARED_EXPIRY_PROTECTED_AUDITS.has(String(event?.type || ''))
      || PREPARED_EXPIRY_PROTECTED_STATES.has(String(event?.from_state || ''))
      || PREPARED_EXPIRY_PROTECTED_STATES.has(String(event?.to_state || ''))
  ));
}

function normalizeExpiredPreparedSubmission({ store, prepare, nowText, nowMs }) {
  if (!prepare || String(prepare.state || '') !== 'prepared') return prepare;
  const expiresMs = new Date(prepare.expires_at || '').getTime();
  if (!Number.isFinite(expiresMs) || expiresMs > nowMs || preparedExpiryHasUnsafeEvidence(prepare)) return prepare;
  const changed = store.compareAndSwap(prepare.id, { version: prepare.version, state: 'prepared' }, {
    state: 'expired',
    error: '准备结果已过期，请重新检查范围。',
    failure_code: 'PREPARE_EXPIRED',
    progress: { ...(prepare.progress || {}), stage: 'expired', message: '准备结果已过期，请重新检查范围' },
    audit_events: auditPatch(prepare, nowText, [{
      type: 'ttl_elapsed', from_state: 'prepared', to_state: 'expired', reason: 'prepared_ttl_elapsed',
    }]),
  });
  return changed.prepare || prepare;
}

export function loadEffectiveSubmission({ store, prepareId, now = () => new Date().toISOString(), onAbort = null }) {
  return refreshSubmissionDeadline({ store, prepareId, now, onAbort });
}

export function loadAllEffectiveSubmissions({ store, now = () => new Date().toISOString(), onAbort = null }) {
  return store.loadAll().map((prepare) => loadEffectiveSubmission({
    store,
    prepareId: prepare.id,
    now,
    onAbort,
  })).filter(Boolean);
}

export function cancelSubmissionCommit({ store, prepareId, now = () => new Date().toISOString(), onAbort = null }) {
  const current = store.load(prepareId);
  if (!current || !PRE_GROUP_COMMIT_STATES.has(String(current.state || '')) || current.group_id) {
    return { changed: false, prepare: current };
  }
  const at = now();
  const changed = store.compareAndSwap(current.id, {
    version: current.version,
    state: current.state,
    commit_lease_id: current.commit_lease_id || '',
  }, {
    state: 'cancelled',
    error: null,
    failure_code: null,
    commit_lease_id: null,
    commit_lease_owner: null,
    commit_lease_expires_at: null,
    progress: { ...(current.progress || {}), stage: 'cancelled', message: '用户已停止提交，未继续启动商品执行' },
    audit_events: auditPatch(current, at, [
      { type: 'abort_requested', reason: 'user_cancelled' },
      { type: 'aborted', reason: 'user_cancelled' },
      stateChangedAudit(current.state, 'cancelled', 'user_cancelled'),
    ]),
  });
  if (changed.ok) onAbort?.(changed.prepare, 'user_cancelled');
  return { changed: changed.ok, prepare: changed.prepare || current };
}

export async function commitSubmission({
  store,
  prepareId,
  confirmText,
  createConfirmText,
  confirmationToken,
  revalidate,
  revalidateAfterCreation,
  createSellerCampaigns,
  startGroup,
  now = () => new Date().toISOString(),
  commitLeaseMs = DEFAULT_COMMIT_LEASE_MS,
  leaseOwner = `node-${process.pid}`,
  recover = false,
  signal = null,
}) {
  let current = store.load(prepareId);
  if (!current) throw submissionError('未找到本次准备结果，请重新准备。', 'PREPARE_NOT_FOUND', 404);
  if (current.group_id) return { prepare: current, group: current.group || { id: current.group_id }, reused: true };
  if (String(confirmText || '') !== 'REAL_SUBMIT') throw submissionError('最终提交需要 REAL_SUBMIT 确认。', 'REAL_SUBMIT_REQUIRED');
  const expectedConfirmationToken = String(current.execution_confirmation_token || '');
  if (!recover && expectedConfirmationToken && String(confirmationToken || '') !== expectedConfirmationToken) {
    throw submissionError('本次最终确认已失效，请重新核对执行范围。', 'EXECUTION_CONFIRMATION_INVALID');
  }
  const selectedBeforeCommit = current.seller_input?.selected_targets || [];
  if (selectedBeforeCommit.length
      && !['created', 'starting', 'executing', 'terminal'].includes(String(current.state || ''))
      && String(createConfirmText || '') !== 'CREATE_SELLER_CAMPAIGN'
      && !current.create_confirmed) {
    throw submissionError('创建自建活动需要 CREATE_SELLER_CAMPAIGN 确认。', 'CREATE_CONFIRM_REQUIRED');
  }
  current = refreshSubmissionDeadline({ store, prepareId, now });
  if (!current) throw submissionError('未找到本次准备结果，请重新准备。', 'PREPARE_NOT_FOUND', 404);
  if (current.group_id) return { prepare: current, group: current.group || { id: current.group_id }, reused: true };
  if (current.state === 'expired') throw stateError(current);
  if (current.state === 'creating' && recover) {
    throw submissionError('创建结果需要先完成只读回查，后台不会重复创建。', 'SELLER_CREATION_RECHECK_REQUIRED');
  }
  if (PRE_GROUP_COMMIT_STATES.has(String(current.state || '')) && !recover) {
    throw submissionError('后台正在处理同一次提交，请继续查询当前状态。', 'COMMIT_IN_PROGRESS', 409, {
      prepare_id: current.id,
      state: current.state,
    });
  }
  if (!['prepared', 'reconfirm_required'].includes(String(current.state || ''))
      && !(recover && PRE_GROUP_COMMIT_STATES.has(String(current.state || '')))) {
    throw stateError(current, 'INVALID_SUBMISSION_STATE');
  }

  const at = now();
  const leaseId = crypto.randomUUID();
  const leaseExpiresAt = new Date(new Date(at).getTime() + Math.max(1_000, Number(commitLeaseMs || DEFAULT_COMMIT_LEASE_MS))).toISOString();
  const fromState = String(current.state || '');
  const acquired = store.compareAndSwap(current.id, {
    version: current.version,
    state: current.state,
    commit_lease_id: current.commit_lease_id || '',
  }, {
    state: ['prepared', 'reconfirm_required'].includes(fromState) ? 'committing' : fromState,
    commit_confirmed: true,
    execution_confirmation_token_consumed_at: current.execution_confirmation_token_consumed_at || at,
    create_confirmed: Boolean(current.create_confirmed || String(createConfirmText || '') === 'CREATE_SELLER_CAMPAIGN'),
    commit_started_at: current.commit_started_at || at,
    commit_lease_id: leaseId,
    commit_lease_owner: String(leaseOwner || `node-${process.pid}`),
    commit_lease_expires_at: leaseExpiresAt,
    error: null,
    failure_code: null,
    progress: { ...(current.progress || {}), stage: ['prepared', 'reconfirm_required'].includes(fromState) ? 'committing' : fromState, message: recover ? '正在恢复已确认的提交' : '正在执行提交前轻量目录复核' },
    audit_events: auditPatch(current, at, [
      recover ? { type: 'recovery_polled', reason: 'node_restart' } : { type: 'commit_started' },
      { type: 'lease_acquired', reason: recover ? 'recovered' : 'initial' },
      ...(['prepared', 'reconfirm_required'].includes(fromState) ? [stateChangedAudit(fromState, 'committing', 'commit_confirmed')] : []),
    ]),
  });
  if (!acquired.ok) throw stateError(acquired.prepare, 'COMMIT_IN_PROGRESS');
  let owned = acquired.prepare;

  const ensureOwned = (allowedStates = null) => {
    if (signal?.aborted) throw abortError();
    const latest = refreshSubmissionDeadline({ store, prepareId, now });
    if (!latest) throw submissionError('未找到本次准备结果，请重新准备。', 'PREPARE_NOT_FOUND', 404);
    if (Number(latest.version || 0) !== Number(owned.version || 0)
        || String(latest.commit_lease_id || '') !== leaseId
        || (allowedStates && !allowedStates.includes(String(latest.state || '')))) {
      throw stateError(latest);
    }
    owned = latest;
    return owned;
  };

  const transition = (nextState, patch = {}, reason = null) => {
    ensureOwned([String(owned.state || '')]);
    const previous = String(owned.state || '');
    const changed = store.compareAndSwap(owned.id, {
      version: owned.version,
      state: previous,
      commit_lease_id: leaseId,
    }, {
      ...patch,
      state: nextState,
      progress: patch.progress || { ...(owned.progress || {}), stage: nextState },
      audit_events: auditPatch(owned, now(), previous === nextState ? [] : [stateChangedAudit(previous, nextState, reason)]),
    });
    if (!changed.ok) throw stateError(changed.prepare);
    owned = changed.prepare;
    return owned;
  };

  const persistCreationProgress = (progress = {}) => {
    ensureOwned(['creating']);
    const changed = store.compareAndSwap(owned.id, {
      version: owned.version,
      state: 'creating',
      commit_lease_id: leaseId,
    }, {
      creation_progress: clone(progress),
      audit_events: auditPatch(owned, now(), [{ type: 'recovery_polled', reason: 'creation_progress_saved' }]),
    });
    if (!changed.ok) throw stateError(changed.prepare);
    owned = changed.prepare;
    return owned;
  };

  const persistCommitProgress = (progress = {}) => {
    ensureOwned(['committing']);
    const changed = store.compareAndSwap(owned.id, {
      version: owned.version,
      state: 'committing',
      commit_lease_id: leaseId,
    }, {
      progress: {
        ...(owned.progress || {}),
        ...clone(progress),
        stage: String(progress.stage || 'revalidating'),
        percent: Math.max(0, Math.min(99, Number(progress.percent || owned.progress?.percent || 0))),
        message: String(progress.message || '正在执行提交前轻量目录复核'),
      },
    });
    if (!changed.ok) throw stateError(changed.prepare);
    owned = changed.prepare;
    return owned;
  };

  const persistCommitPatch = (patch = {}, reason = 'revalidation_applied') => {
    ensureOwned(['committing']);
    const changed = store.compareAndSwap(owned.id, {
      version: owned.version,
      state: 'committing',
      commit_lease_id: leaseId,
    }, {
      ...clone(patch),
      audit_events: auditPatch(owned, now(), [{ type: 'recovery_polled', reason }]),
    });
    if (!changed.ok) throw stateError(changed.prepare);
    owned = changed.prepare;
    return owned;
  };

  try {
    if (owned.state === 'committing') {
      const checked = await awaitAbortable(
        () => revalidate?.(owned, {
          signal,
          checkpoint: () => ensureOwned(['committing']),
          reportProgress: persistCommitProgress,
          targetKeys: clone(owned.reconfirm_target_keys || []),
        }),
        signal,
      );
      ensureOwned(['committing']);
      const revalidationHistory = revalidationHistoryPatch(owned, checked, now());
      const scopeHashChanged = Boolean(checked?.scope_hash && checked.scope_hash !== owned.scope_hash);
      const requiresReconfirm = checked?.reconfirm_required === true
        || (checked?.reconfirm_required !== false && scopeHashChanged);
      if (requiresReconfirm) {
        transition('failed', {
          revalidation_history: revalidationHistory,
          error: '执行动作或活动结构发生实质变化，本次未执行，请重新核对范围。',
          failure_code: 'PREPARE_STALE',
          commit_lease_id: null,
          commit_lease_owner: null,
          commit_lease_expires_at: null,
          progress: {
            ...(owned.progress || {}), stage: 'failed', percent: 100,
            message: '执行范围发生实质变化，请重新核对范围',
          },
        }, 'scope_changed_reprepare');
        throw submissionError('执行动作或活动结构发生实质变化，本次未执行，请重新核对范围。', 'PREPARE_STALE');
      }
      if (checked?.execution_relation_count === 0) {
        transition('failed', {
          ...(checked.prepared_patch || {}),
          revalidation_history: revalidationHistory,
          error: '最终核对后没有可执行商品，本次未创建执行组。',
          failure_code: 'NO_CONFIRMED_TARGETS',
          commit_lease_id: null,
          commit_lease_owner: null,
          commit_lease_expires_at: null,
          progress: {
            ...(owned.progress || {}), stage: 'failed', percent: 100,
            message: '最终核对后没有可执行商品',
          },
        }, 'confirmed_scope_empty');
        throw submissionError('最终核对后没有可执行商品，本次未创建执行组。', 'NO_CONFIRMED_TARGETS');
      }
      if (checked?.prepared_patch || checked?.revalidation_record) {
        persistCommitPatch({
          ...(checked.prepared_patch || {}),
          revalidation_history: revalidationHistory,
          reconfirm_changes: clone(checked.changes || []),
          reconfirm_target_keys: [],
        }, 'frozen_scope_applied');
      }
    }

    const selected = owned.seller_input?.selected_targets || [];
    if (selected.some((target) => target.detection_status === 'unreadable')) {
      transition('failed', {
        error: '所选店铺站点仍无法确认自建活动状态，本次未创建、未执行。',
        failure_code: 'SELLER_TARGET_UNREADABLE',
        commit_lease_id: null,
        commit_lease_owner: null,
        commit_lease_expires_at: null,
      }, 'seller_target_unreadable');
      throw submissionError('所选店铺站点仍无法确认自建活动状态，本次未创建、未执行。', 'SELLER_TARGET_UNREADABLE');
    }
    if (selected.some((target) => String(target.detection_status || '') !== 'confirmed_absent')) {
      transition('failed', {
        error: '所选店铺站点没有可验证的自建活动不存在结论，本次未创建、未执行。',
        failure_code: 'SELLER_TARGET_NOT_CONFIRMED_ABSENT',
        commit_lease_id: null,
        commit_lease_owner: null,
        commit_lease_expires_at: null,
      }, 'seller_target_not_confirmed_absent');
      throw submissionError('所选店铺站点没有可验证的自建活动不存在结论，本次未创建、未执行。', 'SELLER_TARGET_NOT_CONFIRMED_ABSENT');
    }

    if (selected.length && owned.state === 'committing') {
      const creationRunId = owned.creation_run_id || `seller-campaign-create-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      transition('creating', { creation_run_id: creationRunId, creation_progress: owned.creation_progress || null }, 'seller_creation_started');
      const creation = await awaitAbortable(
        () => createSellerCampaigns?.(owned, {
          signal,
          checkpoint: () => ensureOwned(['creating']),
          persistProgress: persistCreationProgress,
          runId: creationRunId,
        }),
        signal,
      );
      ensureOwned(['creating']);
      if (!creation?.ok || Number(creation.failed_count || 0) > 0 || Number(creation.recheck_missing_count || 0) > 0) {
        transition('failed', {
          error: '自建活动创建失败或回查未发现，商品执行未启动。',
          failure_code: 'SELLER_CREATE_FAILED',
          creation_result: creation || null,
          commit_lease_id: null,
          commit_lease_owner: null,
          commit_lease_expires_at: null,
        }, 'seller_creation_failed');
        throw submissionError('自建活动创建失败或回查未发现，商品执行未启动。', 'SELLER_CREATE_FAILED');
      }
      const { prepared_patch: creationPatch = null, ...creationResult } = creation || {};
      transition('created', {
        ...(creationPatch || {}),
        creation_result: creationResult,
        creation_progress: null,
      }, 'seller_creation_verified');
    } else if (owned.state === 'committing') {
      transition('created', { creation_result: null }, 'no_seller_creation_required');
    }

    if (owned.state === 'created' && owned.creation_result && revalidateAfterCreation) {
      const checked = await awaitAbortable(
        () => revalidateAfterCreation(owned, {
          signal,
          checkpoint: () => ensureOwned(['created']),
          targetKeys: ['__SELLER__'],
        }),
        signal,
      );
      ensureOwned(['created']);
      const revalidationHistory = revalidationHistoryPatch(owned, checked, now());
      const scopeHashChanged = Boolean(checked?.scope_hash && checked.scope_hash !== owned.scope_hash);
      const requiresReconfirm = checked?.reconfirm_required === true
        || (checked?.reconfirm_required !== false && scopeHashChanged);
      const changedTargetKeys = clone(checked?.changed_target_keys || []);
      const expectedSellerCreationChange = requiresReconfirm
        && changedTargetKeys.length > 0
        && changedTargetKeys.every((key) => String(key) === '__SELLER__')
        && checked?.prepared_patch?.confirmed_execution_scope;
      if (requiresReconfirm && !expectedSellerCreationChange) {
        transition('failed', {
          revalidation_history: revalidationHistory,
          error: '新建活动回查结果与已确认目标不一致，本次未执行，请重新核对范围。',
          failure_code: 'PREPARE_STALE',
          commit_lease_id: null,
          commit_lease_owner: null,
          commit_lease_expires_at: null,
        }, 'seller_creation_scope_changed');
        throw submissionError('新建活动回查结果与已确认目标不一致，本次未执行，请重新核对范围。', 'PREPARE_STALE');
      }
      if (checked?.execution_relation_count === 0) {
        transition('failed', {
          ...(checked.prepared_patch || {}),
          revalidation_history: revalidationHistory,
          error: '新建活动回查后没有可执行商品，本次未创建执行组。',
          failure_code: 'NO_CONFIRMED_TARGETS',
          commit_lease_id: null,
          commit_lease_owner: null,
          commit_lease_expires_at: null,
        }, 'confirmed_scope_empty_after_creation');
        throw submissionError('新建活动回查后没有可执行商品，本次未创建执行组。', 'NO_CONFIRMED_TARGETS');
      }
      if (checked?.prepared_patch || checked?.revalidation_record) {
        const changed = store.compareAndSwap(owned.id, {
          version: owned.version,
          state: 'created',
          commit_lease_id: leaseId,
        }, {
          ...(checked.prepared_patch || {}),
          revalidation_history: revalidationHistory,
          reconfirm_changes: clone(checked.changes || []),
          reconfirm_target_keys: [],
        });
        if (!changed.ok) throw stateError(changed.prepare);
        owned = changed.prepare;
      }
    }

    if (owned.state === 'created') transition('starting', {}, 'group_starting');
    ensureOwned(['starting']);
    const group = await awaitAbortable(
      () => startGroup?.(owned, { signal, checkpoint: () => ensureOwned(['starting']) }),
      signal,
    );
    ensureOwned(['starting']);
    if (!group?.id) throw submissionError('执行组未成功建立，请重新准备后提交。', 'GROUP_START_UNCONFIRMED', 503);
    transition('executing', {
      group_id: String(group.id),
      group,
      commit_lease_id: null,
      commit_lease_owner: null,
      commit_lease_expires_at: null,
      progress: { ...(owned.progress || {}), stage: 'executing', message: '执行组已建立，正在处理商品' },
    }, 'group_started');
    return { prepare: owned, group, reused: false };
  } catch (error) {
    const latest = store.load(prepareId);
    if (latest
        && PRE_GROUP_COMMIT_STATES.has(String(latest.state || ''))
        && String(latest.commit_lease_id || '') === leaseId
        && Number(latest.version || 0) === Number(owned.version || 0)) {
      const isAbort = error?.code === 'COMMIT_ABORTED';
      const finalState = isAbort ? 'cancelled' : 'failed';
      store.compareAndSwap(latest.id, {
        version: latest.version,
        state: latest.state,
        commit_lease_id: leaseId,
      }, {
        state: finalState,
        error: isAbort ? null : String(error?.message || '本次提交未完成。'),
        failure_code: isAbort ? null : String(error?.code || 'COMMIT_FAILED'),
        commit_lease_id: null,
        commit_lease_owner: null,
        commit_lease_expires_at: null,
        progress: { ...(latest.progress || {}), stage: finalState, message: isAbort ? '本次提交已停止' : '本次提交未完成' },
        audit_events: auditPatch(latest, now(), [
          ...(isAbort ? [{ type: 'aborted', reason: 'abort_signal' }] : []),
          stateChangedAudit(latest.state, finalState, isAbort ? 'abort_signal' : String(error?.code || 'commit_failed')),
        ]),
      });
    }
    throw error;
  }
}
