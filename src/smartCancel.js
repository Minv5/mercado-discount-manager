import { APP_VERSION } from './config.js';

export const SMART_CANCEL_POLICY = Object.freeze({
  default_enabled: false,
  default_sample_limit: 1,
  evidence: 'Official PIX/BANK co-funded DELETE uses promotion_id, promotion_type and offer_id; SMART started rows expose OFFER-* offer_id. This is an adapter path that still requires limited real validation before bulk release.'
});

export function extractSmartOfferId(item = {}) {
  const raw = parseItemRaw(item);
  return stringValue(
    item.offer_id
    ?? item.offerId
    ?? raw.offer_id
    ?? raw.offerId
    ?? raw.offer?.id
    ?? raw.offer?.offer_id
  );
}

export function isStartedSmartOfferId(offerId) {
  return /^OFFER-/i.test(stringValue(offerId));
}

export function smartCancelFieldEvidence({ promotion = {}, item = {} } = {}) {
  const raw = parseItemRaw(item);
  const offerId = extractSmartOfferId(item);
  return {
    item_id: item.item_id || item.id || raw.id || null,
    promotion_id: promotion.promotion_id || promotion.id || item.promotion_id || raw.promotion_id || null,
    promotion_type: String(promotion.promotion_type || promotion.type || item.promotion_type || raw.promotion_type || '').toUpperCase(),
    status: item.status || raw.status || null,
    offer_id: offerId || null,
    offer_id_source: offerId
      ? (item.offer_id || item.offerId ? 'item column/object' : raw.offer_id || raw.offerId ? 'item raw_json' : 'nested raw offer')
      : null,
    offer_id_is_started_offer: isStartedSmartOfferId(offerId),
    price: numberOrNull(item.price ?? raw.price),
    original_price: numberOrNull(item.original_price ?? raw.original_price),
    seller_percentage: numberOrNull(item.seller_percentage ?? raw.seller_percentage),
    meli_percentage: numberOrNull(item.meli_percentage ?? raw.meli_percentage)
  };
}

export function requireSmartCancelFields({ promotion = {}, item = {} } = {}) {
  const evidence = smartCancelFieldEvidence({ promotion, item });
  const missing = [];
  if (!evidence.item_id) missing.push('item_id');
  if (!evidence.promotion_id) missing.push('promotion_id');
  if (evidence.promotion_type !== 'SMART') missing.push('promotion_type=SMART');
  if (!evidence.offer_id) missing.push('offer_id');
  if (evidence.offer_id && !evidence.offer_id_is_started_offer) missing.push('started OFFER-* offer_id');
  if (!['started', 'pending'].includes(String(evidence.status || '').toLowerCase())) missing.push('status=started/pending');

  if (missing.length) {
    const error = new Error(`SMART取消字段不足：${missing.join(', ')}`);
    error.code = 'smart_cancel_fields_incomplete';
    error.policyBlocked = true;
    error.missing_fields = missing;
    error.field_evidence = evidence;
    throw error;
  }
  return evidence;
}

export function buildSmartCancelQuery({ promotion = {}, item = {} } = {}) {
  const evidence = requireSmartCancelFields({ promotion, item });
  return {
    promotion_type: 'SMART',
    promotion_id: evidence.promotion_id,
    offer_id: evidence.offer_id
  };
}

export function buildSmartCancelRequestPreview({ account = {}, promotion = {}, item = {}, marketplace = true } = {}) {
  const query = buildSmartCancelQuery({ promotion, item });
  const evidence = smartCancelFieldEvidence({ promotion, item });
  return {
    method: 'DELETE',
    endpoint_family: 'seller-promotions offer cancel',
    official_evidence: SMART_CANCEL_POLICY.evidence,
    item_id: evidence.item_id,
    promotion_id: query.promotion_id,
    promotion_type: query.promotion_type,
    offer_id: query.offer_id,
    query,
    official_bank_pix_path_template: `/seller-promotions/items/{item_id}?promotion_type=BANK&promotion_id={promotion_id}&offer_id={offer_id}&app_version=${APP_VERSION}`,
    marketplace_path_template: '/marketplace/seller-promotions/items/{item_id}?user_id={child_user_id}&promotion_type=SMART&promotion_id={promotion_id}&offer_id={offer_id}',
    path_template: marketplace
      ? '/marketplace/seller-promotions/items/{item_id}?user_id={child_user_id}&promotion_type=SMART&promotion_id={promotion_id}&offer_id={offer_id}'
      : `/seller-promotions/items/{item_id}?promotion_type=SMART&promotion_id={promotion_id}&offer_id={offer_id}&app_version=${APP_VERSION}`,
    child_user_id: promotion.child_user_id || account.child_user_id || null,
    site_id: promotion.site_id || item.site_id || null,
    field_evidence: evidence,
    headers_required: marketplace
      ? ['Authorization: Bearer <access_token>', 'X-Caller-Id: {child_user_id}', `version: ${APP_VERSION}`]
      : ['Authorization: Bearer <access_token>'],
    body: null,
    can_send_if_explicitly_enabled: true,
    default_policy: {
      enabled: SMART_CANCEL_POLICY.default_enabled,
      sample_limit: SMART_CANCEL_POLICY.default_sample_limit,
      bulk_release_required: true
    }
  };
}

export function normalizeSmartCancelSampleLimit(value, fallback = SMART_CANCEL_POLICY.default_sample_limit) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.floor(n));
}

export function limitSmartCancelPlan(plan, maxItems = SMART_CANCEL_POLICY.default_sample_limit) {
  const limit = normalizeSmartCancelSampleLimit(maxItems);
  let allowed = 0;
  let skippedByLimit = 0;
  const rows = (plan?.rows || []).map((row) => {
    if (row.status !== 'planned') return row;
    if (allowed < limit) {
      allowed += 1;
      return row;
    }
    skippedByLimit += 1;
    return {
      ...row,
      status: 'skipped',
      reason: `SMART取消小样本限制：本次只允许发送 ${limit} 个商品，未发送接口`
    };
  });
  const existingSkipped = (plan?.rows || []).filter((row) => row.status !== 'planned').length;
  return {
    ...plan,
    rows,
    planned: allowed,
    skipped: existingSkipped + skippedByLimit,
    smart_cancel_sample_limit: limit,
    smart_cancel_limited: skippedByLimit > 0
  };
}

function parseItemRaw(item = {}) {
  if (item.raw && typeof item.raw === 'object') {
    const nestedRawJson = parseJsonObject(item.raw.raw_json);
    return { ...nestedRawJson, ...item.raw };
  }
  if (item.raw_json && typeof item.raw_json === 'string') {
    return parseJsonObject(item.raw_json);
  }
  return item;
}

function parseJsonObject(value) {
  if (!value || typeof value !== 'string') return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function stringValue(value) {
  return String(value ?? '').trim();
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
