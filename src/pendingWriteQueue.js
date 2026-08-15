import fs from 'node:fs';
import path from 'node:path';
import { writeJsonFileAtomicallySync } from './processInstanceLock.js';

export function createPendingWriteQueue({
  stateDir,
  now = () => new Date().toISOString(),
  currentPid = process.pid,
  fsImpl = fs,
  retryDelaysMs,
  sleepSync,
  flushDelayMs = 1500,
  onFlushError = null,
} = {}) {
  if (!stateDir) throw new Error('pending write queue stateDir is required');

  const stateCache = new Map();

  function statePath(jobId) {
    return path.join(stateDir, `${safeId(jobId)}.json`);
  }

  function readFromDisk(jobId) {
    const target = statePath(jobId);
    try {
      return JSON.parse(fsImpl.readFileSync(target, 'utf8'));
    } catch (error) {
      if (String(error?.code || '') === 'ENOENT') return { version: 1, job_id: String(jobId), records: {} };
      throw error;
    }
  }

  function cached(jobId) {
    const key = String(jobId);
    let entry = stateCache.get(key);
    if (!entry) {
      entry = { state: readFromDisk(jobId), dirty: false, timer: null };
      stateCache.set(key, entry);
    }
    return entry;
  }

  function scheduleFlush(jobId) {
    const key = String(jobId);
    const entry = stateCache.get(key);
    if (!entry || !entry.dirty || entry.timer) return;
    entry.timer = setTimeout(() => {
      entry.timer = null;
      flushNow(key);
    }, flushDelayMs);
    entry.timer.unref?.();
  }

  function shouldFlushImmediately(state) {
    return Object.keys(state?.records || {}).length <= 200;
  }

  function flushNow(jobId) {
    const key = String(jobId);
    const entry = stateCache.get(key);
    if (!entry) return null;
    if (!entry.dirty) return entry.state;
    entry.state.updated_at = now();
    try {
      writeJsonFileAtomicallySync({
        target: statePath(key),
        value: entry.state,
        currentPid,
        fsImpl,
        retryDelaysMs,
        sleepSync,
      });
      entry.dirty = false;
      entry.last_flush_error = null;
    } catch (error) {
      entry.last_flush_error = error?.message || String(error);
      try { onFlushError?.(key, error); } catch {}
      scheduleFlush(key);
    }
    return entry.state;
  }

  function load(jobId) {
    return cached(jobId).state;
  }

  function persist(state) {
    const key = String(state.job_id || '');
    const entry = cached(key);
    entry.state = state;
    entry.dirty = true;
    return flushNow(key);
  }

  function enqueue(jobId, record = {}) {
    const relationKey = String(record.relation_key || record.relationKey || '');
    if (!relationKey) throw new Error('pending relation key is required');
    const key = String(jobId);
    const entry = cached(key);
    const state = entry.state;
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
    entry.dirty = true;
    if (shouldFlushImmediately(state)) flushNow(key);
    else scheduleFlush(key);
    return state.records[relationKey];
  }

  function resolve(jobId, relationKey, terminalState, details = {}) {
    const key = String(jobId);
    const entry = cached(key);
    const state = entry.state;
    const current = state.records[String(relationKey)];
    if (!current) return null;
    state.records[String(relationKey)] = {
      ...current,
      ...details,
      state: String(terminalState),
      resolved_at: now(),
      updated_at: now(),
    };
    entry.dirty = true;
    if (shouldFlushImmediately(state)) flushNow(key);
    else scheduleFlush(key);
    return state.records[String(relationKey)];
  }

  function pending(jobId, predicate = () => true) {
    return Object.values(load(jobId).records || {})
      .filter((row) => row.state === 'pending' && predicate(row))
      .sort((left, right) => String(left.first_pending_at).localeCompare(String(right.first_pending_at)));
  }

  function flush(jobId) {
    const key = String(jobId);
    const entry = stateCache.get(key);
    if (entry?.timer) {
      clearTimeout(entry.timer);
      entry.timer = null;
    }
    return flushNow(key);
  }

  function flushAll() {
    const flushed = [];
    for (const key of [...stateCache.keys()]) {
      const entry = stateCache.get(key);
      if (entry?.timer) {
        clearTimeout(entry.timer);
        entry.timer = null;
      }
      if (entry?.dirty) {
        flushNow(key);
        flushed.push(key);
      }
    }
    return flushed;
  }

  return { enqueue, flush, flushAll, load, pending, persist, resolve, statePath };
}

export function pendingRelationKey({ accountId, siteId, promotionId, promotionType, itemId, action } = {}) {
  return [accountId, String(siteId || '').toUpperCase(), promotionId, String(promotionType || '').toUpperCase(), itemId, action]
    .map((value) => String(value || ''))
    .join('|');
}

function safeId(value) {
  return String(value || '').replace(/[^A-Za-z0-9_.-]/g, '');
}
