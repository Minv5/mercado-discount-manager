import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { spawn } from 'node:child_process';

import {
  ACTIVE_SUBMISSION_STATES,
  claimSubmissionPreparation,
  commitSubmission,
  createSubmissionPersistence,
  loadAllEffectiveSubmissions,
  loadEffectiveSubmission,
} from '../src/submissionPersistence.js';

const NOW = '2026-07-16T06:00:00.000Z';
const EXPIRED_AT = '2026-07-16T05:59:59.000Z';
const FUTURE_AT = '2026-07-16T06:15:00.000Z';

function temporaryDirectory() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-stale-prepared-'));
}

function createStore() {
  return createSubmissionPersistence({ stateDir: temporaryDirectory(), now: () => NOW });
}

async function waitForHealth(baseUrl) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error('isolated test server did not become healthy');
}

function preparedRecord(overrides = {}) {
  return {
    id: 'prepare-stale',
    client_submission_id: 'submit-stale',
    state: 'prepared',
    expires_at: EXPIRED_AT,
    group_id: null,
    progress: { stage: 'prepared', percent: 100, message: '执行范围准备完成' },
    audit_events: [],
    ...overrides,
  };
}

test('expired clean prepared snapshot is atomically normalized once with ttl_elapsed audit', async () => {
  const store = createStore();
  store.create(preparedRecord());

  const results = await Promise.all(Array.from({ length: 12 }, async () => loadEffectiveSubmission({
    store,
    prepareId: 'prepare-stale',
    now: () => NOW,
  })));

  assert.ok(results.every((row) => row.state === 'expired'));
  const saved = store.load('prepare-stale');
  assert.equal(saved.state, 'expired');
  assert.equal(saved.version, 2);
  assert.equal(saved.failure_code, 'PREPARE_EXPIRED');
  assert.equal(saved.progress.stage, 'expired');
  assert.deepEqual(saved.audit_events.filter((row) => row.type === 'ttl_elapsed').map((row) => ({
    from_state: row.from_state,
    to_state: row.to_state,
    reason: row.reason,
  })), [{ from_state: 'prepared', to_state: 'expired', reason: 'prepared_ttl_elapsed' }]);
});

test('effective active listing expires stale clean prepared snapshots and startup sees no active row', () => {
  const store = createStore();
  store.create(preparedRecord());
  store.create(preparedRecord({
    id: 'prepare-future',
    client_submission_id: 'submit-future',
    expires_at: FUTURE_AT,
  }));

  const rows = loadAllEffectiveSubmissions({ store, now: () => NOW });
  assert.equal(rows.find((row) => row.id === 'prepare-stale').state, 'expired');
  assert.equal(rows.find((row) => row.id === 'prepare-future').state, 'prepared');
  assert.deepEqual(rows.filter((row) => ACTIVE_SUBMISSION_STATES.has(row.state)).map((row) => row.id), ['prepare-future']);
});

test('expired same client id is returned idempotently while no longer blocking another submission', () => {
  const store = createStore();
  store.create(preparedRecord());

  const same = claimSubmissionPreparation({
    store,
    clientSubmissionId: 'submit-stale',
    now: () => NOW,
    createRecord: () => preparedRecord({ id: 'must-not-create' }),
  });
  assert.equal(same.reused, true);
  assert.equal(same.prepare.id, 'prepare-stale');
  assert.equal(same.prepare.state, 'expired');

  const other = claimSubmissionPreparation({
    store,
    clientSubmissionId: 'submit-new',
    now: () => NOW,
    createRecord: () => ({
      id: 'prepare-new', client_submission_id: 'submit-new', state: 'preparing', request: {},
    }),
  });
  assert.equal(other.reused, false);
  assert.equal(other.prepare.id, 'prepare-new');
});

test('expired prepared snapshot cannot commit or recover', async () => {
  const store = createStore();
  store.create(preparedRecord({ seller_input: { selected_targets: [] } }));
  let starts = 0;

  await assert.rejects(() => commitSubmission({
    store,
    prepareId: 'prepare-stale',
    confirmText: 'REAL_SUBMIT',
    now: () => NOW,
    revalidate: async () => ({ scope_hash: 'same' }),
    startGroup: async () => { starts += 1; return { id: 'must-not-start' }; },
  }), (error) => error.code === 'PREPARE_EXPIRED');
  assert.equal(starts, 0);
  assert.equal(loadEffectiveSubmission({ store, prepareId: 'prepare-stale', now: () => NOW }).state, 'expired');
});

test('unexpired prepared snapshot remains active and unchanged', () => {
  const store = createStore();
  const created = store.create(preparedRecord({ expires_at: FUTURE_AT })).prepare;
  const effective = loadEffectiveSubmission({ store, prepareId: created.id, now: () => NOW });
  assert.equal(effective.state, 'prepared');
  assert.equal(effective.version, created.version);
  assert.equal(effective.audit_events.length, 0);
});

test('server startup, active query, and same-client recovery share effective expiry state', async () => {
  const dataDir = temporaryDirectory();
  const submissionDir = path.join(dataDir, 'execution-submissions');
  fs.mkdirSync(submissionDir, { recursive: true });
  const statePath = path.join(submissionDir, 'prepare-expired-api.json');
  fs.writeFileSync(statePath, JSON.stringify(preparedRecord({
    id: 'prepare-expired-api',
    client_submission_id: 'submit-expired-api',
    expires_at: '2026-01-01T00:00:00.000Z',
    request: { client_submission_id: 'submit-expired-api' },
  })), 'utf8');
  const port = 33000 + Math.floor(Math.random() * 1000);
  const child = spawn(process.execPath, ['src/server.js'], {
    cwd: process.cwd(),
    env: { ...process.env, MDM_PORT: String(port), MDM_DATA_DIR: dataDir },
    stdio: 'ignore',
    windowsHide: true,
  });
  try {
    const baseUrl = `http://127.0.0.1:${port}`;
    await waitForHealth(baseUrl);
    const active = await (await fetch(`${baseUrl}/api/execution/submissions/active`)).json();
    assert.equal(active.active, false);

    const recoveredResponse = await fetch(`${baseUrl}/api/execution/submissions/prepare`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ client_submission_id: 'submit-expired-api' }),
    });
    const recovered = await recoveredResponse.json();
    assert.equal(recoveredResponse.status, 200);
    assert.equal(recovered.reused, true);
    assert.equal(recovered.prepare_id, 'prepare-expired-api');
    assert.equal(recovered.prepare.state, 'expired');

    const persisted = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    assert.equal(persisted.state, 'expired');
    assert.equal(persisted.audit_events.filter((row) => row.type === 'ttl_elapsed').length, 1);
  } finally {
    if (child.exitCode === null) {
      child.kill();
      await new Promise((resolve) => child.once('exit', resolve));
    }
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

for (const [name, evidence] of [
  ['group association', { group_id: 'group-existing' }],
  ['commit lease', { commit_lease_id: 'lease-existing', commit_lease_expires_at: FUTURE_AT }],
  ['commit timestamp', { commit_started_at: '2026-07-16T05:30:00.000Z' }],
  ['write attempt count', { write_attempt_count: 1 }],
  ['prior committing audit', {
    audit_events: [{
      at: '2026-07-16T05:30:00.000Z', type: 'state_changed', from_state: 'prepared', to_state: 'committing', reason: 'commit_confirmed',
    }],
  }],
]) {
  test(`stale prepared snapshot with ${name} is never auto-expired`, () => {
    const store = createStore();
    store.create(preparedRecord(evidence));
    const effective = loadEffectiveSubmission({ store, prepareId: 'prepare-stale', now: () => NOW });
    assert.equal(effective.state, 'prepared');
    assert.equal(effective.version, 1);
    assert.equal(effective.audit_events.filter((row) => row.type === 'ttl_elapsed').length, 0);
  });
}
