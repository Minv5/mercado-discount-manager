import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const SERVER_SOURCE = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');

test('configured callback trusts verified cache immediately on startup', () => {
  const start = SERVER_SOURCE.indexOf('if (claimConfig.enabled && claimConfig.secretFile)');
  const end = SERVER_SOURCE.indexOf('activityClaimConsumer.start();', start);
  assert.ok(start >= 0, 'claim consumer startup block is present');
  assert.ok(end > start, 'claim consumer startup block has a start call');
  const block = SERVER_SOURCE.slice(start, end);
  const startupPolicy = block.slice(block.indexOf('// The callback link is already configured'));
  assert.match(startupPolicy, /setActivityCallbackAvailability\(true\);/);
  assert.doesNotMatch(startupPolicy, /setActivityCallbackAvailability\(false\);/);
});
