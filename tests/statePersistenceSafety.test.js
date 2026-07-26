import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
  createExecutionGroupPersistence,
  projectLiveExecutionGroupChildren,
  summarizeExecutionGroup,
} from '../src/executionGroupPersistence.js';
import { createExecutionJobPersistence } from '../src/executionJobPersistence.js';
import { createPendingWriteQueue } from '../src/pendingWriteQueue.js';
import {
  acquireProcessInstanceLock,
  writeJsonFileAtomicallySync,
} from '../src/processInstanceLock.js';

function temporaryDirectory(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function removeDirectory(directory) {
  fs.rmSync(directory, { recursive: true, force: true });
}

function groupFixture(overrides = {}) {
  return {
    id: 'group-safe-1',
    client_submission_id: 'submission-safe-1',
    status: 'running',
    action: 'enroll',
    children: [
      { job_id: 'job-a', account_id: 'A', status: 'completed', result: { execution: { total: 2, success: 2 } } },
      { job_id: 'job-b', account_id: 'B', status: 'failed', result: null, error: 'job failed before result assembly' },
    ],
    created_at: '2026-07-26T00:00:00.000Z',
    ...overrides,
  };
}

test('process instance lock blocks a live owner without touching task state and supports owned release', () => {
  const dataDir = temporaryDirectory('mdm-process-lock-live-');
  try {
    const first = acquireProcessInstanceLock({
      dataDir,
      currentPid: 101,
      instanceId: 'instance-a',
      isProcessAlive: (pid) => pid === 101,
    });
    const groupDir = path.join(dataDir, 'execution-group-states');
    assert.throws(
      () => acquireProcessInstanceLock({
        dataDir,
        currentPid: 202,
        instanceId: 'instance-b',
        isProcessAlive: (pid) => pid === 101,
      }),
      (error) => error?.code === 'PROCESS_INSTANCE_ALREADY_RUNNING'
        && error?.owner?.pid === 101,
    );
    assert.equal(fs.existsSync(groupDir), false);
    assert.deepEqual(first.release(), { released: true, reason: 'released' });
    assert.equal(fs.existsSync(first.lockPath), false);
  } finally {
    removeDirectory(dataDir);
  }
});

test('process instance lock reuses only the same pid and instance identity and reference-counts release', () => {
  const dataDir = temporaryDirectory('mdm-process-lock-reentrant-');
  try {
    const options = {
      dataDir,
      currentPid: 101,
      instanceId: 'same-instance',
      isProcessAlive: () => true,
    };
    const first = acquireProcessInstanceLock(options);
    const second = acquireProcessInstanceLock(options);
    assert.equal(second.reused, true);
    assert.deepEqual(second.release(), { released: false, reason: 'still_held' });
    assert.equal(fs.existsSync(first.lockPath), true);
    assert.deepEqual(first.release(), { released: true, reason: 'released' });
  } finally {
    removeDirectory(dataDir);
  }
});

test('process instance lock recovers a dead owner but never guesses through corrupt lock content', () => {
  const dataDir = temporaryDirectory('mdm-process-lock-stale-');
  try {
    const lockPath = path.join(dataDir, '.mercado-discount-manager.node.lock');
    fs.writeFileSync(lockPath, JSON.stringify({ version: 1, pid: 101, instance_id: 'dead-owner' }), 'utf8');
    const recovered = acquireProcessInstanceLock({
      dataDir,
      currentPid: 202,
      instanceId: 'replacement',
      isProcessAlive: () => false,
    });
    assert.equal(recovered.recoveredStaleLock, true);
    assert.equal(JSON.parse(fs.readFileSync(lockPath, 'utf8')).instance_id, 'replacement');
    assert.equal(fs.readdirSync(dataDir).some((name) => name.includes('.stale-')), false);
    recovered.release();

    fs.writeFileSync(lockPath, '{not-json', 'utf8');
    assert.throws(
      () => acquireProcessInstanceLock({
        dataDir,
        currentPid: 303,
        instanceId: 'must-not-steal',
        isProcessAlive: () => false,
      }),
      (error) => error?.code === 'PROCESS_INSTANCE_LOCK_CORRUPT',
    );
    assert.equal(fs.readFileSync(lockPath, 'utf8'), '{not-json');
  } finally {
    removeDirectory(dataDir);
  }
});

test('process instance lock removes its own partial lock when lock content cannot be written', () => {
  const dataDir = temporaryDirectory('mdm-process-lock-partial-');
  let failed = false;
  const failingFs = {
    ...fs,
    writeFileSync(target, ...args) {
      if (typeof target === 'number' && !failed) {
        failed = true;
        const error = new Error('disk write failed');
        error.code = 'EIO';
        throw error;
      }
      return fs.writeFileSync(target, ...args);
    },
  };
  try {
    assert.throws(
      () => acquireProcessInstanceLock({
        dataDir,
        currentPid: 101,
        instanceId: 'partial-owner',
        fsImpl: failingFs,
      }),
      (error) => error?.code === 'EIO',
    );
    assert.equal(fs.existsSync(path.join(dataDir, '.mercado-discount-manager.node.lock')), false);
  } finally {
    removeDirectory(dataDir);
  }
});

test('group and job persistence distinguish missing, corrupt, unreadable, and identity mismatch states', () => {
  const root = temporaryDirectory('mdm-state-read-status-');
  const groupDir = path.join(root, 'groups');
  const jobDir = path.join(root, 'jobs');
  fs.mkdirSync(groupDir, { recursive: true });
  fs.mkdirSync(jobDir, { recursive: true });
  try {
    const groupStore = createExecutionGroupPersistence({ stateDir: groupDir, currentPid: process.pid });
    const jobStore = createExecutionJobPersistence({ stateDir: jobDir, publicJob: (job) => ({ ...job }), currentPid: process.pid });

    assert.equal(groupStore.inspect('missing-group').status, 'missing');
    assert.equal(jobStore.inspect('missing-job').status, 'missing');

    fs.writeFileSync(groupStore.statePath('bad-json'), '{broken', 'utf8');
    fs.writeFileSync(jobStore.statePath('wrong-id'), JSON.stringify({ id: 'different-job' }), 'utf8');
    assert.equal(groupStore.inspect('bad-json').status, 'corrupt');
    assert.equal(jobStore.inspect('wrong-id').status, 'identity_mismatch');
    assert.throws(() => groupStore.load('bad-json'), (error) => error?.code === 'EXECUTION_GROUP_STATE_CORRUPT');
    assert.throws(() => jobStore.load('wrong-id'), (error) => error?.code === 'EXECUTION_JOB_STATE_IDENTITY_MISMATCH');
    assert.throws(
      () => createExecutionGroupPersistence({ stateDir: groupDir, currentPid: process.pid }),
      (error) => error?.code === 'EXECUTION_GROUP_STATE_CORRUPT',
    );

    const deniedFs = {
      ...fs,
      readFileSync(target, ...args) {
        if (String(target).endsWith('denied.json')) {
          const error = new Error('access denied');
          error.code = 'EACCES';
          throw error;
        }
        return fs.readFileSync(target, ...args);
      },
    };
    fs.writeFileSync(path.join(groupDir, 'denied.json'), '{}', 'utf8');
    const deniedStore = createExecutionGroupPersistence({
      stateDir: groupDir,
      currentPid: process.pid,
      fsImpl: deniedFs,
      loadOnCreate: false,
    });
    assert.equal(deniedStore.inspect('denied').status, 'unreadable');
    assert.throws(() => deniedStore.load('denied'), (error) => error?.code === 'EXECUTION_GROUP_STATE_UNREADABLE');
  } finally {
    removeDirectory(root);
  }
});

test('atomic persistence retries transient Windows file errors, uses unique temp files, and cleans owned temps', () => {
  const root = temporaryDirectory('mdm-atomic-state-');
  const target = path.join(root, 'state.json');
  let renameAttempts = 0;
  const temporaryNames = [];
  const flakyFs = {
    ...fs,
    writeFileSync(file, ...args) {
      temporaryNames.push(String(file));
      return fs.writeFileSync(file, ...args);
    },
    renameSync(from, to) {
      renameAttempts += 1;
      if (renameAttempts <= 2) {
        const error = new Error('temporarily blocked');
        error.code = renameAttempts === 1 ? 'EPERM' : 'EBUSY';
        throw error;
      }
      return fs.renameSync(from, to);
    },
  };
  try {
    writeJsonFileAtomicallySync({
      target,
      value: { ok: true },
      currentPid: 404,
      fsImpl: flakyFs,
      retryDelaysMs: [0, 0, 0],
      sleepSync: () => {},
    });
    assert.equal(renameAttempts, 3);
    assert.deepEqual(JSON.parse(fs.readFileSync(target, 'utf8')), { ok: true });
    assert.equal(new Set(temporaryNames).size, 1);
    assert.match(temporaryNames[0], /\.404\.\d+\.[A-Za-z0-9-]+\.tmp$/);
    assert.equal(fs.readdirSync(root).some((name) => name.endsWith('.tmp')), false);
  } finally {
    removeDirectory(root);
  }
});

test('atomic persistence leaves the prior target intact and cleans its temp after permanent replace failure', () => {
  const root = temporaryDirectory('mdm-atomic-state-failed-');
  const target = path.join(root, 'state.json');
  fs.writeFileSync(target, JSON.stringify({ version: 'prior' }), 'utf8');
  const blockedFs = {
    ...fs,
    renameSync() {
      const error = new Error('permanently blocked');
      error.code = 'EPERM';
      throw error;
    },
  };
  try {
    assert.throws(
      () => writeJsonFileAtomicallySync({
        target,
        value: { version: 'next' },
        currentPid: 505,
        fsImpl: blockedFs,
        retryDelaysMs: [0, 0],
        sleepSync: () => {},
      }),
      (error) => error?.code === 'EPERM',
    );
    assert.deepEqual(JSON.parse(fs.readFileSync(target, 'utf8')), { version: 'prior' });
    assert.equal(fs.readdirSync(root).some((name) => name.endsWith('.tmp')), false);
  } finally {
    removeDirectory(root);
  }
});

test('group, job, and pending queue persistence all use the bounded atomic writer', () => {
  const root = temporaryDirectory('mdm-persistence-writer-');
  const attempts = { group: 0, job: 0, pending: 0 };
  function flakyFsFor(kind) {
    return {
      ...fs,
      renameSync(from, to) {
        attempts[kind] += 1;
        if (attempts[kind] === 1) {
          const error = new Error('antivirus race');
          error.code = 'EACCES';
          throw error;
        }
        return fs.renameSync(from, to);
      },
    };
  }
  try {
    const common = { retryDelaysMs: [0, 0], sleepSync: () => {} };
    const groupStore = createExecutionGroupPersistence({
      stateDir: path.join(root, 'groups'),
      currentPid: process.pid,
      fsImpl: flakyFsFor('group'),
      ...common,
    });
    groupStore.persist(groupFixture({ id: 'group-atomic', status: 'completed' }));

    const jobStore = createExecutionJobPersistence({
      stateDir: path.join(root, 'jobs'),
      publicJob: (job) => ({ ...job }),
      currentPid: process.pid,
      fsImpl: flakyFsFor('job'),
      ...common,
    });
    jobStore.persist({ id: 'job-atomic', status: 'completed', result: { execution: { total: 1, success: 1 } } });

    const pendingQueue = createPendingWriteQueue({
      stateDir: path.join(root, 'pending'),
      currentPid: process.pid,
      fsImpl: flakyFsFor('pending'),
      ...common,
    });
    pendingQueue.enqueue('job-atomic', { relation_key: 'A|MLM|P|DEAL|I|enroll' });

    assert.deepEqual(attempts, { group: 2, job: 2, pending: 2 });
    assert.equal(fs.readdirSync(path.join(root, 'groups')).some((name) => name.endsWith('.tmp')), false);
    assert.equal(fs.readdirSync(path.join(root, 'jobs')).some((name) => name.endsWith('.tmp')), false);
    assert.equal(fs.readdirSync(path.join(root, 'pending')).some((name) => name.endsWith('.tmp')), false);
  } finally {
    removeDirectory(root);
  }
});

test('group projection and summary expose missing child state and null results as incomplete instead of silently complete zeroes', () => {
  const projected = projectLiveExecutionGroupChildren(
    groupFixture({ children: [{ job_id: 'job-missing', account_id: 'A', status: 'running', result: null }] }),
    () => null,
  );
  assert.equal(projected[0].persistence_state, 'missing');
  assert.equal(projected[0].incomplete, true);
  assert.equal(projected[0].incomplete_reason, 'missing_job_state');

  const summary = summarizeExecutionGroup(groupFixture());
  assert.equal(summary.accounting_complete, false);
  assert.equal(summary.incomplete_child_count, 2);
  assert.deepEqual(summary.incomplete_job_ids, ['job-a', 'job-b']);
  assert.equal(summary.stores[1].result_present, false);
  assert.equal(summary.stores[1].incomplete, true);
  assert.deepEqual(summary.incomplete_details, [
    { job_id: 'job-a', reasons: ['accounting_not_proven', 'terminal_counts_not_proven'] },
    { job_id: 'job-b', reasons: ['missing_child_result'] },
  ]);
  assert.ok(summary.incomplete_reasons.includes('missing_child_result'));
});

function closedChildResult(overrides = {}) {
  const terminalCounts = {
    relation_count: 2,
    success: 2,
    failed: 0,
    skipped: 0,
    platform_pending: 0,
    unresolved: 0,
    classified_count: 2,
    is_closed: true,
    is_resolved: true,
    ...(overrides.terminal_counts || {}),
  };
  const execution = {
    relation_count: terminalCounts.relation_count,
    success: terminalCounts.success,
    failed: terminalCounts.failed,
    skipped: terminalCounts.skipped,
    pending: 0,
    pending_verification_count: 0,
    retryable_pending_count: 0,
    platform_pending_count: terminalCounts.platform_pending,
    unresolved: terminalCounts.unresolved,
    accounting_complete: true,
    terminal_counts: { ...terminalCounts },
    ...(overrides.execution || {}),
  };
  return {
    accounting_complete: true,
    terminal_counts: { ...terminalCounts },
    execution,
    ...overrides,
    terminal_counts: { ...terminalCounts, ...(overrides.terminal_counts || {}) },
    execution,
  };
}

test('group accounting requires explicit closed child contracts and never guesses legacy successful counts complete', () => {
  const valid = summarizeExecutionGroup(groupFixture({
    children: [{
      job_id: 'job-valid',
      account_id: 'A',
      status: 'completed',
      result: closedChildResult(),
    }],
  }));
  assert.equal(valid.accounting_complete, true);
  assert.equal(valid.incomplete_child_count, 0);

  const legacy = summarizeExecutionGroup(groupFixture({
    children: [{
      job_id: 'job-legacy',
      account_id: 'A',
      status: 'completed',
      result: { execution: { relation_count: 2, total: 2, success: 2, failed: 0, skipped: 0 } },
    }],
  }));
  assert.equal(legacy.accounting_complete, false);
  assert.deepEqual(legacy.incomplete_details, [{
    job_id: 'job-legacy',
    reasons: ['accounting_not_proven', 'terminal_counts_not_proven'],
  }]);

  const partialProof = closedChildResult();
  delete partialProof.execution.accounting_complete;
  delete partialProof.execution.terminal_counts;
  const partial = summarizeExecutionGroup(groupFixture({
    children: [{
      job_id: 'job-partial-proof',
      account_id: 'A',
      status: 'completed',
      result: partialProof,
    }],
  }));
  assert.deepEqual(partial.incomplete_details, [{
    job_id: 'job-partial-proof',
    reasons: ['accounting_not_proven', 'terminal_counts_not_proven'],
  }]);
});

test('group accounting inherits explicit child accounting and terminal closure failures', () => {
  const summary = summarizeExecutionGroup(groupFixture({
    children: [
      {
        job_id: 'job-accounting-false',
        account_id: 'A',
        status: 'failed',
        result: closedChildResult({ accounting_complete: false }),
      },
      {
        job_id: 'job-terminal-open',
        account_id: 'B',
        status: 'failed',
        result: closedChildResult({
          terminal_counts: { is_closed: false },
          execution: { terminal_counts: { is_closed: false } },
        }),
      },
    ],
  }));
  assert.equal(summary.accounting_complete, false);
  assert.deepEqual(summary.incomplete_job_ids, ['job-accounting-false', 'job-terminal-open']);
  assert.ok(summary.stores[0].incomplete_reasons.includes('accounting_incomplete'));
  assert.ok(summary.stores[1].incomplete_reasons.includes('terminal_counts_not_closed'));
});

test('group accounting keeps exact child reasons for pending unresolved platform pending and relation gaps', () => {
  const summary = summarizeExecutionGroup(groupFixture({
    children: [
      {
        job_id: 'job-pending',
        account_id: 'A',
        status: 'paused',
        result: closedChildResult({ execution: { pending: 1 } }),
      },
      {
        job_id: 'job-unresolved',
        account_id: 'B',
        status: 'paused',
        result: closedChildResult({
          terminal_counts: {
            success: 1, unresolved: 1, classified_count: 2, is_resolved: false,
          },
          execution: {
            success: 1, unresolved: 1,
            terminal_counts: {
              relation_count: 2, success: 1, failed: 0, skipped: 0,
              platform_pending: 0, unresolved: 1, classified_count: 2,
              is_closed: true, is_resolved: false,
            },
          },
        }),
      },
      {
        job_id: 'job-platform-pending',
        account_id: 'C',
        status: 'paused',
        result: closedChildResult({
          terminal_counts: {
            success: 1, platform_pending: 1, classified_count: 2, is_resolved: true,
          },
          execution: {
            success: 1, platform_pending_count: 1,
            terminal_counts: {
              relation_count: 2, success: 1, failed: 0, skipped: 0,
              platform_pending: 1, unresolved: 0, classified_count: 2,
              is_closed: true, is_resolved: true,
            },
          },
        }),
      },
      {
        job_id: 'job-count-gap',
        account_id: 'D',
        status: 'failed',
        result: closedChildResult({
          terminal_counts: { relation_count: 3, classified_count: 2 },
          execution: { relation_count: 3 },
        }),
      },
    ],
  }));

  assert.equal(summary.accounting_complete, false);
  assert.deepEqual(summary.incomplete_details, [
    { job_id: 'job-pending', reasons: ['pending_relations_present'] },
    { job_id: 'job-unresolved', reasons: ['unresolved_relations_present'] },
    { job_id: 'job-platform-pending', reasons: ['platform_pending_present'] },
    { job_id: 'job-count-gap', reasons: ['relation_count_gap'] },
  ]);
});

test('group accounting requires outer and execution terminal counts to match exactly', () => {
  const result = closedChildResult();
  result.execution.terminal_counts = {
    ...result.execution.terminal_counts,
    success: 1,
    failed: 1,
  };
  result.execution.success = 1;
  result.execution.failed = 1;
  const summary = summarizeExecutionGroup(groupFixture({
    children: [{
      job_id: 'job-terminal-mismatch',
      account_id: 'A',
      status: 'completed',
      result,
    }],
  }));

  assert.equal(summary.accounting_complete, false);
  assert.deepEqual(summary.incomplete_details, [{
    job_id: 'job-terminal-mismatch',
    reasons: ['terminal_counts_mismatch'],
  }]);
});

test('group accounting validates each terminal count layer independently', () => {
  const internallyOpen = closedChildResult();
  internallyOpen.execution.terminal_counts = {
    ...internallyOpen.execution.terminal_counts,
    relation_count: 3,
    classified_count: 2,
    is_closed: true,
  };
  internallyOpen.execution.relation_count = 3;

  const missingField = closedChildResult();
  delete missingField.execution.terminal_counts.unresolved;

  const summary = summarizeExecutionGroup(groupFixture({
    children: [
      {
        job_id: 'job-inner-gap',
        account_id: 'A',
        status: 'failed',
        result: internallyOpen,
      },
      {
        job_id: 'job-inner-missing-field',
        account_id: 'B',
        status: 'failed',
        result: missingField,
      },
    ],
  }));

  assert.equal(summary.accounting_complete, false);
  assert.deepEqual(summary.incomplete_details, [
    { job_id: 'job-inner-gap', reasons: ['relation_count_gap', 'terminal_counts_mismatch'] },
    { job_id: 'job-inner-missing-field', reasons: ['terminal_counts_not_proven'] },
  ]);
});
