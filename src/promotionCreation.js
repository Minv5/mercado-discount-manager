export const SELLER_CAMPAIGN_CREATE_PATH = '/marketplace/seller-promotions/seller-campaign';
export const SELLER_CAMPAIGN_MAX_DAYS = 14;
export const SELLER_CAMPAIGN_SUB_TYPES = new Set(['FLEXIBLE_PERCENTAGE']);

export function buildSellerCampaignCreatePreview(input = {}) {
  const normalized = normalizeSellerCampaignCreateInput(input);
  const errors = validateSellerCampaignCreateInput(normalized);
  const requestPreview = {
    method: 'POST',
    path: `${SELLER_CAMPAIGN_CREATE_PATH}/${encodeURIComponent(normalized.child_user_id || '{USER_ID}')}`,
    headers: {
      version: 'v2',
      'X-Caller-Id': normalized.child_user_id || null
    },
    body: {
      promotion_type: 'SELLER_CAMPAIGN',
      name: normalized.name || null,
      sub_type: normalized.sub_type || 'FLEXIBLE_PERCENTAGE',
      start_date: normalized.start_date || null,
      finish_date: normalized.finish_date || null
    },
    preview_only: true,
    writes_external_state: true
  };
  return { input: normalized, errors, request_preview: requestPreview };
}

export function buildSellerCampaignCreateConfirmation(input = {}) {
  const preview = buildSellerCampaignCreatePreview(input);
  const valid = preview.errors.length === 0;
  return {
    package_type: 'seller_campaign_create_precheck',
    status: valid ? 'awaiting_supervisor_confirmation' : 'blocked',
    can_request_final_confirmation: valid,
    mode: 'real',
    action: 'create_seller_campaign',
    account_id: String(preview.input.account_id || ''),
    site_id: preview.input.site_id || null,
    child_user_id: preview.input.child_user_id || null,
    promotion_type: 'SELLER_CAMPAIGN',
    sub_type: preview.input.sub_type || 'FLEXIBLE_PERCENTAGE',
    promotion_name: preview.input.name || '',
    start_date: preview.input.start_date || null,
    finish_date: preview.input.finish_date || null,
    duration_days: preview.input.duration_days,
    request_preview: preview.request_preview,
    validation_errors: preview.errors,
    expected_impact_summary: valid
      ? `将创建一个 ${preview.input.site_id || '-'} 站点 Seller Campaign 自建活动草案预检；真实创建会修改 Mercado 外部状态，本轮不会执行。`
      : '创建活动参数未通过本地预检，不允许请求真实创建确认。',
    recheck_method: '真实创建后需重新读取该 child user 的 seller-promotions 活动列表，确认新活动 ID、状态、开始/结束时间和站点归属。',
    risk_prompts: [
      '创建 Seller Campaign 属于 Mercado 外部写入，本轮只生成预检包，不执行 POST。',
      '最大活动周期 14 天；开始/结束时间必须使用可解析的 ISO 时间。',
      '真实创建前必须由主管再次确认账号、站点、child_user_id、活动名称和时间范围。'
    ]
  };
}

export function normalizeSellerCampaignCreateInput(input = {}) {
  const start = normalizeDate(input.startDate ?? input.start_date);
  const finish = normalizeDate(input.finishDate ?? input.finish_date);
  return {
    account_id: text(input.accountId ?? input.account_id),
    site_id: text(input.siteId ?? input.site_id).toUpperCase(),
    child_user_id: text(input.childUserId ?? input.child_user_id),
    name: text(input.name),
    sub_type: text((input.subType ?? input.sub_type) || 'FLEXIBLE_PERCENTAGE').toUpperCase(),
    start_date: start.iso,
    finish_date: finish.iso,
    start_valid: start.valid,
    finish_valid: finish.valid,
    duration_days: start.date && finish.date ? (finish.date.getTime() - start.date.getTime()) / 86400000 : null
  };
}

export function validateSellerCampaignCreateInput(input = {}) {
  const errors = [];
  if (!input.account_id) errors.push('缺少账号 account_id');
  if (!input.site_id) errors.push('缺少站点 site_id');
  if (!input.child_user_id) errors.push('缺少 child_user_id');
  if (!input.name) errors.push('缺少活动名称 name');
  if (!SELLER_CAMPAIGN_SUB_TYPES.has(input.sub_type)) errors.push('sub_type 目前只允许 FLEXIBLE_PERCENTAGE');
  if (!input.start_date || !input.start_valid) errors.push('start_date 必须是可解析的 ISO 时间');
  if (!input.finish_date || !input.finish_valid) errors.push('finish_date 必须是可解析的 ISO 时间');
  if (input.duration_days !== null) {
    if (input.duration_days <= 0) errors.push('finish_date 必须晚于 start_date');
    if (input.duration_days > SELLER_CAMPAIGN_MAX_DAYS) errors.push('Seller Campaign 最大周期 14 天');
  }
  return errors;
}

function normalizeDate(value) {
  const raw = text(value);
  if (!raw) return { iso: '', valid: false, date: null };
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return { iso: raw, valid: false, date: null };
  return { iso: date.toISOString(), valid: true, date };
}

function text(value) {
  return String(value ?? '').trim();
}
