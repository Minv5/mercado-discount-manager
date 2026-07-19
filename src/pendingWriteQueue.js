import fs from 'node:fs';
import path from 'node:path';

export function createPendingWriteQueue({ stateDir, now = () => new Date().toISOString() } = {}) {
  if (!stateDir) throw new Error('pending write queue stateDir is required');

  function statePath(jobId) {
    return path.join(stateDir, `${safeId(jobId)}.json`);
  }

  function load(jobId) {
    const target = statePath(jobId);
    if (!fs.existsSync(target)) return { version: 1, job_id: String(jobId), records: {} };
    return JSON.parse(fs.readFileSync(target, 'utf8'));
  }

  function persist(state) {
    fs.mkdirSync(stateDir, { recursive: true });
    state.updated_at = now();
    const target = statePath(state.job_id);
    const temporary = `${target}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(state), 'utf8');
    fs.renameSync(temporary, target);
    return state;
  }

  function enqueue(jobId, record = {}) {
    const relationKey = String(record.relation_key || record.relationKey || '');
    if (!relationKey) throw new Error('pending relation key is required');
    const state = load(jobId);
    const current = state.records[relationKey] || {};
    state.records[relationKey] = {
      ...current,
      ...record,
      relation_key: relationKey,
      state: 'pending',
      attempt_count: Math.max(Number(current.attempt_count || 0), Number(record.attempt_count || 0)),
      first_pending_at: current.first_pending_at || now(),
      updated_at: now(),
    };
    persist(state);
    return state.records[relationKey];
  }

  function resolve(jobId, relationKey, terminalState, details = {}) {
    const state = load(jobId);
    const current = state.records[String(relationKey)];
    if (!current) return null;
    state.records[String(relationKey)] = {
      ...current,
      ...details,
      state: String(terminalState),
      resolved_at: now(),
      updated_at: now(),
    };
    persist(state);
    return state.records[String(relationKey)];
  }

  function pending(jobId, predicate = () => true) {
    return Object.values(load(jobId).records || {})
      .filter((row) => row.state === 'pending' && predicate(row))
      .sort((left, right) => String(left.first_pending_at).localeCompare(String(right.first_pending_at)));
  }

  return { enqueue, load, pending, persist, resolve, statePath };
}

export function pendingRelationKey({ accountId, siteId, promotionId, promotionType, itemId, action } = {}) {
  return [accountId, String(siteId || '').toUpperCase(), promotionId, String(promotionType || '').toUpperCase(), itemId, action]
    .map((value) => String(value || ''))
    .join('|');
}

function safeId(value) {
  return String(value || '').replace(/[^A-Za-z0-9_.-]/g, '');
}
