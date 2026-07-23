import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const ROOT = process.cwd();

function temporaryDirectory() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-oauth-settings-'));
}

function runIsolated(script) {
  const dataDir = temporaryDirectory();
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    cwd: ROOT,
    env: { ...process.env, MDM_DATA_DIR: dataDir },
    encoding: 'utf8',
  });
  try {
    assert.equal(result.status, 0, result.stderr || result.stdout);
    return { dataDir, payload: JSON.parse(result.stdout) };
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
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

test('settings persist OAuth credentials encrypted, mask the secret, and preserve it for blank saves', () => {
  const { payload } = runIsolated(`
    import fs from 'node:fs';
    import { readSettings, saveSettings, readConfiguredOAuth, resolveOAuthConfig } from './src/settings.js';
    const first = saveSettings({
      oauthClientId: 'client-123', oauthClientSecret: 'secret-value',
      oauthRedirectUri: 'https://example.test/callback',
      webhookCallbackUrl: 'https://callback.example.test/webhook',
    });
    const second = saveSettings({ oauthClientId: 'client-123', oauthClientSecret: '' });
    const raw = fs.readFileSync(process.env.MDM_DATA_DIR + '/settings.json', 'utf8');
    const configured = readConfiguredOAuth();
    const effective = resolveOAuthConfig({ client_id: 'legacy-id', client_secret: 'legacy-secret', redirect_uri: 'https://legacy.test/callback' });
    console.log(JSON.stringify({ first, second, raw, configured, effective, read: readSettings() }));
  `);
  assert.equal(payload.first.oauthClientSecretConfigured, true);
  assert.equal(payload.second.oauthClientSecretConfigured, true);
  assert.equal(payload.read.oauthClientSecret, undefined);
  assert.equal(payload.raw.includes('secret-value'), false);
  assert.equal(payload.configured.client_secret, 'secret-value');
  assert.equal(payload.effective.client_id, 'client-123');
  assert.equal(payload.effective.client_secret, 'secret-value');
  assert.equal(payload.effective.redirect_uri, 'https://example.test/callback');
  assert.equal(payload.effective.config_source, 'settings');
  assert.equal(payload.read.webhookCallbackUrl, 'https://callback.example.test/webhook');
});

test('empty OAuth settings retain standalone compatibility without exposing a secret', () => {
  const { payload } = runIsolated(`
    import { readSettings, resolveOAuthConfig } from './src/settings.js';
    const effective = resolveOAuthConfig({ client_id: 'legacy-id', client_secret: 'legacy-secret', redirect_uri: 'https://legacy.test/callback' });
    console.log(JSON.stringify({ settings: readSettings(), effective }));
  `);
  assert.equal(payload.settings.oauthClientSecretConfigured, false);
  assert.equal(payload.settings.webhookCallbackUrl, '');
  assert.equal(payload.effective.client_id, 'legacy-id');
  assert.equal(payload.effective.client_secret, 'legacy-secret');
  assert.equal(payload.effective.redirect_uri, 'https://legacy.test/callback');
  assert.equal(payload.effective.config_source, 'standalone-auth-dir');
});

test('legacy 42 and 24 scheduler defaults migrate to the real tested ceilings', () => {
  const { payload } = runIsolated(`
    import fs from 'node:fs';
    import { readSettings, saveSettings } from './src/settings.js';
    fs.mkdirSync(process.env.MDM_DATA_DIR, { recursive: true });
    fs.writeFileSync(process.env.MDM_DATA_DIR + '/settings.json', JSON.stringify({
      readConcurrency: 125, previewConcurrency: 42, writeConcurrency: 24,
    }));
    const migrated = readSettings();
    const persisted = saveSettings({});
    console.log(JSON.stringify({ migrated, persisted }));
  `);
  assert.equal(payload.migrated.readConcurrency, 125);
  assert.equal(payload.migrated.previewConcurrency, 192);
  assert.equal(payload.migrated.writeConcurrency, 160);
  assert.equal(payload.persisted.concurrencyPolicyVersion, 2);
});

test('OAuth start reads saved settings instead of requiring a standalone config edit', async () => {
  const dataDir = temporaryDirectory();
  const port = 33000 + Math.floor(Math.random() * 1000);
  const server = (await import('node:child_process')).spawn(process.execPath, ['src/server.js'], {
    cwd: ROOT,
    env: { ...process.env, MDM_DATA_DIR: dataDir, MDM_PORT: String(port) },
    stdio: 'ignore',
    windowsHide: true,
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    await waitForHealth(baseUrl);
    const saved = await fetch(`${baseUrl}/api/settings`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        oauthClientId: 'saved-client', oauthClientSecret: 'saved-secret',
        oauthRedirectUri: 'https://settings.example.test/callback',
        webhookCallbackUrl: 'https://callback.example.test/webhook',
      }),
    });
    assert.equal(saved.status, 200);
    const publicSettings = (await saved.json()).settings;
    assert.equal(publicSettings.oauthClientSecret, undefined);
    assert.equal(publicSettings.oauthClientSecretConfigured, true);
    const started = await fetch(`${baseUrl}/api/oauth/start/from-config`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}',
    });
    assert.equal(started.status, 200);
    const body = await started.json();
    assert.equal(body.configSource, 'settings');
    assert.match(body.authorizationUrl, /client_id=saved-client/);
    assert.match(body.authorizationUrl, /redirect_uri=https%3A%2F%2Fsettings\.example\.test%2Fcallback/);
  } finally {
    server.kill();
    await new Promise((resolve) => server.once('exit', resolve));
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
