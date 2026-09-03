import assert from 'node:assert/strict';
import test from 'node:test';

import { executePlannedRowsWithConcurrency } from '../src/executor.js';

test('ambiguous write response becomes pending without repeating the write', async () => {
  let calls = 0;
  const saved = [];
  const pending = [];
  const result = await executePlannedRowsWithConcurrency({
    plan: { rows: [{ status: 'planned', item: { item_id: 'MLB1' }, deal_price: 90 }] },
    action: 'cancel', promotionId: 'P1', promotionType: 'DEAL', accountId: 'A1', taskId: 1, mode: 'real',
    executeOne: async () => { calls += 1; throw new Error('socket response lost'); },
    saveResult: async (row) => saved.push(row),
    classifyError: () => ({ interfaceFailure: true, transientFailure: true, ambiguousWrite: true }),
    onPending: async (row) => pending.push(row),
    retryOptions: { maxImmediateRetries: 3, deferredFinalRetry: true },
  });
  assert.equal(calls, 1);
  assert.equal(result.counts.pending, 1);
  assert.equal(result.retrySummary.immediate_retries, 0);
  assert.equal(saved.at(-1).status, 'pending');
  assert.equal(pending.length, 1);
});
