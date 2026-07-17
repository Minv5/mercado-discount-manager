import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  businessDateInShanghai,
  createSameDayConfirmationStore,
  executionRequestScope,
  executionScopeKey,
  findSameDayTerminalGroup,
  sameDayCompletionGate,
} from '../src/sameDayCompletionGate.js';
import { createExecutionGroupPersistence } from '../src/executionGroupPersistence.js';
import { createSubmissionPersistence, submissionRequestFingerprint } from '../src/submissionPersistence.js';

function temporaryDirectory() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-same-day-gate-'));
}

function requestFixture(overrides = {}) {
  return {
    client_submission_id: 'submission-1',
    requested_action: 'update',
    action: 'update',
    accountIds: ['3408885754', '2651442567', '3332096437'],
    filters: {
      siteId: '',
      siteIds: [],
      sellerActivityNames: [],
      officialActivityNames: [],
      excludeSeller: false,
      excludeOfficial: false,
    },
    selectedSiteName: '全部站点',
    sellerDiscountPercent: 10,
    officialDiscountPercent: 10,
    ...overrides,
  };
}

function completedGroup(overrides = {}) {
  return {
    id: 'group-today',
    client_submission_id: 'completed-submission',
    status: 'completed',
    action: 'update',
    finished_at: '2026-07-15T13:23:48.975Z',
    request: requestFixture({
      client_submission_id: 'completed-submission',
      requested_action: 'update',
      action: 'update',
    }),
    result: { total: 1307, success: 809, failed: 9, skipped: 489 },
    ...overrides,
  };
}

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

test('same-day scope canonicalization ignores account order and display-only text', () => {
  const left = executionRequestScope(requestFixture());
  const right = executionRequestScope(requestFixture({
    accountIds: ['3332096437', '3408885754', '2651442567', '3332096437'],
    selectedSiteName: '所有站点',
    storeNames: { 2651442567: '甲', 3332096437: '乙', 3408885754: '丙' },
    filters: {
      siteId: '', siteIds: [],
      sellerActivityNames: ['  Campaign\u200b A ', 'campaign a'],
      officialActivityNames: [' Deal  A '],
      excludeSeller: false, excludeOfficial: false,
    },
  }));
  assert.deepEqual(left.account_ids, ['2651442567', '3332096437', '3408885754']);
  assert.equal(executionScopeKey(right), JSON.stringify([
    ['2651442567', '3332096437', '3408885754'], '', ['campaign a'], ['deal a'], false, false,
  ]));
  assert.notEqual(executionScopeKey(left), executionScopeKey(right));
  assert.equal(executionScopeKey(left), executionScopeKey(executionRequestScope(requestFixture({
    selectedSiteName: '任意展示文本',
    storeNames: { 2651442567: '湖北' },
  }))));
});

test('Shanghai business date and terminal group matching use exact normalized scope', () => {
  assert.equal(businessDateInShanghai('2026-07-15T15:59:59.999Z'), '2026-07-15');
  assert.equal(businessDateInShanghai('2026-07-15T16:00:00.000Z'), '2026-07-16');
  const request = requestFixture();
  assert.equal(findSameDayTerminalGroup([completedGroup()], request, '2026-07-15T15:30:00.000Z')?.id, 'group-today');
  assert.equal(findSameDayTerminalGroup([completedGroup()], request, '2026-07-16T00:00:00.000Z'), null);
  assert.equal(findSameDayTerminalGroup([completedGroup()], requestFixture({
    filters: { ...request.filters, siteId: 'MLM', siteIds: ['MLM'] },
  }), '2026-07-15T15:30:00.000Z'), null);
  assert.equal(findSameDayTerminalGroup([
    completedGroup({ status: 'running' }),
  ], request, '2026-07-15T15:30:00.000Z'), null);
});

test('auto same-scope gate returns TODAY_COMPLETED without issuing a token', () => {
  const store = createSameDayConfirmationStore({ stateDir: temporaryDirectory() });
  assert.throws(
    () => sameDayCompletionGate({
      groups: [completedGroup()],
      request: requestFixture({ action: 'auto', requested_action: 'auto' }),
      confirmationStore: store,
      now: () => '2026-07-15T15:30:00.000Z',
    }),
    (error) => error.code === 'TODAY_COMPLETED'
      && error.status === 409
      && error.details.completed.action === 'update'
      && !error.details.confirmation_token,
  );
  assert.equal(store.loadAll().length, 0);
});

test('manual same-day gate issues a bound short-lived token and accepts it once', () => {
  const stateDir = temporaryDirectory();
  let now = '2026-07-15T15:30:00.000Z';
  const store = createSameDayConfirmationStore({ stateDir, now: () => now, ttlMs: 120_000 });
  const request = requestFixture({ action: 'enroll', requested_action: 'enroll' });
  let token = '';
  assert.throws(
    () => sameDayCompletionGate({ groups: [completedGroup()], request, confirmationStore: store, now: () => now }),
    (error) => {
      token = error.details.confirmation_token;
      return error.code === 'CONFIRM_SAME_DAY_ACTION'
        && error.details.same_action === false
        && Boolean(token);
    },
  );
  assert.equal(store.loadAll()[0].state, 'issued');
  assert.deepEqual(store.loadAll()[0].events.map((row) => row.type), ['warning_issued']);
  const allowed = sameDayCompletionGate({
    groups: [completedGroup()],
    request: { ...request, same_day_confirmation_token: token },
    confirmationStore: store,
    now: () => now,
  });
  assert.equal(allowed.allowed, true);
  assert.equal(allowed.confirmed, true);
  assert.equal(store.loadAll()[0].state, 'consumed');
  assert.deepEqual(store.loadAll()[0].events.map((row) => row.type), ['warning_issued', 'accepted']);
  assert.throws(
    () => store.consume(token, allowed.binding),
    (error) => error.code === 'SAME_DAY_CONFIRMATION_USED',
  );
  const persisted = fs.readFileSync(store.statePath(store.loadAll()[0].id), 'utf8');
  assert.equal(persisted.includes(token), false);
});

test('confirmation token rejects wrong binding, expiry, cancellation, and double consumption', () => {
  let now = '2026-07-15T15:30:00.000Z';
  const store = createSameDayConfirmationStore({ stateDir: temporaryDirectory(), now: () => now, ttlMs: 1_000 });
  const request = requestFixture({ action: 'cancel', requested_action: 'cancel' });
  let issued;
  try {
    sameDayCompletionGate({ groups: [completedGroup()], request, confirmationStore: store, now: () => now });
  } catch (error) {
    issued = error.details;
  }
  assert.ok(issued.confirmation_token);
  const record = store.loadAll()[0];
  const binding = record.binding;
  for (const changed of [
    { requested_action: 'enroll' },
    { client_submission_id: 'submission-other' },
    { scope_key: `${binding.scope_key}-other` },
    { completed_group_id: 'group-other' },
    { business_date: '2026-07-16' },
  ]) {
    assert.throws(
      () => store.consume(issued.confirmation_token, { ...binding, ...changed }),
      (error) => error.code === 'SAME_DAY_CONFIRMATION_MISMATCH',
    );
  }
  store.cancel(issued.confirmation_token);
  assert.equal(store.loadAll()[0].state, 'cancelled');
  assert.deepEqual(store.loadAll()[0].events.map((row) => row.type), ['warning_issued', 'cancelled']);
  assert.throws(
    () => store.consume(issued.confirmation_token, binding),
    (error) => error.code === 'SAME_DAY_CONFIRMATION_CANCELLED',
  );

  const expiring = createSameDayConfirmationStore({ stateDir: temporaryDirectory(), now: () => now, ttlMs: 1_000 });
  let expiringToken;
  try {
    sameDayCompletionGate({ groups: [completedGroup()], request, confirmationStore: expiring, now: () => now });
  } catch (error) {
    expiringToken = error.details.confirmation_token;
  }
  now = '2026-07-15T15:30:02.000Z';
  assert.throws(
    () => expiring.consume(expiringToken, expiring.loadAll()[0].binding),
    (error) => error.code === 'SAME_DAY_CONFIRMATION_EXPIRED',
  );
});

test('concurrent confirmation consumption accepts exactly one request', async () => {
  const store = createSameDayConfirmationStore({ stateDir: temporaryDirectory() });
  const request = requestFixture({ action: 'update', requested_action: 'update' });
  let token;
  try {
    sameDayCompletionGate({
      groups: [completedGroup()], request, confirmationStore: store,
      now: () => '2026-07-15T15:30:00.000Z',
    });
  } catch (error) {
    token = error.details.confirmation_token;
  }
  const binding = store.loadAll()[0].binding;
  const results = await Promise.allSettled([
    Promise.resolve().then(() => store.consume(token, binding)),
    Promise.resolve().then(() => store.consume(token, binding)),
  ]);
  assert.equal(results.filter((row) => row.status === 'fulfilled').length, 1);
  assert.equal(results.filter((row) => row.status === 'rejected' && row.reason.code === 'SAME_DAY_CONFIRMATION_USED').length, 1);
  assert.deepEqual(store.loadAll()[0].events.map((row) => row.type), ['warning_issued', 'accepted']);
});

test('manual same-action is also gated while a truly different or cross-day scope is allowed', () => {
  const store = createSameDayConfirmationStore({ stateDir: temporaryDirectory() });
  assert.throws(
    () => sameDayCompletionGate({
      groups: [completedGroup()], request: requestFixture(), confirmationStore: store,
      now: () => '2026-07-15T15:30:00.000Z',
    }),
    (error) => error.code === 'CONFIRM_SAME_DAY_ACTION' && error.details.same_action === true,
  );
  assert.deepEqual(sameDayCompletionGate({
    groups: [completedGroup()],
    request: requestFixture({
      filters: { ...requestFixture().filters, siteId: 'MLM', siteIds: ['MLM'] },
    }),
    confirmationStore: store,
    now: () => '2026-07-15T15:30:00.000Z',
  }), { allowed: true, confirmed: false, completed: null, binding: null });
  assert.deepEqual(sameDayCompletionGate({
    groups: [completedGroup()], request: requestFixture(), confirmationStore: store,
    now: () => '2026-07-16T15:30:00.000Z',
  }), { allowed: true, confirmed: false, completed: null, binding: null });
});

test('HTTP prepare gate creates zero submissions and prior idempotency cannot change action', async () => {
  const dataDir = temporaryDirectory();
  const groupStore = createExecutionGroupPersistence({
    stateDir: path.join(dataDir, 'execution-group-states'),
    currentPid: process.pid,
  });
  groupStore.persist(completedGroup({ finished_at: new Date().toISOString() }));
  const existingRequest = requestFixture({ client_submission_id: 'existing-client' });
  const submissionStore = createSubmissionPersistence({ stateDir: path.join(dataDir, 'execution-submissions') });
  submissionStore.create({
    id: 'prepare-existing',
    client_submission_id: 'existing-client',
    state: 'terminal',
    request: existingRequest,
    request_fingerprint: submissionRequestFingerprint(existingRequest),
    expires_at: null,
  });
  const resumedRequest = requestFixture({
    client_submission_id: 'paused-old-client', requested_action: 'cancel', action: 'cancel',
  });
  submissionStore.create({
    id: 'prepare-paused',
    client_submission_id: 'paused-old-client',
    state: 'paused',
    request: resumedRequest,
    request_fingerprint: submissionRequestFingerprint(resumedRequest),
    expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
  });

  const port = 32000 + Math.floor(Math.random() * 1000);
  const child = spawn(process.execPath, ['src/server.js'], {
    cwd: process.cwd(),
    env: { ...process.env, MDM_PORT: String(port), MDM_DATA_DIR: dataDir },
    stdio: 'ignore',
    windowsHide: true,
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    await waitForHealth(baseUrl);
    const existing = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(existingRequest),
    });
    assert.equal(existing.status, 200);
    assert.equal((await existing.json()).prepare_id, 'prepare-existing');

    const changed = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...existingRequest, requested_action: 'enroll', action: 'enroll' }),
    });
    assert.equal(changed.status, 409);
    assert.equal((await changed.json()).code, 'CLIENT_SUBMISSION_MISMATCH');

    const auto = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(requestFixture({ client_submission_id: 'auto-new', requested_action: 'auto', action: 'auto' })),
    });
    assert.equal(auto.status, 409);
    assert.equal((await auto.json()).code, 'TODAY_COMPLETED');

    const manualRequest = requestFixture({ client_submission_id: 'manual-new', requested_action: 'cancel', action: 'cancel' });
    const manual = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(manualRequest),
    });
    assert.equal(manual.status, 409);
    const warning = await manual.json();
    assert.equal(warning.code, 'CONFIRM_SAME_DAY_ACTION');
    assert.ok(warning.details.confirmation_token);

    const rebound = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        ...manualRequest,
        client_submission_id: 'manual-rebound',
        same_day_confirmation_token: warning.details.confirmation_token,
      }),
    });
    assert.equal(rebound.status, 409);
    assert.equal((await rebound.json()).code, 'SAME_DAY_CONFIRMATION_MISMATCH');

    const cancelRequest = requestFixture({ client_submission_id: 'manual-cancel', requested_action: 'enroll', action: 'enroll' });
    const cancelWarningResponse = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(cancelRequest),
    });
    assert.equal(cancelWarningResponse.status, 409);
    const cancelWarning = await cancelWarningResponse.json();
    const cancelled = await fetch(`${baseUrl}/api/execution/submissions/same-day-confirmations/cancel`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ confirmation_token: cancelWarning.details.confirmation_token }),
    });
    assert.equal(cancelled.status, 200);
    const retried = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        ...cancelRequest,
        same_day_confirmation_token: cancelWarning.details.confirmation_token,
      }),
    });
    assert.equal(retried.status, 409);
    assert.equal((await retried.json()).code, 'SAME_DAY_CONFIRMATION_CANCELLED');

    const accepted = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...manualRequest, same_day_confirmation_token: warning.details.confirmation_token }),
    });
    assert.equal(accepted.status, 200);
    const acceptedBody = await accepted.json();
    assert.equal(acceptedBody.prepare_id, 'prepare-paused');
    assert.equal(acceptedBody.prepare.state, 'prepared');
    const acceptedState = fs.readFileSync(path.join(dataDir, 'execution-submissions', 'prepare-paused.json'), 'utf8');
    assert.equal(acceptedState.includes(warning.details.confirmation_token), false);

    const responseRetry = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...manualRequest, same_day_confirmation_token: warning.details.confirmation_token }),
    });
    assert.equal(responseRetry.status, 200);
    assert.equal((await responseRetry.json()).prepare_id, 'prepare-paused');

    const files = fs.readdirSync(path.join(dataDir, 'execution-submissions')).filter((name) => name.endsWith('.json'));
    assert.deepEqual(files.sort(), ['prepare-existing.json', 'prepare-paused.json']);
  } finally {
    child.kill();
    await new Promise((resolve) => child.once('exit', resolve));
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
