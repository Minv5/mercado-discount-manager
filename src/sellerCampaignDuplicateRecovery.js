import { accountRouteKey, normalizeAccountRoute } from './accountRouteIdentity.js';
import { createConfirmedExecutionScope } from './submissionScopeFreeze.js';

export const HIDDEN_SELLER_CAMPAIGN_STATUS = 'existing_without_visible_id';
export const HIDDEN_SELLER_CAMPAIGN_REASON = 'duplicate_name_hidden';
export const HIDDEN_SELLER_CAMPAIGN_MESSAGE = '已确认同名活动存在，但平台未返回活动ID，未报名';

const TERMINAL_PROMOTION_STATUSES = new Set([
  'cancelled', 'catalog_removed', 'closed', 'ended', 'expired', 'finished', 'inactive',
]);
const ENROLLABLE_PROMOTION_STATUSES = new Set(['active', 'created', 'pending', 'scheduled', 'started']);

function text(value) {
  return String(value ?? '').trim();
}

function upper(value) {
  return text(value).toUpperCase();
}

function lower(value) {
  return text(value).toLowerCase();
}

function objectTextValues(value, output = []) {
  if (value == null) return output;
  if (['string', 'number', 'boolean'].includes(typeof value)) {
    output.push(String(value));
    return output;
  }
  if (Array.isArray(value)) {
    for (const entry of value) objectTextValues(entry, output);
    return output;
  }
  if (typeof value === 'object') {
    for (const [key, entry] of Object.entries(value)) {
      if (/^(?:error|code|message|cause|reason|detail|type)$/i.test(key)) objectTextValues(entry, output);
    }
  }
  return output;
}

export function isDuplicateSellerCampaignNameError(error) {
  const body = error?.body ?? error?.details ?? null;
  const values = objectTextValues(body, [text(error?.code), text(error?.message)])
    .map((value) => lower(value).replace(/[\s.-]+/g, '_'))
    .filter(Boolean);
  return values.some((value) => (
    /(?:duplicate|duplicated|already_exists|already_exist).*(?:name|campaign|promotion)/.test(value)
      || /(?:name|campaign|promotion).*(?:duplicate|duplicated|already_exists|already_exist)/.test(value)
      || /promotion_name_(?:already_exists|duplicate)/.test(value)
      || /duplicate_(?:promotion_)?name/.test(value)
      || /(?:campana|campaña|promocion|promoción).*(?:mismo_nombre|same_name|ya_existe)/.test(value)
      || /(?:同名|相同名称).*(?:活动|促销).*(?:存在|已存在)/.test(value)
  ));
}

function promotionId(value = {}) {
  return text(value.promotion_id ?? value.promotionId ?? value.id ?? value.deal_id);
}

function promotionName(value = {}) {
  return text(value.name ?? value.promotion_name ?? value.promotionName);
}

function promotionType(value = {}) {
  return upper(value.promotion_type ?? value.promotionType ?? value.type);
}

function promotionStatus(value = {}) {
  const status = value.status && typeof value.status === 'object' ? value.status.id : value.status;
  return lower(status);
}

function finishDate(value = {}) {
  return text(value.finish_date ?? value.finishDate ?? value.end_date ?? value.endDate);
}

function sameRoute(value, expectedRoute) {
  try {
    return accountRouteKey(normalizeAccountRoute(value)) === accountRouteKey(expectedRoute);
  } catch {
    return false;
  }
}

export function validateRecoveredSellerCampaign({ candidate, target, name, now = () => new Date() } = {}) {
  const expectedRoute = normalizeAccountRoute(target);
  if (!candidate || !sameRoute(candidate, expectedRoute)) return null;
  const id = promotionId(candidate);
  const type = promotionType(candidate);
  const status = promotionStatus(candidate);
  const wantedName = text(name);
  if (!id || type !== 'SELLER_CAMPAIGN' || !status || TERMINAL_PROMOTION_STATUSES.has(status)
      || !ENROLLABLE_PROMOTION_STATUSES.has(status)) return null;
  if (!wantedName || promotionName(candidate) !== wantedName) return null;
  const finish = Date.parse(finishDate(candidate));
  const current = now();
  const nowMs = current instanceof Date ? current.getTime() : new Date(current).getTime();
  if (Number.isFinite(finish) && Number.isFinite(nowMs) && finish < nowMs) return null;
  return {
    ...candidate,
    ...expectedRoute,
    promotion_id: id,
    promotion_type: type,
    name: wantedName,
    status,
  };
}

function exactRouteCandidates(values, route, name) {
  const wantedName = text(name);
  const unique = new Map();
  for (const value of Array.isArray(values) ? values : []) {
    if (!sameRoute(value, route)) continue;
    if (promotionType(value) && promotionType(value) !== 'SELLER_CAMPAIGN') continue;
    if (promotionName(value) && promotionName(value) !== wantedName) continue;
    const id = promotionId(value);
    if (!id || unique.has(id)) continue;
    unique.set(id, { ...value, ...route, promotion_id: id, promotion_type: 'SELLER_CAMPAIGN', name: wantedName });
  }
  return [...unique.values()];
}

async function tryVerifiedCandidates({ source, candidates, target, name, readPromotionDetail, now, attempts }) {
  for (const candidate of exactRouteCandidates(candidates, target, name)) {
    try {
      const detail = await readPromotionDetail(candidate);
      const verified = validateRecoveredSellerCampaign({ candidate: detail, target, name, now });
      attempts.push({ source, promotion_id: candidate.promotion_id, verified: Boolean(verified) });
      if (verified) return { verified, source };
    } catch (error) {
      attempts.push({ source, promotion_id: candidate.promotion_id, verified: false, error_code: text(error?.code) });
    }
  }
  return null;
}

export async function recoverDuplicateSellerCampaign({
  target,
  name,
  forceCatalogRefresh,
  listHistoricalCandidates,
  listWebhookCandidates,
  readPromotionDetail,
  now = () => new Date(),
} = {}) {
  const route = normalizeAccountRoute(target);
  const wantedName = text(name);
  const attempts = [];
  let catalog = [];
  try {
    catalog = await forceCatalogRefresh();
  } catch (error) {
    attempts.push({ source: 'live_catalog', verified: false, error_code: text(error?.code) });
  }
  for (const candidate of exactRouteCandidates(catalog, route, wantedName)) {
    const verified = validateRecoveredSellerCampaign({ candidate, target: route, name: wantedName, now });
    attempts.push({ source: 'live_catalog', promotion_id: candidate.promotion_id, verified: Boolean(verified) });
    if (verified) {
      return {
        status: 'existing', hidden_state: HIDDEN_SELLER_CAMPAIGN_REASON,
        promotion_id: verified.promotion_id, promotion: verified,
        recovery_source: 'live_catalog', attempts,
      };
    }
  }

  const history = await tryVerifiedCandidates({
    source: 'history_detail',
    candidates: await listHistoricalCandidates(),
    target: route,
    name: wantedName,
    readPromotionDetail,
    now,
    attempts,
  });
  if (history) {
    return {
      status: 'existing', hidden_state: HIDDEN_SELLER_CAMPAIGN_REASON,
      promotion_id: history.verified.promotion_id, promotion: history.verified,
      recovery_source: history.source, attempts,
    };
  }

  const webhook = await tryVerifiedCandidates({
    source: 'webhook_detail',
    candidates: await listWebhookCandidates(),
    target: route,
    name: wantedName,
    readPromotionDetail,
    now,
    attempts,
  });
  if (webhook) {
    return {
      status: 'existing', hidden_state: HIDDEN_SELLER_CAMPAIGN_REASON,
      promotion_id: webhook.verified.promotion_id, promotion: webhook.verified,
      recovery_source: webhook.source, attempts,
    };
  }

  return {
    status: HIDDEN_SELLER_CAMPAIGN_STATUS,
    hidden_state: HIDDEN_SELLER_CAMPAIGN_REASON,
    promotion_id: '',
    promotion_name: wantedName,
    ...route,
    message: HIDDEN_SELLER_CAMPAIGN_MESSAGE,
    attempts,
  };
}

export async function resolveHiddenSellerCampaignTargets({ targets = [], recoverByName } = {}) {
  if (typeof recoverByName !== 'function') throw new TypeError('recoverByName is required');
  const resolved = [];
  for (const target of Array.isArray(targets) ? targets : []) {
    if (target?.detection_status !== HIDDEN_SELLER_CAMPAIGN_STATUS) {
      resolved.push(target);
      continue;
    }
    const names = [...new Set([
      ...(Array.isArray(target.hidden_activity_names) ? target.hidden_activity_names : []),
      target.promotion_name,
      target.name,
    ].map(text).filter(Boolean))];
    let recovered = null;
    for (const name of names) {
      const candidate = await recoverByName(target, name);
      if (candidate?.status === 'existing' && text(candidate.promotion_id)) {
        recovered = { ...candidate, promotion_name: name };
        break;
      }
    }
    resolved.push(recovered ? {
      ...target,
      hasSellerCampaign: true,
      existing_seller_campaign_count: Math.max(1, Number(target.existing_seller_campaign_count || 0)),
      detection_status: 'existing',
      detection_message: '已核对到同名自建活动ID，本次可按现有活动处理。',
      promotion_id: text(recovered.promotion_id),
      promotion_name: recovered.promotion_name,
      recovery_source: recovered.recovery_source || null,
    } : target);
  }
  return resolved;
}

function sellerTargetKey(value = {}) {
  return accountRouteKey(value).toUpperCase();
}

export function applyHiddenSellerCampaignSkip(prepare = {}, hiddenTargets = []) {
  const hidden = (Array.isArray(hiddenTargets) ? hiddenTargets : []).map((target) => ({
    ...target,
    ...normalizeAccountRoute(target),
    promotion_type: 'SELLER_CAMPAIGN',
    detection_status: HIDDEN_SELLER_CAMPAIGN_STATUS,
    detection_reason: HIDDEN_SELLER_CAMPAIGN_REASON,
    detection_message: HIDDEN_SELLER_CAMPAIGN_MESSAGE,
  }));
  const hiddenKeys = new Set(hidden.map(sellerTargetKey));
  const sourceScope = prepare.confirmed_execution_scope || prepare.group_request?.confirmedExecutionScope || {};
  const sellerStates = { ...(sourceScope.seller_target_states || {}) };
  for (const target of hidden) sellerStates[sellerTargetKey(target)] = HIDDEN_SELLER_CAMPAIGN_STATUS;
  const scope = createConfirmedExecutionScope({
    action: sourceScope.action || prepare.resolved_action,
    activities: sourceScope.activities || [],
    sellerCreateTargetKeys: (sourceScope.seller_create_target_keys || []).filter((key) => !hiddenKeys.has(upper(key))),
    sellerTargetStates: sellerStates,
  });
  const sellerDetection = { ...(prepare.seller_detection || {}) };
  sellerDetection.confirmed_absent = (sellerDetection.confirmed_absent || []).filter((target) => !hiddenKeys.has(sellerTargetKey(target)));
  sellerDetection.existing_without_visible_id = [
    ...(sellerDetection.existing_without_visible_id || []).filter((target) => !hiddenKeys.has(sellerTargetKey(target))),
    ...hidden,
  ];
  const summary = text(prepare.confirmation_summary);
  const message = hidden.length
    ? `${HIDDEN_SELLER_CAMPAIGN_MESSAGE}（${hidden.length} 个店铺站点）；其余活动继续。`
    : '';
  return {
    seller_input: {
      ...(prepare.seller_input || {}),
      selected_targets: (prepare.seller_input?.selected_targets || []).filter((target) => !hiddenKeys.has(sellerTargetKey(target))),
    },
    seller_detection: sellerDetection,
    confirmed_execution_scope: scope,
    group_request: {
      ...(prepare.group_request || {}),
      confirmedExecutionScope: scope,
    },
    confirmation_summary: [summary, message].filter(Boolean).join(' '),
    seller_hidden_without_visible_id: hidden,
  };
}
