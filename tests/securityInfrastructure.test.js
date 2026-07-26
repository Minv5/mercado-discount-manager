import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const ROOT = process.cwd();

function temporaryDirectory(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function removeDirectory(directory) {
  fs.rmSync(directory, { recursive: true, force: true });
}

test('settings use atomic unique temporary files and retry transient Windows rename failures', async () => {
  const root = temporaryDirectory('mdm-settings-atomic-');
  const target = path.join(root, 'settings.json');
  const temporaryNames = [];
  let targetRenameAttempts = 0;
  const flakyFs = {
    ...fs,
    writeFileSync(file, ...args) {
      temporaryNames.push(String(file));
      return fs.writeFileSync(file, ...args);
    },
    renameSync(from, to) {
      if (String(to) === target) {
        targetRenameAttempts += 1;
      }
      if (String(to) === target && targetRenameAttempts <= 3) {
        const error = new Error('temporarily occupied');
        error.code = ['EPERM', 'EACCES', 'EBUSY'][targetRenameAttempts - 1];
        throw error;
      }
      return fs.renameSync(from, to);
    },
  };
  try {
    const { writeJsonFileAtomicallySync } = await import('../src/jsonFilePersistence.js');
    writeJsonFileAtomicallySync({
      target,
      value: { version: 1, readConcurrency: 125 },
      fsImpl: flakyFs,
      retryDelaysMs: [0, 0, 0],
      sleepSync: () => {},
    });

    assert.equal(targetRenameAttempts, 4);
    assert.deepEqual(JSON.parse(fs.readFileSync(target, 'utf8')), {
      version: 1,
      readConcurrency: 125,
    });
    assert.deepEqual(JSON.parse(fs.readFileSync(`${target}.bak`, 'utf8')), {
      version: 1,
      readConcurrency: 125,
    });
    assert.ok(temporaryNames.some((name) => /\.tmp$/.test(name)));
    assert.equal(new Set(temporaryNames).size, temporaryNames.length);
    assert.equal(fs.readdirSync(root).some((name) => name.endsWith('.tmp')), false);
  } finally {
    removeDirectory(root);
  }
});

test('corrupt settings block startup instead of silently falling back and keep recovery evidence', () => {
  const dataDir = temporaryDirectory('mdm-settings-corrupt-');
  try {
    const script = `
      import fs from 'node:fs';
      import path from 'node:path';
      import { saveSettings, readSettings } from './src/settings.js';
      saveSettings({ sellerDefaultDiscount: 17 });
      const target = path.join(process.env.MDM_DATA_DIR, 'settings.json');
      const backup = target + '.bak';
      fs.writeFileSync(target, '{"sellerDefaultDiscount":', 'utf8');
      let captured = null;
      let saveCaptured = null;
      try {
        readSettings();
      } catch (error) {
        captured = {
          code: error.code,
          operation: error.operation,
          storage_target: error.storage_target,
          backup_target: error.backup_target,
          backup_available: error.backup_available,
          message: error.message,
        };
      }
      try {
        saveSettings({ sellerDefaultDiscount: 9 });
      } catch (error) {
        saveCaptured = { code: error.code, operation: error.operation };
      }
      console.log(JSON.stringify({
        captured,
        saveCaptured,
        primary: fs.readFileSync(target, 'utf8'),
        backup: JSON.parse(fs.readFileSync(backup, 'utf8')),
      }));
    `;
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
      cwd: ROOT,
      env: {
        ...process.env,
        MDM_DATA_DIR: dataDir,
        MDM_MASTER_KEY: 'isolated-test-master-key',
      },
      encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    const payload = JSON.parse(result.stdout);
    assert.equal(payload.captured.code, 'SETTINGS_JSON_CORRUPT');
    assert.equal(payload.captured.operation, 'read_settings');
    assert.equal(payload.captured.storage_target, 'settings.json');
    assert.equal(payload.captured.backup_target, 'settings.json.bak');
    assert.equal(payload.captured.backup_available, true);
    assert.match(payload.captured.message, /设置文件损坏/);
    assert.deepEqual(payload.saveCaptured, {
      code: 'SETTINGS_JSON_CORRUPT',
      operation: 'read_settings',
    });
    assert.equal(payload.primary, '{"sellerDefaultDiscount":');
    assert.equal(payload.backup.sellerDefaultDiscount, 17);
  } finally {
    removeDirectory(dataDir);
  }
});

test('bounded JSON reader rejects oversized bodies with 413 before parsing', async () => {
  const {
    DEFAULT_JSON_BODY_MAX_BYTES,
    readJsonBody,
  } = await import('../src/security.js');
  const defaultLimitRequest = Readable.from([
    Buffer.alloc(DEFAULT_JSON_BODY_MAX_BYTES + 1, 0x61),
  ]);
  await assert.rejects(
    () => readJsonBody(defaultLimitRequest),
    (error) => error?.statusCode === 413
      && error?.code === 'REQUEST_BODY_TOO_LARGE'
      && error?.max_bytes === DEFAULT_JSON_BODY_MAX_BYTES,
  );

  const configuredLimitRequest = Readable.from([
    Buffer.from('{"value":"'),
    Buffer.alloc(128, 0x61),
    Buffer.from('"}'),
  ]);

  await assert.rejects(
    () => readJsonBody(configuredLimitRequest, { maxBytes: 64 }),
    (error) => error?.statusCode === 413
      && error?.code === 'REQUEST_BODY_TOO_LARGE'
      && error?.max_bytes === 64,
  );
});

test('safe error details expose only approved fields and never raw response material', async () => {
  const { safeErrorDetails } = await import('../src/security.js');
  const error = Object.assign(new Error('Bearer secret-token raw platform response'), {
    kind: 'service',
    status: 503,
    code: 'UPSTREAM_UNAVAILABLE',
    cause: Object.assign(new Error('socket failed with secret-token'), {
      code: 'ECONNRESET',
    }),
    operation: 'fetch_activity_catalog',
    stage: 'prepare',
    response: {
      body: '{"access_token":"secret-token"}',
      headers: { authorization: 'Bearer secret-token' },
    },
    token: 'secret-token',
  });

  const details = safeErrorDetails(error, {
    operation: 'prepare_catalog',
    raw_body: 'secret-token',
    account_id: 'should-not-leak',
  });

  assert.deepEqual(details, {
    kind: 'service',
    status: 503,
    code: 'UPSTREAM_UNAVAILABLE',
    cause_code: 'ECONNRESET',
    operation: 'prepare_catalog',
    stage: 'prepare',
  });
  assert.equal(JSON.stringify(details).includes('secret-token'), false);
  assert.equal('message' in details, false);
  assert.equal('response' in details, false);
  assert.equal('account_id' in details, false);
});
