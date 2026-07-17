import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import test from 'node:test';

import { resolveStoreIdentity } from '../src/storeNameDomain.js';

const root = process.cwd();

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

async function waitForExit(child) {
  if (child.exitCode !== null) return child.exitCode;
  return new Promise((resolve) => child.once('exit', resolve));
}

test('store identity has one Node authority and keeps raw names separate from daily names', () => {
  assert.deepEqual(resolveStoreIdentity({
    accountId: '3408885754',
    profile: { display_name: 'CNLIUYANGSHIZHEPINGDIAN' },
    storeAliases: { '3408885754': '湖南' },
  }), {
    raw_display_name: 'CNLIUYANGSHIZHEPINGDIAN',
    store_name: '湖南',
    store_name_source: 'explicit_alias',
  });
  assert.equal(resolveStoreIdentity({ accountId: '3332096437', profile: { display_name: 'RAW' } }).store_name, '店铺待命名');
  assert.equal(resolveStoreIdentity({ accountId: '3408885754', profile: { display_name: 'RAW' } }).store_name, '店铺待命名');
  assert.equal(resolveStoreIdentity({ accountId: 'NEW', profile: { display_name: 'ENGLISH-NAME' } }).store_name, '店铺待命名');
  assert.equal(resolveStoreIdentity({ accountId: 'NEW', profile: { display_name: '中文店名' } }).store_name, '店铺待命名');
});

test('/api/accounts returns raw, business, and source names from the Node authority', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-stage-d1-'));
  const authDir = path.join(dataDir, 'no-standalone-auth');
  const port = 30000 + Math.floor(Math.random() * 1000);
  const env = {
    ...process.env,
    MDM_PORT: String(port),
    MDM_DATA_DIR: dataDir,
    MDM_DB_PATH: path.join(dataDir, 'test.sqlite'),
    MDM_KEY_PATH: path.join(dataDir, 'local.key'),
    ML_STANDALONE_AUTH_DIR: authDir,
  };
  const seedCode = `
    const { saveTokenAccount } = await import('./src/repository.js');
    const { saveSettings } = await import('./src/settings.js');
    const rows = [
      ['2651442567', 'CNHUBEISHENGRUIHESHANGM'],
      ['3332096437', 'CNGUANGZHOULINGTANGMINB'],
      ['3408885754', 'CNLIUYANGSHIZHEPINGDIAN'],
      ['9999999999', 'UNKNOWN-ENGLISH']
    ];
    for (const [id, nickname] of rows) {
      saveTokenAccount({
        token: { user_id: id, access_token: 'test-access-' + id, refresh_token: 'test-refresh-' + id },
        profile: { id, nickname, site_id: 'CBT' },
        clientId: 'test-client',
        clientSecret: 'test-secret',
        redirectUri: 'http://127.0.0.1/callback',
        authDomain: 'test'
      });
    }
    saveSettings({ storeAliases: { '2651442567': '湖北', '3332096437': '广州', '3408885754': '湖南' } });
  `;
  const seed = spawn(process.execPath, ['--input-type=module', '-e', seedCode], {
    cwd: root,
    env,
    stdio: 'ignore',
    windowsHide: true,
  });
  assert.equal(await waitForExit(seed), 0);

  const server = spawn(process.execPath, ['src/server.js'], {
    cwd: root,
    env,
    stdio: 'ignore',
    windowsHide: true,
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    await waitForHealth(baseUrl);
    const response = await fetch(`${baseUrl}/api/accounts`);
    const payload = await response.json();
    assert.equal(response.status, 200, JSON.stringify(payload));
    assert.equal(Array.isArray(payload.accounts), true, JSON.stringify(payload));
    const accounts = Object.fromEntries(payload.accounts.map((row) => [String(row.account_id), row]));
    assert.deepEqual(
      [accounts['2651442567'].raw_display_name, accounts['2651442567'].store_name, accounts['2651442567'].store_name_source],
      ['CNHUBEISHENGRUIHESHANGM', '湖北', 'explicit_alias'],
    );
    assert.equal(accounts['3332096437'].store_name, '广州');
    assert.equal(accounts['3332096437'].store_name_source, 'explicit_alias');
    assert.equal(accounts['3408885754'].store_name, '湖南');
    assert.equal(accounts['3408885754'].store_name_source, 'explicit_alias');
    assert.equal(accounts['9999999999'].raw_display_name, 'UNKNOWN-ENGLISH');
    assert.equal(accounts['9999999999'].store_name, '店铺待命名');
    assert.equal(accounts['9999999999'].store_name_source, 'fallback');
  } finally {
    server.kill();
    await waitForExit(server);
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('production consumers do not duplicate account-id store mappings', () => {
  const server = fs.readFileSync(path.join(root, 'src/server.js'), 'utf8');
  const repository = fs.readFileSync(path.join(root, 'src/repository.js'), 'utf8');
  const core = fs.readFileSync(path.join(root, 'desktop-pyside/core.py'), 'utf8');
  const storeDomain = fs.readFileSync(path.join(root, 'src/storeNameDomain.js'), 'utf8');
  for (const source of [server, repository, core, storeDomain]) {
    assert.doesNotMatch(source, /2651442567[\s\S]{0,80}湖北店/);
    assert.doesNotMatch(source, /3332096437[\s\S]{0,80}湖南店/);
    assert.doesNotMatch(source, /3408885754[\s\S]{0,80}广东店/);
  }
  assert.doesNotMatch(core, /KNOWN_STORES|infer_store_name/);
  assert.doesNotMatch(storeDomain, /KNOWN_STORE_NAMES|known_account/);
  assert.match(core, /row\.get\("store_name"\)/);
  assert.match(server, /display_name: identity\.raw_display_name/);
  assert.match(server, /\.\.\.identity/);
  assert.match(server, /storeNames\[accountId\] = storeIdentityForAccount\(accountId, account, settings\)\.store_name/);
});

test('public root is service-only and carries no legacy write workbench', () => {
  const html = fs.readFileSync(path.join(root, 'public/index.html'), 'utf8');
  const server = fs.readFileSync(path.join(root, 'src/server.js'), 'utf8');
  assert.doesNotMatch(html, /提交执行|SMART|压测|报名|更新|取消|app\.js/);
  assert.match(html, /本地服务/);
  assert.equal(fs.existsSync(path.join(root, 'public/app.js')), false);
  assert.match(server, /\/api\/oauth\/start\/from-config/);
  assert.match(server, /\/api\/oauth\/complete-callback/);
  assert.match(server, /\/oauth\/callback/);
});

test('PySide release stages only the minimal public allowlist', () => {
  const build = fs.readFileSync(path.join(root, 'desktop-pyside/build-release.ps1'), 'utf8');
  assert.match(build, /PublicReleaseFiles/);
  assert.doesNotMatch(build, /Copy-Item[^\n]+ProjectRoot 'public'[^\n]+-Recurse/);
  assert.doesNotMatch(build, /_internal\\app\\public\\app\.js/);
  assert.doesNotMatch(build, /Copy-Item[^\n]+README\.md/);
});

test('validation defaults to PySide and Legacy remains explicit', () => {
  const validate = fs.readFileSync(path.join(root, 'scripts/validate.ps1'), 'utf8');
  assert.match(validate, /\[string\]\$PackageTarget = 'PySide'/);
  assert.match(validate, /\$PackageTarget -in @\('Legacy','Both'\)/);
  assert.match(validate, /package-legacy/);
});
