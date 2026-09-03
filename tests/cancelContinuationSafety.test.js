import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  assertCancelContinuationScope,
  cancelContinuationBaseline,
  cancelRecoveryFinalDelayMs,
  cancelRecoveryFreshAfter,
  isExplicitCancelContinuationRequest,
  isExplicitManualBatchRequest,
  selectTerminalCancelRecoveryGroups,
  selectTerminalWriteRecoveryGroups,
} from '../src/cancelContinuationSafety.js';
import { applyWriteRepeatGuards } from '../src/writeRepeatGuard.js';

const scopeKey = () => 'ALL-STORES';
const businessDate = () => '2026-08-28';

function runIsolated(source) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-cancel-continuation-'));
  try {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: process.cwd(), env: { ...process.env, MDM_DATA_DIR: dataDir }, encoding: 'utf8', timeout: 30_000,
    });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    return JSON.parse(String(result.stdout || '').trim());
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

test('continuation baseline ignores a newer unconfirmed oversized run and keeps the last confirmed cancellation', () => {
  const baseline = cancelContinuationBaseline({
    requestScopeKey: 'ALL-STORES', scopeKey, businessDate, now: new Date('2026-08-28T06:00:00Z'),
    groups: [
      { id: 'confirmed', action: 'cancel', status: 'cancelled', finished_at: '2026-08-28T05:24:00Z', request: {}, result: { live_verified_removed_count: 5601, failed: 9, pending: 1, retryable_pending_count: 1 } },
      { id: 'oversized', action: 'cancel', status: 'cancelled', finished_at: '2026-08-28T06:36:00Z', request: {}, result: { live_verified_removed_count: 0, failed: 0, pending: 63, pending_verification_count: 4667 } },
    ],
  });
  assert.equal(baseline.group_id, 'confirmed');
  assert.equal(baseline.expected_remaining_relations, 10);
  assert.equal(baseline.confirmed_removed_relations, 5601);
});

test('same-day cancel continuation forbids cached reads and blocks real 10-to-113302 scope growth', () => {
  const baseline = { expected_remaining_relations: 10, confirmed_removed_relations: 5601 };
  assert.throws(
    () => assertCancelContinuationScope({ baseline, liveRows: [{ cache_reused: true, is_full_fetch: true }], observedRelationCount: 10 }),
    (error) => error.code === 'CANCEL_CONTINUATION_LIVE_READ_REQUIRED',
  );
  assert.throws(
    () => assertCancelContinuationScope({ baseline, liveRows: [{ cache_reused: false, fetch_mode: 'api', is_full_fetch: true }], observedRelationCount: 113302 }),
    (error) => error.code === 'CANCEL_CONTINUATION_SCOPE_EXPANDED'
      && error.details.expected_relation_count === 10
      && error.details.observed_relation_count === 113302,
  );
  const safe = assertCancelContinuationScope({ baseline, liveRows: [{ cache_reused: false, fetch_mode: 'api', is_full_fetch: true }], observedRelationCount: 9 });
  assert.equal(safe.continuation, true);
  assert.equal(safe.observed_relation_count, 9);
});

test('manual batch cancel is a fresh batch while only explicit recovery enables continuation safety', () => {
  assert.equal(isExplicitCancelContinuationRequest({
    action: 'cancel',
    requested_action: 'cancel',
    mode: 'real',
  }), false);
  assert.equal(isExplicitCancelContinuationRequest({ action: 'cancel', resumePendingOnly: true }), true);
  assert.equal(isExplicitCancelContinuationRequest({ action: 'cancel', resume_pending_only: true }), true);
  assert.equal(isExplicitCancelContinuationRequest({ action: 'cancel', cancelContinuation: true }), true);
  assert.equal(isExplicitCancelContinuationRequest({ action: 'cancel', cancel_continuation: true }), true);

  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  assert.match(
    source,
    /resolvedAction === 'cancel' && isExplicitCancelContinuationRequest\(input\)[\s\S]*?cancelContinuationBaseline/,
  );
});

test('all three explicit manual modes bypass older same-day batch guards without weakening recovery', () => {
  for (const action of ['enroll', 'update', 'cancel']) {
    assert.equal(isExplicitManualBatchRequest({ action, requested_action: action }, action), true);
  }
  assert.equal(isExplicitManualBatchRequest({ action: 'cancel', requested_action: 'auto' }, 'cancel'), false);
  assert.equal(isExplicitManualBatchRequest({ action: 'cancel', resumePendingOnly: true }, 'cancel'), false);
  assert.equal(isExplicitManualBatchRequest({ action: 'cancel' }, 'cancel'), false);

  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  assert.match(source, /const explicitManualBatch = isExplicitManualBatchRequest\(request, action\)/);
  assert.match(source, /request\?\.resumePendingOnly \|\| explicitManualBatch[\s\S]*?\? \[\][\s\S]*?: listLatestWriteRepeatGuards/);
});

test('pending and request-success rows are repeat guarded like pending verification', () => {
  for (const status of ['pending', 'request_success', 'pending_verification']) {
    const guarded = applyWriteRepeatGuards({
      promotion: { promotion_id: 'P', promotion_type: 'DEAL' },
      rows: [{ status: 'planned', item: { item_id: 'I' }, deal_price: null }],
    }, [{ promotion_id: 'P', promotion_type: 'DEAL', item_id: 'I', status }], 'cancel');
    assert.equal(guarded.planned, 0);
    assert.equal(guarded.rows[0].repeat_guard_status, status);
  }
});

test('stopped cancel recovery is GET-only and normal writes dirty only the touched activity cache', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const recovery = source.slice(
    source.indexOf('async function recoverTerminalCancelGroupReadOnly'),
    source.indexOf('function executionTerminalCounts'),
  );
  assert.match(recovery, /recoverPendingVerificationRecords/);
  assert.doesNotMatch(recovery, /executeBatchPlans|executePlannedRowsWithConcurrency|executeOnePlanned|cancelItem/);
  assert.match(source, /invalidateActivityItemsAfterWrite/);
  assert.match(source, /itemStatuses:[\s\S]*?\['started', 'pending'\]/);
  assert.match(source, /\['candidate', 'started', 'pending'\]/);
  assert.match(source, /invalidatePendingWriteActivityCaches/);
  assert.match(source, /requiredFreshAfter = cancelRecoveryFreshAfter/);
  assert.match(source, /stateFreshAndFull\(startedState\)[\s\S]*stateFreshAndFull\(pendingState\)/);
  assert.match(source, /Number\(activityState\?\.dirty \|\| 0\) === 0/);
});

test('cancel invalidation removes only started and pending fetch states', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    const route = { childUserId: 'C', siteId: 'MLB' };
    for (const status of ['candidate', 'started', 'pending']) repo.saveItemFetchState({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', promotionId: 'P', promotionType: 'DEAL',
      itemStatus: status, platformTotal: 1, savedCount: 1, detailStatus: 'full'
    });
    repo.invalidatePromotionItemFetchStates({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', promotionId: 'P', promotionType: 'DEAL',
      itemStatuses: ['started', 'pending']
    });
    console.log(JSON.stringify({
      candidate: Boolean(repo.getItemFetchState('A','P','DEAL','candidate',route)),
      started: Boolean(repo.getItemFetchState('A','P','DEAL','started',route)),
      pending: Boolean(repo.getItemFetchState('A','P','DEAL','pending',route)),
    }));
  `);
  assert.deepEqual(result, { candidate: true, started: false, pending: false });
});

test('startup recovery isolates history and selects only the latest two stopped cancel groups today', () => {
  const groups = [
    { id: 'old-history', action: 'cancel', status: 'cancelled', cancel_requested: true, finished_at: '2026-08-20T06:00:00Z' },
    { id: 'today-not-stopped', action: 'cancel', status: 'cancelled', cancel_requested: false, finished_at: '2026-08-28T04:00:00Z' },
    { id: 'today-enroll', action: 'enroll', status: 'cancelled', cancel_requested: true, finished_at: '2026-08-28T05:00:00Z' },
    { id: 'today-first', action: 'cancel', status: 'cancelled', cancel_requested: true, finished_at: '2026-08-28T05:24:00Z' },
    { id: 'today-second', action: 'cancel', status: 'cancelled', cancel_requested: true, finished_at: '2026-08-28T06:36:00Z' },
    { id: 'today-third', action: 'cancel', status: 'failed', cancel_requested: true, finished_at: '2026-08-28T07:00:00Z' },
  ];
  const selected = selectTerminalCancelRecoveryGroups({
    groups,
    businessDate: (value) => value instanceof Date ? '2026-08-28' : String(value).slice(0, 10),
    now: new Date('2026-08-28T08:00:00Z'),
    limit: 2,
  });
  assert.deepEqual(selected.map((group) => group.id), ['today-third', 'today-second']);
  assert.ok(!selected.some((group) => group.id === 'old-history'));
});

test('stopped enroll and update groups expose only the latest same-day read-only recovery scope', () => {
  const groups = [
    { id: 'old', action: 'enroll', status: 'interrupted', cancel_requested: true, finished_at: '2026-08-27T01:00:00Z' },
    { id: 'cancel', action: 'cancel', status: 'cancelled', cancel_requested: true, finished_at: '2026-08-28T04:00:00Z' },
    { id: 'enroll', action: 'enroll', status: 'interrupted', cancel_requested: true, finished_at: '2026-08-28T05:00:00Z' },
    { id: 'update', action: 'update', status: 'cancelled', cancel_requested: true, finished_at: '2026-08-28T06:00:00Z' },
  ];
  const selected = selectTerminalWriteRecoveryGroups({
    groups,
    businessDate: (value) => value instanceof Date ? '2026-08-28' : String(value).slice(0, 10),
    now: new Date('2026-08-28T08:00:00Z'),
    limit: 1,
  });
  assert.deepEqual(selected.map((group) => group.id), ['update']);
});

test('terminal cancel recovery waits for startup and reuses fresh complete started and pending cache before platform reads', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const cached = source.slice(
    source.indexOf('function readAppliedWriteRowsFromAuthoritativeCache'),
    source.indexOf('function isCompletePromotionRead'),
  );
  assert.match(cached, /getItemFetchState[\s\S]*?'started'/);
  assert.match(cached, /getItemFetchState[\s\S]*?'pending'/);
  assert.match(cached, /getItemFetchState[\s\S]*?'candidate'/);
  assert.match(cached, /\['cancel', 'enroll', 'update'\]\.includes\(normalizedAction\)/);
  assert.match(cached, /isStateFull/);
  assert.match(cached, /updated >= threshold/);
  assert.match(cached, /authoritative_startup_cache/);

  const terminal = source.slice(
    source.indexOf('async function recoverTerminalCancelGroupReadOnly'),
    source.indexOf('function executionTerminalCounts'),
  );
  assert.match(terminal, /startupCacheRefreshState\.status/);
  assert.match(terminal, /recoverTerminalCancelGroupReadOnly\(key, wave\)/);
  assert.match(terminal, /\['cancel', 'enroll', 'update'\]\.includes\(action\)/);

  const recovery = source.slice(
    source.indexOf('async function recoverPendingVerificationRecords'),
    source.indexOf('async function recoverTerminalCancelGroupReadOnly'),
  );
  assert.match(recovery, /readAppliedWriteRowsFromAuthoritativeCache/);
  assert.match(recovery, /if \(!verification\)[\s\S]*?confirmAppliedWrites/);
  assert.match(recovery, /verification_source/);
});

test('enroll and update recovery reuse only a fresh complete three-status startup snapshot', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const cached = source.slice(
    source.indexOf('function readAppliedWriteRowsFromAuthoritativeCache'),
    source.indexOf('function isCompletePromotionRead'),
  );
  assert.match(cached, /const candidateState = normalizedAction === 'cancel'/);
  assert.match(cached, /normalizedAction !== 'cancel' && !stateIsFreshAndFull\(candidateState\)/);
  assert.match(cached, /candidateItems: normalizedAction === 'cancel'[\s\S]*listItems\([^\n]*'candidate'/);
  assert.match(cached, /source: 'authoritative_startup_cache'/);
});

test('startup started stage refreshes and persists both started and pending cancellation states', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const startup = source.slice(
    source.indexOf("for (const itemStatus of ['candidate', 'started'])"),
    source.indexOf("await Promise.allSettled(accounts.map", source.indexOf("for (const itemStatus of ['candidate', 'started'])")),
  );
  assert.match(startup, /action: itemStatus === 'candidate' \? 'enroll' : 'cancel'/);
  assert.match(source, /action === 'cancel' && itemStatus === 'started'/);
  assert.match(source, /CANCEL_ITEM_STATUSES\.map/);

  const persisted = runIsolated(`
    const repo = await import('./src/repository.js');
    const route = { childUserId: 'C', siteId: 'MLB' };
    repo.saveItemFetchState({
      accountId: 'A', childUserId: 'C', siteId: 'MLB', promotionId: 'P', promotionType: 'DEAL',
      itemStatus: 'pending', platformTotal: 0, savedCount: 0, detailStatus: 'empty'
    });
    const state = repo.getItemFetchState('A', 'P', 'DEAL', 'pending', route);
    console.log(JSON.stringify({
      exists: Boolean(state),
      platform_total: state?.platform_total,
      saved_count: state?.saved_count,
      detail_status: state?.detail_status,
    }));
  `);
  assert.deepEqual(persisted, {
    exists: true,
    platform_total: 0,
    saved_count: 0,
    detail_status: 'empty',
  });
});

test('terminal cancellation schedules one fresh read after the latest grace deadline', () => {
  const records = [
    { first_pending_at: '2026-08-28T06:30:00.000Z' },
    { first_pending_at: '2026-08-28T06:33:00.000Z' },
  ];
  const graceMs = 120 * 60 * 1000;
  assert.equal(
    cancelRecoveryFreshAfter(records, { graceMs, nowMs: Date.parse('2026-08-28T08:20:00.000Z') }),
    '2026-08-28T06:33:00.000Z',
  );
  assert.equal(
    cancelRecoveryFinalDelayMs(records, { graceMs, nowMs: Date.parse('2026-08-28T08:20:00.000Z') }),
    13 * 60 * 1000,
  );
  assert.equal(
    cancelRecoveryFreshAfter(records, { graceMs, nowMs: Date.parse('2026-08-28T08:34:00.000Z') }),
    '2026-08-28T08:33:00.000Z',
  );
  assert.equal(
    cancelRecoveryFinalDelayMs(records, { graceMs, nowMs: Date.parse('2026-08-28T08:34:00.000Z') }),
    0,
  );

  const source = fs.readFileSync(path.join(process.cwd(), 'src', 'server.js'), 'utf8');
  const recovery = source.slice(
    source.indexOf('async function recoverTerminalCancelGroupReadOnly'),
    source.indexOf('function executionTerminalCounts'),
  );
  assert.match(recovery, /cancelRecoveryFinalDelayMs/);
  assert.match(recovery, /finalGraceDelayMs \+ 1_000/);
  assert.match(source, /cancelRecoveryFreshAfter\(campaignRecords/);
  assert.match(source, /const finalCancelRead = action === 'cancel'/);
  assert.match(source, /settleDelaysMs: finalCancelRead[\s\S]*?\? \[\]/);
  const invalidation = source.slice(
    source.indexOf('function invalidatePendingWriteActivityCaches'),
    source.indexOf('async function initializeServerState'),
  );
  assert.match(invalidation, /if \(reusable\) continue/);
  assert.match(invalidation, /invalidatePromotionItemFetchStates/);
});
