export const REAL_ENROLL_SMOKE_POLICY = Object.freeze({
  enabled: false,
  requires_confirm_text: 'REAL_SUBMIT',
  requires_supervisor_release_code: true,
  release_code_issued: false,
  write_concurrency: 1,
  smart_default_policy: 'blocked_by_policy',
  release_code_source: '本轮只准备固定 4 商品真实报名冒烟执行器，不生成、不展示、不启用 release code。'
});

export const REAL_ENROLL_SMOKE_TARGETS = Object.freeze([
  {
    key: 'deal_mlb_1',
    interface_type: 'DEAL',
    account_id: '2651442567',
    site_id: 'MLB',
    child_user_id: '2668031897',
    promotion_id: 'P-MLB17489058',
    promotion_type: 'DEAL',
    item_id: 'MLB4685849149',
    status: 'candidate',
    current_price: 12.31,
    original_price: 13.09,
    deal_price: 12.30,
    currency_id: 'USD'
  },
  {
    key: 'seller_campaign_mlm_1',
    interface_type: 'SELLER_CAMPAIGN',
    account_id: '2651442567',
    site_id: 'MLM',
    child_user_id: '2668034127',
    promotion_id: 'C-MLM1209743',
    promotion_type: 'SELLER_CAMPAIGN',
    item_id: 'MLM3061896345',
    status: 'candidate',
    current_price: 0,
    original_price: 11.91,
    min_discounted_price: 2.39,
    max_discounted_price: 11.31,
    deal_price: 11.31,
    currency_id: 'USD'
  },
  {
    key: 'lightning_mlm_1',
    interface_type: 'LIGHTNING',
    account_id: '2651442567',
    site_id: 'MLM',
    child_user_id: '2668034127',
    promotion_id: 'LGH-MLM1000',
    promotion_type: 'LIGHTNING',
    item_id: 'MLM2942567755',
    status: 'candidate',
    current_price: 144.45,
    original_price: 168.93,
    stock_min: 5,
    deal_price: 144.45,
    currency_id: 'USD'
  },
  {
    key: 'smart_mlb_1',
    interface_type: 'SMART',
    account_id: '2651442567',
    site_id: 'MLB',
    child_user_id: '2668031897',
    promotion_id: 'P-MLB17755282',
    promotion_type: 'SMART',
    item_id: 'MLB6729392606',
    status: 'candidate',
    offer_id: 'CANDIDATE-MLB6729392606-76453189919',
    current_price: 19.62,
    original_price: 21.76,
    seller_percentage: 8.9,
    meli_percentage: 1,
    currency_id: 'USD'
  }
]);

export function listRealEnrollSmokeTargets() {
  return REAL_ENROLL_SMOKE_TARGETS.map((target) => targetWithPreview(target));
}

export function buildRealEnrollSmokeConfirmation(request = {}) {
  const validation = validateRealEnrollSmokeRequest(request);
  const targets = listRealEnrollSmokeTargets().map((target) => ({
    ...target,
    policy_state: target.promotion_type === 'SMART' ? 'blocked_by_policy' : 'ready_for_supervisor_release',
    can_execute_if_released: target.promotion_type !== 'SMART',
    execute_block_reason: target.promotion_type === 'SMART'
      ? 'SMART 仅有 PIX/BANK offer_id 模式证据，默认不纳入本轮冒烟真实执行；如主管特别决定，需 SMART_RELEASE=true 独立放行。'
      : '本轮 release code 未启用，当前只能生成预检包。'
  }));

  return {
    package_type: 'real_enroll_smoke_precheck',
    status: 'blocked_release_not_enabled',
    enabled: false,
    can_execute_now: false,
    can_request_final_confirmation: false,
    real_write_not_executed: true,
    mode: 'real_enroll_smoke_preparation',
    action: 'enroll',
    write_concurrency: REAL_ENROLL_SMOKE_POLICY.write_concurrency,
    release_policy: { ...REAL_ENROLL_SMOKE_POLICY },
    validation,
    account_id: '2651442567',
    targets_total: targets.length,
    planned_targets: targets.filter((target) => target.can_execute_if_released).length,
    blocked_targets: targets.filter((target) => !target.can_execute_if_released).length,
    targets,
    expected_impact_summary: '如后续主管一次性放行，本冒烟包最多只允许固定 4 个候选商品中的非阻断目标执行报名；本轮不会执行 Mercado 写接口。',
    recheck_method: targets.map((target) => ({
      key: target.key,
      item_id: target.item_id,
      promotion_id: target.promotion_id,
      method: '真实执行后只读 GET 同活动 candidate/pending/started，确认该 item 是否从 candidate 转到 pending 或 started；记录响应，不输出 token。'
    })),
    failure_handling: [
      '失败只记录错误，不自动重试。',
      '成功后不自动取消；如需撤销，必须另行生成 cancel 确认包。',
      '不得把冒烟结果推广为全量报名放行。'
    ],
    hard_blocks: [
      '本轮 enabled=false。',
      '任何目标字段或 body 不完全匹配固定候选包都拒绝。',
      '不允许 UI 或 API 自由扩大商品列表。',
      'SMART 默认 blocked_by_policy，除非主管后续明确 SMART_RELEASE=true。'
    ]
  };
}

export function buildRealEnrollSmokeExecuteDisabled(request = {}) {
  return {
    ok: false,
    status: 409,
    confirmation_required: true,
    error: '固定 4 商品真实报名冒烟执行器本轮仅准备，enabled=false，禁止执行 Mercado POST。',
    confirmation_package: buildRealEnrollSmokeConfirmation(request)
  };
}

export function validateRealEnrollSmokeRequest(request = {}) {
  const requestedTargets = normalizeRequestedTargets(request);
  const allowedKeys = new Set(REAL_ENROLL_SMOKE_TARGETS.map((target) => target.key));
  const unknownKeys = requestedTargets.filter((key) => !allowedKeys.has(key));
  const expanded = requestedTargets.length > REAL_ENROLL_SMOKE_TARGETS.length;
  const itemMismatches = validateRequestedItems(request.items);
  const bodyMismatches = validateRequestedBodies(request.targets || request.items || []);
  const confirmTextOk = request.confirmText === REAL_ENROLL_SMOKE_POLICY.requires_confirm_text;
  const releaseCodePresent = Boolean(String(request.supervisorReleaseCode || '').trim());
  const reasons = [];
  if (unknownKeys.length) reasons.push(`包含未授权 target key：${unknownKeys.join(', ')}`);
  if (expanded) reasons.push('请求目标数量超过固定候选包');
  if (itemMismatches.length) reasons.push(...itemMismatches);
  if (bodyMismatches.length) reasons.push(...bodyMismatches);
  if (!confirmTextOk && request.confirmText !== undefined) reasons.push('confirmText 必须精确等于 REAL_SUBMIT');
  if (releaseCodePresent) reasons.push('本轮未签发 supervisorReleaseCode，不能执行');
  if (!REAL_ENROLL_SMOKE_POLICY.enabled) reasons.push('本轮 enabled=false，禁止执行真实写接口');
  return {
    valid_fixed_targets: reasons.every((reason) => /^本轮 enabled=false|^本轮未签发|^confirmText/.test(reason)),
    requested_target_keys: requestedTargets,
    unknown_keys: unknownKeys,
    expanded,
    item_mismatches: itemMismatches,
    body_mismatches: bodyMismatches,
    confirm_text_ok: confirmTextOk,
    supervisor_release_code_present: releaseCodePresent,
    reasons
  };
}

export function buildSmokeEnrollRequestPreview(targetOrKey) {
  const target = resolveTarget(targetOrKey);
  return {
    method: 'POST',
    path: `/marketplace/seller-promotions/items/${encodeURIComponent(target.item_id)}?user_id=${encodeURIComponent(target.child_user_id)}`,
    headers: requestHeadersForTarget(target),
    body: buildSmokeEnrollBody(target),
    preview_only: true,
    writes_external_state: true
  };
}

export function buildSmokeEnrollBody(targetOrKey) {
  const target = resolveTarget(targetOrKey);
  if (target.promotion_type === 'LIGHTNING') {
    return {
      deal_id: target.promotion_id,
      deal_price: target.deal_price,
      original_price: target.original_price,
      promotion_type: 'LIGHTNING',
      stock: target.stock_min
    };
  }
  if (target.promotion_type === 'SMART') {
    return {
      promotion_id: target.promotion_id,
      promotion_type: 'SMART',
      offer_id: target.offer_id
    };
  }
  return {
    promotion_id: target.promotion_id,
    promotion_type: target.promotion_type,
    deal_price: target.deal_price
  };
}

export function assertSmokeTargetMatches(input = {}) {
  const key = String(input.key || '').trim();
  const target = resolveTarget(key);
  const mismatches = targetMismatches(target, input);
  if (mismatches.length) {
    const error = new Error(`固定冒烟报名目标不匹配：${mismatches.join(', ')}`);
    error.status = 400;
    error.mismatches = mismatches;
    throw error;
  }
  return true;
}

function targetWithPreview(target) {
  return {
    ...target,
    request_preview: buildSmokeEnrollRequestPreview(target),
    recheck_plan: {
      statuses: ['candidate', 'pending', 'started'],
      method: '只读 GET 对应 promotion 的 candidate/pending/started，按 item_id 核对状态变化。',
      no_auto_retry: true,
      no_auto_cancel: true
    }
  };
}

function requestHeadersForTarget(target) {
  const headers = {
    version: 'v2',
    'X-Caller-Id': target.child_user_id,
    Accept: 'application/json',
    'Content-Type': 'application/json'
  };
  if (target.promotion_type === 'LIGHTNING') headers['X-Client-Id'] = '<client_id_required_by_mercado>';
  return headers;
}

function resolveTarget(targetOrKey) {
  if (typeof targetOrKey === 'object' && targetOrKey?.key) {
    return resolveTarget(targetOrKey.key);
  }
  const key = String(targetOrKey || '').trim();
  const target = REAL_ENROLL_SMOKE_TARGETS.find((row) => row.key === key);
  if (!target) throw new Error(`未知固定冒烟目标：${key || '(empty)'}`);
  return target;
}

function normalizeRequestedTargets(request = {}) {
  const raw = request.targetKeys || request.target_keys || request.keys || null;
  if (Array.isArray(raw)) return raw.map((key) => String(key).trim()).filter(Boolean);
  if (typeof raw === 'string') return raw.split(',').map((key) => key.trim()).filter(Boolean);
  if (Array.isArray(request.targets)) return request.targets.map((target) => String(target.key || '').trim()).filter(Boolean);
  if (Array.isArray(request.items)) return request.items.map((item) => String(item.key || '').trim()).filter(Boolean);
  return REAL_ENROLL_SMOKE_TARGETS.map((target) => target.key);
}

function validateRequestedItems(items) {
  if (!Array.isArray(items) || items.length === 0) return [];
  const reasons = [];
  for (const item of items) {
    const key = String(item.key || '').trim();
    const target = REAL_ENROLL_SMOKE_TARGETS.find((row) => row.key === key);
    if (!target) {
      reasons.push(`item ${item.item_id || key || '(unknown)'} 不在固定候选包中`);
      continue;
    }
    const mismatches = targetMismatches(target, item);
    reasons.push(...mismatches.map((field) => `${key}.${field} 不匹配固定候选包`));
  }
  return reasons;
}

function validateRequestedBodies(entries) {
  if (!Array.isArray(entries) || entries.length === 0) return [];
  const reasons = [];
  for (const entry of entries) {
    const key = String(entry.key || '').trim();
    const target = REAL_ENROLL_SMOKE_TARGETS.find((row) => row.key === key);
    if (!target || !entry.body) continue;
    const expected = buildSmokeEnrollBody(target);
    if (JSON.stringify(entry.body) !== JSON.stringify(expected)) reasons.push(`${key}.body 不完全匹配固定 preview body`);
  }
  return reasons;
}

function targetMismatches(target, input) {
  const fields = ['account_id', 'site_id', 'child_user_id', 'promotion_id', 'promotion_type', 'item_id', 'status'];
  const aliases = {
    account_id: ['accountId'],
    site_id: ['siteId'],
    child_user_id: ['childUserId'],
    promotion_id: ['promotionId'],
    promotion_type: ['promotionType'],
    item_id: ['itemId']
  };
  const mismatches = [];
  for (const field of fields) {
    const actual = valueByAliases(input, field, aliases[field] || []);
    if (actual !== '' && String(actual) !== String(target[field])) mismatches.push(field);
  }
  for (const field of ['deal_price', 'original_price', 'current_price', 'min_discounted_price', 'max_discounted_price', 'stock_min', 'seller_percentage', 'meli_percentage']) {
    const actual = valueByAliases(input, field, []);
    if (actual !== '' && actual !== null && actual !== undefined && !sameNumber(actual, target[field])) mismatches.push(field);
  }
  if (target.offer_id && input.offer_id !== undefined && String(input.offer_id) !== target.offer_id) mismatches.push('offer_id');
  return mismatches;
}

function valueByAliases(input, field, aliases) {
  for (const key of [field, ...aliases]) {
    if (input[key] !== undefined && input[key] !== null) return String(input[key]).trim();
  }
  return '';
}

function sameNumber(a, b) {
  if (a === undefined || b === undefined || a === null || b === null) return false;
  return Math.abs(Number(a) - Number(b)) < 0.000001;
}
