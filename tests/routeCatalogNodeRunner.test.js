import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';

import { createBalancedReadScheduler } from '../src/balancedReadScheduler.js';
import {
  createLowConcurrencyMarketplaceClient,
  runRouteCatalogResume,
  writeJsonAtomicWindows,
} from '../scripts/route-catalog-node-runner.mjs';

function routeState(total = 1705) {
  return [{
    route_hash: 'R1', account_id: 'A1', child_identity_hash: 'C1', child_user_id: 'CHILD-1', site_id: 'MLB',
    total, expected_remaining_pages: total, pages_completed: 0, materialized_unique_count: 0, next_cursor: 'c0', complete: false,
  }];
}

test('Node runner uses the existing mlClient scan entry, low concurrency, bounded retry, and no duplicate page materialization', async () => {
  const faultPages = new Set(Array.from({ length: 20 }, (_, index) => (index + 1) * 75));
  const faulted = new Set();
  let attempts = 0;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const cursor = url.searchParams.get('scroll_id') || 'c0';
    const page = Number(String(cursor).slice(1)) + 1;
    attempts += 1;
    if (faultPages.has(page) && !faulted.has(page)) {
      faulted.add(page);
      if (page % 3 === 0) {
        req.socket.destroy();
      } else {
        res.writeHead(page % 3 === 1 ? 429 : 503, { 'Retry-After': '0' });
        res.end();
      }
      return;
    }
    const body = JSON.stringify({ results: [{ id: `ITEM-${page}` }], paging: { total: 1705, limit: 50, scroll_id: `c${page}` } });
    res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const scheduler = createBalancedReadScheduler({ initialLimit: 1, maxLimit: 1, perAccountLimit: 1, activityLimit: 1, successesPerIncrease: 10_000 });
  const client = createLowConcurrencyMarketplaceClient({ accessToken: 'synthetic', userId: 'CHILD-1', apiBaseUrl: `http://127.0.0.1:${port}`, scheduler });
  const states = routeState();
  const ids = new Map([['R1', new Set()]]);
  const sidecars = [];
  const progress = [];
  try {
    const result = await runRouteCatalogResume({
      routeStates: states,
      clientFactory: async () => client,
      identitySets: ids,
      retryReserve: 20,
      attemptCeiling: 1725,
      successPageCeiling: 1705,
      sleep: async () => {},
      writePage: async ({ records }) => ({ sha256: `page-${records[0].local_item_id}` }),
      writeSidecar: async (value) => sidecars.push(value),
      writeCheckpoint: async () => {},
      onProgress: async (value) => progress.push(value),
    });
    assert.equal(result.status, 'complete');
    assert.equal(result.successPages, 1705);
    assert.equal(result.retryState.retries, 20);
    assert.equal(result.retryState.attempts, 1725);
    assert.equal(ids.get('R1').size, 1705);
    assert.equal(sidecars.length, 1705);
    assert.equal(progress.length, 5);
    assert.equal(attempts, 1725);
    assert.equal(states[0].materialized_unique_count, 1705);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test('Node runner retries local checkpoint EPERM, supports interruption/resume, and never repeats a successful page', async () => {
  const states = routeState(5);
  const ids = new Map([['R1', new Set()]]);
  let pageCalls = 0;
  const writeCalls = [];
  let permissionFailures = 2;
  const client = {
    async searchMarketplaceUserItems({ scrollId }) {
      pageCalls += 1;
      const page = Number(String(scrollId).slice(1)) + 1;
      return { results: [{ id: `I-${page}` }], paging: { total: 5, limit: 50, scroll_id: `c${page}` } };
    },
  };
  const writer = async (value) => {
    if (permissionFailures > 0) {
      permissionFailures -= 1;
      throw Object.assign(new Error('sharing violation'), { code: 'EPERM' });
    }
    writeCalls.push(value);
  };
  const interrupted = await runRouteCatalogResume({
    routeStates: states,
    clientFactory: async () => client,
    identitySets: ids,
    successPageCeiling: 5,
    sleep: async () => {},
    writePage: async () => ({ sha256: 'synthetic' }),
    writeSidecar: async () => {},
    writeCheckpoint: writer,
    shouldStop: () => states[0].pages_completed >= 2,
  });
  assert.equal(interrupted.status, 'interrupted');
  assert.equal(interrupted.successPages, 2);
  assert.equal(pageCalls, 2);
  const resumed = await runRouteCatalogResume({
    routeStates: states,
    clientFactory: async () => client,
    identitySets: ids,
    initialSuccessPages: 2,
    successPageCeiling: 5,
    sleep: async () => {},
    writePage: async () => ({ sha256: 'synthetic' }),
    writeSidecar: async () => {},
    writeCheckpoint: writer,
  });
  assert.equal(resumed.status, 'complete');
  assert.equal(resumed.successPages, 5);
  assert.equal(pageCalls, 5);
  assert.equal(ids.get('R1').size, 5);
  assert.ok(writeCalls.length >= 4);
});

test('Node Windows writer keeps a backup and retries sharing violations', async () => {
  const fs = await import('node:fs/promises');
  const os = await import('node:os');
  const path = await import('node:path');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'route-catalog-node-writer-'));
  const target = path.join(directory, 'checkpoint.json');
  await fs.writeFile(target, JSON.stringify({ version: 1 }), 'utf8');
  let calls = 0;
  const rename = async (source, destination) => {
    if (destination === target && calls++ < 2) throw Object.assign(new Error('sharing'), { code: 'EPERM' });
    await fs.rename(source, destination);
  };
  const result = await writeJsonAtomicWindows(target, { version: 2 }, { rename, sleep: async () => {} });
  assert.equal(result.replaced, true);
  assert.deepEqual(JSON.parse(await fs.readFile(target, 'utf8')), { version: 2 });
  assert.deepEqual(JSON.parse(await fs.readFile(`${target}.bak`, 'utf8')), { version: 1 });
});
