import fs from 'node:fs';
import path from 'node:path';
import { writeJsonFileAtomicallySync } from './processInstanceLock.js';

function safeJobId(jobId) {
  return String(jobId || '').replace(/[^A-Za-z0-9_.-]/g, '');
}

function persistenceReadError(kind, jobId, target, cause = null) {
  const code = `EXECUTION_JOB_STATE_${String(kind || 'UNKNOWN').toUpperCase()}`;
  const error = new Error(`执行任务状态${kind === 'corrupt' ? '已损坏' : kind === 'unreadable' ? '无法读取' : '身份不匹配'}，已停止以保护任务状态。`, cause ? { cause } : undefined);
  error.code = code;
  error.state_kind = 'execution_job';
  error.state_id = String(jobId || '');
  error.state_path = target;
  error.read_status = kind;
  return error;
}

export function createExecutionJobPersistence({
  stateDir,
  publicJob,
  currentPid = process.pid,
  now = () => new Date().toISOString(),
  fsImpl = fs,
  retryDelaysMs,
  sleepSync,
}) {
  function statePath(jobId) {
    return path.join(stateDir, `${safeJobId(jobId)}.json`);
  }

  function persist(job) {
    if (!job?.id) return;
    const target = statePath(job.id);
    const snapshot = {
      ...publicJob(job),
      request: job.request || null,
      batch_task_id: job.batch_task_id || null,
      process_pid: currentPid,
      persisted_at: now(),
    };
    writeJsonFileAtomicallySync({
      target,
      value: snapshot,
      currentPid,
      fsImpl,
      retryDelaysMs,
      sleepSync,
    });
  }

  function inspect(jobId) {
    const target = statePath(jobId);
    let text;
    try {
      text = fsImpl.readFileSync(target, 'utf8');
    } catch (error) {
      if (String(error?.code || '') === 'ENOENT') return { status: 'missing', value: null, path: target };
      return { status: 'unreadable', value: null, path: target, error };
    }
    let job;
    try {
      job = JSON.parse(text);
    } catch (error) {
      return { status: 'corrupt', value: null, path: target, error };
    }
    if (!job || typeof job !== 'object') return { status: 'corrupt', value: null, path: target };
    if (String(job.id || '') !== String(jobId || '')) {
      return { status: 'identity_mismatch', value: null, path: target };
    }
    return { status: 'ok', value: job, path: target };
  }

  function load(jobId) {
    const inspected = inspect(jobId);
    if (inspected.status === 'missing') return null;
    if (inspected.status !== 'ok') {
      throw persistenceReadError(inspected.status, jobId, inspected.path, inspected.error);
    }
    const job = inspected.value;
    if (['queued', 'running', 'stopping'].includes(String(job.status || '')) && Number(job.process_pid || 0) !== currentPid) {
      job.status = 'interrupted';
      job.finished_at = job.finished_at || now();
      job.error = '程序重启，任务已中断；已完成结果保留，请查看历史详情。';
      job.progress = { ...(job.progress || {}), stage: 'interrupted', recovered_after_restart: true };
      job.logs = Array.isArray(job.logs) ? job.logs : [];
      job.userLogs = Array.isArray(job.userLogs) ? job.userLogs : [];
      job.userLogs.push({ at: now(), message: job.error });
      persist(job);
    }
    return job;
  }

  return { inspect, load, persist, statePath };
}
