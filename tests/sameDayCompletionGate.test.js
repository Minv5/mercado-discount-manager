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
  shanghaiBusinessDayEndIso,
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
    result: {
      accounting_complete: true,
      relation_count: 1307,
      success: 809,
      failed: 0,
      skipped: 498,
      activity_failure_count: 0,
      platform_pending_count: 0,
      retryable_pending_count: 0,
      unresolved_count: 0,
    },
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
  assert.equal(shanghaiBusinessDayEndIso('2026-07-15T13:01:47.000Z'), '2026-07-15T16:00:00.000Z');
  assert.equal(shanghaiBusinessDayEndIso('2026-07-15T16:00:00.000Z'), '2026-07-16T16:00:00.000Z');
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

test('auto gate does not treat failed, cancelled, interrupted, or incomplete completed groups as today completed', () => {
  const automaticRequest = requestFixture({ action: 'auto', requested_action: 'auto' });
  const cases = [
    ['failed', completedGroup({ status: 'failed' }), 'group_status_failed'],
    ['cancelled', completedGroup({ status: 'cancelled' }), 'group_status_cancelled'],
    ['interrupted', completedGroup({ status: 'interrupted' }), 'group_status_interrupted'],
    ['null result', completedGroup({ result: null }), 'result_missing'],
    ['legacy result', completedGroup({
      result: { total: 1307, success: 1307, failed: 0, skipped: 0 },
    }), 'accounting_not_proven'],
    ['incomplete accounting', completedGroup({
      result: { ...completedGroup().result, accounting_complete: false },
    }), 'accounting_incomplete'],
    ['failed relation', completedGroup({
      result: { ...completedGroup().result, failed: 1, success: 808 },
    }), 'failed_relations_present'],
    ['activity failure', completedGroup({
      result: { ...completedGroup().result, activity_failure_count: 1 },
    }), 'activity_failures_present'],
    ['retryable pending', completedGroup({
      result: { ...completedGroup().result, retryable_pending_count: 1 },
    }), 'retryable_pending_present'],
    ['unresolved', completedGroup({
      result: {
        ...completedGroup().result,
        unresolved_count: 1,
        success: 808,
      },
    }), 'unresolved_relations_present'],
    ['platform pending', completedGroup({
      result: {
        ...completedGroup().result,
        platform_pending_count: 1,
        success: 808,
      },
    }), 'platform_pending_requires_recovery'],
    ['count gap', completedGroup({
      result: {
        ...completedGroup().result,
        relation_count: 1308,
      },
    }), 'relation_count_gap'],
  ];

  for (const [label, group, reason] of cases) {
    const result = sameDayCompletionGate({
      groups: [group],
      request: automaticRequest,
      now: () => '2026-07-15T15:30:00.000Z',
    });
    assert.equal(result.allowed, true, label);
    assert.equal(result.completed, null, label);
    assert.equal(result.completion_audit?.eligible, false, label);
    assert.equal(result.completion_audit?.reason, reason, label);
    assert.equal(result.completion_audit?.group_id, 'group-today', label);
  }
});

test('auto gate blocks only a fully accounted completed group even when a newer terminal attempt is incomplete', () => {
  const newerFailed = completedGroup({
    id: 'group-newer-failed',
    status: 'failed',
    finished_at: '2026-07-15T14:23:48.975Z',
  });
  assert.throws(
    () => sameDayCompletionGate({
      groups: [completedGroup(), newerFailed],
      request: requestFixture({ action: 'auto', requested_action: 'auto' }),
      now: () => '2026-07-15T15:30:00.000Z',
    }),
    (error) => error.code === 'TODAY_COMPLETED'
      && error.details.completed.group_id === 'group-today',
  );
});

test('manual same-day actions bypass the retired secondary confirmation without issuing a token', () => {
  const store = createSameDayConfirmationStore({ stateDir: temporaryDirectory() });
  for (const action of ['enroll', 'update', 'cancel']) {
    const result = sameDayCompletionGate({
      groups: [completedGroup()],
      request: requestFixture({ action, requested_action: action }),
      now: () => '2026-07-15T15:30:00.000Z',
    });
    assert.equal(result.allowed, true);
    assert.equal(result.confirmed, false);
    assert.equal(result.binding, null);
    assert.equal(result.completed?.group_id, 'group-today');
    assert.equal(result.completed?.action, 'update');
  }
  assert.equal(store.loadAll().length, 0);
  const incompleteManual = sameDayCompletionGate({
    groups: [completedGroup({ status: 'failed' })],
    request: requestFixture({ action: 'cancel', requested_action: 'cancel' }),
    now: () => '2026-07-15T15:30:00.000Z',
  });
  assert.equal(incompleteManual.allowed, true);
  assert.equal(incompleteManual.confirmed, false);
  assert.equal(incompleteManual.completed, null);
  assert.equal(incompleteManual.binding, null);
  assert.equal(incompleteManual.completion_audit.reason, 'group_status_failed');
  assert.deepEqual(sameDayCompletionGate({
    groups: [completedGroup()],
    request: requestFixture({
      filters: { ...requestFixture().filters, siteId: 'MLM', siteIds: ['MLM'] },
    }),
    confirmationStore: store,
    now: () => '2026-07-15T15:30:00.000Z',
  }), { allowed: true, confirmed: false, completed: null, binding: null });
  assert.deepEqual(sameDayCompletionGate({
    groups: [completedGroup()], request: requestFixture(),
    now: () => '2026-07-16T15:30:00.000Z',
  }), { allowed: true, confirmed: false, completed: null, binding: null });
});

test('HTTP prepare gate blocks automatic duplicates and resumes manual preparation without a same-day warning', async () => {
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
    assert.equal(manual.status, 200);
    const manualBody = await manual.json();
    assert.equal(manualBody.prepare_id, 'prepare-paused');
    assert.equal(manualBody.prepare.state, 'prepared');
    const acceptedState = fs.readFileSync(path.join(dataDir, 'execution-submissions', 'prepare-paused.json'), 'utf8');
    assert.equal(acceptedState.includes('same_day_confirmation_token'), false);
    assert.equal(acceptedState.includes('same_day_warning'), false);

    const responseRetry = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(manualRequest),
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
