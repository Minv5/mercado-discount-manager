import { createHmac } from 'node:crypto';
import fs from 'node:fs';

const DEFAULT_CLAIM_URL = 'https://xingtupro1020.com/meli-callback/consumer/claim';
const DEFAULT_ACK_URL = 'https://xingtupro1020.com/meli-callback/consumer/ack';
const DEFAULT_POLL_MS = 2000;
const DEFAULT_LEASE_GRACE_MS = 30000;
const DEFAULT_MAX_PROCESSING = 20;
const SUPPORTED_TOPICS = new Set(['public_offers', 'public_candidates', 'items', 'marketplace_items']);

function claimConsumerError(message, code, status = 502) {
  const error = new Error(message);
  error.code = code;
  error.status = status;
  return error;
}

function readSecret(file) {
  const value = fs.readFileSync(file, 'utf8').trim();
  if (value.length < 32) throw claimConsumerError('活动回调消费者密钥长度不足。', 'ACTIVITY_CLAIM_SECRET_INVALID');
  return value;
}

function fetchJson(url, { method = 'POST', headers = {}, body = null, timeoutMs = 25000, signal = null } = {}) {
  return new Promise((resolve, reject) => {
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      reject(claimConsumerError('活动回调地址无效。', 'ACTIVITY_CLAIM_URL_INVALID', 400));
      return;
    }
    if (parsed.protocol !== 'https:') {
      // The Bearer secret must never travel in plaintext.
      reject(claimConsumerError('活动回调地址必须使用 https。', 'ACTIVITY_CLAIM_URL_INSECURE', 400));
      return;
    }
    const httpModule = import('node:https');
    httpModule.then((mod) => {
      const request = mod.request(parsed, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...headers,
        },
        signal,
      }, (response) => {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(chunk));
        response.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          let payload = null;
          try {
            payload = text ? JSON.parse(text) : {};
          } catch {
            payload = { raw: text };
          }
          if (response.statusCode >= 200 && response.statusCode < 300) {
            resolve({ status: response.statusCode, payload });
          } else {
            const error = claimConsumerError(
              `活动回调服务器返回 ${response.statusCode}：${payload?.error || payload?.raw || '未知错误'}`,
              'ACTIVITY_CLAIM_SERVER_ERROR',
              response.statusCode,
            );
            error.payload = payload;
            reject(error);
          }
        });
      });
      request.on('error', reject);
      if (body !== null) request.write(typeof body === 'string' ? body : JSON.stringify(body));
      request.end();
      const timer = setTimeout(() => request.destroy(new Error('timeout')), timeoutMs);
      request.on('close', () => clearTimeout(timer));
    }).catch(reject);
  });
}

export function createActivityClaimConsumer({
  claimUrl = DEFAULT_CLAIM_URL,
  ackUrl = DEFAULT_ACK_URL,
  secretFile = '',
  applicationId = '',
  pollMs = DEFAULT_POLL_MS,
  leaseGraceMs = DEFAULT_LEASE_GRACE_MS,
  maxProcessing = DEFAULT_MAX_PROCESSING,
  consumeEvent = null,
  onError = null,
  onPoll = null,
  isEventCompleted = null,
  now = () => Date.now(),
  fetchImpl = fetchJson,
  log = null,
} = {}) {
  let enabled = false;
  let running = false;
  let pollTimer = null;
  const processing = new Map();
  const info = (message) => { try { log?.(message); } catch {} };

  function loadSecret() {
    return secretFile ? readSecret(secretFile) : '';
  }

  async function claim() {
    const secret = loadSecret();
    const { payload } = await fetchImpl(claimUrl, {
      headers: { Authorization: `Bearer ${secret}` },
      body: {},
    });
    return Array.isArray(payload?.events) ? payload.events : [];
  }

  async function ack(eventId, leaseId, ok, error = null) {
    const secret = loadSecret();
    await fetchImpl(ackUrl, {
      headers: { Authorization: `Bearer ${secret}` },
      body: { event_id: eventId, lease_id: leaseId, ok, error: error ? String(error) : undefined },
    });
  }

  function normalizeClaimedEvent(event) {
    const normalized = {
      schema_version: '2',
      event_id: String(event.event_id || '').trim(),
      topic: String(event.topic || '').trim().toLowerCase(),
      resource: String(event.resource || '').trim(),
      remote_user_id: String(event.remote_user_id || event.user_id || '').trim(),
      application_id: String(event.application_id || '').trim(),
      received_at: String(event.received_at || '').trim(),
    };
    if (applicationId && normalized.application_id && normalized.application_id !== applicationId) {
      throw claimConsumerError('活动回调应用标识不匹配，已忽略。', 'ACTIVITY_CLAIM_APPLICATION_MISMATCH', 403);
    }
    if (!normalized.event_id || !normalized.resource || !normalized.remote_user_id || !normalized.application_id || !normalized.received_at) {
      throw claimConsumerError('活动回调通知缺少必要字段，已忽略。', 'ACTIVITY_CLAIM_FIELDS_MISSING', 400);
    }
    if (!SUPPORTED_TOPICS.has(normalized.topic)) {
      throw claimConsumerError(`该类活动通知暂不支持：${normalized.topic}`, 'ACTIVITY_CLAIM_TOPIC_UNSUPPORTED', 422);
    }
    return normalized;
  }

  async function processEvent(event) {
    const key = String(event.event_id || '');
    const leaseId = String(event.lease_id || '');
    const startedAt = now();
    try {
      if (typeof isEventCompleted === 'function' && await isEventCompleted(key)) {
        await ack(key, leaseId, true);
        return { key, ok: true, skipped: true, reason: 'already_completed' };
      }
      processing.set(key, { lease_id: leaseId, started_at: startedAt });
      if (typeof consumeEvent !== 'function') {
        throw claimConsumerError('活动回调消费器尚未就绪。', 'ACTIVITY_CLAIM_CONSUMER_NOT_READY', 503);
      }
      await consumeEvent(event, { signal: null });
      await ack(key, leaseId, true);
      return { key, ok: true };
    } catch (error) {
      const expired = now() - startedAt >= leaseGraceMs;
      if (!expired) {
        try {
          await ack(key, leaseId, false, error?.message || String(error));
        } catch {
          // Lease may have already expired server-side; the event will be redelivered.
        }
      }
      return { key, ok: false, error };
    } finally {
      processing.delete(key);
    }
  }

  async function pollOnce() {
    if (!enabled || running) return;
    running = true;
    let claimed = [];
    try {
      claimed = await claim();
      try { onPoll?.(null); } catch {}
    } catch (error) {
      info(`活动回调领取失败：${error?.message || error}`);
      try { onError?.('claim', error); } catch {}
      try { onPoll?.(error); } catch {}
      running = false;
      return;
    }
    try {
      const pending = [...processing.values()].filter((row) => now() - row.started_at >= leaseGraceMs);
      for (const row of pending) {
        for (const key of [...processing.keys()]) {
          if (processing.get(key) === row) processing.delete(key);
        }
      }
      const available = Math.max(1, maxProcessing - processing.size);
      const batch = claimed.slice(0, available);
      await Promise.all(batch.map(async (event) => {
        try {
          const normalized = normalizeClaimedEvent(event);
          await processEvent({ ...normalized, lease_id: String(event.lease_id || '') });
        } catch (error) {
          try {
            await ack(String(event.event_id || ''), String(event.lease_id || ''), false, error?.message || String(error));
          } catch {}
        }
      }));
    } finally {
      running = false;
    }
  }

  async function runForever() {
    if (pollTimer) return;
    const tick = async () => {
      if (!enabled) return;
      try {
        await pollOnce();
      } catch (error) {
        try { onError?.('poll', error); } catch {}
      }
      if (enabled) pollTimer = setTimeout(tick, pollMs);
    };
    pollTimer = setTimeout(tick, 0);
  }

  function start() {
    enabled = true;
    if (!running) runForever();
    return true;
  }

  function stop() {
    enabled = false;
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    return true;
  }

  function isEnabled() {
    return enabled;
  }

  return { start, stop, pollOnce, isEnabled, claim, ack };
}

export function activityClaimConfig(env = process.env, settings = null) {
  const settingsEnabled = settings?.activityCallbackEnabled === true;
  const envEnabled = /^(?:1|true|yes)$/i.test(String(env.MDM_ACTIVITY_CLAIM_ENABLED || env.MDM_ACTIVITY_CALLBACK_ENABLED || ''));
  const enabled = envEnabled || settingsEnabled;
  const envSecretFile = String(env.MDM_ACTIVITY_CLAIM_SECRET_FILE || env.MDM_ACTIVITY_CALLBACK_SECRET_FILE || '').trim();
  const settingsSecretFile = String(settings?.activityCallbackSecretFile || '').trim();
  const envApplicationId = String(env.MDM_ACTIVITY_CLAIM_APPLICATION_ID || env.MDM_ACTIVITY_CALLBACK_APPLICATION_ID || '').trim();
  const settingsApplicationId = String(settings?.activityCallbackApplicationId || '').trim();
  const envClaimUrl = String(env.MDM_ACTIVITY_CLAIM_URL || '').trim();
  const settingsClaimUrl = String(settings?.activityCallbackClaimUrl || '').trim();
  const envAckUrl = String(env.MDM_ACTIVITY_CLAIM_ACK_URL || '').trim();
  const settingsAckUrl = String(settings?.activityCallbackAckUrl || '').trim();
  const secretFile = envSecretFile || settingsSecretFile;
  const applicationId = envApplicationId || settingsApplicationId;
  const claimUrl = envClaimUrl || settingsClaimUrl || DEFAULT_CLAIM_URL;
  const ackUrl = envAckUrl || settingsAckUrl || DEFAULT_ACK_URL;
  const pollMs = Math.max(500, Math.floor(Number(env.MDM_ACTIVITY_CLAIM_POLL_MS) || DEFAULT_POLL_MS));
  return { enabled, secretFile, applicationId, claimUrl, ackUrl, pollMs };
}
