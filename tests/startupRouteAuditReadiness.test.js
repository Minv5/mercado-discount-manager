import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import {
  assessStartupReadiness,
  classifyStartupPromotionWindow,
  createStartupAccountAudit,
  finishStartupAccountAudit,
  partitionStartupPromotions,
  recordStartupAuditStage,
} from '../src/startupRefreshAudit.js';

const ROUTES = {
  hubei: { account_id: '2651442567', child_user_id: '2659555001', site_id: 'MLB' },
  hunan: { account_id: '3408885754', child_user_id: '3409555001', site_id: 'MLB' },
  guangdong: { account_id: '3332096437', child_user_id: '3333555001', site_id: 'MLB' },
};

function completeAudit(account, route, { detailError = false, reused = false } = {}) {
  let audit = createStartupAccountAudit({ accountId: account, storeName: account, routes: [route] });
  for (const stage of ['catalog', 'candidate', 'started']) {
    audit = recordStartupAuditStage(audit, {
      stage,
      status: detailError && stage === 'started' ? 'failed' : reused ? 'reused' : 'ok',
      mode: reused ? 'reused' : 'executed',
      sub_stages: [
        { stage: 'detail', status: detailError && stage === 'started' ? 'failed' : reused ? 'reused' : 'ok', ready: !(detailError && stage === 'started') },
        { stage: 'cache', status: reused ? 'reused' : 'ok', ready: true },
      ],
      ...(detailError && stage === 'started' ? { error_cn: '商品明细读取失败。' } : {}),
      routes: [{ ...route, status: detailError && stage === 'started' ? 'failed' : reused ? 'reused' : 'ok', mode: reused ? 'reused' : 'executed', ready: !(detailError && stage === 'started'), ...(detailError && stage === 'started' ? { error_cn: '商品明细读取失败。' } : {}) }],
    });
  }
  return finishStartupAccountAudit(audit, { status: detailError ? 'failed' : 'ok' });
}

test('three-account out-of-order completion preserves one audit per shop', () => {
  const audits = [
    completeAudit('3332096437', ROUTES.guangdong),
    completeAudit('2651442567', ROUTES.hubei),
    completeAudit('3408885754', ROUTES.hunan),
  ];
  const result = assessStartupReadiness({
    accountAudits: audits,
    requiredAccounts: Object.keys(ROUTES).map((key) => ({ account_id: key === 'hubei' ? '2651442567' : key === 'hunan' ? '3408885754' : '3332096437' })),
    cbtReplay: { remaining_events: 0, remaining_unique_resources: 0, failed: 0, unresolved: 0 },
  });
  assert.equal(result.ready, true);
  assert.deepEqual(new Set(result.ready_accounts), new Set(['2651442567', '3408885754', '3332096437']));
});

test('missing Guangdong audit remains unknown and blocks readiness', () => {
  const result = assessStartupReadiness({
    accountAudits: [completeAudit('2651442567', ROUTES.hubei), completeAudit('3408885754', ROUTES.hunan)],
    requiredAccounts: [{ account_id: '2651442567' }, { account_id: '3408885754' }, { account_id: '3332096437' }],
    cbtReplay: { remaining_events: 0, remaining_unique_resources: 0 },
  });
  assert.equal(result.ready, false);
  assert.deepEqual(result.unknown_accounts, ['3332096437']);
  assert.ok(result.reasons.some((reason) => reason.code === 'startup_account_audit_missing'));
});

test('detail error is a failed stage, not a successful all-settled refresh', () => {
  const audit = completeAudit('3332096437', ROUTES.guangdong, { detailError: true });
  const startedStage = audit.stages.find((stage) => stage.stage === 'started');
  assert.equal(startedStage?.ready, false);
  assert.ok(startedStage?.error_cn);
  assert.equal(startedStage?.sub_stages?.find((stage) => stage.stage === 'detail')?.status, 'failed');
  const result = assessStartupReadiness({
    accountAudits: [audit],
    requiredAccounts: [{ account_id: '3332096437' }],
    cbtReplay: { remaining_events: 0, remaining_unique_resources: 0 },
  });
  assert.equal(result.ready, false);
  assert.ok(result.reasons.some((reason) => reason.code === 'startup_stage_failed'));
});

test('safe cache reuse is ready when recent replay has no remaining work', () => {
  const result = assessStartupReadiness({
    accountAudits: [completeAudit('2651442567', ROUTES.hubei, { reused: true })],
    requiredAccounts: [{ account_id: '2651442567' }],
    cbtReplay: { remaining_events: 0, remaining_unique_resources: 0, failed: 0, unresolved: 0 },
    historicalOutsideRecent: { event_count: 3137, item_count: 2902 },
  });
  assert.equal(result.ready, true);
  assert.deepEqual(result.history_outside_recent, { event_count: 3137, item_count: 2902 });
});

test('remaining, retained failure, and budget exhaustion each block readiness', () => {
  const accountAudits = [completeAudit('2651442567', ROUTES.hubei)];
  const remaining = assessStartupReadiness({ accountAudits, requiredAccounts: [{ account_id: '2651442567' }], cbtReplay: { remaining_events: 1, remaining_unique_resources: 1 } });
  assert.equal(remaining.ready, false);
  assert.ok(remaining.reasons.some((reason) => reason.code === 'cbt_replay_remaining'));
  const failed = assessStartupReadiness({ accountAudits, requiredAccounts: [{ account_id: '2651442567' }], cbtReplay: { remaining_events: 0, remaining_unique_resources: 0, unresolved: 2 } });
  assert.equal(failed.ready, false);
  assert.ok(failed.reasons.some((reason) => reason.code === 'cbt_replay_retained_failure'));
  const budget = assessStartupReadiness({ accountAudits, requiredAccounts: [{ account_id: '2651442567' }], cbtReplay: { remaining_events: 0, remaining_unique_resources: 0, budget_exhausted: true } });
  assert.equal(budget.ready, false);
  assert.ok(budget.reasons.some((reason) => reason.code === 'cbt_replay_budget_exhausted'));
  const duplicateSummary = assessStartupReadiness({
    accountAudits,
    requiredAccounts: [{ account_id: '2651442567' }],
    cbtReplay: { remaining_events: 0, remaining_unique_resources: 0, unresolved: 392, failed: 0, unique_resource_unresolved: 351 },
  });
  assert.equal(duplicateSummary.replay_unique_retained_failure, 351);
  assert.match(duplicateSummary.reasons.find((reason) => reason.code === 'cbt_replay_retained_failure')?.reason_cn || '', /351/);
});

test('readiness uses unique classification snapshot: terminal rows do not block and partial remains eligible', () => {
  const accountAudits = [completeAudit('2651442567', ROUTES.hubei)];
  const result = assessStartupReadiness({
    accountAudits,
    requiredAccounts: [{ account_id: '2651442567' }],
    cbtReplay: {
      remaining_events: 66,
      remaining_unique_resources: 59,
      remaining_eligible_events: 1,
      remaining_eligible_resources: 1,
      classification_counts: {
        quarantined_unknown: 59,
        terminal_irrelevant: 6,
        eligible_partial: 1,
      },
      classification_item_counts: {
        quarantined_unknown: 52,
        terminal_irrelevant: 6,
        eligible_partial: 1,
      },
      unique_resource_succeeded: 51,
      cache_updated_count: 236,
      physical_get_used: 367,
      physical_budget: 1000,
    },
  });
  assert.equal(result.ready, false);
  assert.equal(result.replay_quarantined_unknown, 52);
  assert.equal(result.replay_terminal_irrelevant, 6);
  assert.equal(result.replay_eligible_partial, 1);
  assert.equal(result.replay_unique_retained_failure, 52);
  assert.equal(result.replay_remaining_eligible_events, 1);
  assert.ok(result.reasons.some((reason) => reason.code === 'cbt_replay_remaining'));
  assert.match(result.reasons.find((reason) => reason.code === 'cbt_replay_retained_failure')?.reason_cn || '', /52/);
  assert.doesNotMatch(result.reasons.find((reason) => reason.code === 'cbt_replay_retained_failure')?.reason_cn || '', /59 个商品资源/);
});

test('terminal-only unresolved snapshot is safely excluded from readiness blocking', () => {
  const result = assessStartupReadiness({
    accountAudits: [completeAudit('2651442567', ROUTES.hubei)],
    requiredAccounts: [{ account_id: '2651442567' }],
    cbtReplay: {
      remaining_events: 6,
      remaining_unique_resources: 6,
      remaining_eligible_events: 0,
      remaining_eligible_resources: 0,
      classification_counts: { terminal_irrelevant: 6 },
      classification_item_counts: { terminal_irrelevant: 6 },
    },
  });
  assert.equal(result.ready, true);
  assert.deepEqual(result.reasons, []);
  assert.equal(result.replay_terminal_irrelevant, 6);
});

test('readiness isolates quarantined accounts by selected scope', () => {
  const audits = [
    completeAudit('2651442567', ROUTES.hubei),
    completeAudit('3332096437', ROUTES.guangdong),
    completeAudit('3408885754', ROUTES.hunan),
  ];
  const replay = {
    remaining_events: 14,
    remaining_unique_resources: 14,
    remaining_eligible_events: 0,
    remaining_eligible_resources: 0,
    classification_counts: { quarantined_unknown: 14 },
    classification_item_counts: { quarantined_unknown: 14 },
    classification_account_counts: { quarantined_unknown: { '3332096437': 14 } },
    classification_item_account_counts: { quarantined_unknown: { '3332096437': 14 } },
  };
  const readyScope = assessStartupReadiness({
    accountAudits: audits,
    requiredAccounts: Object.keys(ROUTES).map((key) => ({ account_id: ROUTES[key].account_id })),
    cbtReplay: replay,
    scopeAccountIds: ['2651442567', '3408885754'],
  });
  assert.equal(readyScope.ready, true);
  assert.deepEqual(readyScope.blocked_account_ids, []);
  const blockedScope = assessStartupReadiness({
    accountAudits: audits,
    requiredAccounts: Object.keys(ROUTES).map((key) => ({ account_id: ROUTES[key].account_id })),
    cbtReplay: replay,
    scopeAccountIds: ['3332096437'],
  });
  assert.equal(blockedScope.ready, false);
  assert.deepEqual(blockedScope.blocked_account_ids, ['3332096437']);
  assert.equal(blockedScope.replay_quarantined_unknown, 14);
});

test('unknown replay state blocks instead of treating a stale zero as ready', () => {
  const result = assessStartupReadiness({
    accountAudits: [completeAudit('2651442567', ROUTES.hubei)],
    requiredAccounts: [{ account_id: '2651442567' }],
    cbtReplay: { remaining_events: null },
  });
  assert.equal(result.ready, false);
  assert.ok(result.reasons.some((reason) => reason.code === 'cbt_replay_remaining_unknown'));
});

test('startup filters exact finished activities but keeps same-day unknown times blocking on 404', () => {
  const now = new Date('2026-08-24T06:11:00.000Z');
  const finished = { account_id: '2651442567', child_user_id: '2668031897', site_id: 'MLB', promotion_id: 'P-EXPIRED', promotion_type: 'DEAL', finish_date: '2026-08-24T03:00:00Z' };
  const future = { ...finished, promotion_id: 'P-ACTIVE', finish_date: '2026-08-24T09:00:00Z' };
  const dateOnly = { ...finished, promotion_id: 'P-UNKNOWN-TIME', finish_date: '2026-08-24' };
  assert.equal(classifyStartupPromotionWindow(finished, now).status, 'expired');
  assert.equal(classifyStartupPromotionWindow(future, now).status, 'active');
  assert.equal(classifyStartupPromotionWindow(dateOnly, now).time_complete, false);
  const partition = partitionStartupPromotions([finished, future, dateOnly, finished], { now });
  assert.deepEqual(partition.expired.map(({ promotion }) => promotion.promotion_id), ['P-EXPIRED']);
  assert.deepEqual(partition.active.map((promotion) => promotion.promotion_id), ['P-ACTIVE', 'P-UNKNOWN-TIME']);
  assert.deepEqual(partition.uncertain.map(({ promotion }) => promotion.promotion_id), ['P-UNKNOWN-TIME']);
});

test('server integration keeps per-shop progress, auto-replay scope, and no ambiguous aggregate label', () => {
  const serverSource = fs.readFileSync(path.resolve(import.meta.dirname, '../src/server.js'), 'utf8');
  assert.match(serverSource, /account_audits/);
  assert.match(serverSource, /STARTUP_CBT_REPLAY_TOTAL_BUDGET/);
  assert.match(serverSource, /runCbtReplayWaves\(\{[\s\S]*replay: cbtWebhookReplay/);
  assert.match(serverSource, /createCbtItemRoutesResolver\(\{ createResourceClient: createWebhookResourceClient \}\)/);
  assert.doesNotMatch(serverSource, /const client = await createResourceClient\(\{ account_id: event\.remote_user_id/);
  assert.match(serverSource, /partitionStartupPromotions/);
  assert.match(serverSource, /expireStartupPromotionLocally/);
  assert.match(serverSource, /onProgress: \(progress\)/);
  assert.match(serverSource, /progress_total/);
  assert.match(serverSource, /account_progress/);
  assert.match(serverSource, /replay_progress/);
  assert.match(serverSource, /const replayScopeRawTotal = replayScope\.event_count/);
  assert.doesNotMatch(serverSource, /eligible_event_count \?\? replayScope\.event_count/);
  assert.match(serverSource, /startupReadinessForScope/);
  assert.match(serverSource, /STARTUP_SCOPE_NOT_READY/);
  assert.match(serverSource, /商品通知补偿：正在统计最近48小时待处理数据/);
  assert.match(serverSource, /商品补偿第\$\{replayProgress\.wave\}波第\$\{replayProgress\.page\}页/);
  assert.doesNotMatch(serverSource, /account_index: accountTotal/);
  assert.doesNotMatch(serverSource, /Math\.min\(99, accountTotal \? 92/);
  assert.doesNotMatch(serverSource, /Math\.min\(98, 92/);
  assert.match(serverSource, /wavePhysicalBudget: STARTUP_CBT_REPLAY_TOTAL_BUDGET/);
  assert.match(serverSource, /STARTUP_CBT_REPLAY_MAX_WAVES/);
  assert.match(serverSource, /new Date\(Date\.now\(\) - 48 \* 60 \* 60 \* 1000\)/);
  assert.match(serverSource, /status: readiness\.ready === true \? 'ok' : 'blocked'/);
  assert.match(serverSource, /STARTUP_BACKGROUND_INITIAL_DELAY_MS = 60_000/);
  assert.match(serverSource, /const backgroundStartupTimer = setTimeout/);
  assert.match(serverSource, /startStartupBackgroundWorkOnce/);
  assert.match(serverSource, /\/api\/startup-refresh\/start/);
  assert.match(serverSource, /status: started \? 'started' : 'already_started'/);
  assert.doesNotMatch(serverSource, /setImmediate\(\(\) => resumePersistedExecutionSubmissions\(\)\)/);
  assert.doesNotMatch(serverSource, /activeAccounts\.join/);
  assert.match(serverSource, /startupReadinessForScope\(accountIds\)/);
});

test('Webhook-recovered cache rows are auditable full cache rows, never inherited errors', () => {
  const source = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  assert.match(source, /const recoveredFromWebhook = decision\.reason === 'webhook_recovered_cache'/);
  assert.match(source, /recoveredRelationFetchState\(/);
  assert.match(source, /saveItemFetchState\(\{/);
  assert.match(source, /selectedState\?\.saved_count/);
  assert.match(source, /detail_status: recoveredFromWebhook \? 'full'/);
  assert.match(source, /blocked: recoveredFromWebhook \? false/);
  assert.match(source, /旧读取错误已被较新的完整本地关系校准覆盖/);
});
