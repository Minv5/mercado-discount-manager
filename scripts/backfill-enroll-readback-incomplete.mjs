import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

// 一次性回填：把「报名/更新写入请求已成功，但平台回读不完整」误判出的
// pending_verification 冗余行删掉，让每个商品只保留真正的 success 行。
//
// 背景：旧逻辑在报名后回读不完整时，会把已经写成功的商品再补写一条
// pending_verification（并错误地从 success 计数里扣除），导致同一商品同时
// 记成 success 和 pending，UI 上成功数被严重低估。本次回填删除这些冗余行。
//
// 实现：先按 task 定位包含标记行的任务（用 task_id 索引），再在每个 task 内
// 用带成功兄弟校验的相关子查询定位可删行，避免全表 EXISTS 扫描。
//
// 用法：
//   预览（只读，安全）：
//     node scripts/backfill-enroll-readback-incomplete.mjs --data-dir <data目录>
//   执行（需备份目录，且会先校验无运行中的执行状态）：
//     node scripts/backfill-enroll-readback-incomplete.mjs --data-dir <data目录> --backup-dir <备份目录> --apply

const RECOVERY_ID = 'enroll-readback-incomplete-v1-20260817';
const MARKER = '写入请求已成功，但平台回读不完整，未判定生效状态；继续只读确认且不会盲目重写';
const ACTIVE_SUBMISSIONS = new Set(['preparing', 'prepared', 'reconfirm_required', 'committing', 'creating', 'created', 'starting', 'executing']);
const ACTIVE_GROUPS = new Set(['queued', 'running', 'stopping']);
const ACTIVE_JOBS = new Set(['queued', 'running', 'stopping']);

const args = new Map();
for (let index = 2; index < process.argv.length; index += 1) {
  const key = process.argv[index];
  if (!key.startsWith('--')) continue;
  const next = process.argv[index + 1];
  if (next && !next.startsWith('--')) { args.set(key, next); index += 1; }
  else args.set(key, true);
}
const dataDir = path.resolve(String(args.get('--data-dir') || ''));
const backupDir = args.get('--backup-dir') ? path.resolve(String(args.get('--backup-dir'))) : '';
const apply = args.has('--apply');
if (!dataDir) throw new Error('--data-dir is required');
if (apply && !backupDir) throw new Error('--backup-dir is required with --apply');

function readJson(target) { return JSON.parse(fs.readFileSync(target, 'utf8')); }
function sha256(target) { return crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex').toUpperCase(); }
function listJson(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((name) => name.endsWith('.json')).map((name) => path.join(dir, name));
}
function copyFileExact(source, destination) {
  try {
    fs.copyFileSync(source, destination);
  } catch (error) {
    if (error?.code !== 'EBUSY') throw error;
    fs.writeFileSync(destination, fs.readFileSync(source));
  }
  if (fs.statSync(source).size !== fs.statSync(destination).size || sha256(source) !== sha256(destination)) {
    throw new Error(`backup copy verification failed: ${path.basename(source)}`);
  }
}

function assertNoActive() {
  const checks = [
    ['submission', 'execution-submissions', ACTIVE_SUBMISSIONS, 'state'],
    ['group', 'execution-group-states', ACTIVE_GROUPS, 'status'],
    ['job', 'execution-job-states', ACTIVE_JOBS, 'status'],
  ];
  const active = [];
  for (const [kind, dir, states, field] of checks) {
    for (const target of listJson(path.join(dataDir, dir))) {
      let record;
      try { record = readJson(target); } catch { continue; }
      if (states.has(String(record[field] || ''))) active.push({ kind, id: record.id, state: record[field] });
    }
  }
  if (active.length) throw new Error(`active execution state blocks backfill: ${JSON.stringify(active)}`);
}

const dbPath = path.join(dataDir, 'discount-manager.sqlite');

// 定位包含标记行的 task（错误文案无索引，此查询走一次顺序扫描，但只投影 task_id/action）。
function collectTaskIds(db) {
  return db.prepare(
    `SELECT DISTINCT task_id, action
       FROM promo_action_results
      WHERE status = 'pending_verification' AND error_cn = ?
      ORDER BY task_id`
  ).all(MARKER);
}

// 单个 task 内的可删行 id（带成功兄弟校验）。
function removableIdsForTask(db, taskId) {
  return db.prepare(
    `SELECT p.id
       FROM promo_action_results p
      WHERE p.task_id = ?
        AND p.status = 'pending_verification'
        AND p.error_cn = ?
        AND EXISTS (
          SELECT 1 FROM promo_action_results s
           WHERE s.task_id = p.task_id
             AND s.status = 'success'
             AND s.account_id = p.account_id
             AND s.promotion_id = p.promotion_id
             AND s.promotion_type = p.promotion_type
             AND s.item_id = p.item_id
             AND s.action = p.action
        )
      ORDER BY p.id`
  ).all(Number(taskId), MARKER).map((row) => Number(row.id));
}

function collectPlan(db) {
  const tasks = collectTaskIds(db);
  const byTask = [];
  let removable = 0;
  for (const task of tasks) {
    const ids = removableIdsForTask(db, task.task_id);
    removable += ids.length;
    byTask.push({ task_id: Number(task.task_id), action: task.action, removable: ids.length });
  }
  const markerTotal = tasks.reduce((sum, task) => sum + Number(db.prepare(
    `SELECT COUNT(*) AS c FROM promo_action_results
      WHERE task_id = ? AND status = 'pending_verification' AND error_cn = ?`
  ).get(Number(task.task_id), MARKER).c || 0), 0);
  return {
    marker_total: markerTotal,
    removable,
    without_sibling: markerTotal - removable,
    task_ids: tasks.map((task) => Number(task.task_id)),
    by_task: byTask,
  };
}

// ---------- 预览 ----------
{
  const db = new DatabaseSync(dbPath, { readOnly: true });
  const plan = collectPlan(db);
  db.close();
  const preview = {
    ok: true,
    applied: false,
    recovery_id: RECOVERY_ID,
    db_path: dbPath,
    marker: MARKER,
    ...plan,
    note: '这些 pending_verification 行都有同关系的 success 兄弟行，属于旧逻辑重复写出的脏数据，删除后每个商品只保留 success。',
  };
  if (!apply) {
    console.log(JSON.stringify(preview, null, 2));
    process.exit(0);
  }
  if (!plan.removable) {
    console.log(JSON.stringify({ ...preview, applied: false, reason: 'nothing_to_remove' }, null, 2));
    process.exit(0);
  }
}

// ---------- 执行 ----------
assertNoActive();
const sqliteFiles = ['discount-manager.sqlite', 'discount-manager.sqlite-wal', 'discount-manager.sqlite-shm'];
const backupManifest = { recovery_id: RECOVERY_ID, created_at: new Date().toISOString(), files: [] };
fs.mkdirSync(backupDir, { recursive: false });
for (const name of sqliteFiles) {
  const source = path.join(dataDir, name);
  if (!fs.existsSync(source)) continue;
  const destination = path.join(backupDir, name);
  copyFileExact(source, destination);
  backupManifest.files.push({ source, backup: destination, length: fs.statSync(destination).size, sha256: sha256(destination) });
}
fs.writeFileSync(path.join(backupDir, 'backup-manifest.json'), JSON.stringify(backupManifest, null, 2), 'utf8');

const backupDb = new DatabaseSync(path.join(backupDir, 'discount-manager.sqlite'), { readOnly: true });
const backupIntegrity = backupDb.prepare('PRAGMA integrity_check').get();
backupDb.close();
if (String(backupIntegrity.integrity_check || '').toLowerCase() !== 'ok') {
  throw new Error('SQLite backup integrity check failed');
}

let db = new DatabaseSync(dbPath);
db.exec('PRAGMA busy_timeout = 5000');
let deleted = 0;
let appliedAt = '';
try {
  const tasks = collectTaskIds(db);
  const perTask = [];
  db.exec('BEGIN IMMEDIATE');
  for (const task of tasks) {
    const ids = removableIdsForTask(db, task.task_id);
    if (!ids.length) continue;
    const placeholders = ids.map(() => '?').join(',');
    const result = db.prepare(`DELETE FROM promo_action_results WHERE id IN (${placeholders})`).run(...ids);
    deleted += Number(result.changes || 0);
    perTask.push({ task_id: Number(task.task_id), deleted: Number(result.changes || 0) });
  }
  appliedAt = new Date().toISOString();
  db.exec('COMMIT');
  db.close();
  db = null;

  // 用仓库层重算受影响任务的 summary，并重新物化历史汇总。
  let recount = null;
  process.env.MDM_DB_PATH = dbPath;
  process.env.MDM_DATA_DIR = dataDir;
  const repository = await import('../src/repository.js');
  recount = repository.recountTaskResultCounts(perTask.map((entry) => entry.task_id));
  const databaseModule = await import('../src/db.js');
  databaseModule.closeDb();

  const verifyDb = new DatabaseSync(dbPath, { readOnly: true });
  const integrity = verifyDb.prepare('PRAGMA integrity_check').get();
  const remaining = verifyDb.prepare(
    `SELECT COUNT(*) AS c FROM promo_action_results
      WHERE status = 'pending_verification' AND error_cn = ?`
  ).get(MARKER);
  verifyDb.close();
  if (String(integrity.integrity_check || '').toLowerCase() !== 'ok') {
    throw new Error('SQLite integrity check failed after backfill');
  }

  const report = {
    ok: true,
    applied: true,
    applied_at: appliedAt,
    recovery_id: RECOVERY_ID,
    deleted,
    per_task: perTask,
    remaining_marker_rows: Number(remaining.c || 0),
    recount,
    backup_dir: backupDir,
    backup_integrity: 'ok',
    sqlite_integrity: 'ok',
  };
  fs.writeFileSync(path.join(backupDir, 'backfill-report.json'), JSON.stringify(report, null, 2), 'utf8');
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  try { db?.exec('ROLLBACK'); } catch {}
  try { db?.close(); } catch {}
  throw error;
}
