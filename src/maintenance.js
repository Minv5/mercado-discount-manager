import fs from 'node:fs';
import path from 'node:path';

const ORPHAN_STATUSES = new Set(['queued', 'running', 'stopping', 'legacy_unknown']);
const ACTIVE_STATUSES = new Set(['queued', 'running', 'stopping']);

function tableExists(database, name) {
  return Number(database.prepare("SELECT COUNT(*) AS count FROM sqlite_master WHERE type='table' AND name=?").get(name)?.count || 0) > 0;
}

export function dropLegacyHistoryCache(database, { confirm = false } = {}) {
  const exists = tableExists(database, 'history_task_summary_cache');
  if (!exists) return { mode: confirm ? 'confirm' : 'preview', would_drop: false, dropped: false };
  if (!confirm) return { mode: 'preview', would_drop: true, dropped: false };
  database.exec('BEGIN IMMEDIATE');
  try {
    database.exec('DROP TABLE history_task_summary_cache');
    database.exec('COMMIT');
    return { mode: 'confirm', would_drop: true, dropped: true };
  } catch (error) {
    try { database.exec('ROLLBACK'); } catch {}
    throw error;
  }
}

function activeSnapshots(directory) {
  if (!directory || !fs.existsSync(directory)) return [];
  const active = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith('.json')) continue;
    try {
      const snapshot = JSON.parse(fs.readFileSync(path.join(directory, entry.name), 'utf8'));
      if (ACTIVE_STATUSES.has(String(snapshot?.status || ''))) {
        active.push({ id: String(snapshot.id || entry.name.slice(0, -5)), status: String(snapshot.status), file: entry.name });
      }
    } catch {
      active.push({ id: entry.name, status: 'unreadable_snapshot', file: entry.name });
    }
  }
  return active;
}

export function previewLegacyOrphanTasks(database, {
  now = new Date(),
  safeAgeMs = 24 * 60 * 60 * 1000,
  groupStateDir,
  jobStateDir,
} = {}) {
  const active_states = [...activeSnapshots(groupStateDir), ...activeSnapshots(jobStateDir)];
  if (active_states.length) {
    return { mode: 'preview', blocked_by_active_state: true, active_states, candidates: [] };
  }
  const columns = new Set(database.prepare('PRAGMA table_info(promo_tasks)').all().map((row) => String(row.name)));
  const groupSelect = columns.has('execution_group_id') ? 'execution_group_id' : 'NULL AS execution_group_id';
  const jobSelect = columns.has('execution_job_id') ? 'execution_job_id' : 'NULL AS execution_job_id';
  const groupWhere = columns.has('execution_group_id') ? 'AND execution_group_id IS NULL' : '';
  const cutoff = new Date(now.getTime() - Math.max(0, Number(safeAgeMs) || 0)).toISOString();
  const placeholders = [...ORPHAN_STATUSES].map(() => '?').join(',');
  const rows = database.prepare(
    `SELECT id, ${groupSelect}, ${jobSelect}, status, completed, total_count,
            success_count, failed_count, skipped_count, summary_json, updated_at
       FROM promo_tasks
      WHERE 1=1
        ${groupWhere}
        AND status IN (${placeholders})
        AND updated_at < ?
      ORDER BY id`
  ).all(...ORPHAN_STATUSES, cutoff);
  return {
    mode: 'preview',
    blocked_by_active_state: false,
    active_states: [],
    cutoff,
    candidates: rows.map((row) => ({ ...row, before: { status: row.status, completed: Number(row.completed || 0) }, after: { status: 'interrupted', completed: 1 } })),
  };
}

function preservedInterruptedSummary(value) {
  let summary = {};
  try {
    const parsed = JSON.parse(value || '{}');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) summary = parsed;
  } catch {
    summary = { legacy_summary_text: String(value || '').slice(0, 2000) };
  }
  return { ...summary, interruption_reason: '历史任务未正常收口', maintenance_calibrated: true };
}

export function calibrateLegacyOrphanTasks(database, options = {}) {
  const preview = previewLegacyOrphanTasks(database, options);
  if (!options.confirm || preview.blocked_by_active_state || !preview.candidates.length) {
    return { ...preview, updated: 0 };
  }
  const ids = preview.candidates.map((row) => Number(row.id));
  const columns = new Set(database.prepare('PRAGMA table_info(promo_tasks)').all().map((row) => String(row.name)));
  const groupGuard = columns.has('execution_group_id') ? ' AND execution_group_id IS NULL' : '';
  database.exec('BEGIN IMMEDIATE');
  try {
    const statement = database.prepare(
      `UPDATE promo_tasks
          SET status='interrupted', completed=1, summary_json=?
        WHERE id=?${groupGuard} AND status=? AND updated_at=?`
    );
    let updated = 0;
    for (const row of preview.candidates) {
      const result = statement.run(JSON.stringify(preservedInterruptedSummary(row.summary_json)), Number(row.id), row.status, row.updated_at);
      updated += Number(result.changes || 0);
    }
    database.exec('COMMIT');
    if (updated && typeof options.rematerialize === 'function') options.rematerialize(ids);
    return { ...preview, mode: 'confirm', updated, task_ids: ids };
  } catch (error) {
    try { database.exec('ROLLBACK'); } catch {}
    throw error;
  }
}
