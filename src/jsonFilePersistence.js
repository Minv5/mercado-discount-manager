import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const TRANSIENT_WINDOWS_FILE_CODES = new Set(['EPERM', 'EACCES', 'EBUSY']);
const DEFAULT_RETRY_DELAYS_MS = [20, 50, 100];

export class JsonFilePersistenceError extends Error {
  constructor(message, details = {}, options = {}) {
    super(message, options);
    this.name = 'JsonFilePersistenceError';
    Object.assign(this, details);
  }
}

export function readJsonFileSync({
  target,
  backupPath = `${target}.bak`,
  fsImpl = fs,
  allowMissing = true,
} = {}) {
  const storageTarget = basename(target);
  const backupTarget = basename(backupPath);
  if (!target) {
    throw new TypeError('target is required');
  }
  if (!safeExistsSync(fsImpl, target)) {
    if (allowMissing) {
      return {
        value: null,
        exists: false,
        backup_available: Boolean(backupPath && safeExistsSync(fsImpl, backupPath)),
      };
    }
    throw new JsonFilePersistenceError('JSON 文件不存在', {
      code: 'JSON_FILE_MISSING',
      operation: 'read_json_file',
      storage_target: storageTarget,
      backup_target: backupTarget,
      backup_available: Boolean(backupPath && safeExistsSync(fsImpl, backupPath)),
    });
  }

  let text;
  try {
    text = fsImpl.readFileSync(target, 'utf8');
  } catch (error) {
    throw new JsonFilePersistenceError('JSON 文件无法读取', {
      code: 'JSON_FILE_UNREADABLE',
      cause_code: safeCode(error?.code),
      operation: 'read_json_file',
      storage_target: storageTarget,
      backup_target: backupTarget,
      backup_available: Boolean(backupPath && safeExistsSync(fsImpl, backupPath)),
    }, { cause: error });
  }

  try {
    return {
      value: JSON.parse(text),
      exists: true,
      backup_available: Boolean(backupPath && safeExistsSync(fsImpl, backupPath)),
    };
  } catch (error) {
    throw new JsonFilePersistenceError('JSON 文件内容损坏', {
      code: 'JSON_FILE_CORRUPT',
      cause_code: 'INVALID_JSON',
      operation: 'parse_json_file',
      storage_target: storageTarget,
      backup_target: backupTarget,
      backup_available: Boolean(backupPath && safeExistsSync(fsImpl, backupPath)),
    }, { cause: error });
  }
}

export function writeJsonFileAtomicallySync({
  target,
  value,
  backupPath = `${target}.bak`,
  fsImpl = fs,
  retryDelaysMs = DEFAULT_RETRY_DELAYS_MS,
  sleepSync = defaultSleepSync,
  currentPid = process.pid,
  space = 2,
} = {}) {
  if (!target) {
    throw new TypeError('target is required');
  }
  const directory = path.dirname(target);
  fsImpl.mkdirSync(directory, { recursive: true });
  const payload = `${JSON.stringify(value, null, space)}\n`;

  if (backupPath) {
    atomicReplaceSync({
      target: backupPath,
      payload,
      fsImpl,
      retryDelaysMs,
      sleepSync,
      currentPid,
    });
  }
  atomicReplaceSync({
    target,
    payload,
    fsImpl,
    retryDelaysMs,
    sleepSync,
    currentPid,
  });
}

function atomicReplaceSync({
  target,
  payload,
  fsImpl,
  retryDelaysMs,
  sleepSync,
  currentPid,
}) {
  const directory = path.dirname(target);
  fsImpl.mkdirSync(directory, { recursive: true });
  const temporaryPath = path.join(
    directory,
    `.${path.basename(target)}.${currentPid}.${Date.now()}.${crypto.randomUUID()}.tmp`,
  );
  let written = false;
  try {
    withTransientWindowsRetry(() => {
      fsImpl.writeFileSync(temporaryPath, payload, { encoding: 'utf8', flag: 'wx' });
      written = true;
    }, { retryDelaysMs, sleepSync });
    withTransientWindowsRetry(
      () => fsImpl.renameSync(temporaryPath, target),
      { retryDelaysMs, sleepSync },
    );
    written = false;
  } catch (error) {
    throw new JsonFilePersistenceError('JSON 文件原子保存失败', {
      code: 'JSON_FILE_WRITE_FAILED',
      cause_code: safeCode(error?.code),
      operation: 'write_json_file',
      storage_target: basename(target),
    }, { cause: error });
  } finally {
    if (written || safeExistsSync(fsImpl, temporaryPath)) {
      try {
        fsImpl.rmSync(temporaryPath, { force: true });
      } catch {}
    }
  }
}

function withTransientWindowsRetry(operation, {
  retryDelaysMs = DEFAULT_RETRY_DELAYS_MS,
  sleepSync = defaultSleepSync,
} = {}) {
  let attempt = 0;
  while (true) {
    try {
      return operation();
    } catch (error) {
      if (!TRANSIENT_WINDOWS_FILE_CODES.has(String(error?.code || '').toUpperCase())
          || attempt >= retryDelaysMs.length) {
        throw error;
      }
      const delay = Math.max(0, Number(retryDelaysMs[attempt]) || 0);
      attempt += 1;
      if (delay > 0) sleepSync(delay);
    }
  }
}

function defaultSleepSync(milliseconds) {
  const shared = new Int32Array(new SharedArrayBuffer(4));
  Atomics.wait(shared, 0, 0, milliseconds);
}

function basename(value) {
  return value ? path.basename(String(value)) : '';
}

function safeCode(value) {
  const code = String(value || '').trim().toUpperCase();
  return /^[A-Z0-9_]{1,80}$/.test(code) ? code : '';
}

function safeExistsSync(fsImpl, target) {
  try {
    return fsImpl.existsSync(target);
  } catch {
    return false;
  }
}
