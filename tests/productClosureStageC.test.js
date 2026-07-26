import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { spawn } from 'node:child_process';

import {
  PROMOTION_BUCKETS,
  ordinaryPromotions,
  partitionPromotions,
  promotionBucket,
  promotionBucketCounts,
} from '../src/promotionDomain.js';
import { filterPromotions } from '../src/planner.js';
import { classifyPromotionType } from '../src/cycle.js';
import { classifyPrepareError, prepareErrorMessage } from '../src/errors.js';
import {
  claimSubmissionPreparation,
  cancelSubmissionCommit,
  commitSubmission,
  createSubmissionPersistence,
  refreshSubmissionDeadline,
  resumePausedSubmission,
  runSubmissionPreparation,
  submissionRequestFingerprint,
  submissionScopeHash,
} from '../src/submissionPersistence.js';

function temporaryDirectory() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-stage-c-'));
}

async function waitForStageCHealth(baseUrl) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error('stage C test server did not become healthy');
}

test('promotion domain has one exact five-bucket classification', () => {
  assert.equal(promotionBucket('SELLER_CAMPAIGN'), PROMOTION_BUCKETS.seller);
  assert.equal(promotionBucket('deal'), PROMOTION_BUCKETS.official);
  assert.equal(promotionBucket('SMART'), PROMOTION_BUCKETS.smart);
  assert.equal(promotionBucket('LIGHTNING'), PROMOTION_BUCKETS.lightning);
  assert.equal(promotionBucket('PIX'), PROMOTION_BUCKETS.other);
  const rows = [
    { promotion_id: 'C', promotion_type: 'SELLER_CAMPAIGN' },
    { promotion_id: 'D', promotion_type: 'DEAL' },
    { promotion_id: 'S', promotion_type: 'SMART' },
    { promotion_id: 'L', promotion_type: 'LIGHTNING' },
    { promotion_id: 'X', promotion_type: 'OTHER' },
  ];
  assert.deepEqual(ordinaryPromotions(rows).map((row) => row.promotion_id), ['C', 'D']);
  assert.deepEqual(Object.fromEntries(Object.entries(partitionPromotions(rows)).map(([key, value]) => [key, value.length])), {
    seller: 1, official: 1, smart: 1, lightning: 1, other: 1,
  });
  assert.deepEqual(promotionBucketCounts(rows), { seller: 1, official: 1, smart: 1, lightning: 1, other: 1 });
  assert.equal(classifyPromotionType('SMART'), null);
  assert.equal(classifyPromotionType('LIGHTNING'), null);
});

test('ordinary activity selection matrix never admits SMART LIGHTNING or other', () => {
  const promotions = [
    { account_id: 'A', site_id: 'MLM', promotion_id: 'C-A', promotion_type: 'SELLER_CAMPAIGN', name: '95' },
    { account_id: 'A', site_id: 'MLM', promotion_id: 'D-A', promotion_type: 'DEAL', name: 'Deal A' },
    { account_id: 'A', site_id: 'MLM', promotion_id: 'S-A', promotion_type: 'SMART', name: 'Smart' },
    { account_id: 'A', site_id: 'MLM', promotion_id: 'L-A', promotion_type: 'LIGHTNING', name: 'Lightning' },
    { account_id: 'B', site_id: 'MLB', promotion_id: 'C-B', promotion_type: 'SELLER_CAMPAIGN', name: '90' },
    { account_id: 'B', site_id: 'MLB', promotion_id: 'D-B', promotion_type: 'DEAL', name: 'Deal B' },
    { account_id: 'B', site_id: 'MLB', promotion_id: 'X-B', promotion_type: 'PIX', name: 'Other' },
  ];
  const actions = ['auto', 'enroll', 'update', 'cancel'];
  const accountScopes = [null, 'A'];
  const siteScopes = [null, 'MLM'];
  const sellerModes = ['all', 'exclude', '95'];
  const officialModes = ['all', 'exclude', 'Deal A'];
  let cases = 0;
  for (const _action of actions) for (const account of accountScopes) for (const site of siteScopes) for (const seller of sellerModes) for (const official of officialModes) {
    const source = account ? promotions.filter((row) => row.account_id === account) : promotions;
    const filters = {
      siteIds: site ? [site] : [],
      excludeSeller: seller === 'exclude',
      excludeOfficial: official === 'exclude',
      sellerActivityNames: !['all', 'exclude'].includes(seller) ? [seller] : [],
      officialActivityNames: !['all', 'exclude'].includes(official) ? [official] : [],
    };
    const selected = ordinaryPromotions(filterPromotions(source, filters));
    assert.ok(selected.every((row) => ['SELLER_CAMPAIGN', 'DEAL'].includes(row.promotion_type)));
    cases += 1;
  }
  assert.equal(cases, 144);
});

test('submission persistence is idempotent by client submission and survives reload', () => {
  const stateDir = temporaryDirectory();
  const store = createSubmissionPersistence({ stateDir, now: () => '2026-07-14T00:00:00.000Z' });
  const input = {
    id: 'prepare-1', client_submission_id: 'submit-1', state: 'prepared',
    scope_hash: submissionScopeHash({ accountIds: ['A'], action: 'enroll' }),
    expires_at: '2026-07-15T00:00:00.000Z',
  };
  assert.equal(store.create(input).reused, false);
  assert.equal(store.create({ ...input, id: 'prepare-2' }).prepare.id, 'prepare-1');
  const reloaded = createSubmissionPersistence({ stateDir, now: () => '2026-07-14T00:00:01.000Z' });
  assert.equal(reloaded.findBySubmissionId('submit-1').id, 'prepare-1');
});

test('submission persistence versions every mutation and rejects stale compare-and-swap', () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-16T00:00:00.000Z' });
  const created = store.create({ id: 'prepare-versioned', client_submission_id: 'submit-versioned', state: 'prepared' }).prepare;
  assert.equal(created.version, 1);
  const changed = store.compareAndSwap(created.id, { version: 1, state: 'prepared' }, { state: 'committing' });
  assert.equal(changed.ok, true);
  assert.equal(changed.prepare.version, 2);
  const stale = store.compareAndSwap(created.id, { version: 1, state: 'prepared' }, { state: 'failed' });
  assert.equal(stale.ok, false);
  assert.equal(stale.prepare.state, 'committing');
  assert.equal(stale.prepare.version, 2);
});

test('submission progress persistence retries a transient EPERM, uses unique temporary names, and coalesces high-frequency updates', async () => {
  const stateDir = temporaryDirectory();
  const calls = [];
  let renameAttempts = 0;
  const fsOps = {
    ...fs,
    renameSync(from, to) {
      calls.push({ from, to });
      renameAttempts += 1;
      if (renameAttempts === 1) {
        const error = Object.assign(new Error('sharing violation'), { code: 'EPERM', syscall: 'rename', path: to });
        throw error;
      }
      return fs.renameSync(from, to);
    },
  };
  const store = createSubmissionPersistence({ stateDir, fsOps, retryDelaysMs: [0, 0] });
  store.create({ id: 'prepare-eprem', client_submission_id: 'submit-eprem', state: 'preparing', request: {} });
  calls.length = 0;
  renameAttempts = 0;
  for (let index = 1; index <= 40; index += 1) {
    store.queueProgress('prepare-eprem', { stage: 'started', percent: index, message: `进度 ${index}` });
  }
  await new Promise((resolve) => setTimeout(resolve, 20));
  store.flushProgress('prepare-eprem');
  const persisted = createSubmissionPersistence({ stateDir }).load('prepare-eprem');
  assert.equal(persisted.progress.percent, 40);
  assert.equal(renameAttempts, 2);
  assert.notEqual(calls[0].from, calls[1].from);
  assert.ok(persisted.version < 10, `expected coalesced writes, got version ${persisted.version}`);
});

test('submission preparation is persisted before a delayed snapshot and survives restart', async () => {
  const stateDir = temporaryDirectory();
  const store = createSubmissionPersistence({ stateDir, now: () => '2026-07-15T04:00:00.000Z' });
  store.create({
    id: 'prepare-delayed', client_submission_id: 'submit-delayed', state: 'preparing',
    request: { accountIds: ['A'], requested_action: 'update' },
    progress: { stage: 'queued', percent: 0, message: '正在排队准备执行范围' },
  });
  let releaseSnapshot;
  const delayed = new Promise((resolve) => { releaseSnapshot = resolve; });
  const running = runSubmissionPreparation({
    store,
    prepareId: 'prepare-delayed',
    buildSnapshot: async (_request, reportProgress) => {
      reportProgress({ stage: 'activities', percent: 40, message: '正在核对活动' });
      reportProgress({
        read_scheduler: {
          dynamic_limit: 120,
          max_limit: 125,
          inflight: 9,
          peak: 12,
          detail_inflight: 7,
          fallback_active: 2,
          queued: 18,
          cooldown_ms: 2500,
          rate_limit_count: 1,
          network_error_count: 2,
          service_error_count: 3,
          timeout_error_count: 4,
          failure_count: 5,
          retry_count: 2,
          per_account: { A: 4 },
        },
      });
      await delayed;
      return { scope_hash: 'HASH', resolved_action: 'update' };
    },
    preparedPatch: (snapshot) => ({ ...snapshot, expires_at: '2026-07-15T04:15:00.000Z' }),
  });
  store.flushProgress('prepare-delayed');
  const reloaded = createSubmissionPersistence({ stateDir, now: () => '2026-07-15T04:00:01.000Z' });
  assert.equal(reloaded.load('prepare-delayed').state, 'preparing');
  assert.equal(reloaded.load('prepare-delayed').progress.percent, 40);
  assert.equal(reloaded.load('prepare-delayed').progress.read_scheduler.dynamic_limit, 120);
  assert.equal(reloaded.load('prepare-delayed').progress.read_scheduler.per_account.A, 4);
  assert.equal(reloaded.load('prepare-delayed').progress.read_scheduler.network_error_count, 2);
  assert.equal(reloaded.load('prepare-delayed').progress.read_scheduler.service_error_count, 3);
  assert.equal(reloaded.load('prepare-delayed').progress.read_scheduler.timeout_error_count, 4);
  assert.equal(reloaded.load('prepare-delayed').progress.read_scheduler.failure_count, 5);
  releaseSnapshot();
  await running;
  const completed = createSubmissionPersistence({ stateDir, now: () => '2026-07-15T04:00:02.000Z' }).load('prepare-delayed');
  assert.equal(completed.state, 'prepared');
  assert.equal(completed.scope_hash, 'HASH');
  assert.equal(completed.progress.percent, 100);
});

test('submission preparation failure is terminal and never starts a group', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory() });
  store.create({ id: 'prepare-failed', client_submission_id: 'submit-failed', state: 'preparing', request: {} });
  const result = await runSubmissionPreparation({
    store,
    prepareId: 'prepare-failed',
    buildSnapshot: async () => { throw new Error('范围读取失败'); },
    preparedPatch: () => ({}),
  });
  assert.equal(result.state, 'failed');
  assert.match(result.error, /范围读取失败/);
  assert.equal(result.group_id ?? null, null);
});

test('submission preparation failure preserves safe context, scheduler evidence and structured audit', async () => {
  const stateDir = temporaryDirectory();
  const store = createSubmissionPersistence({ stateDir, now: () => '2026-07-20T05:42:25.743Z' });
  store.create({ id: 'prepare-observed-failure', client_submission_id: 'submit-observed-failure', state: 'preparing', request: {} });
  const logged = [];
  const result = await runSubmissionPreparation({
    store,
    prepareId: 'prepare-observed-failure',
    buildSnapshot: async (_request, reportProgress) => {
      reportProgress({
        stage: 'catalog', percent: 8, completed: 0, total: 3,
        current_store: '广州', current_site: '墨西哥站', current_activity: '95',
        read_scheduler: { dynamic_limit: 120, max_limit: 125, inflight: 7, peak: 12, queued: 4 },
      });
      throw Object.assign(new Error('upstream service unavailable'), {
        status: 503, code: 'HTTP_RESPONSE_ERROR', cause: { code: 'UND_ERR_SOCKET' }, operation: 'activity_catalog',
      });
    },
    preparedPatch: () => ({}),
    classifyError: classifyPrepareError,
    formatError: (error) => prepareErrorMessage(classifyPrepareError(error)),
    logFailure: (event) => logged.push(event),
  });
  assert.equal(result.state, 'failed');
  assert.equal(result.error, '平台服务暂时异常，请稍后重新核对。');
  assert.equal(result.error_kind, 'service');
  assert.equal(result.http_status, 503);
  assert.equal(result.code, 'HTTP_RESPONSE_ERROR');
  assert.equal(result.cause_code, 'UND_ERR_SOCKET');
  assert.equal(result.operation, 'activity_catalog');
  assert.equal(result.stage, 'catalog');
  assert.equal(result.account, '广州');
  assert.equal(result.site, '墨西哥站');
  assert.equal(result.activity, '95');
  assert.equal(result.progress.current_store, '广州');
  assert.equal(result.progress.current_site, '墨西哥站');
  assert.equal(result.progress.current_activity, '95');
  assert.equal(result.progress.read_scheduler.dynamic_limit, 120);
  assert.equal(result.progress.read_scheduler.peak, 12);
  assert.equal(logged.length, 1);
  assert.equal(logged[0].type, 'prepare_failed');
  assert.equal(logged[0].error_kind, 'service');
  assert.equal(logged[0].account, '广州');
  assert.equal(logged[0].read_scheduler.dynamic_limit, 120);
  const audit = fs.readFileSync(store.auditPath('prepare-observed-failure'), 'utf8').trim().split(/\r?\n/).map(JSON.parse);
  assert.equal(audit.length, 1);
  assert.equal(audit[0].type, 'prepare_failed');
  assert.equal(audit[0].error_kind, 'service');
  assert.equal(audit[0].account, '广州');
  assert.equal(audit[0].read_scheduler.dynamic_limit, 120);
  assert.equal(JSON.stringify(audit).includes('upstream service unavailable'), false);
});

test('prepare errors classify rate limit, service, timeout, network, local contract, local storage and unknown safely', () => {
  const cases = [
    [Object.assign(new Error('limited'), { status: 429, code: 'HTTP_429' }), 'rate_limit'],
    [Object.assign(new Error('unavailable'), { status: 502 }), 'service'],
    [Object.assign(new Error('request timed out'), { code: 'ETIMEDOUT' }), 'timeout'],
    [Object.assign(new Error('fetch failed'), { cause: { code: 'ECONNRESET' } }), 'network'],
    [new TypeError('(values || []).map is not a function'), 'local_contract'],
    [Object.assign(new Error('sharing violation'), { code: 'EPERM', syscall: 'rename' }), 'local_storage'],
    [new Error('opaque failure'), 'unknown'],
  ];
  for (const [error, expected] of cases) assert.equal(classifyPrepareError(error).error_kind, expected);
});

test('local storage preparation failures retain only a sanitized persistence context', async () => {
  const stateDir = temporaryDirectory();
  const store = createSubmissionPersistence({ stateDir });
  store.create({ id: 'prepare-storage-context', client_submission_id: 'submit-storage-context', state: 'preparing', request: {} });
  const error = Object.assign(new Error('rename C:\\private\\state\\prepare-storage-context.json failed'), {
    code: 'EPERM',
    syscall: 'rename',
    storage_operation: 'submission_state_persist',
    storage_syscall: 'rename',
    storage_target: 'prepare-storage-context.json',
  });
  const result = await runSubmissionPreparation({
    store,
    prepareId: 'prepare-storage-context',
    buildSnapshot: async () => { throw error; },
    preparedPatch: () => ({}),
    classifyError: classifyPrepareError,
    formatError: (value) => prepareErrorMessage(classifyPrepareError(value)),
  });
  assert.equal(result.error_kind, 'local_storage');
  assert.equal(result.storage_operation, 'submission_state_persist');
  assert.equal(result.storage_syscall, 'rename');
  assert.equal(result.storage_target, 'prepare-storage-context.json');
  const audit = fs.readFileSync(store.auditPath('prepare-storage-context'), 'utf8');
  assert.match(audit, /submission_state_persist/);
  assert.match(audit, /prepare-storage-context\.json/);
  assert.equal(audit.includes('C:\\private'), false);
});

test('submission preparation claim reuses one client id and blocks a second active client', () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory() });
  const createRecord = (id, client) => ({ id, client_submission_id: client, state: 'preparing', request: {} });
  const first = claimSubmissionPreparation({
    store, clientSubmissionId: 'same-client', createRecord: () => createRecord('prepare-one', 'same-client'),
  });
  const repeated = claimSubmissionPreparation({
    store, clientSubmissionId: 'same-client', createRecord: () => createRecord('prepare-two', 'same-client'),
  });
  assert.equal(first.reused, false);
  assert.equal(repeated.reused, true);
  assert.equal(repeated.prepare.id, 'prepare-one');
  assert.throws(() => claimSubmissionPreparation({
    store, clientSubmissionId: 'other-client', createRecord: () => createRecord('prepare-other', 'other-client'),
  }), (error) => error.code === 'ACTIVE_SUBMISSION_EXISTS' && error.status === 409);
});

test('cancelled preparation stops at the next progress checkpoint without becoming failed', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory() });
  store.create({ id: 'prepare-stop', client_submission_id: 'submit-stop', state: 'preparing', request: {} });
  let release;
  const wait = new Promise((resolve) => { release = resolve; });
  let reachedAfterCancel = false;
  const running = runSubmissionPreparation({
    store, prepareId: 'prepare-stop', preparedPatch: () => ({}),
    buildSnapshot: async (_request, report) => {
      report({ stage: 'accounts', percent: 10, message: '读取店铺' });
      await wait;
      report({ stage: 'items', percent: 60, message: '读取商品' });
      reachedAfterCancel = true;
      return {};
    },
  });
  store.update('prepare-stop', { state: 'cancelled', error: null });
  release();
  const result = await running;
  assert.equal(result.state, 'cancelled');
  assert.equal(reachedAfterCancel, false);
  assert.equal(result.group_id ?? null, null);
});

test('paused prepared snapshot is reused only for the same unexpired request fingerprint', () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-15T06:00:00.000Z' });
  const request = { accountIds: ['A', 'B'], requested_action: 'update', filters: { siteIds: ['MLM'] }, sellerDiscountPercent: 9, officialDiscountPercent: 10 };
  store.create({
    id: 'prepare-paused', client_submission_id: 'old-client', state: 'paused', request,
    request_fingerprint: submissionRequestFingerprint(request), scope_hash: 'SCOPE',
    expires_at: '2026-07-15T06:10:00.000Z', seller_input: { selected_targets: [] },
  });
  const reused = resumePausedSubmission({ store, request, clientSubmissionId: 'new-client', now: () => '2026-07-15T06:01:00.000Z' });
  assert.equal(reused.prepare.id, 'prepare-paused');
  assert.equal(reused.prepare.state, 'prepared');
  assert.equal(reused.prepare.client_submission_id, 'new-client');
  store.update('prepare-paused', { state: 'paused', client_submission_id: 'new-client' });
  assert.equal(resumePausedSubmission({ store, request: { ...request, officialDiscountPercent: 9 }, clientSubmissionId: 'different', now: () => '2026-07-15T06:01:00.000Z' }), null);
  assert.equal(resumePausedSubmission({ store, request, clientSubmissionId: 'expired', now: () => '2026-07-15T06:11:00.000Z' }), null);
});

test('structural scope change fails safely without a second confirmation or writes', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-14T00:00:00.000Z' });
  store.create({
    id: 'prepare-stale', client_submission_id: 'submit-stale', state: 'prepared',
    scope_hash: 'before', expires_at: '2026-07-15T00:00:00.000Z',
    seller_input: { selected_targets: [{ account_id: 'A', site_id: 'MLM', detection_status: 'unreadable' }] },
  });
  let createCalls = 0;
  let groupCalls = 0;
  await assert.rejects(() => commitSubmission({
    store, prepareId: 'prepare-stale', confirmText: 'REAL_SUBMIT', createConfirmText: 'CREATE_SELLER_CAMPAIGN',
    revalidate: async () => ({
      scope_hash: 'after', changes: ['候选商品数量已变化'], changed_target_keys: ['A|MLM|P-1|DEAL'],
      prepared_patch: { confirmation_summary: '范围已更新，请再次确认。', live_read: { rows: [] } },
    }),
    createSellerCampaigns: async () => { createCalls += 1; },
    startGroup: async () => { groupCalls += 1; },
    now: () => '2026-07-14T00:00:01.000Z',
  }), (error) => error.code === 'PREPARE_STALE');
  assert.equal(store.load('prepare-stale').state, 'failed');
  assert.equal(createCalls, 0);
  assert.equal(groupCalls, 0);
});

test('one submission never enters a repeated reconfirmation loop', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-16T00:00:00.000Z' });
  store.create({
    id: 'prepare-reconfirm-once', client_submission_id: 'submit-reconfirm-once', state: 'prepared',
    scope_hash: 'before', expires_at: '2026-07-17T00:00:00.000Z', seller_input: { selected_targets: [] },
  });
  await assert.rejects(() => commitSubmission({
    store, prepareId: 'prepare-reconfirm-once', confirmText: 'REAL_SUBMIT',
    revalidate: async () => ({ scope_hash: 'after-1', reconfirm_required: true, changes: ['material change'] }),
    now: () => '2026-07-16T00:00:01.000Z',
  }), (error) => error.code === 'PREPARE_STALE');
  assert.equal(store.load('prepare-reconfirm-once').state, 'failed');
  let groupCalls = 0;
  await assert.rejects(() => commitSubmission({
    store, prepareId: 'prepare-reconfirm-once', confirmText: 'REAL_SUBMIT',
    revalidate: async () => ({ scope_hash: 'after-2', reconfirm_required: true, changes: ['another material change'] }),
    startGroup: async () => { groupCalls += 1; },
    now: () => '2026-07-16T00:00:02.000Z',
  }), (error) => error.code === 'PREPARE_STALE');
  assert.equal(groupCalls, 0);
  assert.equal(store.load('prepare-reconfirm-once').state, 'failed');
});

test('empty frozen intersection fails before seller creation or group start', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-16T00:30:00.000Z' });
  store.create({
    id: 'prepare-empty-frozen', client_submission_id: 'submit-empty-frozen', state: 'prepared',
    scope_hash: 'before', expires_at: '2026-07-17T00:00:00.000Z', seller_input: { selected_targets: [] },
  });
  let createCalls = 0;
  let groupCalls = 0;
  await assert.rejects(() => commitSubmission({
    store, prepareId: 'prepare-empty-frozen', confirmText: 'REAL_SUBMIT',
    revalidate: async () => ({
      scope_hash: 'empty',
      reconfirm_required: false,
      execution_relation_count: 0,
      prepared_patch: { confirmed_execution_scope: { action: 'enroll', activities: [] } },
    }),
    createSellerCampaigns: async () => { createCalls += 1; },
    startGroup: async () => { groupCalls += 1; },
    now: () => '2026-07-16T00:30:01.000Z',
  }), (error) => error.code === 'NO_CONFIRMED_TARGETS');
  assert.equal(createCalls, 0);
  assert.equal(groupCalls, 0);
  assert.equal(store.load('prepare-empty-frozen').state, 'failed');
});

test('non-structural item drift persists one frozen intersection and starts exactly one fake group with it', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-16T00:40:00.000Z' });
  const frozen = {
    action: 'enroll',
    activities: [{
      account_id: 'A', site_id: 'MLM', promotion_id: 'P-1', promotion_type: 'DEAL',
      item_status: 'candidate', item_ids: ['I-1', 'I-2'],
    }],
    seller_create_target_keys: [], seller_target_states: {},
  };
  store.create({
    id: 'prepare-frozen-intersection', client_submission_id: 'submit-frozen-intersection', state: 'prepared',
    scope_hash: 'before', expires_at: '2026-07-17T00:00:00.000Z', seller_input: { selected_targets: [] },
    confirmed_execution_scope: frozen,
  });
  const groupScopes = [];
  const result = await commitSubmission({
    store, prepareId: 'prepare-frozen-intersection', confirmText: 'REAL_SUBMIT',
    revalidate: async () => ({
      scope_hash: 'intersection', reconfirm_required: false, execution_relation_count: 1,
      changes: ['已自动剔除 1 个失效或不再可处理的商品', '发现新增 1 个商品，本次不纳入'],
      prepared_patch: {
        confirmed_execution_scope: {
          ...frozen,
          activities: [{ ...frozen.activities[0], item_ids: ['I-2'] }],
        },
      },
      revalidation_record: {
        before: [{ activity_key: 'A|MLM|P-1|DEAL', item_count: 2, item_digest: 'BEFORE' }],
        after: [{ activity_key: 'A|MLM|P-1|DEAL', item_count: 2, item_digest: 'AFTER' }],
        activity_diffs: [{ activity_key: 'A|MLM|P-1|DEAL', added_item_ids: ['I-3'], removed_item_ids: ['I-1'] }],
      },
    }),
    startGroup: async (submission) => {
      groupScopes.push(submission.confirmed_execution_scope);
      return { id: 'group-frozen', status: 'queued' };
    },
    now: () => '2026-07-16T00:40:01.000Z',
  });
  assert.equal(result.group.id, 'group-frozen');
  assert.equal(groupScopes.length, 1);
  assert.deepEqual(groupScopes[0].activities[0].item_ids, ['I-2']);
  const saved = store.load('prepare-frozen-intersection');
  assert.equal(saved.reconfirm_count ?? 0, 0);
  assert.equal(saved.revalidation_history.length, 1);
  assert.deepEqual(saved.revalidation_history[0].activity_diffs[0].added_item_ids, ['I-3']);
  assert.deepEqual(saved.revalidation_history[0].activity_diffs[0].removed_item_ids, ['I-1']);
});

test('seller creation confirmed in the final summary starts one group without a second confirmation', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-16T00:50:00.000Z' });
  store.create({
    id: 'prepare-seller-post-create', client_submission_id: 'submit-seller-post-create', state: 'prepared',
    scope_hash: 'before-create', expires_at: '2026-07-17T00:00:00.000Z',
    seller_input: { selected_targets: [{ account_id: 'A', child_user_id: 'A-CHILD', site_id: 'MLM', detection_status: 'confirmed_absent' }] },
    confirmed_execution_scope: { action: 'enroll', activities: [], seller_create_target_keys: ['A|MLM'] },
  });
  let createCalls = 0;
  let groupCalls = 0;
  const result = await commitSubmission({
    store, prepareId: 'prepare-seller-post-create', confirmText: 'REAL_SUBMIT', createConfirmText: 'CREATE_SELLER_CAMPAIGN',
    revalidate: async () => ({ scope_hash: 'before-create', reconfirm_required: false, execution_relation_count: 1 }),
    createSellerCampaigns: async () => { createCalls += 1; return { ok: true, created_count: 1 }; },
    revalidateAfterCreation: async () => ({
      scope_hash: 'after-create', reconfirm_required: true, execution_relation_count: 2,
      changes: ['新建自建活动已回查，请确认本次商品范围'], changed_target_keys: ['__SELLER__'],
      prepared_patch: {
        seller_input: { selected_targets: [] },
        confirmed_execution_scope: {
          action: 'enroll',
          activities: [{ account_id: 'A', site_id: 'MLM', promotion_id: 'C-NEW', promotion_type: 'SELLER_CAMPAIGN', item_status: 'candidate', item_ids: ['I-1', 'I-2'] }],
          seller_create_target_keys: [],
        },
      },
    }),
    startGroup: async () => { groupCalls += 1; return { id: 'group-after-create', status: 'queued' }; },
    now: () => '2026-07-16T00:50:01.000Z',
  });
  assert.equal(result.group.id, 'group-after-create');
  assert.equal(createCalls, 1);
  assert.equal(groupCalls, 1);
});

test('commit keeps both gates, creation failure starts no group, and created retry reuses one group', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-14T00:00:00.000Z' });
  const base = {
    id: 'prepare-commit', client_submission_id: 'submit-commit', state: 'prepared',
    scope_hash: 'same', expires_at: '2026-07-15T00:00:00.000Z',
    seller_input: { selected_targets: [{ account_id: 'A', child_user_id: 'A-CHILD', site_id: 'MLM', detection_status: 'confirmed_absent' }] },
  };
  store.create(base);
  await assert.rejects(() => commitSubmission({ store, prepareId: base.id, confirmText: '', revalidate: async () => ({ scope_hash: 'same' }), now: () => '2026-07-14T00:00:01.000Z' }), /REAL_SUBMIT/);
  await assert.rejects(() => commitSubmission({ store, prepareId: base.id, confirmText: 'REAL_SUBMIT', revalidate: async () => ({ scope_hash: 'same' }), now: () => '2026-07-14T00:00:01.000Z' }), /CREATE_SELLER_CAMPAIGN/);
  let groupCalls = 0;
  await assert.rejects(() => commitSubmission({
    store, prepareId: base.id, confirmText: 'REAL_SUBMIT', createConfirmText: 'CREATE_SELLER_CAMPAIGN',
    revalidate: async () => ({ scope_hash: 'same' }),
    createSellerCampaigns: async () => ({ ok: false, failed_count: 1, created_count: 0 }),
    startGroup: async () => { groupCalls += 1; },
    now: () => '2026-07-14T00:00:01.000Z',
  }), /创建失败/);
  assert.equal(groupCalls, 0);

  store.update(base.id, {
    state: 'created', creation_result: { ok: true, created_count: 1 },
    commit_confirmed: true, create_confirmed: true,
  });
  const starts = [];
  const startGroup = async (submission) => {
    starts.push(submission.client_submission_id);
    return { id: 'group-1', status: 'queued' };
  };
  const first = await commitSubmission({ store, prepareId: base.id, confirmText: 'REAL_SUBMIT', createConfirmText: 'CREATE_SELLER_CAMPAIGN', revalidate: async () => ({ scope_hash: 'same' }), startGroup, now: () => '2026-07-14T00:00:01.000Z', recover: true, leaseOwner: 'restart-owner' });
  const second = await commitSubmission({ store, prepareId: base.id, confirmText: 'REAL_SUBMIT', createConfirmText: 'CREATE_SELLER_CAMPAIGN', revalidate: async () => ({ scope_hash: 'same' }), startGroup, now: () => '2026-07-14T00:00:01.000Z' });
  assert.equal(first.group.id, 'group-1');
  assert.equal(second.group.id, 'group-1');
  assert.deepEqual(starts, ['submit-commit']);
});

test('expired prepare and selected unreadable are zero-write failures while empty selection starts normally', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-14T12:00:00.000Z' });
  store.create({ id: 'expired', client_submission_id: 'expired-submit', state: 'prepared', scope_hash: 'same', expires_at: '2026-07-14T11:59:59.000Z', seller_input: { selected_targets: [] } });
  let starts = 0;
  await assert.rejects(() => commitSubmission({
    store, prepareId: 'expired', confirmText: 'REAL_SUBMIT', revalidate: async () => ({ scope_hash: 'same' }),
    startGroup: async () => { starts += 1; }, now: () => '2026-07-14T12:00:00.000Z',
  }), (error) => error.code === 'PREPARE_EXPIRED');
  assert.equal(starts, 0);

  store.create({ id: 'empty', client_submission_id: 'empty-submit', state: 'prepared', scope_hash: 'same', expires_at: '2026-07-15T00:00:00.000Z', seller_input: { selected_targets: [] } });
  const result = await commitSubmission({
    store, prepareId: 'empty', confirmText: 'REAL_SUBMIT', revalidate: async () => ({ scope_hash: 'same' }),
    startGroup: async () => { starts += 1; return { id: 'empty-group', status: 'queued' }; },
    now: () => '2026-07-14T12:00:00.000Z',
  });
  assert.equal(result.group.id, 'empty-group');
  assert.equal(starts, 1);

  store.create({ id: 'created-expired', client_submission_id: 'created-expired-submit', state: 'created', scope_hash: 'same', expires_at: '2026-07-14T11:59:59.000Z', seller_input: { selected_targets: [] } });
  const resumed = await commitSubmission({
    store, prepareId: 'created-expired', confirmText: 'REAL_SUBMIT',
    startGroup: async () => { starts += 1; return { id: 'resumed-group', status: 'queued' }; },
    now: () => '2026-07-14T12:00:00.000Z', recover: true, leaseOwner: 'restart-owner',
  });
  assert.equal(resumed.group.id, 'resumed-group');
  assert.equal(starts, 2);
});

test('commit lease survives prepared TTL but expires explicitly after the lease deadline', async () => {
  let clock = '2026-07-16T00:00:00.000Z';
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => clock });
  store.create({
    id: 'prepare-long-live', client_submission_id: 'submit-long-live', state: 'prepared',
    scope_hash: 'same', expires_at: '2026-07-16T00:15:00.000Z', seller_input: { selected_targets: [] },
  });
  let release;
  const delayed = new Promise((resolve) => { release = resolve; });
  let groupCalls = 0;
  const running = commitSubmission({
    store, prepareId: 'prepare-long-live', confirmText: 'REAL_SUBMIT',
    revalidate: async () => { await delayed; return { scope_hash: 'same' }; },
    startGroup: async () => { groupCalls += 1; return { id: 'should-not-start' }; },
    now: () => clock, commitLeaseMs: 30 * 60 * 1000, leaseOwner: 'owner-long-live',
  });
  await new Promise((resolve) => setImmediate(resolve));
  clock = '2026-07-16T00:20:00.000Z';
  assert.equal(refreshSubmissionDeadline({ store, prepareId: 'prepare-long-live', now: () => clock }).state, 'committing');
  clock = '2026-07-16T00:31:00.000Z';
  assert.equal(refreshSubmissionDeadline({ store, prepareId: 'prepare-long-live', now: () => clock }).state, 'failed');
  release();
  await assert.rejects(running, (error) => ['COMMIT_LEASE_EXPIRED', 'COMMIT_STATE_CHANGED'].includes(error.code));
  assert.equal(groupCalls, 0);
});

test('commit cancellation during live revalidation aborts the stale continuation and starts no group', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-16T01:00:00.000Z' });
  store.create({
    id: 'prepare-cancel-live', client_submission_id: 'submit-cancel-live', state: 'prepared',
    scope_hash: 'same', expires_at: '2026-07-16T01:15:00.000Z', seller_input: { selected_targets: [] },
  });
  const controller = new AbortController();
  let release;
  const delayed = new Promise((resolve) => { release = resolve; });
  let groupCalls = 0;
  const running = commitSubmission({
    store, prepareId: 'prepare-cancel-live', confirmText: 'REAL_SUBMIT', signal: controller.signal,
    revalidate: async () => { await delayed; return { scope_hash: 'same' }; },
    startGroup: async () => { groupCalls += 1; return { id: 'should-not-start' }; },
    now: () => '2026-07-16T01:00:00.000Z', leaseOwner: 'owner-cancel-live',
  });
  await new Promise((resolve) => setImmediate(resolve));
  const active = store.load('prepare-cancel-live');
  assert.equal(active.state, 'committing');
  assert.equal(store.compareAndSwap(active.id, {
    version: active.version, state: 'committing', commit_lease_id: active.commit_lease_id,
  }, { state: 'cancelled', error: null }).ok, true);
  controller.abort('user_cancelled');
  release();
  await assert.rejects(running, (error) => ['COMMIT_ABORTED', 'SUBMISSION_CANCELLED', 'COMMIT_STATE_CHANGED'].includes(error.code));
  assert.equal(groupCalls, 0);
  assert.equal(store.load('prepare-cancel-live').state, 'cancelled');
});

test('concurrent commit and state drift after seller creation never duplicate create or start a group', async () => {
  const store = createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => '2026-07-16T02:00:00.000Z' });
  store.create({
    id: 'prepare-create-race', client_submission_id: 'submit-create-race', state: 'prepared',
    scope_hash: 'same', expires_at: '2026-07-16T02:15:00.000Z',
    seller_input: { selected_targets: [{ account_id: 'A', child_user_id: 'A-CHILD', site_id: 'MLM', detection_status: 'confirmed_absent' }] },
  });
  let releaseCreation;
  const creationWait = new Promise((resolve) => { releaseCreation = resolve; });
  let createCalls = 0;
  let groupCalls = 0;
  const first = commitSubmission({
    store, prepareId: 'prepare-create-race', confirmText: 'REAL_SUBMIT', createConfirmText: 'CREATE_SELLER_CAMPAIGN',
    revalidate: async () => ({ scope_hash: 'same' }),
    createSellerCampaigns: async () => { createCalls += 1; await creationWait; return { ok: true, created_count: 1, failed_count: 0, recheck_missing_count: 0 }; },
    startGroup: async () => { groupCalls += 1; return { id: 'should-not-start' }; },
    now: () => '2026-07-16T02:00:00.000Z', leaseOwner: 'owner-create-race',
  });
  await new Promise((resolve) => setImmediate(resolve));
  await assert.rejects(() => commitSubmission({
    store, prepareId: 'prepare-create-race', confirmText: 'REAL_SUBMIT', createConfirmText: 'CREATE_SELLER_CAMPAIGN',
    now: () => '2026-07-16T02:00:01.000Z', leaseOwner: 'owner-create-race',
  }), (error) => error.code === 'COMMIT_IN_PROGRESS');
  const creating = store.load('prepare-create-race');
  assert.equal(creating.state, 'creating');
  assert.equal(store.compareAndSwap(creating.id, {
    version: creating.version, state: 'creating', commit_lease_id: creating.commit_lease_id,
  }, { state: 'cancelled', error: null }).ok, true);
  releaseCreation();
  await assert.rejects(first, (error) => ['SUBMISSION_CANCELLED', 'COMMIT_STATE_CHANGED'].includes(error.code));
  assert.equal(createCalls, 1);
  assert.equal(groupCalls, 0);
});

test('node restart reclaims a persisted committing lease but never resumes creating by another POST', async () => {
  const stateDir = temporaryDirectory();
  const oldStore = createSubmissionPersistence({ stateDir, now: () => '2026-07-16T03:00:00.000Z' });
  oldStore.create({
    id: 'prepare-restart', client_submission_id: 'submit-restart', state: 'committing',
    scope_hash: 'same', commit_confirmed: true,
    commit_lease_id: 'old-lease', commit_lease_owner: 'old-node', commit_lease_expires_at: '2026-07-16T03:30:00.000Z',
    seller_input: { selected_targets: [] },
  });
  const restarted = createSubmissionPersistence({ stateDir, now: () => '2026-07-16T03:01:00.000Z' });
  let starts = 0;
  const resumed = await commitSubmission({
    store: restarted, prepareId: 'prepare-restart', confirmText: 'REAL_SUBMIT', recover: true, leaseOwner: 'new-node',
    revalidate: async () => ({ scope_hash: 'same' }),
    startGroup: async () => { starts += 1; return { id: 'group-restart' }; },
    now: () => '2026-07-16T03:01:00.000Z',
  });
  assert.equal(resumed.group.id, 'group-restart');
  assert.equal(starts, 1);
  assert.ok(restarted.load('prepare-restart').audit_events.some((row) => row.type === 'recovery_polled'));

  restarted.create({
    id: 'prepare-creating-restart', client_submission_id: 'submit-creating-restart', state: 'creating',
    scope_hash: 'same', commit_confirmed: true, create_confirmed: true,
    commit_lease_id: 'old-create-lease', commit_lease_owner: 'old-node', commit_lease_expires_at: '2026-07-16T03:30:00.000Z',
    seller_input: { selected_targets: [{ account_id: 'A', child_user_id: 'A-CHILD', site_id: 'MLM', detection_status: 'confirmed_absent' }] },
  });
  let creates = 0;
  await assert.rejects(() => commitSubmission({
    store: restarted, prepareId: 'prepare-creating-restart', confirmText: 'REAL_SUBMIT', createConfirmText: 'CREATE_SELLER_CAMPAIGN',
    recover: true, leaseOwner: 'new-node', createSellerCampaigns: async () => { creates += 1; },
    now: () => '2026-07-16T03:01:00.000Z',
  }), (error) => error.code === 'SELLER_CREATION_RECHECK_REQUIRED');
  assert.equal(creates, 0);
});

test('commit cancel and recovery polling audits are persisted without changing business state', async () => {
  const stateDir = temporaryDirectory();
  const store = createSubmissionPersistence({ stateDir, now: () => '2026-07-16T04:00:00.000Z' });
  store.create({
    id: 'prepare-audit', client_submission_id: 'submit-audit', state: 'committing',
    commit_lease_id: 'audit-lease', commit_lease_owner: 'node', commit_lease_expires_at: '2026-07-16T04:30:00.000Z',
  });
  const cancelled = cancelSubmissionCommit({ store, prepareId: 'prepare-audit', now: () => '2026-07-16T04:01:00.000Z' });
  assert.equal(cancelled.changed, true);
  assert.equal(cancelled.prepare.state, 'cancelled');
  assert.deepEqual(cancelled.prepare.audit_events.map((row) => row.type), ['abort_requested', 'aborted', 'state_changed']);
  const version = cancelled.prepare.version;
  assert.equal(store.appendAudit('prepare-audit', { type: 'recovery_polled', state: 'cancelled' }), true);
  assert.equal(store.load('prepare-audit').version, version);
  assert.match(fs.readFileSync(store.auditPath('prepare-audit'), 'utf8'), /recovery_polled/);
});

test('server and PySide expose one prepare/commit product path and retire old orchestration', () => {
  const server = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  const mainWindow = fs.readFileSync(new URL('../desktop-pyside/main_window.py', import.meta.url), 'utf8');
  assert.match(server, /\/api\/execution\/submissions\/prepare/);
  assert.match(server, /state: 'preparing'/);
  assert.match(server, /scheduleExecutionSubmissionPreparation/);
  assert.match(server, /new Promise\(\(resolve\) => setImmediate\(resolve\)\)/);
  assert.match(server, /ACTIVE_SUBMISSION_STATES/);
  assert.match(server, /submissionCommitMatch/);
  assert.match(server, /deprecated: true/);
  assert.match(mainWindow, /\/api\/execution\/submissions\/prepare/);
  assert.match(mainWindow, /\/commit/);
  assert.match(mainWindow, /def _submit_prepared_submission/);
  assert.doesNotMatch(mainWindow, /ConfirmDialog\("最终执行确认"/);
  assert.match(server, /expires_at: shanghaiBusinessDayEndIso\(preparedAt\)/);
  assert.doesNotMatch(server, /preparedAt\.getTime\(\) \+ 15 \* 60 \* 1000/);
  assert.doesNotMatch(mainWindow, /batch-precheck|batch-create|\/api\/execution\/groups\/start/);
});

test('submission prepare and commit retain the full live-read gate before group start', () => {
  const server = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  assert.match(server, /prepareItemsForExecution\(\{[\s\S]*fetchMode: 'full'/);
  assert.match(server, /live_read: liveRead\.rows/);
  assert.match(server, /current\.live_read\?\.all_blocked/);
  assert.match(server, /LIVE_READ_BLOCKED/);
  assert.match(server, /status: 'activity_failed'/);
  assert.match(server, /CANCEL_RESULT_STATUS\.requestSuccess/);
  assert.match(server, /CANCEL_RESULT_STATUS\.pendingVerification/);
});

test('final execution consumes the frozen intersection without a second full activity or item scan', () => {
  const server = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  assert.match(server, /const frozenScope = hasConfirmedExecutionScope\(request\)/);
  assert.match(server, /filterPromotionsByConfirmedScope\(\{ accountId: account\.account_id, promotions: localPromotions, request \}\)/);
  assert.match(server, /const itemPrep = frozenScope[\s\S]*使用最终确认商品关系，不再进行同级全量读取/);
  assert.match(server, /filterItemsByConfirmedScope\(\{[\s\S]*?accountId: account\.account_id,[\s\S]*?requiredRecords: request\.resumePendingOnly \? pendingWriteRecords : null/);
  assert.match(server, /revalidateAfterCreation:/);
  assert.match(server, /buildFinalRevalidationPlan\(/);
  assert.match(server, /finalRevalidation: true/);
  assert.match(server, /selectedForLiveRead = targetedRevalidate \? selected\.filter\(promotionNeedsItemRead\) : selected/);
  assert.match(server, /item_read_activity_count/);
  assert.match(server, /正在启动任务/);
  assert.match(server, /正在复核变化活动/);
  assert.doesNotMatch(server, /提交前轻量复核完成/);
  assert.doesNotMatch(server, /function submissionRevalidationDiff/);
});

test('commit HTTP accepts quickly and persists background state before final revalidation', async () => {
  const dataDir = temporaryDirectory();
  const submissionDir = path.join(dataDir, 'execution-submissions');
  fs.mkdirSync(submissionDir, { recursive: true });
  fs.writeFileSync(path.join(submissionDir, 'prepare-fast.json'), JSON.stringify({
    id: 'prepare-fast', client_submission_id: 'submit-fast', state: 'prepared', version: 1,
    scope_hash: 'scope-fast', expires_at: '2099-01-01T00:00:00.000Z',
    request: { accountIds: [] }, seller_input: { selected_targets: [] },
    created_at: '2026-07-16T00:00:00.000Z', updated_at: '2026-07-16T00:00:00.000Z',
  }), 'utf8');
  const port = 32000 + Math.floor(Math.random() * 1000);
  const child = spawn(process.execPath, ['src/server.js'], {
    cwd: process.cwd(), env: { ...process.env, MDM_PORT: String(port), MDM_DATA_DIR: dataDir },
    stdio: 'ignore', windowsHide: true,
  });
  try {
    const baseUrl = `http://127.0.0.1:${port}`;
    await waitForStageCHealth(baseUrl);
    const startedAt = Date.now();
    const response = await fetch(`${baseUrl}/api/execution/submissions/prepare-fast/commit`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ confirmText: 'REAL_SUBMIT' }),
    });
    const body = await response.json();
    assert.equal(response.status, 202);
    assert.equal(body.accepted, true);
    assert.equal(body.group, null);
    assert.equal(body.prepare.state, 'committing');
    assert.ok(Date.now() - startedAt < 1_000);
    const deadline = Date.now() + 3_000;
    let current = body.prepare;
    while (Date.now() < deadline && current.state === 'committing') {
      await new Promise((resolve) => setTimeout(resolve, 25));
      current = (await (await fetch(`${baseUrl}/api/execution/submissions/prepare-fast`)).json()).prepare;
    }
    assert.equal(current.state, 'failed');
    const activeGroup = await (await fetch(`${baseUrl}/api/execution/groups/active`)).json();
    assert.equal(activeGroup.active, false);
  } finally {
    if (child.exitCode === null) {
      child.kill();
      await new Promise((resolve) => child.once('exit', resolve));
    }
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
