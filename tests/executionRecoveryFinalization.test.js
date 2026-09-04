import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { mergeExecutionRecoveryRound, reconcileExecutionWithPendingQueue, recordCompletedPromotion } from '../src/executionRecovery.js';
import { summarizeExecutionGroup } from '../src/executionGroupPersistence.js';

function recoveryFixture({ relationCount, success, failed, pending, activityFailures = 0, recoveredSuccess, noisySkipped, remainingPending = 0 }) {
  const previous = {
    total: relationCount,
    relation_count: relationCount,
    unique_item_count: relationCount,
    success,
    failed,
    skipped: 0,
    pending,
    retryable_pending_count: pending,
    pending_verification_count: 0,
    request_success_count: success,
    live_verified_removed_count: success,
    activity_failure_count: activityFailures,
    promotions_total: 10,
    promotions: [],
  };
  const current = {
    total: pending,
    relation_count: pending,
    unique_item_count: pending,
    success: recoveredSuccess,
    failed: 0,
    skipped: noisySkipped,
    pending: remainingPending,
    retryable_pending_count: remainingPending,
    pending_verification_count: remainingPending,
    request_success_count: recoveredSuccess,
    live_verified_removed_count: recoveredSuccess,
    activity_failure_count: 0,
    promotions_total: 9,
    promotions: [],
  };
  return mergeExecutionRecoveryRound(previous, current);
}

test('real 5611-relation recovery fixture does not double count retry planning skips', () => {
  const hunan = recoveryFixture({ relationCount: 2715, success: 2619, failed: 4, pending: 92, activityFailures: 1, recoveredSuccess: 91, noisySkipped: 93, remainingPending: 1 });
  const guangzhou = recoveryFixture({ relationCount: 2700, success: 2580, failed: 3, pending: 117, recoveredSuccess: 117, noisySkipped: 117 });
  const hubei = recoveryFixture({ relationCount: 196, success: 143, failed: 2, pending: 51, recoveredSuccess: 51, noisySkipped: 51 });

  assert.deepEqual(
    [hunan.total, hunan.relation_count, hunan.success, hunan.failed, hunan.skipped, hunan.pending, hunan.activity_failure_count],
    [2715, 2715, 2710, 4, 0, 1, 1],
  );
  assert.deepEqual(
    [guangzhou.total, guangzhou.relation_count, guangzhou.success, guangzhou.failed, guangzhou.skipped, guangzhou.pending],
    [2700, 2700, 2697, 3, 0, 0],
  );
  assert.deepEqual(
    [hubei.total, hubei.relation_count, hubei.success, hubei.failed, hubei.skipped, hubei.pending],
    [196, 196, 194, 2, 0, 0],
  );

  const child = (id, execution) => ({
    job_id: id,
    status: execution.pending ? 'paused' : 'completed',
    result: {
      accounting_complete: true,
      terminal_counts: {
        relation_count: execution.relation_count,
        success: execution.success,
        failed: execution.failed,
        skipped: execution.skipped,
        platform_pending: 0,
        unresolved: execution.pending,
        classified_count: execution.relation_count,
        is_closed: true,
        is_resolved: execution.pending === 0,
      },
      execution: {
        ...execution,
        accounting_complete: true,
        terminal_counts: {
          relation_count: execution.relation_count,
          success: execution.success,
          failed: execution.failed,
          skipped: execution.skipped,
          platform_pending: 0,
          unresolved: execution.pending,
          classified_count: execution.relation_count,
          is_closed: true,
          is_resolved: execution.pending === 0,
        },
        unresolved: execution.pending,
      },
    },
  });
  const summary = summarizeExecutionGroup({ action: 'cancel', children: [child('hunan', hunan), child('guangzhou', guangzhou), child('hubei', hubei)] });
  assert.equal(summary.total, 5611);
  assert.equal(summary.relation_count, 5611);
  assert.equal(summary.success, 5601);
  assert.equal(summary.failed, 9);
  assert.equal(summary.skipped, 0);
  assert.equal(summary.pending, 1);
  assert.equal(summary.activity_failure_count, 1);
  assert.ok(summary.incomplete_reasons.includes('pending_relations_present'));
  assert.ok(!summary.incomplete_reasons.includes('relation_count_gap'));
});

test('recovery progress is deduplicated by activity and never exceeds total', () => {
  let progress = { total_promotions: 3, execute_completed_promotions: 0, execute_completed_promotion_tokens: [] };
  const event = { total: 10, promotion_id: 'P1', promotion_type: 'DEAL', promotion: { site_id: 'MLB' } };
  let next = recordCompletedPromotion(progress, event, (value) => value);
  progress = { ...progress, total_promotions: next.total, execute_completed_promotions: next.completed, execute_completed_promotion_tokens: next.tokens };
  next = recordCompletedPromotion(progress, event, (value) => value);
  assert.equal(next.completed, 1);
  assert.equal(next.total, 10);
  progress = { ...progress, execute_completed_promotions: next.completed, execute_completed_promotion_tokens: next.tokens };
  const second = recordCompletedPromotion(progress, { ...event, promotion_id: 'P2' }, (value) => value);
  assert.equal(second.completed, 2);
  const final = recordCompletedPromotion({ ...progress, total_promotions: 10, execute_completed_promotions: 10 }, { ...event, promotion_id: 'P11' }, (value) => value);
  assert.equal(final.completed, 10);
  assert.ok(final.completed <= final.total);
});

test('empty recovery queue is authoritative and never falls through to ordinary execution', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const start = source.indexOf('const startupPendingRecords');
  const end = source.indexOf('const readOnlyVerificationCategories', start);
  const guard = source.slice(start, end);
  assert.match(guard, /request\.resumePendingOnly && startupPendingRecords\.length === 0/);
  assert.match(guard, /recovery_queue_mismatch:\s*true/);
  assert.match(guard, /未回落普通执行/);
  assert.doesNotMatch(guard, /startupPendingRecords\.length === 0 && existingRetryablePendingCount === 0/);
});

test('stopped 113302 relation run replaces overlapping skips with 4667 read-only confirmations', () => {
  const merged = mergeExecutionRecoveryRound({
    relation_count: 113302,
    total: 113365,
    success: 0,
    failed: 0,
    skipped: 113302,
    pending: 63,
    pending_verification_count: 4667,
    retryable_pending_count: 0,
    live_verified_removed_count: 0,
    promotions: [],
  }, {
    relation_count: 4667,
    total: 4667,
    success: 4667,
    failed: 0,
    skipped: 0,
    pending: 0,
    pending_verification_count: 0,
    live_verified_removed_count: 4667,
    promotions: [],
  });
  assert.equal(merged.total, 113302);
  assert.equal(merged.relation_count, 113302);
  assert.equal(merged.success, 4667);
  assert.equal(merged.skipped, 108635);
  assert.equal(merged.pending, 0);
  assert.equal(merged.success + merged.failed + merged.skipped + merged.pending, merged.relation_count);
});

test('pending verification reads activities in bounded parallel but persists results sequentially', () => {
  const server = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const recovery = server.slice(
    server.indexOf('async function recoverPendingVerificationRecords'),
    server.indexOf('async function recoverTerminalCancelGroupReadOnly'),
  );
  assert.match(server, /PENDING_VERIFICATION_ACTIVITY_READ_CONCURRENCY = 3/);
  assert.match(recovery, /const verificationGroups = await mapLimited/);
  assert.match(recovery, /pending_verification_activity_completed \+= 1/);
  assert.match(recovery, /for \(const \{ campaign, campaignRecords, rows, verification \} of verificationGroups\)/);
  assert.match(recovery, /if \(job\.cancel_requested\)[\s\S]*execution\.cancelled = true/);

  const resume = server.slice(
    server.indexOf('if \(request.resumePendingOnly\) {'),
    server.indexOf("job.progress.stage = 'items'"),
  );
  assert.match(resume, /恢复读回后检测到停止请求，保留待确认队列并立即收口/);
  assert.match(resume, /persistExecutionJob\(job\);[\s\S]*return;/);
  assert.doesNotMatch(server, /const terminalWriteRecoveryGroups = selectTerminalWriteRecoveryGroups/);
  assert.match(server, /const terminalRecoveryGroups = terminalCancelRecoveryGroups/);
  assert.match(server, /\['enroll', 'update'\]\.includes\(String\(group\.action \|\| group\.request\?\.action \|\| ''\)\.toLowerCase\(\)\)\) continue/);
  assert.match(server, /executionGroupPersistence\.updateChild\(key, job\)/);
  assert.match(server, /后台\$\{actionDisplayName\(action\)\}确认第/);
});

test('queue-authoritative reconciliation closes the real Guangzhou 222-success accounting gap', () => {
  const records = {};
  for (let index = 0; index < 5603; index += 1) records[`success-${index}`] = { state: 'success' };
  for (let index = 0; index < 9; index += 1) records[`failed-${index}`] = { state: 'failed' };
  for (let index = 0; index < 68; index += 1) records[`pending-${index}`] = { state: 'pending' };
  const reconciled = reconcileExecutionWithPendingQueue({
    relation_count: 8174,
    total: 8174,
    success: 5381,
    failed: 1825,
    skipped: 678,
    pending: 68,
  }, { records });
  assert.deepEqual(
    [reconciled.success, reconciled.failed, reconciled.skipped, reconciled.pending, reconciled.unresolved],
    [5603, 1825, 678, 68, 0],
  );
  assert.equal(reconciled.success + reconciled.failed + reconciled.skipped + reconciled.pending, 8174);
});

test('three exhausted enroll writes with complete candidate read become terminal failures, not permanent pending', () => {
  const server = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const recovery = server.slice(
    server.indexOf('for (const row of verification.confirmed_candidate'),
    server.indexOf('let unresolvedPendingCount', server.indexOf('for (const row of verification.confirmed_candidate')),
  );
  assert.match(recovery, /status: 'failed'/);
  assert.match(recovery, /confirmed_candidate_after_max_attempts/);
  assert.match(recovery, /confirmedCandidateFailedCount \+= 1/);
  assert.doesNotMatch(recovery, /retry_category: 'manual_verification_required'/);
});

test('compact execution group retains pending and accounting fields for background UI summaries', () => {
  const server = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const compact = server.slice(
    server.indexOf('function publicExecutionGroup'),
    server.indexOf('function requestExecutionGroupCancel'),
  );
  assert.match(compact, /'pending'/);
  assert.match(compact, /'accounting_complete'/);
  assert.match(compact, /'incomplete_reasons'/);
  assert.match(server, /publishHistorySummaryForExecutionGroup\(group\.id, \{[\s\S]*authoritative: summary/);
});

test('three incomplete readbacks become terminal verification failures without another write', () => {
  const server = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const block = server.slice(
    server.indexOf('for (const row of verification.read_incomplete'),
    server.indexOf('const verifiedCount', server.indexOf('for (const row of verification.read_incomplete')),
  );
  assert.match(block, /verificationAttempts >= MAX_PENDING_VERIFICATION_READ_ATTEMPTS/);
  assert.match(block, /status: exhausted \? 'failed'/);
  assert.match(block, /read_incomplete_after_max_attempts/);
  assert.match(block, /readIncompleteFailedCount \+= 1/);
  assert.doesNotMatch(block, /executeOnePlanned|client\.request|method: 'PUT'/);
});

test('three unresolved verification polling attempts become terminal failures without endless paused loops', () => {
  const server = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const block = server.slice(
    server.indexOf('for (const row of verification.unresolved'),
    server.indexOf('for (const row of verification.read_incomplete'),
  );
  assert.match(block, /verificationAttempts >= MAX_PENDING_VERIFICATION_READ_ATTEMPTS/);
  assert.match(block, /verification_polling_exhausted/);
  assert.match(block, /pendingWriteQueue\.resolve\(job\.id, record\?\.relation_key, 'failed'/);
  assert.match(block, /verificationExhaustedCount \+= 1/);
  assert.match(server, /execution\.failed \+= .*?\+ verificationExhaustedCount/);
  assert.match(server, /execution\.pending \+= platformPendingCount \+ unresolvedPendingCount \+ retryCount \+ readIncompletePendingCount;/);
  assert.doesNotMatch(block, /executeOnePlanned|client\.request|method: 'PUT'/);
});

test('stop request prevents queued verification activities from starting new GETs', () => {
  const server = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const recovery = server.slice(
    server.indexOf('const verificationGroups = await mapLimited'),
    server.indexOf('for (const { campaign, campaignRecords, rows, verification } of verificationGroups'),
  );
  assert.match(recovery, /if \(job\.cancel_requested\)/);
  assert.match(recovery, /cancelled_before_activity_read/);
  assert.ok(recovery.indexOf('if (job.cancel_requested)') < recovery.indexOf('const client = makeWriteClient'));
});
