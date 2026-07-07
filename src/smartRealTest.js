import { APP_VERSION } from './config.js';

export const SMART_REAL_TEST_TARGET = Object.freeze({
  account_id: '2651442567',
  nickname: 'CNHUBEISHENGRUIHESHANGM',
  site_id: 'MLB',
  child_user_id: '2668031897',
  promotion_id: 'P-MLB17755282',
  promotion_type: 'SMART',
  action: 'enroll',
  status: 'candidate',
  item_id: 'MLB6729392606',
  offer_id: 'CANDIDATE-MLB6729392606-76453189919',
  price: 19.62,
  original_price: 21.76,
  currency_id: 'USD',
  seller_percentage: 8.9,
  meli_percentage: 1,
  write_concurrency: 1
});

export const SMART_REAL_TEST_RELEASE_POLICY = Object.freeze({
  enabled: false,
  release_code_issued: false,
  requires_confirm_text: 'REAL_SUBMIT',
  requires_supervisor_release_code: true,
  release_code_source: '本轮不生成、不展示、不启用 release code；后续必须由主管单独下派一次性放行字段。'
});

export function buildSmartEnrollPayload(input = {}) {
  const normalized = normalizeSmartInput(input);
  requireTargetMatch(normalized);
  return {
    promotion_id: SMART_REAL_TEST_TARGET.promotion_id,
    promotion_type: SMART_REAL_TEST_TARGET.promotion_type,
    offer_id: SMART_REAL_TEST_TARGET.offer_id
  };
}

export function buildSmartEnrollRequestPreview(input = {}) {
  const payload = buildSmartEnrollPayload(input);
  return {
    method: 'POST',
    endpoint_family: 'seller-promotions item write',
    path_template: '/seller-promotions/items/{item_id}?app_version=v2',
    marketplace_path_template: '/marketplace/seller-promotions/items/{item_id}?app_version=v2',
    item_id: SMART_REAL_TEST_TARGET.item_id,
    app_version: APP_VERSION,
    body: payload,
    headers_required: [
      'Authorization: Bearer <access_token>',
      `X-Caller-Id: ${SMART_REAL_TEST_TARGET.child_user_id}`,
      'Accept: application/json',
      'Content-Type: application/json',
      'version: v2 for marketplace path'
    ],
    preview_only: true
  };
}

export function validateSmartRealTestRelease(request = {}, policy = SMART_REAL_TEST_RELEASE_POLICY) {
  const normalized = normalizeSmartInput(request);
  const mismatches = targetMismatches(normalized);
  const releaseCodePresent = Boolean(String(request.supervisorReleaseCode || '').trim());
  const releaseCodeMatches = Boolean(policy.release_code && String(request.supervisorReleaseCode || '') === String(policy.release_code));
  const confirmTextMatches = request.confirmText === policy.requires_confirm_text;
  const reasons = [];

  if (mismatches.length) reasons.push(...mismatches.map((field) => `${field} 与 SMART 单商品验证目标不匹配`));
  if (!confirmTextMatches) reasons.push('confirmText 必须精确等于 REAL_SUBMIT');
  if (!releaseCodePresent) reasons.push('缺少 supervisorReleaseCode');
  else if (!releaseCodeMatches) reasons.push('supervisorReleaseCode 不匹配或本轮未签发');
  if (!policy.enabled) reasons.push('本轮 release code 机制未启用，禁止执行真实写接口');

  return {
    allowed: false,
    would_allow_if_enabled: policy.enabled && !mismatches.length && confirmTextMatches && releaseCodeMatches,
    status: 409,
    release_policy: {
      enabled: policy.enabled,
      release_code_issued: Boolean(policy.release_code_issued),
      requires_supervisor_release_code: policy.requires_supervisor_release_code,
      release_code_source: policy.release_code_source
    },
    target_match: mismatches.length === 0,
    mismatches,
    confirm_text_ok: confirmTextMatches,
    release_code_present: releaseCodePresent,
    release_code_matches: releaseCodeMatches,
    reasons
  };
}

export function buildSingleItemRealTestConfirmation(request = {}) {
  const normalized = normalizeSmartInput(request);
  const release = validateSmartRealTestRelease(request);
  const releaseSummary = {
    ...release,
    enabled: release.release_policy.enabled,
    release_code_issued: release.release_policy.release_code_issued,
    code_present: release.release_code_present,
    code_matches: release.release_code_matches
  };
  let payloadPreview = null;
  let payloadError = null;
  try {
    payloadPreview = buildSmartEnrollPayload(normalized);
  } catch (error) {
    payloadError = error.message;
  }

  return {
    package_type: 'single_item_real_test_confirmation',
    status: 'blocked_release_not_enabled',
    can_execute_now: false,
    can_request_final_confirmation: false,
    real_write_not_executed: true,
    release: releaseSummary,
    target: { ...SMART_REAL_TEST_TARGET },
    received: normalized,
    action: SMART_REAL_TEST_TARGET.action,
    mode: 'real_test_preparation',
    write_concurrency: SMART_REAL_TEST_TARGET.write_concurrency,
    request_preview: payloadPreview ? buildSmartEnrollRequestPreview(normalized) : null,
    payload_error: payloadError,
    expected_impact_summary: '如未来主管一次性放行，该操作只会尝试将 1 个 SMART candidate 商品报名到 P-MLB17755282，不得批量扩展。',
    recheck_method: [
      '真实执行后只读 GET 同活动 candidate/pending/started。',
      `确认 ${SMART_REAL_TEST_TARGET.item_id} 是否从 candidate 变化到 pending 或 started，或是否仍 candidate 且返回错误。`,
      '记录 Mercado 原始响应和回查结果，不输出 token。'
    ],
    failure_handling: [
      '失败只记录错误，不自动重试。',
      '成功后如需撤销，必须另行生成 cancel 确认包，不自动取消。',
      '不得把该单品验证结果推广为 SMART 批量放行。'
    ],
    hard_blocks: [
      '本轮 release code 未启用。',
      '任何字段不匹配目标候选都拒绝。',
      '不支持 SMART update/cancel 真实执行。',
      '不支持批量 SMART 真实执行。'
    ]
  };
}

export function normalizeSmartInput(input = {}) {
  const item = Array.isArray(input.items) && input.items.length === 1 ? input.items[0] : input.item || {};
  return {
    account_id: stringValue(input.accountId ?? input.account_id),
    site_id: stringValue(input.siteId ?? input.site_id),
    child_user_id: stringValue(input.childUserId ?? input.child_user_id),
    promotion_id: stringValue(input.promotionId ?? input.promotion_id),
    promotion_type: stringValue(input.promotionType ?? input.promotion_type).toUpperCase(),
    action: stringValue(input.action),
    status: stringValue(input.status),
    item_id: stringValue(input.itemId ?? input.item_id ?? item.item_id ?? item.id),
    offer_id: stringValue(input.offerId ?? input.offer_id ?? item.offer_id ?? item.raw?.offer_id),
    price: numberOrNull(input.price ?? item.price ?? item.raw?.price),
    original_price: numberOrNull(input.originalPrice ?? input.original_price ?? item.original_price ?? item.raw?.original_price),
    seller_percentage: numberOrNull(input.sellerPercentage ?? input.seller_percentage ?? item.seller_percentage ?? item.raw?.seller_percentage),
    meli_percentage: numberOrNull(input.meliPercentage ?? input.meli_percentage ?? item.meli_percentage ?? item.raw?.meli_percentage),
    write_concurrency: Number(input.writeConcurrency ?? input.write_concurrency ?? SMART_REAL_TEST_TARGET.write_concurrency)
  };
}

function requireTargetMatch(input) {
  const mismatches = targetMismatches(input);
  if (mismatches.length) {
    const error = new Error(`SMART 单商品验证字段不匹配：${mismatches.join(', ')}`);
    error.status = 400;
    error.mismatches = mismatches;
    throw error;
  }
}

function targetMismatches(input) {
  const fields = [
    'account_id',
    'site_id',
    'child_user_id',
    'promotion_id',
    'promotion_type',
    'action',
    'status',
    'item_id',
    'offer_id',
    'write_concurrency'
  ];
  const mismatches = fields.filter((field) => String(input[field] ?? '') !== String(SMART_REAL_TEST_TARGET[field] ?? ''));
  for (const field of ['price', 'original_price', 'seller_percentage', 'meli_percentage']) {
    if (!sameNumber(input[field], SMART_REAL_TEST_TARGET[field])) mismatches.push(field);
  }
  return mismatches;
}

function stringValue(value) {
  return String(value ?? '').trim();
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function sameNumber(a, b) {
  return Number.isFinite(Number(a)) && Number.isFinite(Number(b)) && Math.abs(Number(a) - Number(b)) < 0.000001;
}
