import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { DatabaseSync } from 'node:sqlite';

import { createAsyncLimiter } from '../src/executor.js';
import {
  ACTIVE_EXECUTION_GROUP_STATUSES,
  createExecutionGroupPersistence,
  executionGroupBusinessScope,
  projectLiveExecutionGroupChildren,
  summarizeExecutionGroup,
} from '../src/executionGroupPersistence.js';

function groupFixture(overrides = {}) {
  return {
    id: 'group-1',
    client_submission_id: 'submission-1',
    status: 'queued',
    action: 'update',
    request: { accountIds: ['A', 'B', 'C'], globalWriteConcurrency: 2 },
    children: [
      { job_id: 'job-a', account_id: 'A', status: 'queued' },
      { job_id: 'job-b', account_id: 'B', status: 'queued' },
      { job_id: 'job-c', account_id: 'C', status: 'queued' },
    ],
    created_at: '2026-07-14T00:00:00.000Z',
    updated_at: '2026-07-14T00:00:00.000Z',
    finished_at: null,
    cancel_requested: false,
    global_peak_in_flight: 0,
    ...overrides,
  };
}

test('execution group persistence is idempotent by client submission id and rejects a second active group', () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-group-state-'));
  try {
    const store = createExecutionGroupPersistence({ stateDir, currentPid: 100, now: () => '2026-07-14T00:00:01.000Z' });
    const created = store.create(groupFixture());
    assert.equal(created.reused, false);
    assert.equal(store.create(groupFixture({ id: 'group-retry' })).group.id, 'group-1');
    assert.equal(store.create(groupFixture({ id: 'group-retry' })).reused, true);
    assert.throws(
      () => store.create(groupFixture({ id: 'group-2', client_submission_id: 'submission-2' })),
      (error) => error?.code === 'ACTIVE_EXECUTION_GROUP',
    );
  } finally {
    fs.rmSync(stateDir, { recursive: true, force: true });
  }
});

test('execution group recovery marks a foreign-process unfinished group and every unfinished child interrupted', () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-group-recovery-'));
  try {
    const first = createExecutionGroupPersistence({ stateDir, currentPid: 100, now: () => '2026-07-14T00:00:01.000Z' });
    first.create(groupFixture({ status: 'running', children: [
      { job_id: 'job-a', account_id: 'A', status: 'completed' },
      { job_id: 'job-b', account_id: 'B', status: 'running' },
      { job_id: 'job-c', account_id: 'C', status: 'queued' },
    ] }));
    const restarted = createExecutionGroupPersistence({ stateDir, currentPid: 200, now: () => '2026-07-14T00:10:00.000Z' });
    const [group] = restarted.loadAll();
    assert.equal(group.status, 'interrupted');
    assert.equal(group.children[0].status, 'completed');
    assert.equal(group.children[1].status, 'interrupted');
    assert.equal(group.children[2].status, 'interrupted');
    assert.equal(restarted.active(), null);
  } finally {
    fs.rmSync(stateDir, { recursive: true, force: true });
  }
});

test('group summary keeps a failed-to-start child visible and uses existing display count semantics', () => {
  const summary = summarizeExecutionGroup(groupFixture({
    status: 'failed',
    children: [
      { job_id: 'job-a', account_id: 'A', status: 'completed', result: { execution: { total: 2, success: 2, failed: 0, skipped: 0 } } },
      { job_id: 'job-b', account_id: 'B', status: 'failed', error: '子任务启动失败', result: null },
      { job_id: 'job-c', account_id: 'C', status: 'cancelled', result: { execution: { total: 1, success: 0, failed: 0, skipped: 1 } } },
    ],
  }));
  assert.equal(summary.stores.length, 3);
  assert.equal(summary.stores[1].status, 'failed');
  assert.equal(summary.total, 3);
  assert.equal(summary.success, 2);
  assert.equal(summary.skipped, 1);
});

test('running group responses project each live child instead of waiting for child completion', () => {
  const jobs = new Map([
    ['job-a', {
      id: 'job-a',
      status: 'running',
      progress: { stage: 'execute', completed_promotions: 2 },
      userLogs: [{ at: '2026-07-23T05:47:28.000Z', message: '湖北实时日志' }],
    }],
    ['job-b', {
      id: 'job-b',
      status: 'running',
      progress: { stage: 'execute', completed_promotions: 1 },
      userLogs: [{ at: '2026-07-23T05:47:29.000Z', message: '广州实时日志' }],
    }],
  ]);
  const children = projectLiveExecutionGroupChildren(
    groupFixture(),
    (jobId) => jobs.get(jobId),
    (job) => ({
      job_id: job.id,
      status: job.status,
      progress: job.progress,
      userLogs: job.userLogs,
    }),
  );
  assert.equal(children[0].status, 'running');
  assert.equal(children[0].progress.completed_promotions, 2);
  assert.equal(children[0].userLogs[0].message, '湖北实时日志');
  assert.equal(children[1].userLogs[0].message, '广州实时日志');
  assert.equal(children[2].status, 'queued');
});

test('execution group exposes the exact safe business scope used by the completed submission', () => {
  const scope = executionGroupBusinessScope(groupFixture({
    request: {
      accountIds: ['B', 'A'],
      selectedSiteName: '墨西哥站',
      filters: {
        siteId: 'MLM',
        sellerActivityNames: ['活动 95'],
        officialActivityNames: ['Deal 6'],
        excludeSeller: false,
        excludeOfficial: true,
      },
      sellerDiscountPercent: 10,
      officialDiscountPercent: 10,
    },
  }));
  assert.deepEqual(scope, {
    account_ids: ['A', 'B'],
    site_id: 'MLM',
    selected_site_name: '墨西哥站',
    seller_activity_names: ['活动 95'],
    official_activity_names: ['Deal 6'],
    exclude_seller: false,
    exclude_official: true,
    seller_discount_percent: 10,
    official_discount_percent: 10,
  });
  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  assert.match(source, /scope:\s*executionGroupBusinessScope\(group\)/);
  assert.match(source, /compact:\s*url\.searchParams\.get\('compact'\) === '1'/);
  assert.match(source, /const children = compact \? \[\] : projectedChildren/);
  assert.match(source, /summarizeExecutionGroup\(\{ \.\.\.group, children: projectedChildren \}\)/);
});

test('one shared limiter caps all child workloads and is removable after group terminal', async () => {
  const limiters = new Map();
  const limiter = createAsyncLimiter(2);
  limiters.set('group-1', limiter);
  await Promise.all(Array.from({ length: 12 }, () => limiter.run(async () => {
    await new Promise((resolve) => setTimeout(resolve, 3));
  })));
  assert.equal(limiter.maxActive, 2);
  limiters.delete('group-1');
  assert.equal(limiters.has('group-1'), false);
});

test('group status vocabulary keeps persistent pending work active while paused', () => {
  assert.deepEqual([...ACTIVE_EXECUTION_GROUP_STATUSES].sort(), ['paused', 'queued', 'running', 'stopping']);
});

test('paused pending group is recovered as queued instead of being marked interrupted after restart', () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-group-paused-recovery-'));
  try {
    const first = createExecutionGroupPersistence({ stateDir, currentPid: 100, now: () => '2026-07-14T00:00:01.000Z' });
    first.create(groupFixture({ status: 'paused', children: [
      { job_id: 'job-a', account_id: 'A', status: 'completed' },
      { job_id: 'job-b', account_id: 'B', status: 'paused' },
    ] }));
    const restarted = createExecutionGroupPersistence({ stateDir, currentPid: 200, now: () => '2026-07-14T00:10:00.000Z' });
    const [group] = restarted.loadAll();
    assert.equal(group.status, 'queued');
    assert.equal(group.children[0].status, 'completed');
    assert.equal(group.children[1].status, 'queued');
    assert.equal(group.recovered_pending_after_restart, true);
  } finally {
    fs.rmSync(stateDir, { recursive: true, force: true });
  }
});

test('server exposes group APIs and retires direct product job start', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  assert.match(source, /\/api\/execution\/groups\/start/);
  assert.match(source, /\/api\/execution\/groups\/active/);
  assert.match(source, /executionGroupCancelMatch/);
  assert.match(source, /executionGroupMatch/);
  const directStart = source.slice(source.indexOf("url.pathname === '/api/execution/jobs/start'"), source.indexOf("url.pathname === '/api/execution/jobs/active'"));
  assert.match(directStart, /410/);
  assert.doesNotMatch(directStart, /runExecutionJob/);
});

test('task schema and history materialization use explicit execution group identity', () => {
  const db = fs.readFileSync(path.join(process.cwd(), 'src', 'db.js'), 'utf8');
  const repository = fs.readFileSync(path.join(process.cwd(), 'src', 'repository.js'), 'utf8');
  assert.match(db, /execution_group_id/);
  assert.match(db, /execution_job_id/);
  assert.match(repository, /publishHistorySummaryForExecutionGroup/);
  assert.match(repository, /execution-group:/);
});

async function waitForHealth(baseUrl) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('test server did not become healthy');
}

test('group HTTP boundary blocks prepare-only and unconfirmed writes without creating a group', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-group-http-'));
  const port = 30000 + Math.floor(Math.random() * 1000);
  const child = spawn(process.execPath, ['src/server.js'], {
    cwd: process.cwd(),
    env: { ...process.env, MDM_PORT: String(port), MDM_DATA_DIR: dataDir },
    stdio: 'ignore',
    windowsHide: true,
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    await waitForHealth(baseUrl);
    const prepare = await fetch(`${baseUrl}/api/execution/groups/start`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ client_submission_id: 'P-1', accountIds: ['A'], action: 'update', prepareOnly: true }),
    });
    assert.equal(prepare.status, 410);
    const unconfirmed = await fetch(`${baseUrl}/api/execution/groups/start`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ client_submission_id: 'P-2', accountIds: ['A'], action: 'update' }),
    });
    assert.ok(unconfirmed.status >= 400 && unconfirmed.status < 500);
    const direct = await fetch(`${baseUrl}/api/execution/jobs/start`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}',
    });
    assert.equal(direct.status, 410);
    const active = await (await fetch(`${baseUrl}/api/execution/groups/active`)).json();
    assert.equal(active.active, false);
    const groupDir = path.join(dataDir, 'execution-group-states');
    assert.equal(fs.existsSync(groupDir) ? fs.readdirSync(groupDir).filter((name) => name.endsWith('.json')).length : 0, 0);
  } finally {
    child.kill();
    await new Promise((resolve) => child.once('exit', resolve));
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('promo task group columns migrate additively and the pre-migration snapshot remains restorable', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-group-migration-'));
  const dbPath = path.join(dataDir, 'legacy.sqlite');
  const backupPath = `${dbPath}.before-stage-b`;
  try {
    const source = `
      import fs from 'node:fs';
      import { DatabaseSync } from 'node:sqlite';
      const db = new DatabaseSync(process.env.MDM_DB_PATH);
      db.exec(\`CREATE TABLE promo_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, promotion_id TEXT NOT NULL,
        promotion_type TEXT NOT NULL, action TEXT NOT NULL, mode TEXT NOT NULL, discount_percent REAL,
        direct_price REAL, status TEXT NOT NULL, total_count INTEGER NOT NULL DEFAULT 0,
        success_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
        skipped_count INTEGER NOT NULL DEFAULT 0, empty_count INTEGER NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0, summary_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      ); INSERT INTO promo_tasks (account_id,promotion_id,promotion_type,action,mode,status,created_at,updated_at)
      VALUES ('A','C-A','SELLER_CAMPAIGN','update','real','completed','2026-07-01','2026-07-01');\`);
      db.close();
      fs.copyFileSync(process.env.MDM_DB_PATH, process.env.MDM_DB_PATH + '.before-stage-b');
      const migrated = await import('./src/db.js');
      const columns = migrated.all('PRAGMA table_info(promo_tasks)').map((row) => row.name);
      const row = migrated.get('SELECT account_id, execution_group_id, execution_job_id FROM promo_tasks WHERE id=1');
      migrated.closeDb();
      console.log(JSON.stringify({ columns, row }));
    `;
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: process.cwd(), env: { ...process.env, MDM_DATA_DIR: dataDir, MDM_DB_PATH: dbPath }, encoding: 'utf8', timeout: 30_000,
    });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    const payload = JSON.parse(result.stdout.trim());
    assert.ok(payload.columns.includes('execution_group_id'));
    assert.ok(payload.columns.includes('execution_job_id'));
    assert.equal(payload.row.account_id, 'A');
    assert.equal(payload.row.execution_group_id, null);
    assert.equal(payload.row.execution_job_id, null);
    assert.ok(fs.existsSync(backupPath));
    const backup = new DatabaseSync(backupPath, { readOnly: true });
    assert.equal(backup.prepare('SELECT COUNT(*) AS count FROM promo_tasks').get().count, 1);
    backup.close();
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
