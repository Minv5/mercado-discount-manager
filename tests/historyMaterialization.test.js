import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const projectRoot = process.cwd();

function runIsolated(source, dataDir) {
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: projectRoot,
    env: { ...process.env, MDM_DATA_DIR: dataDir },
    encoding: 'utf8',
    timeout: 30_000
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return JSON.parse(String(result.stdout || '').trim());
}

test('materialized history publishes only terminal tasks and survives restart', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-history-materialized-'));
  try {
    const first = runIsolated(`
      const repo = await import('./src/repository.js');
      await repo.backfillHistoryBatchSummaries();
      const taskId = Number(repo.createTask({
        accountId: 'A', promotionId: 'C-A', promotionType: 'SELLER_CAMPAIGN',
        action: 'enroll', mode: 'real', discountPercent: 6,
        plan: { total: 1, planned: 1, skipped: 0, priceMode: 'discount', rows: [] }
      }));
      repo.saveExecutionResult({ taskId, accountId: 'A', promotionId: 'C-A', promotionType: 'SELLER_CAMPAIGN', itemId: 'ITEM-1', action: 'enroll', mode: 'real', status: 'success' });
      const beforeFinish = repo.listTaskSummaries(20, { includeDetails: false });
      repo.finishTask(taskId, { success: 1, failed: 0, skipped: 0 }, 'completed', true);
      const afterFinish = repo.listTaskSummaries(20, { includeDetails: false });
      console.log(JSON.stringify({ beforeFinish, afterFinish }));
    `, dataDir);
    assert.equal(first.beforeFinish.length, 0);
    assert.equal(first.afterFinish.length, 1);
    assert.equal(first.afterFinish[0].success_count, 1);

    const restarted = runIsolated(`
      const repo = await import('./src/repository.js');
      const started = performance.now();
      const rows = repo.listTaskSummaries(20, { includeDetails: false });
      console.log(JSON.stringify({ rows, elapsed: performance.now() - started }));
    `, dataDir);
    assert.equal(restarted.rows.length, 1);
    assert.equal(restarted.rows[0].success_count, 1);
    assert.ok(restarted.elapsed < 300, `restart read took ${restarted.elapsed}ms`);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('materialized history incrementally merges cross-store batch rows without publishing activity fragments', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-history-cross-store-'));
  try {
    const result = runIsolated(`
      const repo = await import('./src/repository.js');
      await repo.backfillHistoryBatchSummaries();
      function finishActivity(accountId, promotionId, promotionType, discountPercent, itemId, status) {
        const taskId = Number(repo.createTask({ accountId, promotionId, promotionType, action: 'update', mode: 'real', discountPercent, plan: { total: 1, planned: 1, skipped: 0, priceMode: 'discount', rows: [] } }));
        repo.saveExecutionResult({ taskId, accountId, promotionId, promotionType, itemId, action: 'update', mode: 'real', status });
        repo.finishTask(taskId, { success: status === 'success' ? 1 : 0, failed: status === 'failed' ? 1 : 0, skipped: 0 }, status === 'success' ? 'completed' : 'partial_or_failed', status === 'success', { publishHistory: false });
        return taskId;
      }
      function finishBatch(accountId) {
        const taskId = Number(repo.createTask({ accountId, promotionId: '__BATCH__', promotionType: 'BATCH', action: 'update', mode: 'real', plan: { total: 2, planned: 1, skipped: 0, priceMode: 'batch', rows: [] } }));
        repo.finishTask(taskId, { success: 1, failed: 1, skipped: 0, planned: 1, total: 2, promotions_total: 2 }, 'partial_or_failed', false);
        return taskId;
      }
      const activityIds = [];
      activityIds.push(finishActivity('A', 'C-A', 'SELLER_CAMPAIGN', 7, 'A-1', 'success'));
      activityIds.push(finishActivity('A', 'P-A', 'DEAL', 8, 'A-2', 'failed'));
      const batchA = finishBatch('A');
      const afterA = repo.listTaskSummaries(20, { includeDetails: false });
      activityIds.push(finishActivity('B', 'C-B', 'SELLER_CAMPAIGN', 7, 'B-1', 'success'));
      activityIds.push(finishActivity('B', 'P-B', 'DEAL', 8, 'B-2', 'failed'));
      const batchB = finishBatch('B');
      const afterB = repo.listTaskSummaries(20, { includeDetails: false });
      console.log(JSON.stringify({ activityIds, batchA, batchB, afterA, afterB }));
    `, dataDir);
    assert.equal(result.afterA.length, 1);
    assert.equal(result.afterB.length, 1);
    assert.equal(result.afterB[0].seller_activity_text, '7%');
    assert.equal(result.afterB[0].official_activity_text, '8%');
    assert.equal(result.afterB[0].success_count, 2);
    assert.equal(result.afterB[0].failed_count, 2);
    assert.ok(result.afterB[0].task_ids.includes(result.batchA));
    assert.ok(result.afterB[0].task_ids.includes(result.batchB));
    assert.equal(result.afterB[0].task_ids.length, 6);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('materialized history backfill is idempotent and resumes an interrupted marker', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-history-backfill-'));
  try {
    const result = runIsolated(`
      const repo = await import('./src/repository.js');
      const db = await import('./src/db.js');
      const first = await repo.backfillHistoryBatchSummaries();
      db.run("UPDATE history_summary_state SET status = 'building', completed_at = NULL WHERE id = 1");
      const second = await repo.backfillHistoryBatchSummaries();
      const state = db.get('SELECT * FROM history_summary_state WHERE id = 1');
      const count = db.get('SELECT COUNT(*) AS count FROM history_batch_summaries').count;
      console.log(JSON.stringify({ first, second, state, count }));
    `, dataDir);
    assert.equal(result.first.summary_count, 0);
    assert.equal(result.second.summary_count, 0);
    assert.equal(result.state.status, 'complete');
    assert.equal(result.count, 0);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('tasks API reads the materialized table instead of the legacy full-result cache', () => {
  const repository = fs.readFileSync(path.join(projectRoot, 'src', 'repository.js'), 'utf8');
  const listStart = repository.indexOf('export function listTaskSummaries');
  const listEnd = repository.indexOf('export function buildLegacyHistoryBaseline');
  const source = repository.slice(listStart, listEnd);
  assert.match(source, /history_batch_summaries/);
  assert.doesNotMatch(source, /buildLegacyTaskSummaries|promo_action_results|history_task_summary_cache/);
});

test('explicit execution groups publish exactly one terminal history row and never merge adjacent groups', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-history-execution-group-'));
  try {
    const result = runIsolated(`
      const repo = await import('./src/repository.js');
      await repo.backfillHistoryBatchSummaries();
      function addGroup(groupId, jobId, accountId, promotionId, itemId) {
        const detail = Number(repo.createTask({
          accountId, promotionId, promotionType: 'SELLER_CAMPAIGN', action: 'update', mode: 'real',
          discountPercent: 8, executionGroupId: groupId, executionJobId: jobId,
          plan: { total: 1, planned: 1, skipped: 0, priceMode: 'discount', rows: [] }
        }));
        repo.saveExecutionResult({ taskId: detail, accountId, promotionId, promotionType: 'SELLER_CAMPAIGN', itemId, action: 'update', mode: 'real', status: 'success' });
        repo.finishTask(detail, { success: 1, failed: 0, skipped: 0 }, 'completed', true, { publishHistory: false });
        const batch = Number(repo.createTask({
          accountId, promotionId: '__BATCH__', promotionType: 'BATCH', action: 'update', mode: 'real',
          executionGroupId: groupId, executionJobId: jobId,
          plan: { total: 1, planned: 1, skipped: 0, priceMode: 'batch', rows: [] }
        }));
        repo.finishTask(batch, { success: 1, failed: 0, skipped: 0, planned: 1, total: 1, promotions_total: 1 }, 'completed', true, { publishHistory: false });
        return { detail, batch };
      }
      const a = addGroup('GROUP-A', 'JOB-A', 'A', 'C-A', 'ITEM-A');
      const beforePublish = repo.listTaskSummaries(20, { includeDetails: false });
      repo.publishHistorySummaryForExecutionGroup('GROUP-A');
      const afterA = repo.listTaskSummaries(20, { includeDetails: false });
      const b = addGroup('GROUP-B', 'JOB-B', 'B', 'C-B', 'ITEM-B');
      repo.publishHistorySummaryForExecutionGroup('GROUP-B');
      const afterB = repo.listTaskSummaries(20, { includeDetails: false });
      console.log(JSON.stringify({ a, b, beforePublish, afterA, afterB }));
    `, dataDir);
    assert.equal(result.beforePublish.length, 0);
    assert.equal(result.afterA.length, 1);
    assert.equal(result.afterA[0].execution_group_id, 'GROUP-A');
    assert.deepEqual(result.afterA[0].task_ids.sort((a, b) => a - b), [result.a.detail, result.a.batch].sort((a, b) => a - b));
    assert.equal(result.afterB.length, 2);
    assert.deepEqual(new Set(result.afterB.map((row) => row.execution_group_id)), new Set(['GROUP-A', 'GROUP-B']));
    assert.ok(result.afterB.every((row) => row.success_count === 1));
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
