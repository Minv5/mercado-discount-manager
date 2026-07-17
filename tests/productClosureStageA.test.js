import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { readProductBuildInfo } from '../src/productContract.js';

test('product build info accepts UTF-8 JSON with and without BOM', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-build-info-'));
  const fingerprint = 'A'.repeat(64);
  const payload = JSON.stringify({
    product: 'mercado-discount-manager',
    protocol_version: '3',
    build_fingerprint: fingerprint,
  });
  try {
    for (const prefix of ['', '\uFEFF']) {
      fs.writeFileSync(path.join(root, 'build-info.json'), `${prefix}${payload}`, 'utf8');
      assert.equal(readProductBuildInfo(root).build_fingerprint, fingerprint);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

async function waitForHealth(baseUrl) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) return response.json();
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('test server did not become healthy');
}

test('health contract and retired synchronous writes are enforced over HTTP', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-stage-a-'));
  const port = 29000 + Math.floor(Math.random() * 1000);
  const child = spawn(process.execPath, ['src/server.js'], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      MDM_PORT: String(port),
      MDM_DATA_DIR: dataDir,
      MDM_DB_PATH: path.join(dataDir, 'test.sqlite'),
      MDM_KEY_PATH: path.join(dataDir, 'local.key'),
    },
    stdio: 'ignore',
    windowsHide: true,
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    const health = await waitForHealth(baseUrl);
    assert.equal(health.product, 'mercado-discount-manager');
    assert.equal(health.protocol_version, '3');
    assert.match(health.build_fingerprint, /^[A-F0-9]{64}$/);

    for (const route of [
      '/api/execute',
      '/api/batch/execute',
      '/api/today/execute',
      '/api/today/precheck',
      '/api/cancel/filtered/precheck',
      '/api/real-enroll-smoke/execute',
      '/api/concurrency-benchmark/write/execute',
    ]) {
      const response = await fetch(`${baseUrl}${route}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
      assert.equal(response.status, 410, route);
      const payload = await response.json();
      assert.equal(payload.retired, true, route);
    }
  } finally {
    child.kill();
    await new Promise((resolve) => child.once('exit', resolve));
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
