import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

const TARGET_GROUP_ID = 'exec-group-20260720070702663-24128-1';
const RECOVERY_ID = 'cancel-result-dual-accounting-v1-20260720';
const EXPECTED = Object.freeze({ actual: 28611, confirmed: 28282, exception: 329 });
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
function writeJsonAtomic(target, value) {
  const temporary = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value), 'utf8');
  fs.renameSync(temporary, target);
}
function sha256(target) {
  return crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex').toUpperCase();
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
function listJson(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((name) => name.endsWith('.json')).map((name) => path.join(dir, name));
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
  if (active.length) throw new Error(`active execution state blocks recovery: ${JSON.stringify(active)}`);
}
function relationKey(value = {}) {
  return [
    value.account_id ?? value.accountId ?? '',
    value.site_id ?? value.siteId ?? '',
    value.promotion_id ?? value.promotionId ?? '',
    value.promotion_type ?? value.promotionType ?? '',
    value.item_id ?? value.itemId ?? '',
  ].map(String).join('|');
}
function emptyCounts() {
  return {
    relation_count: 0, unique_item_count: 0, activity_failure_count: 0,
    request_success_count: 0, live_verified_removed_count: 0, pending_verification_count: 0,
    success: 0, failed: 0, skipped: 0, pending: 0,
  };
}
function summarize(items) {
  const counts = emptyCounts();
  const uniqueItems = new Set();
  for (const item of items) {
    counts.relation_count += 1;
    uniqueItems.add(`${item.accountId}|${item.itemId}`);
    if (item.sent_to_api === true) counts.request_success_count += 1;
    const status = String(item.status || item.result_status || '').toLowerCase();
    if (status === 'live_verified_removed') {
      counts.success += 1;
      counts.live_verified_removed_count += 1;
    } else if (status === 'pending_verification') {
      counts.skipped += 1;
      counts.pending += 1;
      counts.pending_verification_count += 1;
    } else if (status === 'skipped') counts.skipped += 1;
    else counts.failed += 1;
  }
  counts.unique_item_count = uniqueItems.size;
  return counts;
}
function sumCounts(values) {
  const result = emptyCounts();
  for (const value of values) {
    for (const field of Object.keys(result)) result[field] += Number(value?.[field] || 0);
  }
  return result;
}
function scopeBlock(counts, reason = null) {
  return reason ? { ...counts, reason } : { ...counts };
}
function readFinalItems(jobId) {
  const eventDir = path.join(dataDir, 'execution-job-events');
  const names = fs.readdirSync(eventDir).filter((name) => name.startsWith(`${jobId}-`) && name.endsWith('.jsonl'));
  if (names.length !== 1) throw new Error(`expected one JSONL for ${jobId}, found ${names.length}`);
  const rows = fs.readFileSync(path.join(eventDir, names[0]), 'utf8').split(/\r?\n/).filter(Boolean).map(JSON.parse);
  return { file: names[0], items: rows.filter((row) => row.type === 'item_cancel_final_state').map((row) => row.item) };
}
function withDualFields(target, actual, confirmed, exception) {
  return {
    ...target,
    ...actual,
    total: actual.relation_count,
    scope_accounting_version: 1,
    actual_scope_includes_exceptions: exception.relation_count > 0,
    confirmed_scope: scopeBlock(confirmed),
    scope_exception: scopeBlock(exception, 'live_recheck_expanded_beyond_confirmed_scope'),
    result_recovery: {
      recovery_id: RECOVERY_ID,
      source: 'jsonl_item_cancel_final_state_plus_sqlite_contract',
      original_terminal_status_preserved: true,
      product_requests_replayed: false,
    },
  };
}

assertNoActive();
const groupPath = path.join(dataDir, 'execution-group-states', `${TARGET_GROUP_ID}.json`);
const group = readJson(groupPath);
if (group.id !== TARGET_GROUP_ID || group.status !== 'failed') throw new Error('target group is not the expected failed group');
const confirmedActivities = group.request?.confirmedExecutionScope?.activities || [];
const confirmedKeys = new Set();
for (const activity of confirmedActivities) for (const itemId of activity.item_ids || []) confirmedKeys.add(relationKey({ ...activity, item_id: itemId }));

const childPatches = [];
for (const child of group.children || []) {
  const statePath = path.join(dataDir, 'execution-job-states', `${child.job_id}.json`);
  const state = readJson(statePath);
  const alreadyRecovered = state.result_recovery?.recovery_id === RECOVERY_ID;
  if (state.status !== 'failed' || (state.result != null && !alreadyRecovered)) {
    throw new Error(`child ${child.job_id} is not an eligible failed result`);
  }
  const jsonl = readFinalItems(child.job_id);
  const confirmedItems = jsonl.items.filter((item) => confirmedKeys.has(relationKey(item)));
  const exceptionItems = jsonl.items.filter((item) => !confirmedKeys.has(relationKey(item)));
  const actual = summarize(jsonl.items);
  const confirmed = summarize(confirmedItems);
  const exception = summarize(exceptionItems);
  const execution = withDualFields({
    promotions_total: confirmedActivities.filter((activity) => String(activity.account_id) === String(child.account_id)).length,
    result_contract_version: 2,
  }, actual, confirmed, exception);
  const result = {
    ok: false,
    message: '执行结果已从本地审计记录恢复；原执行器收口错误仍保留，未重跑任何请求。',
    action: 'cancel',
    itemStatus: 'started',
    execution,
    result_recovery: execution.result_recovery,
  };
  childPatches.push({ child, state, statePath, jsonl: jsonl.file, actual, confirmed, exception, result });
}
const actual = sumCounts(childPatches.map((row) => row.actual));
const confirmed = sumCounts(childPatches.map((row) => row.confirmed));
const exception = sumCounts(childPatches.map((row) => row.exception));
if (actual.relation_count !== EXPECTED.actual || confirmed.relation_count !== EXPECTED.confirmed || exception.relation_count !== EXPECTED.exception) {
  throw new Error(`dual accounting mismatch: ${JSON.stringify({ actual, confirmed, exception })}`);
}

const dbPath = path.join(dataDir, 'discount-manager.sqlite');
let db = new DatabaseSync(dbPath);
db.exec('PRAGMA busy_timeout = 5000');
const historyKey = `v1:execution-group:${TARGET_GROUP_ID}`;
const historyRow = db.prepare('SELECT * FROM history_batch_summaries WHERE summary_key = ?').get(historyKey);
if (!historyRow) throw new Error('materialized history row is missing');
const historyData = JSON.parse(historyRow.data_json);
if (Number(historyData.relation_count) !== actual.relation_count || Number(historyData.success_count) !== actual.success || Number(historyData.failed_count) !== actual.failed) {
  throw new Error('SQLite materialized history does not match JSONL final-state totals');
}
const batchTasks = db.prepare("SELECT id, account_id, summary_json FROM promo_tasks WHERE execution_group_id = ? AND promotion_id = '__BATCH__' ORDER BY id").all(TARGET_GROUP_ID);
if (batchTasks.length !== 3) throw new Error(`expected three batch tasks, found ${batchTasks.length}`);

const groupStores = childPatches.map((row) => withDualFields({
  job_id: row.child.job_id,
  account_id: String(row.child.account_id || ''),
  store_name: row.child.store_name || '',
  site_name: row.child.site_name || '全部站点',
  status: row.child.status,
  error: row.child.error || null,
}, row.actual, row.confirmed, row.exception));
const groupResult = withDualFields({
  action: 'cancel', store_count: groupStores.length, stores: groupStores,
}, actual, confirmed, exception);

const stateTargets = [groupPath, ...childPatches.map((row) => row.statePath)];
const dbRecoveryPresent = historyData.result_recovery?.recovery_id === RECOVERY_ID
  && batchTasks.every((task) => JSON.parse(task.summary_json || '{}').result_recovery?.recovery_id === RECOVERY_ID);
const stateRecoveryPresent = group.result_recovery?.recovery_id === RECOVERY_ID
  && childPatches.every((row) => row.state.result_recovery?.recovery_id === RECOVERY_ID);
if (dbRecoveryPresent && stateRecoveryPresent) {
  console.log(JSON.stringify({ ok: true, applied: false, idempotent: true, recovery_id: RECOVERY_ID, actual, confirmed_scope: confirmed, scope_exception: exception }, null, 2));
  db.close();
  process.exit(0);
}

const preview = {
  ok: true, applied: false, recovery_id: RECOVERY_ID, group_id: TARGET_GROUP_ID,
  state_files: stateTargets, jsonl_files: childPatches.map((row) => row.jsonl),
  actual, confirmed_scope: confirmed, scope_exception: exception,
  children: childPatches.map((row) => ({ job_id: row.child.job_id, account_id: row.child.account_id, actual: row.actual, confirmed_scope: row.confirmed, scope_exception: row.exception })),
};
if (!apply) {
  console.log(JSON.stringify(preview, null, 2));
  db.close();
  process.exit(0);
}

const reuseBackup = args.has('--reuse-backup');
if (!reuseBackup) fs.mkdirSync(backupDir, { recursive: false });
else if (!fs.existsSync(backupDir)) throw new Error('reused backup directory does not exist');
const stateBackupDir = path.join(backupDir, 'state-files');
const databaseBackupDir = path.join(backupDir, 'database');
if (!reuseBackup) {
  fs.mkdirSync(stateBackupDir);
  fs.mkdirSync(databaseBackupDir);
}
const sqliteFiles = ['discount-manager.sqlite', 'discount-manager.sqlite-wal', 'discount-manager.sqlite-shm'];
const backupManifest = { recovery_id: RECOVERY_ID, created_at: new Date().toISOString(), files: [] };
const sqliteSourceHashes = reuseBackup ? {} : Object.fromEntries(sqliteFiles.map((name) => [name, sha256(path.join(dataDir, name))]));

db.close();
try {
  for (const target of stateTargets) {
    const destination = path.join(stateBackupDir, path.basename(target));
    if (!reuseBackup) copyFileExact(target, destination);
    if (!fs.existsSync(destination)) throw new Error(`reused state backup is missing: ${path.basename(target)}`);
    backupManifest.files.push({ source: target, backup: destination, length: fs.statSync(destination).size, sha256: sha256(destination) });
  }
  for (const name of sqliteFiles) {
    const source = path.join(dataDir, name);
    const destination = path.join(databaseBackupDir, name);
    if (!reuseBackup) {
      if (!fs.existsSync(source)) throw new Error(`required SQLite backup member is missing: ${name}`);
      copyFileExact(source, destination);
    }
    if (!fs.existsSync(destination)) throw new Error(`reused SQLite backup member is missing: ${name}`);
    backupManifest.files.push({ source, backup: destination, length: fs.statSync(destination).size, sha256: sha256(destination) });
  }
  if (!reuseBackup) {
    for (const name of sqliteFiles) {
      if (sha256(path.join(dataDir, name)) !== sqliteSourceHashes[name]) {
        throw new Error(`SQLite source changed while backing up: ${name}`);
      }
    }
  }
  fs.writeFileSync(path.join(backupDir, 'backup-manifest.json'), JSON.stringify(backupManifest, null, 2), 'utf8');

  const copiedDb = new DatabaseSync(path.join(databaseBackupDir, 'discount-manager.sqlite'), { readOnly: true });
  const copiedIntegrity = copiedDb.prepare('PRAGMA integrity_check').get();
  copiedDb.close();
  if (String(copiedIntegrity.integrity_check || '').toLowerCase() !== 'ok') throw new Error('SQLite backup integrity check failed');

  db = new DatabaseSync(dbPath);
  db.exec('PRAGMA busy_timeout = 5000; BEGIN IMMEDIATE');
  const appliedAt = new Date().toISOString();
  for (const row of childPatches) {
    row.result.result_recovery.applied_at = appliedAt;
    row.result.execution.result_recovery.applied_at = appliedAt;
    const patched = { ...row.state, result: row.result, result_recovery: row.result.result_recovery };
    writeJsonAtomic(row.statePath, patched);
  }
  for (const [index, child] of (group.children || []).entries()) {
    child.result = childPatches[index].result;
  }
  groupResult.result_recovery.applied_at = appliedAt;
  group.result = groupResult;
  group.result_recovery = groupResult.result_recovery;
  writeJsonAtomic(groupPath, group);

  for (const task of batchTasks) {
    const row = childPatches.find((candidate) => String(candidate.child.account_id) === String(task.account_id));
    if (!row) throw new Error(`batch task account has no child result: ${task.account_id}`);
    const summary = JSON.parse(task.summary_json || '{}');
    const patched = withDualFields(summary, row.actual, row.confirmed, row.exception);
    patched.result_recovery.applied_at = appliedAt;
    db.prepare('UPDATE promo_tasks SET summary_json = ? WHERE id = ?').run(JSON.stringify(patched), task.id);
  }
  const historySummary = JSON.parse(historyData.summary_json || '{}');
  const patchedHistorySummary = withDualFields(historySummary, actual, confirmed, exception);
  patchedHistorySummary.result_recovery.applied_at = appliedAt;
  const patchedHistoryData = withDualFields(historyData, actual, confirmed, exception);
  patchedHistoryData.result_recovery.applied_at = appliedAt;
  patchedHistoryData.summary_json = JSON.stringify(patchedHistorySummary);
  db.prepare('UPDATE history_batch_summaries SET data_json = ? WHERE summary_key = ?').run(JSON.stringify(patchedHistoryData), historyKey);
  db.exec('COMMIT');
} catch (error) {
  try { db?.exec('ROLLBACK'); } catch {}
  try { db?.close(); } catch {}
  throw error;
}
db.close();

const verifyDb = new DatabaseSync(dbPath, { readOnly: true });
const integrity = verifyDb.prepare('PRAGMA integrity_check').get();
const verifyHistory = JSON.parse(verifyDb.prepare('SELECT data_json FROM history_batch_summaries WHERE summary_key = ?').get(historyKey).data_json);
verifyDb.close();
if (String(integrity.integrity_check || '').toLowerCase() !== 'ok') throw new Error('SQLite integrity check failed after recovery');
if (verifyHistory.result_recovery?.recovery_id !== RECOVERY_ID) throw new Error('history recovery marker is missing');
const report = {
  ...preview, applied: true, applied_at: groupResult.result_recovery.applied_at,
  backup_dir: backupDir, backup_integrity: 'ok', sqlite_integrity: 'ok',
  before: { group_result: group.result?.relation_count ? 'already_nonzero' : 'zero' },
  after: { group_result_relation_count: groupResult.relation_count, history_relation_count: verifyHistory.relation_count },
};
fs.writeFileSync(path.join(backupDir, 'recovery-report.json'), JSON.stringify(report, null, 2), 'utf8');
console.log(JSON.stringify(report, null, 2));
