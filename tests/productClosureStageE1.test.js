import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { DatabaseSync } from 'node:sqlite';

import {
  calibrateLegacyOrphanTasks,
  dropLegacyHistoryCache,
  previewLegacyOrphanTasks,
} from '../src/maintenance.js';
import { compressExecutionAudits, readAuditEvents, readAuditText } from '../src/executionAudit.js';
import { applyCleanupPlan, buildCleanupPreview } from '../src/productCleanup.js';

function temporaryRoot(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function createMaintenanceDb(file) {
  const db = new DatabaseSync(file);
  db.exec(`
    CREATE TABLE history_task_summary_cache (cache_key TEXT PRIMARY KEY, data_json TEXT NOT NULL);
    CREATE TABLE history_batch_summaries (summary_key TEXT PRIMARY KEY, data_json TEXT NOT NULL);
    CREATE TABLE promo_tasks (
      id INTEGER PRIMARY KEY, execution_group_id TEXT, execution_job_id TEXT, status TEXT NOT NULL,
      completed INTEGER NOT NULL DEFAULT 0, total_count INTEGER NOT NULL DEFAULT 0,
      success_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
      skipped_count INTEGER NOT NULL DEFAULT 0, summary_json TEXT, updated_at TEXT NOT NULL
    );
    INSERT INTO history_task_summary_cache VALUES ('old', '{}');
    INSERT INTO history_batch_summaries VALUES ('keep', '{}');
  `);
  return db;
}

test('legacy history cache is removed only by an explicit idempotent migration', () => {
  const root = temporaryRoot('mdm-e1-db-');
  const db = createMaintenanceDb(path.join(root, 'test.sqlite'));
  try {
    assert.equal(dropLegacyHistoryCache(db).would_drop, true);
    assert.equal(db.prepare("SELECT COUNT(*) count FROM sqlite_master WHERE type='table' AND name='history_task_summary_cache'").get().count, 1);
    assert.equal(dropLegacyHistoryCache(db, { confirm: true }).dropped, true);
    assert.equal(db.prepare("SELECT COUNT(*) count FROM sqlite_master WHERE type='table' AND name='history_task_summary_cache'").get().count, 0);
    assert.equal(db.prepare("SELECT COUNT(*) count FROM sqlite_master WHERE type='table' AND name='history_batch_summaries'").get().count, 1);
    assert.equal(dropLegacyHistoryCache(db, { confirm: true }).dropped, false);
    assert.equal(db.prepare('PRAGMA integrity_check').get().integrity_check, 'ok');
  } finally {
    db.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('legacy orphan calibration is preview-first, active-safe, and preserves business counts', () => {
  const root = temporaryRoot('mdm-e1-orphan-');
  const db = createMaintenanceDb(path.join(root, 'test.sqlite'));
  const groupDir = path.join(root, 'groups');
  const jobDir = path.join(root, 'jobs');
  fs.mkdirSync(groupDir);
  fs.mkdirSync(jobDir);
  const old = '2026-06-01T00:00:00.000Z';
  const recent = '2026-07-14T11:59:00.000Z';
  db.exec(`
    INSERT INTO promo_tasks VALUES (1, NULL, 'legacy-1', 'running', 0, 10, 4, 2, 1, '{"kept":true}', '${old}');
    INSERT INTO promo_tasks VALUES (2, 'group-2', 'job-2', 'running', 0, 9, 1, 0, 0, '{}', '${old}');
    INSERT INTO promo_tasks VALUES (3, NULL, 'legacy-3', 'queued', 0, 8, 0, 0, 0, '{}', '${recent}');
    INSERT INTO promo_tasks VALUES (4, NULL, 'legacy-4', 'completed', 1, 7, 7, 0, 0, '{}', '${old}');
  `);
  fs.writeFileSync(path.join(groupDir, 'active.json'), JSON.stringify({ id: 'active', status: 'running' }));
  try {
    const options = { now: new Date('2026-07-14T12:00:00.000Z'), safeAgeMs: 60 * 60 * 1000, groupStateDir: groupDir, jobStateDir: jobDir };
    const blocked = previewLegacyOrphanTasks(db, options);
    assert.equal(blocked.blocked_by_active_state, true);
    assert.deepEqual(blocked.candidates, []);
    fs.rmSync(path.join(groupDir, 'active.json'));
    const preview = previewLegacyOrphanTasks(db, options);
    assert.deepEqual(preview.candidates.map((row) => row.id), [1]);
    assert.equal(db.prepare('SELECT status FROM promo_tasks WHERE id=1').get().status, 'running');
    let rematerialized = [];
    const applied = calibrateLegacyOrphanTasks(db, {
      ...options,
      confirm: true,
      rematerialize: (ids) => { rematerialized = ids; },
    });
    assert.equal(applied.updated, 1);
    assert.deepEqual(rematerialized, [1]);
    const changed = db.prepare('SELECT * FROM promo_tasks WHERE id=1').get();
    assert.equal(changed.status, 'interrupted');
    assert.equal(changed.completed, 1);
    assert.deepEqual([changed.total_count, changed.success_count, changed.failed_count, changed.skipped_count], [10, 4, 2, 1]);
    const summary = JSON.parse(changed.summary_json);
    assert.equal(summary.kept, true);
    assert.equal(summary.interruption_reason, '历史任务未正常收口');
    assert.equal(db.prepare('SELECT status FROM promo_tasks WHERE id=2').get().status, 'running');
  } finally {
    db.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('legacy orphan preview supports the pre-group promo_tasks schema without migrating it', () => {
  const root = temporaryRoot('mdm-e1-old-schema-');
  const db = new DatabaseSync(path.join(root, 'old.sqlite'));
  db.exec(`
    CREATE TABLE promo_tasks (
      id INTEGER PRIMARY KEY, status TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
      total_count INTEGER NOT NULL DEFAULT 0, success_count INTEGER NOT NULL DEFAULT 0,
      failed_count INTEGER NOT NULL DEFAULT 0, skipped_count INTEGER NOT NULL DEFAULT 0,
      summary_json TEXT, updated_at TEXT NOT NULL
    );
    INSERT INTO promo_tasks VALUES (1, 'legacy_unknown', 0, 3, 1, 1, 1, '{}', '2026-06-01T00:00:00.000Z');
  `);
  try {
    const options = { now: new Date('2026-07-14T12:00:00.000Z'), safeAgeMs: 1000 };
    assert.deepEqual(previewLegacyOrphanTasks(db, options).candidates.map((row) => row.id), [1]);
    assert.equal(calibrateLegacyOrphanTasks(db, { ...options, confirm: true }).updated, 1);
    assert.equal(db.prepare('SELECT status FROM promo_tasks WHERE id=1').get().status, 'interrupted');
    assert.equal(db.prepare("SELECT COUNT(*) count FROM pragma_table_info('promo_tasks') WHERE name='execution_group_id'").get().count, 0);
  } finally {
    db.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('terminal execution audit compression is byte-safe and active audits are skipped', () => {
  const root = temporaryRoot('mdm-e1-audit-');
  const events = path.join(root, 'events');
  const jobs = path.join(root, 'jobs');
  const groups = path.join(root, 'groups');
  fs.mkdirSync(events); fs.mkdirSync(jobs); fs.mkdirSync(groups);
  const body = '{"type":"item_start","jobId":"job-1","group_id":"group-1"}\n{"type":"item_finish","jobId":"job-1","group_id":"group-1"}\n';
  const activeBody = '{"type":"item_start","jobId":"job-2","group_id":"group-2"}\n';
  fs.writeFileSync(path.join(events, 'run-1.jsonl'), body);
  fs.writeFileSync(path.join(events, 'run-2.jsonl'), activeBody);
  fs.writeFileSync(path.join(jobs, 'job-1.json'), JSON.stringify({ id: 'job-1', status: 'completed' }));
  fs.writeFileSync(path.join(groups, 'group-1.json'), JSON.stringify({ id: 'group-1', status: 'completed' }));
  fs.writeFileSync(path.join(jobs, 'job-2.json'), JSON.stringify({ id: 'job-2', status: 'running' }));
  fs.writeFileSync(path.join(groups, 'group-2.json'), JSON.stringify({ id: 'group-2', status: 'running' }));
  const old = new Date(Date.now() - 10_000);
  fs.utimesSync(path.join(events, 'run-1.jsonl'), old, old);
  fs.utimesSync(path.join(events, 'run-2.jsonl'), old, old);
  try {
    const options = { eventDir: events, jobStateDir: jobs, groupStateDir: groups, olderThanMs: 1000 };
    const preview = compressExecutionAudits(options);
    assert.equal(preview.candidates.length, 1);
    assert.equal(preview.blocked.length, 1);
    const result = compressExecutionAudits({ ...options, confirm: true });
    assert.equal(result.compressed.length, 1);
    const gzip = path.join(events, 'run-1.jsonl.gz');
    assert.equal(fs.existsSync(path.join(events, 'run-1.jsonl')), false);
    assert.equal(readAuditText(gzip), body);
    assert.deepEqual(readAuditEvents(gzip), body.trim().split('\n').map(JSON.parse));
    assert.equal(fs.existsSync(path.join(events, 'run-2.jsonl')), true);
    const server = fs.readFileSync('src/server.js', 'utf8');
    assert.match(server, /readAuditText\(candidate\)/);
    assert.match(server, /endsWith\('\.jsonl\.gz'\)/);
    assert.match(server, /readAuditText\(file\)/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('product cleanup is preview-first, path-safe, and gates legacy WinForms separately', () => {
  const root = temporaryRoot('mdm-e1-clean-');
  const project = path.join(root, 'project');
  const local = path.join(root, 'local');
  const currentDist = path.join(project, 'dist-pyside', '美客多活动助手');
  const currentInstall = path.join(local, 'Programs', 'MercadoDiscountManagerPySide');
  for (const target of [
    currentDist,
    currentInstall,
    path.join(project, 'release-backups', 'old-a'),
    path.join(project, 'release-backups', 'new-b'),
    path.join(project, 'dist-full'),
    path.join(project, 'desktop-pyside', 'runtime-staging'),
    path.join(local, 'Programs', 'MercadoDiscountManagerPySide.backup-old'),
    path.join(local, 'Programs', 'MercadoDiscountManagerPySide.backup-new'),
    path.join(local, 'Programs', 'MercadoDiscountManager'),
    path.join(project, 'data', 'validation-evidence', 'run-pass-old'),
    path.join(project, 'data', 'validation-evidence', 'run-failed'),
    path.join(project, 'data', 'validation-evidence', 'run-pass-new'),
  ]) {
    fs.mkdirSync(target, { recursive: true });
    fs.writeFileSync(path.join(target, 'payload.bin'), '1234');
  }
  fs.writeFileSync(path.join(project, 'data', 'validation-evidence', 'run-pass-old', 'summary.json'), `\uFEFF${JSON.stringify({ overall: 'PASS' })}`);
  fs.writeFileSync(path.join(project, 'data', 'validation-evidence', 'run-failed', 'summary.json'), JSON.stringify({ overall: 'FAIL' }));
  fs.writeFileSync(path.join(project, 'data', 'validation-evidence', 'run-pass-new', 'summary.json'), JSON.stringify({ overall: 'PASS' }));
  const now = Date.now();
  fs.utimesSync(path.join(project, 'data', 'validation-evidence', 'run-pass-old'), new Date(now - 3000), new Date(now - 3000));
  fs.utimesSync(path.join(project, 'data', 'validation-evidence', 'run-failed'), new Date(now - 2000), new Date(now - 2000));
  fs.utimesSync(path.join(project, 'data', 'validation-evidence', 'run-pass-new'), new Date(now - 1000), new Date(now - 1000));
  fs.writeFileSync(path.join(project, 'tmp-proof.png'), 'image');
  try {
    const options = { projectRoot: project, localAppData: local, currentDist, currentInstall };
    const preview = buildCleanupPreview(options);
    assert.equal(preview.mode, 'preview');
    assert.ok(preview.items.some((item) => item.path === currentDist && item.decision === 'retain'));
    assert.ok(preview.items.some((item) => item.path === currentInstall && item.decision === 'blocked'));
    assert.ok(preview.items.some((item) => item.category === 'legacy_winforms' && item.decision === 'blocked'));
    assert.ok(preview.items.some((item) => item.path.endsWith('run-pass-old') && item.decision === 'delete'));
    assert.ok(preview.items.some((item) => item.path.endsWith('run-failed') && item.decision === 'retain'));
    assert.ok(preview.items.some((item) => item.path.endsWith('run-pass-new') && item.decision === 'retain'));
    assert.ok(preview.items.some((item) => item.category === 'temporary_visual' && item.decision === 'delete'));
    assert.equal(fs.existsSync(path.join(project, 'dist-full')), true);
    const applied = applyCleanupPlan(preview, { confirm: true });
    assert.ok(applied.deleted.length > 0);
    assert.equal(fs.existsSync(currentDist), true);
    assert.equal(fs.existsSync(currentInstall), true);
    assert.equal(fs.existsSync(path.join(local, 'Programs', 'MercadoDiscountManager')), true);
    const legacyPlan = buildCleanupPreview({ ...options, includeLegacyWinForms: true });
    assert.ok(legacyPlan.items.some((item) => item.category === 'legacy_winforms' && item.decision === 'delete'));
    assert.throws(() => applyCleanupPlan({ ...preview, items: [{ path: root, decision: 'delete', allowed_root: project }] }, { confirm: true }), /outside allowed root/i);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('build inputs ignore generated products and successful release scripts clean their own staging', () => {
  const ignore = fs.readFileSync('.gitignore', 'utf8');
  for (const value of ['dist-pyside/', 'release-backups/', 'runtime-staging/', 'install-validation/']) assert.match(ignore, new RegExp(value.replace('/', '\\/')));
  const build = fs.readFileSync('desktop-pyside/build-release.ps1', 'utf8');
  const installerTest = fs.readFileSync('desktop-pyside/test-install-release.ps1', 'utf8');
  assert.match(build, /Remove-Item[^\n]+\$Staging/);
  assert.match(build, /Remove-Item[^\n]+\$Work/);
  assert.match(installerTest, /Remove-Item[^\n]+\$Root/);
});
