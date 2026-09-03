export const WEBHOOK_CALLBACK_ORIGIN = 'https://webhook.xingtupro1020.com';

export const DEFAULT_WEBHOOK_CALLBACK_URL = `${WEBHOOK_CALLBACK_ORIGIN}/webhook/mercado-libre/`;
export const DEFAULT_ACTIVITY_CLAIM_URL = `${WEBHOOK_CALLBACK_ORIGIN}/meli-callback/consumer/claim`;
export const DEFAULT_ACTIVITY_ACK_URL = `${WEBHOOK_CALLBACK_ORIGIN}/meli-callback/consumer/ack`;

export const LEGACY_WEBHOOK_CALLBACK_URLS = Object.freeze([
  'https://xingtupro1020.com/webhook/mercado-libre/',
  'https://xingtupro1020.com/webhook/mercado-libre',
]);
export const LEGACY_ACTIVITY_CLAIM_URLS = Object.freeze([
  'https://xingtupro1020.com/meli-callback/consumer/claim',
]);
export const LEGACY_ACTIVITY_ACK_URLS = Object.freeze([
  'https://xingtupro1020.com/meli-callback/consumer/ack',
]);

export function migrateCallbackEndpoint(value, fallback, legacyValues = []) {
  const candidate = String(value || '').trim();
  if (!candidate || legacyValues.includes(candidate)) return fallback;
  return candidate;
}
