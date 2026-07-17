import fs from 'node:fs';
import path from 'node:path';

function safeJobId(jobId) {
  return String(jobId || '').replace(/[^A-Za-z0-9_.-]/g, '');
}

export function createExecutionJobPersistence({ stateDir, publicJob, currentPid = process.pid, now = () => new Date().toISOString() }) {
  function statePath(jobId) {
    return path.join(stateDir, `${safeJobId(jobId)}.json`);
  }

  function persist(job) {
    if (!job?.id) return;
    fs.mkdirSync(stateDir, { recursive: true });
    const target = statePath(job.id);
    const temporary = `${target}.${currentPid}.tmp`;
    const snapshot = { ...publicJob(job), process_pid: currentPid, persisted_at: now() };
    fs.writeFileSync(temporary, JSON.stringify(snapshot), 'utf8');
    fs.renameSync(temporary, target);
  }

  function load(jobId) {
    const target = statePath(jobId);
    if (!fs.existsSync(target)) return null;
    try {
      const job = JSON.parse(fs.readFileSync(target, 'utf8'));
      if (!job || String(job.id || '') !== String(jobId || '')) return null;
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
    } catch {
      return null;
    }
  }

  return { load, persist, statePath };
}
