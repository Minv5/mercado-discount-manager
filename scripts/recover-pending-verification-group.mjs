import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { summarizeExecutionGroup } from '../src/executionGroupPersistence.js';

const args = new Map();
for (let index = 2; index < process.argv.length; index += 1) {
  const key = process.argv[index];
  if (!key.startsWith('--')) throw new Error(`未知参数：${key}`);
  if (key === '--apply') args.set(key, true);
  else args.set(key, process.argv[++index]);
}

const groupId = String(args.get('--group') || '').trim();
if (!groupId) throw new Error('必须提供 --group');
const apply = args.get('--apply') === true;
const verificationAttempts = Number(args.get('--verification-attempts') || 0);
if (!Number.isInteger(verificationAttempts) || verificationAttempts < 0 || verificationAttempts > 2) {
  throw new Error('--verification-attempts 必须为 0..2 的整数');
}
const dataDir = path.resolve(String(args.get('--data-dir') || path.join(process.env.LOCALAPPDATA || '', 'MercadoDiscountManagerStandalone', 'data')));
const groupPath = path.join(dataDir, 'execution-group-states', `${safeId(groupId)}.json`);
const dbPath = path.join(dataDir, 'discount-manager.sqlite');
const group = readJson(groupPath);
if (String(group.id || '') !== groupId) throw new Error('执行组文件身份不匹配');
if (String(group.status || '') !== 'failed') throw new Error(`目标执行组必须为 failed，当前=${group.status}`);

assertNoOtherActiveState(dataDir, groupId);

const jobs = (group.children || []).map((child) => {
  const jobPath = path.join(dataDir, 'execution-job-states', `${safeId(child.job_id)}.json`);
  const job = readJson(jobPath);
  if (String(job.status || '') !== 'failed') throw new Error(`目标 child 必须为 failed：${job.id}=${job.status}`);
  if (!job.request?.resumePendingOnly) throw new Error(`目标 child 未标记 resumePendingOnly：${job.id}`);
  return { path: jobPath, job };
});
if (!jobs.length) throw new Error('目标执行组没有 child job');

const promotionByTask = new Map();
for (const { job } of jobs) {
  for (const promotion of job.result?.execution?.promotions || []) {
    const taskId = Number(promotion.taskId || 0);
    if (!taskId) continue;
    promotionByTask.set(taskId, { job, promotion });
  }
}
const taskIds = [...promotionByTask.keys()];
if (!taskIds.length) throw new Error('目标执行组没有活动 task_id');

const database = new DatabaseSync(dbPath, { readOnly: true });
const placeholders = taskIds.map(() => '?').join(',');
const latestRows = database.prepare(`
  WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY task_id, promotion_id, promotion_type, item_id, action
      ORDER BY id DESC
    ) AS rn
    FROM promo_action_results
    WHERE task_id IN (${placeholders})
      AND TRIM(COALESCE(item_id, '')) <> ''
  )
  SELECT task_id, account_id, promotion_id, promotion_type, item_id, action, deal_price,
         status, error_cn, created_at
  FROM ranked
  WHERE rn = 1
  ORDER BY account_id, task_id, item_id
`).all(...taskIds);
database.close();
const isPlatformPending = (row) => (
  String(row.status || '') === 'pending_verification'
  && String(row.error_cn || '').startsWith('平台已明确返回 pending（待生效）')
);
const pendingRows = latestRows.filter((row) => (
  String(row.status || '') === 'pending_verification' && !isPlatformPending(row)
));
const isRecoveredPriceMismatch = (row) => (
  String(row.status || '') === 'failed'
  && String(row.error_cn || '').startsWith('商品已进入活动，但平台活动价 ')
  && String(row.error_cn || '').endsWith('；本次不重复报名')
);
const isVerificationExhausted = (row) => (
  String(row.status || '') === 'failed'
  && String(row.error_cn || '').startsWith('写入接口已成功，但连续 ')
  && String(row.error_cn || '').endsWith('；已停止回查且不会重复提交')
);

const queues = new Map(jobs.map(({ job }) => [String(job.id), {
  version: 1,
  job_id: String(job.id),
  records: {},
} ]));
const countsByJob = new Map(jobs.map(({ job }) => [String(job.id), 0]));
const terminalizedByJob = new Map(jobs.map(({ job }) => [String(job.id), 0]));
const platformPendingByJob = new Map(jobs.map(({ job }) => [String(job.id), 0]));
const failedTerminalizedByJob = new Map(jobs.map(({ job }) => [String(job.id), 0]));
const pendingByTask = new Map();
const platformPendingByTask = new Map();
const failedTerminalizedByTask = new Map();
for (const row of pendingRows) {
  const mapped = promotionByTask.get(Number(row.task_id));
  if (!mapped) throw new Error(`pending task 不属于目标组：${row.task_id}`);
  const { job, promotion } = mapped;
  const record = {
    account_id: String(row.account_id || ''),
    child_user_id: String(promotion.child_user_id || ''),
    site_id: String(promotion.site_id || '').toUpperCase(),
    promotion_id: String(row.promotion_id || ''),
    promotion_type: String(row.promotion_type || '').toUpperCase(),
    item_id: String(row.item_id || ''),
    action: String(row.action || ''),
    task_id: Number(row.task_id),
    row: { status: 'planned', item: { item_id: String(row.item_id || '') }, deal_price: row.deal_price ?? null },
    retry_category: 'pending_verification',
    error_cn: '历史写入请求已成功，等待平台实时状态只读确认',
  };
  assertConfirmedRelation(job.request, record);
  const relationKey = [record.account_id, record.site_id, record.promotion_id, record.promotion_type, record.item_id, record.action].join('|');
  const now = new Date().toISOString();
  queues.get(String(job.id)).records[relationKey] = {
    ...record,
    relation_key: relationKey,
    state: 'pending',
    attempt_count: 1,
    verification_attempt_count: verificationAttempts,
    first_pending_at: row.created_at || now,
    updated_at: now,
  };
  countsByJob.set(String(job.id), Number(countsByJob.get(String(job.id)) || 0) + 1);
  pendingByTask.set(Number(row.task_id), Number(pendingByTask.get(Number(row.task_id)) || 0) + 1);
}
for (const row of latestRows.filter((value) => (
  isRecoveredPriceMismatch(value) || isPlatformPending(value) || isVerificationExhausted(value)
))) {
  const mapped = promotionByTask.get(Number(row.task_id));
  if (!mapped) throw new Error(`已终态关系不属于目标组：${row.task_id}`);
  const jobId = String(mapped.job.id);
  terminalizedByJob.set(jobId, Number(terminalizedByJob.get(jobId) || 0) + 1);
  if (isPlatformPending(row)) {
    platformPendingByJob.set(jobId, Number(platformPendingByJob.get(jobId) || 0) + 1);
    platformPendingByTask.set(Number(row.task_id), Number(platformPendingByTask.get(Number(row.task_id)) || 0) + 1);
  } else {
    failedTerminalizedByJob.set(jobId, Number(failedTerminalizedByJob.get(jobId) || 0) + 1);
    failedTerminalizedByTask.set(Number(row.task_id), Number(failedTerminalizedByTask.get(Number(row.task_id)) || 0) + 1);
  }
}

for (const { job } of jobs) {
  const expected = Number(job.result?.execution?.pending || 0);
  const pending = Number(countsByJob.get(String(job.id)) || 0);
  const terminalized = Number(terminalizedByJob.get(String(job.id)) || 0);
  if (pending + terminalized !== expected) {
    throw new Error(`pending 数量不一致：${job.id} expected=${expected} pending=${pending} terminalized=${terminalized}`);
  }
}

const totalExpected = jobs.reduce((sum, { job }) => sum + Number(job.result?.execution?.pending || 0), 0);
const totalTerminalized = [...terminalizedByJob.values()].reduce((sum, value) => sum + Number(value || 0), 0);
if (pendingRows.length + totalTerminalized !== totalExpected) {
  throw new Error(`组 pending 数量不一致：expected=${totalExpected} pending=${pendingRows.length} terminalized=${totalTerminalized}`);
}

const preview = {
  ok: true,
  mode: apply ? 'apply' : 'dry_run',
  group_id: groupId,
  data_dir: dataDir,
  pending_verification_count: pendingRows.length,
  already_terminalized_count: totalTerminalized,
  by_job: Object.fromEntries(countsByJob),
  terminalized_by_job: Object.fromEntries(terminalizedByJob),
  platform_pending_by_job: Object.fromEntries(platformPendingByJob),
  failed_terminalized_by_job: Object.fromEntries(failedTerminalizedByJob),
  repeated_write_requests: 0,
  verification_attempts_before_recovery: verificationAttempts,
  scope_validation: 'passed',
};
if (!apply) {
  process.stdout.write(`${JSON.stringify(preview, null, 2)}\n`);
  process.exit(0);
}

const stamp = localTimestamp();
const backupDir = path.join(os.homedir(), 'Documents', '美客多折扣管家', 'release-backups', `pending-verification-recovery-${stamp}`);
fs.mkdirSync(backupDir, { recursive: true });
const backupTargets = [groupPath, ...jobs.map((entry) => entry.path), dbPath, `${dbPath}-wal`, `${dbPath}-shm`]
  .filter((target) => fs.existsSync(target));
for (const source of backupTargets) fs.copyFileSync(source, path.join(backupDir, path.basename(source)));

const now = new Date().toISOString();
for (const { path: jobPath, job } of jobs) {
  const jobId = String(job.id);
  const pendingCount = Number(countsByJob.get(jobId) || 0);
  const platformPendingCount = Number(platformPendingByJob.get(jobId) || 0);
  const failedTerminalizedCount = Number(failedTerminalizedByJob.get(jobId) || 0);
  const execution = job.result?.execution;
  if (!execution) throw new Error(`child 缺少原执行结果：${job.id}`);
  execution.failed = Number(execution.failed || 0) + failedTerminalizedCount;
  execution.pending = pendingCount;
  execution.pending_verification_count = pendingCount;
  execution.platform_pending_count = platformPendingCount;
  execution.retryable_pending_count = pendingCount;
  execution.promotions = (execution.promotions || []).map((promotion) => {
    const taskId = Number(promotion.taskId || 0);
    return {
      ...promotion,
      failed: Number(promotion.failed || 0) + Number(failedTerminalizedByTask.get(taskId) || 0),
      pending: Number(pendingByTask.get(taskId) || 0),
      pending_verification_count: Number(pendingByTask.get(taskId) || 0),
      platform_pending_count: Number(platformPendingByTask.get(taskId) || 0),
      retryable_pending_count: Number(pendingByTask.get(taskId) || 0),
    };
  });
  job.status = pendingCount > 0 ? 'paused' : 'completed';
  job.error = null;
  job.finished_at = pendingCount > 0 ? null : now;
  job.process_pid = 0;
  job.persisted_at = now;
  job.request.resumePendingOnly = pendingCount > 0;
  job.progress = { ...(job.progress || {}), stage: job.status, pending_relations: pendingCount, recovery_mode: 'read_only_verification' };
  job.logs = Array.isArray(job.logs) ? job.logs : [];
  job.userLogs = Array.isArray(job.userLogs) ? job.userLogs : [];
  job.logs.push({ at: now, message: `本地恢复：重建 ${pendingCount} 条只读确认关系，并入 ${platformPendingCount + failedTerminalizedCount} 条已终态关系；禁止重复写入。` });
  job.userLogs.push({ at: now, message: pendingCount > 0
    ? `已恢复 ${pendingCount} 条待平台确认关系，只做状态回查，不会重复提交。`
    : `待平台确认关系已全部终态化：平台待生效 ${platformPendingCount} 条，失败 ${failedTerminalizedCount} 条，未重复提交。` });
  writeJsonAtomic(jobPath, job);
  const queue = queues.get(String(job.id));
  queue.updated_at = now;
  writeJsonAtomic(path.join(dataDir, 'pending-write-queues', `${safeId(job.id)}.json`), queue);
}

const hasPendingRecovery = [...countsByJob.values()].some((count) => Number(count || 0) > 0);
group.status = hasPendingRecovery ? 'paused' : 'completed';
group.error = null;
group.finished_at = hasPendingRecovery ? null : now;
group.process_pid = 0;
group.persisted_at = now;
group.pending_since = hasPendingRecovery ? now : null;
group.pending_retry_round = 0;
group.next_retry_at = null;
group.recovered_pending_after_restart = false;
group.children = (group.children || []).map((child) => {
  const matched = jobs.find(({ job }) => String(job.id) === String(child.job_id));
  if (!matched) return child;
  const pendingCount = Number(countsByJob.get(String(child.job_id)) || 0);
  return {
    ...child,
    status: matched.job.status,
    error: null,
    finished_at: matched.job.finished_at,
    progress: { ...(child.progress || {}), stage: matched.job.status, pending_relations: pendingCount, recovery_mode: 'read_only_verification' },
    result: matched.job.result,
  };
});
group.result = summarizeExecutionGroup(group);
writeJsonAtomic(groupPath, group);

const writableDatabase = new DatabaseSync(dbPath);
try {
  writableDatabase.exec('BEGIN IMMEDIATE');
  const selectBatch = writableDatabase.prepare(`
    SELECT id, summary_json
    FROM promo_tasks
    WHERE execution_group_id = ? AND account_id = ? AND promotion_id = '__BATCH__'
    ORDER BY id DESC
  `);
  const updateBatch = writableDatabase.prepare(`
    UPDATE promo_tasks
    SET status = ?, total_count = ?, success_count = ?, failed_count = ?, skipped_count = ?,
        completed = ?, summary_json = ?, updated_at = ?
    WHERE id = ?
  `);
  for (const { job } of jobs) {
    const execution = job.result.execution;
    const matches = selectBatch.all(groupId, String(job.request?.accountId || job.request_summary?.accountId || ''));
    if (matches.length !== 1) throw new Error(`child 批次历史数量异常：${job.id}=${matches.length}`);
    const task = matches[0];
    const previousSummary = JSON.parse(task.summary_json || '{}');
    const completed = Number(execution.failed || 0) === 0
      && Number(execution.pending || 0) === 0
      && Number(execution.activity_failure_count || 0) === 0;
    const summary = {
      ...previousSummary,
      success: Number(execution.success || 0),
      failed: Number(execution.failed || 0),
      skipped: Number(execution.skipped || 0),
      pending: Number(execution.pending || 0),
      total: Number(execution.total || 0),
      relation_count: Number(execution.relation_count || execution.total || 0),
      unique_item_count: Number(execution.unique_item_count || 0),
      activity_failure_count: Number(execution.activity_failure_count || 0),
      request_success_count: Number(execution.request_success_count || 0),
      live_verified_removed_count: Number(execution.live_verified_removed_count || 0),
      pending_verification_count: Number(execution.pending_verification_count || 0),
      platform_pending_count: Number(execution.platform_pending_count || 0),
      retryable_pending_count: Number(execution.retryable_pending_count || 0),
    };
    updateBatch.run(
      completed ? 'completed' : 'partial_or_failed',
      summary.total,
      summary.success,
      summary.failed,
      summary.skipped,
      completed ? 1 : 0,
      JSON.stringify(summary),
      now,
      Number(task.id),
    );
  }
  writableDatabase.exec('COMMIT');
} catch (error) {
  try { writableDatabase.exec('ROLLBACK'); } catch {}
  throw error;
} finally {
  writableDatabase.close();
}
process.env.MDM_DATA_DIR = dataDir;
const { publishHistorySummaryForExecutionGroup } = await import('../src/repository.js');
const historyPublish = publishHistorySummaryForExecutionGroup(groupId);

const report = {
  ...preview,
  backup_dir: backupDir,
  applied_at: now,
  backup_files: backupTargets.map((target) => ({ name: path.basename(target), sha256: sha256(target) })),
  queue_files: jobs.map(({ job }) => ({ job_id: job.id, count: countsByJob.get(String(job.id)) })),
  history_publish: historyPublish,
};
fs.writeFileSync(path.join(backupDir, 'recovery-report.json'), JSON.stringify(report, null, 2), 'utf8');
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);

function assertConfirmedRelation(request, record) {
  const activities = request?.confirmedExecutionScope?.activities || request?.confirmed_execution_scope?.activities || [];
  const match = activities.find((activity) => (
    String(activity.account_id ?? activity.accountId ?? '') === record.account_id
    && String(activity.child_user_id ?? activity.childUserId ?? '') === record.child_user_id
    && String(activity.site_id ?? activity.siteId ?? '').toUpperCase() === record.site_id
    && String(activity.promotion_id ?? activity.promotionId ?? '') === record.promotion_id
    && String(activity.promotion_type ?? activity.promotionType ?? '').toUpperCase() === record.promotion_type
    && (activity.item_ids ?? activity.itemIds ?? []).map((value) => String(value).toUpperCase()).includes(record.item_id.toUpperCase())
  ));
  if (!match || String(request.action || '').toLowerCase() !== record.action.toLowerCase()) {
    throw new Error(`pending 关系超出原确认范围：${record.account_id}|${record.child_user_id}|${record.site_id}|${record.promotion_id}|${record.item_id}`);
  }
}

function assertNoOtherActiveState(root, targetGroupId) {
  const activeGroup = new Set(['queued', 'running', 'stopping', 'paused']);
  const activeJob = new Set(['queued', 'running', 'stopping', 'paused']);
  const activeSubmission = new Set(['preparing', 'prepared', 'committing', 'creating', 'created', 'starting', 'executing']);
  for (const [dir, active, idField] of [
    ['execution-group-states', activeGroup, 'id'],
    ['execution-job-states', activeJob, 'id'],
    ['execution-submissions', activeSubmission, 'id'],
  ]) {
    const target = path.join(root, dir);
    if (!fs.existsSync(target)) continue;
    for (const file of fs.readdirSync(target).filter((name) => name.endsWith('.json'))) {
      const value = readJson(path.join(target, file));
      if (!active.has(String(value.status || value.state || ''))) continue;
      if (dir === 'execution-group-states' && String(value[idField] || '') === targetGroupId) continue;
      throw new Error(`存在其它 active 状态，停止本地恢复：${dir}/${file}`);
    }
  }
}

function readJson(target) {
  return JSON.parse(fs.readFileSync(target, 'utf8'));
}

function writeJsonAtomic(target, value) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value), 'utf8');
  fs.renameSync(temporary, target);
}

function safeId(value) {
  return String(value || '').replace(/[^A-Za-z0-9_.-]/g, '');
}

function sha256(target) {
  return crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex').toUpperCase();
}

function localTimestamp() {
  const date = new Date();
  const parts = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).formatToParts(date).reduce((output, part) => ({ ...output, [part.type]: part.value }), {});
  return `${parts.year}${parts.month}${parts.day}-${parts.hour}${parts.minute}${parts.second}`;
}
