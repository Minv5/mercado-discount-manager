export const SELLER_CAMPAIGN_CREATE_PATH = '/marketplace/seller-promotions/seller-campaign';
export const SELLER_CAMPAIGN_SUB_TYPES = new Set(['FLEXIBLE_PERCENTAGE']);

export function summarizeSellerCampaignLiveSites(children = []) {
  const summary = new Map();
  for (const child of Array.isArray(children) ? children : []) {
    const siteId = String(child?.site_id || '').trim().toUpperCase();
    if (!siteId) continue;
    const current = summary.get(siteId) || {
      ok_count: 0,
      error_count: 0,
      seller_campaign_count: 0,
      errors: []
    };
    if (String(child?.status || '').toLowerCase() === 'ok') {
      current.ok_count += 1;
      current.seller_campaign_count += Number(child?.seller_campaign_count || 0);
    } else {
      current.error_count += 1;
      if (child?.error) current.errors.push(String(child.error));
    }
    summary.set(siteId, current);
  }
  return summary;
}

export function buildSellerCampaignCreatePreview(input = {}) {
  const normalized = normalizeSellerCampaignCreateInput(input);
  const errors = validateSellerCampaignCreateInput(normalized);
  const requestPreview = {
    method: 'POST',
    path: `${SELLER_CAMPAIGN_CREATE_PATH}/${encodeURIComponent(normalized.child_user_id || '{USER_ID}')}`,
    headers: {
      version: 'v2',
      'X-Caller-Id': normalized.account_id || null,
      'X-Client-Id': normalized.account_id || null
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
      '结束日期按官网日历口径限制在开始日期所在月份内；开始/结束时间必须使用 Mercado 可接受的本地时间格式。',
      '真实创建前必须由主管再次确认账号、站点、child_user_id、活动名称和时间范围。'
    ]
  };
}

export function buildSellerCampaignBatchCreatePrecheck(input = {}) {
  const targets = Array.isArray(input.targets) ? input.targets : [];
  const existingTargets = targets.filter((target) => sellerCampaignDetectionStatus(target) === 'existing');
  const hiddenExistingTargets = targets.filter((target) => sellerCampaignDetectionStatus(target) === 'existing_without_visible_id');
  const confirmedAbsentTargets = targets.filter((target) => sellerCampaignDetectionStatus(target) === 'confirmed_absent');
  const reviewTargets = targets.filter((target) => sellerCampaignDetectionStatus(target) === 'visibility_unknown');
  const unreadableTargets = targets.filter((target) => sellerCampaignDetectionStatus(target) === 'unreadable');
  const prechecks = confirmedAbsentTargets.map((target) => {
    const confirmation = buildSellerCampaignCreateConfirmation({
      accountId: target.account_id,
      siteId: target.site_id,
      childUserId: target.child_user_id,
      name: input.name,
      startDate: input.startDate ?? input.start_date,
      finishDate: input.finishDate ?? input.finish_date
    });
    return {
      store_name: target.store_name || '',
      site_name: target.site_name || '',
      account_id: target.account_id || '',
      site_id: target.site_id || '',
      promotion_type: 'SELLER_CAMPAIGN',
      child_user_id: target.child_user_id || '',
      detection_status: 'confirmed_absent',
      status: confirmation.validation_errors.length ? 'blocked' : 'preview_ready',
      validation_errors: confirmation.validation_errors,
      confirmation_package: confirmation
    };
  });
  const reviewRows = reviewTargets.map((target) => ({
    ...target,
    detection_status: 'visibility_unknown',
    status: 'needs_manual_review',
    error: target.detection_message || '接口未读取到自建活动，但不能据此确认后台不存在；本次禁止自动创建。',
  }));
  const unreadableRows = unreadableTargets.map((target) => ({
    store_name: target.store_name || '',
    site_name: target.site_name || '',
    account_id: target.account_id || '',
    site_id: target.site_id || '',
    promotion_type: 'SELLER_CAMPAIGN',
    detection_status: 'unreadable',
    status: 'blocked',
    error: target.detection_message || '无法确认该店铺站点是否已有自建活动，已阻断创建。'
  }));
  const validationErrors = [...new Set(prechecks.flatMap((entry) => entry.validation_errors || []).filter(Boolean))];
  return {
    ok: true,
    mode: 'preview_only',
    action: 'create_seller_campaign',
    promotion_type: 'SELLER_CAMPAIGN',
    creates_official_activity: false,
    writes_external_state: false,
    existing_count: existingTargets.length,
    existing_without_visible_id_count: hiddenExistingTargets.length,
    duplicate_name_hidden_count: hiddenExistingTargets.length,
    missing_count: confirmedAbsentTargets.length,
    confirmed_absent_count: confirmedAbsentTargets.length,
    needs_manual_review_count: reviewTargets.length,
    visibility_unknown_count: reviewTargets.length,
    unknown_not_returned_count: reviewTargets.length,
    unreadable_count: unreadableTargets.length,
    preview_ready_count: prechecks.filter((entry) => entry.status === 'preview_ready').length,
    blocked_count: prechecks.filter((entry) => entry.status === 'blocked').length + reviewRows.length + unreadableRows.length,
    validation_errors: validationErrors,
    existing: existingTargets,
    existing_without_visible_id: hiddenExistingTargets,
    duplicate_name_hidden: hiddenExistingTargets,
    missing: confirmedAbsentTargets,
    confirmed_absent: confirmedAbsentTargets,
    needs_manual_review: reviewRows,
    visibility_unknown: reviewRows,
    unknown_not_returned: reviewRows,
    unreadable: unreadableRows,
    prechecks,
    user_message: confirmedAbsentTargets.length
      ? `有 ${confirmedAbsentTargets.length} 个店铺站点由可验证来源确认不存在自建活动，可进入创建预检。`
      : reviewTargets.length
        ? `有 ${reviewTargets.length} 个店铺站点活动可见性未知，本次需要复核且禁止自动创建。`
        : hiddenExistingTargets.length
          ? `有 ${hiddenExistingTargets.length} 个店铺站点已确认同名自建活动存在，但平台未返回活动ID，本次不会重复创建。`
          : '所选范围内店铺站点已存在自建活动，或当前无法安全确认创建条件。'
  };
}

function sellerCampaignDetectionStatus(target = {}) {
  const status = String(target.detection_status || target.seller_campaign_detection_status || '').trim().toLowerCase();
  if (status === 'existing' || status === 'existing_without_visible_id' || status === 'duplicate_name_hidden'
      || status === 'confirmed_absent' || status === 'unreadable') {
    return status === 'duplicate_name_hidden' ? 'existing_without_visible_id' : status;
  }
  if (status === 'visibility_unknown' || status === 'needs_manual_review' || status === 'unknown_not_returned') return 'visibility_unknown';
  return target.hasSellerCampaign ? 'existing' : 'visibility_unknown';
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
    if (isAfterStartMonth(input.start_date, input.finish_date)) {
      errors.push('finish_date 不能超过开始日期所在月份的最后一天');
    }
  }
  return errors;
}

function isAfterStartMonth(startValue, finishValue) {
  const startMonth = monthKey(startValue);
  const finishMonth = effectiveFinishMonthKey(finishValue);
  if (!startMonth || !finishMonth) return false;
  return startMonth !== finishMonth;
}

function monthKey(value) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})/);
  return match ? `${match[1]}-${match[2]}` : '';
}

function effectiveFinishMonthKey(value) {
  const raw = String(value || '');
  const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::(\d{2}))?)?/);
  if (!match) return '';
  let year = Number(match[1]);
  let month = Number(match[2]);
  let day = Number(match[3]);
  const hour = match[4] ?? '';
  const minute = match[5] ?? '';
  const second = match[6] ?? '00';
  const isExclusiveMidnight = hour === '00' && minute === '00' && second === '00';
  if (isExclusiveMidnight) {
    const date = new Date(Date.UTC(year, month - 1, day));
    date.setUTCDate(date.getUTCDate() - 1);
    year = date.getUTCFullYear();
    month = date.getUTCMonth() + 1;
  }
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`;
}

function normalizeDate(value) {
  const raw = text(value);
  if (!raw) return { iso: '', valid: false, date: null };
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return { iso: raw, valid: false, date: null };
  return { iso: raw, valid: true, date };
}

function text(value) {
  return String(value ?? '').trim();
}
