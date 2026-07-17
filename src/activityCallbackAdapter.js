import crypto from 'node:crypto';
import fs from 'node:fs';

export const ACTIVITY_CALLBACK_SCHEMA_VERSION = '2';

export function activityCallbackConfig(env = process.env) {
  const enabled = /^(?:1|true|yes)$/i.test(String(env.MDM_ACTIVITY_CALLBACK_ENABLED || ''));
  const secretFile = String(env.MDM_ACTIVITY_CALLBACK_SECRET_FILE || '').trim();
  let secret = String(env.MDM_ACTIVITY_CALLBACK_SECRET || '');
  if (secretFile) {
    try {
      secret = fs.readFileSync(secretFile, 'utf8').trim();
    } catch {
      secret = '';
    }
  }
  return {
    enabled,
    secret,
    secretFile,
    applicationId: String(env.MDM_ACTIVITY_CALLBACK_APPLICATION_ID || ''),
    source: 'local-forwarder',
  };
}

export function activityCallbackSigningText(input = {}) {
  const event = normalizeEvent(input);
  if (event.schema_version === '2') {
    return [
      event.schema_version,
      event.event_id,
      event.topic,
      event.resource,
      event.remote_user_id,
      event.application_id,
      event.received_at,
    ].join('\n');
  }
  return [
    event.schema_version,
    event.event_id,
    event.account_id,
    event.site_id,
    event.promotion_id,
    event.promotion_type,
    event.cursor,
    event.previous_cursor,
  ].join('\n');
}

export function signActivityCallbackEvent(event, secret) {
  return crypto.createHmac('sha256', String(secret || '')).update(activityCallbackSigningText(event)).digest('hex');
}

export function createActivityCallbackAdapter({ config, hasEvent, saveEvent, getCacheState, markDirty, consumeEvent }) {
  const options = config || activityCallbackConfig();
  return {
    async accept(input, signature) {
      if (!options.enabled) return { accepted: false, status: 'disabled' };
      if (!options.secret) throw callbackError('活动变化回调尚未配置安全密钥。', 503);
      const event = normalizeEvent(input);
      if (event.schema_version === '2') {
        if (!options.applicationId) throw callbackError('活动变化回调尚未配置应用标识。', 503);
        if (event.application_id !== options.applicationId) throw callbackError('活动变化回调的应用标识不匹配。', 403);
      }
      verifySignature(event, signature, options.secret);
      if (await hasEvent(event.event_id)) return { accepted: true, status: 'duplicate', event_id: event.event_id };
      if (event.schema_version === '2') {
        if (typeof consumeEvent !== 'function') throw callbackError('活动变化回调消费器尚未就绪。', 503);
        const outcome = await consumeEvent(event);
        await saveEvent({
          ...event,
          ...outcome,
          received_at: event.received_at || new Date().toISOString(),
          raw_json: JSON.stringify({ event, outcome }),
        });
        return { accepted: true, status: 'accepted', event_id: event.event_id, outcome: outcome.outcome };
      }
      const current = await getCacheState(event.account_id, event.site_id, event.promotion_id, event.promotion_type);
      const currentCursor = String(current?.event_cursor || '');
      const gap = Boolean(event.previous_cursor && currentCursor && event.previous_cursor !== currentCursor);
      await markDirty({
        accountId: event.account_id,
        siteId: event.site_id,
        promotionId: event.promotion_id,
        promotionType: event.promotion_type,
        eventCursor: event.cursor || null,
        gap,
      });
      await saveEvent({ ...event, gap, received_at: new Date().toISOString() });
      return { accepted: true, status: 'accepted', event_id: event.event_id, gap };
    },
  };
}

function normalizeEvent(input = {}) {
  if (String(input.schema_version || '') === '2') return normalizeV2Event(input);
  const event = {
    schema_version: String(input.schema_version || ''),
    event_id: String(input.event_id || '').trim(),
    account_id: String(input.account_id || '').trim(),
    site_id: String(input.site_id || '').trim().toUpperCase(),
    promotion_id: String(input.promotion_id || '').trim(),
    promotion_type: String(input.promotion_type || '').trim().toUpperCase(),
    cursor: input.cursor == null ? '' : String(input.cursor),
    previous_cursor: input.previous_cursor == null ? '' : String(input.previous_cursor),
  };
  if (event.schema_version !== '1') throw callbackError('活动变化回调版本不受支持。', 400);
  if (!event.event_id || !event.account_id || !event.site_id) throw callbackError('活动变化回调缺少必要业务字段。', 400);
  if (Boolean(event.promotion_id) !== Boolean(event.promotion_type)) throw callbackError('活动变化回调的活动标识不完整。', 400);
  return event;
}

function normalizeV2Event(input = {}) {
  const event = {
    schema_version: '2',
    event_id: scalarText(input.event_id),
    topic: scalarText(input.topic).toLowerCase(),
    resource: scalarText(input.resource),
    remote_user_id: scalarText(input.remote_user_id),
    application_id: scalarText(input.application_id),
    received_at: scalarText(input.received_at),
  };
  if (!event.event_id || !event.topic || !event.resource || !event.remote_user_id || !event.application_id || !event.received_at) {
    throw callbackError('活动变化回调缺少必要业务字段。', 400);
  }
  return event;
}

function scalarText(value) {
  if (!['string', 'number', 'bigint'].includes(typeof value)) return '';
  return String(value).trim();
}

function verifySignature(event, signature, secret) {
  const expected = Buffer.from(signActivityCallbackEvent(event, secret), 'hex');
  const suppliedText = String(signature || '').replace(/^sha256=/i, '');
  let supplied;
  try { supplied = Buffer.from(suppliedText, 'hex'); } catch { supplied = Buffer.alloc(0); }
  if (supplied.length !== expected.length || !crypto.timingSafeEqual(supplied, expected)) {
    throw callbackError('活动变化回调签名无效。', 401);
  }
}

function callbackError(message, status) {
  const error = new Error(message);
  error.status = status;
  return error;
}
