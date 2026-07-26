import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const TRANSIENT_FILE_ERROR_CODES = new Set(['EPERM', 'EACCES', 'EBUSY']);
const DEFAULT_RETRY_DELAYS_MS = [20, 50, 100];
const DEFAULT_LOCK_FILE_NAME = '.mercado-discount-manager.node.lock';
const heldLockReferences = new Map();

function defaultSleepSync(delayMs) {
  const waitMs = Math.max(0, Number(delayMs || 0));
  if (!waitMs) return;
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, waitMs);
}

function isTransientFileError(error) {
  return TRANSIENT_FILE_ERROR_CODES.has(String(error?.code || '').toUpperCase());
}

export function retryFileOperationSync(
  operation,
  {
    retryDelaysMs = DEFAULT_RETRY_DELAYS_MS,
    sleepSync = defaultSleepSync,
  } = {},
) {
  const delays = Array.isArray(retryDelaysMs) ? retryDelaysMs : DEFAULT_RETRY_DELAYS_MS;
  let retryIndex = 0;
  while (true) {
    try {
      return operation();
    } catch (error) {
      if (!isTransientFileError(error) || retryIndex >= delays.length) throw error;
      sleepSync(Math.max(0, Number(delays[retryIndex] || 0)));
      retryIndex += 1;
    }
  }
}

function ownedTemporaryPath(target, currentPid) {
  return `${target}.${currentPid}.${Date.now()}.${crypto.randomUUID()}.tmp`;
}

function cleanupOwnedFile(filePath, options) {
  try {
    retryFileOperationSync(() => options.fsImpl.unlinkSync(filePath), options);
  } catch (error) {
    if (String(error?.code || '') !== 'ENOENT') throw error;
  }
}

export function writeJsonFileAtomicallySync({
  target,
  value,
  currentPid = process.pid,
  fsImpl = fs,
  retryDelaysMs = DEFAULT_RETRY_DELAYS_MS,
  sleepSync = defaultSleepSync,
} = {}) {
  if (!target) throw new Error('atomic JSON target is required');
  const options = { fsImpl, retryDelaysMs, sleepSync };
  retryFileOperationSync(() => fsImpl.mkdirSync(path.dirname(target), { recursive: true }), options);
  const temporary = ownedTemporaryPath(target, currentPid);
  let replaced = false;
  try {
    retryFileOperationSync(
      () => fsImpl.writeFileSync(temporary, JSON.stringify(value), 'utf8'),
      options,
    );
    retryFileOperationSync(() => fsImpl.renameSync(temporary, target), options);
    replaced = true;
    return value;
  } finally {
    if (!replaced) cleanupOwnedFile(temporary, options);
  }
}

export function isProcessAlive(pid, { processKill = process.kill } = {}) {
  const normalizedPid = Number(pid || 0);
  if (!Number.isInteger(normalizedPid) || normalizedPid <= 0) return false;
  try {
    processKill(normalizedPid, 0);
    return true;
  } catch (error) {
    if (String(error?.code || '') === 'ESRCH') return false;
    // EPERM means the process exists but this user cannot signal it.
    return true;
  }
}

function lockError(code, message, lockPath, owner = null, cause = null) {
  const error = new Error(message, cause ? { cause } : undefined);
  error.code = code;
  error.lockPath = lockPath;
  error.owner = owner;
  return error;
}

function readLockOwner(lockPath, fsImpl) {
  let text;
  try {
    text = fsImpl.readFileSync(lockPath, 'utf8');
  } catch (error) {
    if (String(error?.code || '') === 'ENOENT') return { status: 'missing', owner: null };
    return { status: 'unreadable', owner: null, error };
  }
  try {
    const owner = JSON.parse(text);
    if (
      !owner
      || !Number.isInteger(Number(owner.pid))
      || Number(owner.pid) <= 0
      || !String(owner.instance_id || '').trim()
    ) {
      return { status: 'corrupt', owner: null };
    }
    return { status: 'ok', owner };
  } catch (error) {
    return { status: 'corrupt', owner: null, error };
  }
}

function createLockFile(lockPath, owner, options) {
  let descriptor = null;
  let created = false;
  try {
    descriptor = options.fsImpl.openSync(lockPath, 'wx');
    created = true;
    options.fsImpl.writeFileSync(descriptor, JSON.stringify(owner), 'utf8');
    options.fsImpl.fsyncSync?.(descriptor);
    options.fsImpl.closeSync(descriptor);
    descriptor = null;
  } catch (error) {
    if (descriptor !== null) {
      try {
        options.fsImpl.closeSync(descriptor);
      } catch {}
    }
    if (created) cleanupOwnedFile(lockPath, options);
    throw error;
  }
}

function lockHandle({ lockPath, owner, recoveredStaleLock, options, reused = false }) {
  const referenceKey = `${lockPath}\0${owner.pid}\0${owner.instance_id}`;
  const existingReferenceCount = Number(heldLockReferences.get(referenceKey) || 0);
  heldLockReferences.set(referenceKey, existingReferenceCount + 1);
  let released = false;
  return {
    acquired: true,
    reused,
    recoveredStaleLock,
    lockPath,
    owner: { ...owner },
    release() {
      if (released) return { released: false, reason: 'already_released' };
      const referenceCount = Number(heldLockReferences.get(referenceKey) || 0);
      if (referenceCount > 1) {
        heldLockReferences.set(referenceKey, referenceCount - 1);
        released = true;
        return { released: false, reason: 'still_held' };
      }
      const current = readLockOwner(lockPath, options.fsImpl);
      if (current.status === 'missing') {
        heldLockReferences.delete(referenceKey);
        released = true;
        return { released: false, reason: 'missing' };
      }
      if (
        current.status !== 'ok'
        || Number(current.owner.pid) !== Number(owner.pid)
        || String(current.owner.instance_id) !== String(owner.instance_id)
      ) {
        return { released: false, reason: 'ownership_changed' };
      }
      retryFileOperationSync(() => options.fsImpl.unlinkSync(lockPath), options);
      heldLockReferences.delete(referenceKey);
      released = true;
      return { released: true, reason: 'released' };
    },
  };
}

export function acquireProcessInstanceLock({
  dataDir,
  lockFileName = DEFAULT_LOCK_FILE_NAME,
  currentPid = process.pid,
  instanceId = `node-${currentPid}-${crypto.randomUUID()}`,
  now = () => new Date().toISOString(),
  fsImpl = fs,
  processAlive = isProcessAlive,
  isProcessAlive: injectedProcessAlive,
  retryDelaysMs = DEFAULT_RETRY_DELAYS_MS,
  sleepSync = defaultSleepSync,
  maxAcquireAttempts = 4,
} = {}) {
  if (!dataDir) throw new Error('process instance lock dataDir is required');
  if (!Number.isInteger(Number(currentPid)) || Number(currentPid) <= 0 || !String(instanceId || '').trim()) {
    throw lockError(
      'PROCESS_INSTANCE_IDENTITY_INVALID',
      '程序实例身份无效，本次启动已停止。',
      path.join(dataDir, lockFileName),
    );
  }
  const options = { fsImpl, retryDelaysMs, sleepSync };
  const owner = {
    version: 1,
    pid: Number(currentPid),
    instance_id: String(instanceId),
    acquired_at: now(),
  };
  const ownerAlive = injectedProcessAlive || processAlive;
  const lockPath = path.join(dataDir, lockFileName);
  retryFileOperationSync(() => fsImpl.mkdirSync(dataDir, { recursive: true }), options);
  let recoveredStaleLock = false;

  for (let attempt = 0; attempt < Math.max(1, Number(maxAcquireAttempts || 1)); attempt += 1) {
    try {
      retryFileOperationSync(() => createLockFile(lockPath, owner, options), options);
      return lockHandle({ lockPath, owner, recoveredStaleLock, options });
    } catch (error) {
      if (String(error?.code || '') !== 'EEXIST') throw error;
    }

    const current = readLockOwner(lockPath, fsImpl);
    if (current.status === 'missing') continue;
    if (current.status === 'unreadable') {
      throw lockError(
        'PROCESS_INSTANCE_LOCK_UNREADABLE',
        '程序实例锁无法读取，已停止启动以保护任务状态。',
        lockPath,
        null,
        current.error,
      );
    }
    if (current.status === 'corrupt') {
      throw lockError(
        'PROCESS_INSTANCE_LOCK_CORRUPT',
        '程序实例锁内容损坏，无法证明旧进程已退出，已停止启动。',
        lockPath,
      );
    }
    if (
      Number(current.owner.pid) === owner.pid
      && String(current.owner.instance_id) === owner.instance_id
    ) {
      return lockHandle({ lockPath, owner, recoveredStaleLock, options, reused: true });
    }
    if (ownerAlive(Number(current.owner.pid))) {
      throw lockError(
        'PROCESS_INSTANCE_ALREADY_RUNNING',
        '已有程序组件正在使用当前数据目录，本次启动已停止。',
        lockPath,
        current.owner,
      );
    }

    const stalePath = `${lockPath}.stale-${current.owner.pid}-${Date.now()}-${crypto.randomUUID()}`;
    try {
      retryFileOperationSync(() => fsImpl.renameSync(lockPath, stalePath), options);
    } catch (error) {
      if (String(error?.code || '') === 'ENOENT') continue;
      throw lockError(
        'PROCESS_INSTANCE_LOCK_RECOVERY_FAILED',
        '陈旧程序实例锁无法安全转移，本次启动已停止。',
        lockPath,
        current.owner,
        error,
      );
    }
    cleanupOwnedFile(stalePath, options);
    recoveredStaleLock = true;
  }

  throw lockError(
    'PROCESS_INSTANCE_LOCK_RACE',
    '程序实例锁在启动时持续发生竞争，本次启动已停止。',
    lockPath,
  );
}
