import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  buildDryRunPlan,
  buildItemMultigetUrl,
  isCliEntry,
  runSamples,
} from '../scripts/evidence/local-item-multiget-harness.mjs';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const HARNESS = fileURLToPath(new URL('../scripts/evidence/local-item-multiget-harness.mjs', import.meta.url));
const SAMPLES = ['A', 'B', 'C'].map((account_mask, index) => ({
  account_mask,
  route_label: `${account_mask}-01`,
  token_path: `synthetic-token-${index}`,
  expected_user_id: `child-${index}`,
  item_id: `MLB-SYNTH-${index}`,
  expected: { child_user_id: `child-${index}`, site_id: 'MLB' },
}));

function startServer(handler) {
  const server = http.createServer(handler);
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({
    server,
    baseUrl: `http://127.0.0.1:${server.address().port}`,
  })));
}

function closeServer(server) {
  return new Promise((resolve) => server.close(resolve));
}

function runtimeTokenLoader(_path, expectedUserId) {
  return Promise.resolve({ ok: true, accessToken: `runtime-token-${expectedUserId}` });
}

test('URL construction is exact and uses one encoded ids query value', () => {
  const url = buildItemMultigetUrl('http://127.0.0.1:1234', 'MLB-SYNTH/1');
  assert.equal(url, 'http://127.0.0.1:1234/items?ids=MLB-SYNTH%2F1');
});

test('dry-run plans exactly three GETs without exposing IDs or tokens', () => {
  const result = buildDryRunPlan(SAMPLES);
  assert.deepEqual(result, {
    ok: true,
    status: 'dry_run',
    endpoint_family: 'global_items_multiget',
    method: 'GET',
    planned_gets: 3,
    batch_limit: 20,
    nonGET: 0,
    oauth: 0,
  });
  const cli = execFileSync(process.execPath, [HARNESS, '--dry-run'], {
    cwd: ROOT,
    input: JSON.stringify({ samples: SAMPLES }),
    encoding: 'utf8',
  });
  assert.doesNotMatch(cli, /SYNTH|runtime-token|child-\d/);
});

test('absolute Windows path with spaces and Chinese characters runs main, while import stays side-effect free', async () => {
  assert.equal(isCliEntry(new URL(`file://${HARNESS.replaceAll('\\', '/')}`), HARNESS), true);
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), '美客多 multiget harness '));
  const staged = path.join(tempRoot, 'local item harness.mjs');
  try {
    await fs.copyFile(HARNESS, staged);
    const cli = execFileSync(process.execPath, [staged, '--dry-run'], {
      cwd: ROOT,
      input: JSON.stringify({ samples: SAMPLES }),
      encoding: 'utf8',
    });
    assert.match(cli, /"status":"dry_run"/);
    assert.match(cli, /"planned_gets":3/);
    assert.doesNotMatch(cli, /SYNTH|runtime-token|child-\d/);
  } finally {
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
});

test('302 is not followed and produces one safe transport result', async () => {
  let requests = 0;
  const { server, baseUrl } = await startServer((_req, res) => {
    requests += 1;
    res.writeHead(302, { Location: '/redirected' });
    res.end();
  });
  try {
    const result = await runSamples({ samples: SAMPLES, baseUrl, tokenLoader: runtimeTokenLoader });
    assert.equal(requests, 1);
    assert.equal(result.get_count, 1);
    assert.equal(result.nonGET, 0);
    assert.equal(result.oauth, 0);
    assert.equal(result.ok, false);
    assert.equal(result.results[0].reason_code, 'TRANSPORT_OR_REDIRECT_BLOCKED');
    assert.doesNotMatch(JSON.stringify(result), /SYNTH|runtime-token|child-\d/);
  } finally {
    await closeServer(server);
  }
});

test('three 200 multiget rows require outer code 200 and exact identity booleans', async () => {
  let requests = 0;
  const { server, baseUrl } = await startServer((req, res) => {
    requests += 1;
    const itemId = new URL(req.url, baseUrl).searchParams.get('ids');
    const index = Number(String(itemId).split('-').at(-1));
    const body = { id: itemId, seller_id: `child-${index}`, site_id: 'MLB', status: 'active', price: 123 };
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify([{ code: 200, body }]));
  });
  try {
    const result = await runSamples({ samples: SAMPLES, baseUrl, tokenLoader: runtimeTokenLoader });
    assert.equal(requests, 3);
    assert.equal(result.get_count, 3);
    assert.equal(result.nonGET, 0);
    assert.equal(result.oauth, 0);
    assert.equal(result.ok, true);
    for (const row of result.results) {
      assert.equal(row.http_status, 200);
      assert.equal(row.outer_code, 200);
      assert.deepEqual(row.body_field_presence, { id: true, seller_id: true, site_id: true, status: true, price: true });
      assert.equal(row.identity_match, true);
    }
    assert.doesNotMatch(JSON.stringify(result), /SYNTH|runtime-token|child-\d|123/);
  } finally {
    await closeServer(server);
  }
});

test('404 item response stops at first sample and preserves per-item code contract', async () => {
  let requests = 0;
  const { server, baseUrl } = await startServer((_req, res) => {
    requests += 1;
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify([{ code: 404, body: { message: 'not found', id: 'hidden' } }]));
  });
  try {
    const result = await runSamples({ samples: SAMPLES, baseUrl, tokenLoader: runtimeTokenLoader });
    assert.equal(requests, 1);
    assert.equal(result.get_count, 1);
    assert.equal(result.results[0].outer_code, 404);
    assert.equal(result.ok, false);
    assert.doesNotMatch(JSON.stringify(result), /not found|hidden|SYNTH|runtime-token/);
  } finally {
    await closeServer(server);
  }
});

test('identity mismatch stops without accepting a 200 row', async () => {
  let requests = 0;
  const { server, baseUrl } = await startServer((req, res) => {
    requests += 1;
    const itemId = new URL(req.url, baseUrl).searchParams.get('ids');
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify([{ code: 200, body: { id: itemId, seller_id: 'wrong', site_id: 'MLM', status: 'active', price: 123 } }]));
  });
  try {
    const result = await runSamples({ samples: SAMPLES, baseUrl, tokenLoader: runtimeTokenLoader });
    assert.equal(requests, 1);
    assert.equal(result.results[0].http_status, 200);
    assert.equal(result.results[0].outer_code, 200);
    assert.equal(result.results[0].identity_match, false);
    assert.equal(result.ok, false);
  } finally {
    await closeServer(server);
  }
});
