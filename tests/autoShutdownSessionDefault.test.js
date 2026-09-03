import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

test('server resets automatic shutdown at the beginning of every product session', () => {
  const source = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  const start = source.indexOf('async function initializeServerState');
  const end = source.indexOf('async function', start + 20);
  const body = source.slice(start, end > start ? end : start + 4000);
  assert.match(body, /saveSettings\(\{ autoShutdownAfterExecution: false \}\)/);
  assert.match(body, /新会话默认关闭自动关机/);
});
