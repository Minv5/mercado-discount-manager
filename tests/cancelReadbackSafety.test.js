import assert from 'node:assert/strict';
import test from 'node:test';

import { cancellationReadbackApplied } from '../src/executionResultContract.js';
import { classifyWriteFailure } from '../src/writeFailurePolicy.js';

test('cancellation readback requires both started and pending absence', () => {
  assert.equal(cancellationReadbackApplied({
    startedComplete: true,
    pendingComplete: true,
    inStarted: false,
    inPending: true,
  }), false);
  assert.equal(cancellationReadbackApplied({
    startedComplete: true,
    pendingComplete: false,
    inStarted: false,
    inPending: false,
  }), false);
  assert.equal(cancellationReadbackApplied({
    startedComplete: true,
    pendingComplete: true,
    inStarted: false,
    inPending: false,
  }), true);
});

test('server failures are ambiguous writes while explicit rate limits remain retryable rejections', () => {
  const serviceFailure = classifyWriteFailure({ status: 503, message: 'service unavailable' });
  assert.equal(serviceFailure.ambiguousWrite, true);
  assert.equal(classifyWriteFailure({ status: 500, message: 'server error' }).ambiguousWrite, true);
  assert.equal(classifyWriteFailure({ status: 429, message: 'rate limit' }).ambiguousWrite, false);
});
