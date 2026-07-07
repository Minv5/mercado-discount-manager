import { buildSubmitPayloadPreview, isDirectPayloadPromotionType } from './promotionPayload.js';
import { normalizeWriteConcurrency } from './concurrency.js';
import { CANDIDATE_INCOMPLETE_CODE, MARKETPLACE_CANDIDATE_INCOMPLETE_CODE, buildCandidateIncompleteResolution } from './candidateResolution.js';
import { fetchCompleteness, fullFetchRequiredWarning } from './planner.js';

const CANDIDATE_INCOMPLETE_STATUSES = new Set([CANDIDATE_INCOMPLETE_CODE, MARKETPLACE_CANDIDATE_INCOMPLETE_CODE]);
const API_INCOMPLETE_MESSAGE = '平台返回 candidate total，但 marketplace child 未返回 candidate 明细；近似 status 会返回 started，已禁止作为 fallback；可等待官方/平台修复、联系 Mercado 支持，或先人工导入 candidate item_id 草案后再用只读明细补齐价格。';

export function buildConfirmationPackage({ account, campaign, action, mode = 'real', status, plan, fetchState, request = {} }) {
  const rows = plan?.rows || [];
  const apiIncomplete = CANDIDATE_INCOMPLETE_STATUSES.has(fetchState?.detail_status);
  const promotionType = String(campaign?.promotion_type || plan?.promotion?.promotion_type || request.promotionType || '').toUpperCase();
  const typeBlocked = Boolean(promotionType) && !isDirectPayloadPromotionType(promotionType);
  const itemPreviews = rows.map((row) => ({
    row,
    payloadPreview: buildSubmitPayloadPreview({ promotion: campaign || plan?.promotion, row, action })
  }));
  const adapterBlocked = itemPreviews.filter(({ row, payloadPreview }) => row.status === 'planned' && !payloadPreview.can_submit);
  const executable = itemPreviews.filter(({ row, payloadPreview }) => row.status === 'planned' && payloadPreview.can_submit);
  const skipped = rows.filter((row) => row.status === 'skipped');
  const platformTotal = numberOrNull(fetchState?.platform_total);
  const fetchInfo = fetchCompleteness(fetchState, rows.length);
  const requireFullFetch = Boolean(request.requireFullFetch);
  const requestedSampleOnly = Boolean(request.sampleOnly);
  const sampleOnly = Boolean(requestedSampleOnly || fetchInfo.sample_only);
  const fullFetchBlocked = action === 'enroll' && requireFullFetch && !fetchInfo.is_full_fetch;
  const sampleOnlyBlocked = action === 'enroll' && mode === 'real' && requestedSampleOnly && !requireFullFetch;
  const typeBlockCount = typeBlocked && adapterBlocked.length === 0 ? 1 : 0;
  const incompleteCount = apiIncomplete ? (platformTotal || 1) : 0;
  const fullFetchBlockedCount = fullFetchBlocked ? (platformTotal || fetchInfo.saved_count || 1) : 0;
  const sampleOnlyBlockedCount = sampleOnlyBlocked ? (fetchInfo.saved_count || rows.length || 1) : 0;
  const blockedCount = incompleteCount || fullFetchBlockedCount || sampleOnlyBlockedCount || adapterBlocked.length + typeBlockCount;
  const canProceed = !apiIncomplete && !fullFetchBlocked && !sampleOnlyBlocked && !typeBlocked && blockedCount === 0 && executable.length > 0;
  const priceRule = priceRuleFromPlan(plan, request);
  const writeConcurrency = normalizeWriteConcurrency(request.writeConcurrency);
  const risks = riskPrompts({ action, campaign, apiIncomplete, adapterBlocked, typeBlocked, canProceed, writeConcurrency });
  const candidateResolution = apiIncomplete ? buildCandidateIncompleteResolution({
    promotionId: campaign?.promotion_id || plan?.promotion?.promotion_id || request.promotionId,
    promotionType,
    platformTotal,
    warning: fetchState?.warning,
    state: fetchState?.detail_status || MARKETPLACE_CANDIDATE_INCOMPLETE_CODE
  }) : null;

  return {
    package_type: 'real_write_precheck',
    status: canProceed ? 'awaiting_supervisor_confirmation' : 'blocked',
    can_request_final_confirmation: canProceed,
    account_id: String(account?.account_id || campaign?.account_id || request.accountId || ''),
    merchant_id: String(campaign?.merchant_id || account?.account_id || request.accountId || ''),
    site_id: campaign?.site_id || account?.site_id || null,
    child_user_id: campaign?.child_user_id || account?.account_id || null,
    logistic_type: campaign?.logistic_type || null,
    promotion_id: campaign?.promotion_id || plan?.promotion?.promotion_id || request.promotionId || null,
    promotion_type: campaign?.promotion_type || plan?.promotion?.promotion_type || request.promotionType || null,
    promotion_name: campaign?.name || plan?.promotion?.name || '',
    action,
    mode,
    item_status: status,
    write_concurrency: writeConcurrency,
    platform_total: fetchInfo.platform_total,
    saved_count: fetchInfo.saved_count,
    is_full_fetch: fetchInfo.is_full_fetch,
    sample_only: sampleOnly,
    inventory_fallback_ready: fetchInfo.inventory_fallback_ready,
    inventory_fallback_source: fetchInfo.inventory_fallback_source,
    inventory_scan_total: fetchInfo.inventory_scan_total,
    inventory_scan_saved: fetchInfo.inventory_scan_saved,
    inventory_detail_success: fetchInfo.inventory_detail_success,
    inventory_detail_failed: fetchInfo.inventory_detail_failed,
    inventory_excluded_started_pending: fetchInfo.inventory_excluded_started_pending,
    inventory_existing_candidate_count: fetchInfo.inventory_existing_candidate_count,
    inventory_added_count: fetchInfo.inventory_added_count,
    inventory_listing_status: fetchInfo.inventory_listing_status,
    partial_readable_subset: fetchInfo.partial_readable_subset,
    missing_count: fetchInfo.missing_count,
    pages_read: fetchInfo.pages_read,
    empty_page_count: fetchInfo.empty_page_count,
    consecutive_empty_pages: fetchInfo.consecutive_empty_pages,
    unique_count: fetchInfo.unique_count,
    duplicate_count: fetchInfo.duplicate_count,
    stop_reason: fetchInfo.stop_reason,
    full_fetch_required: requireFullFetch,
    price_rule: priceRule,
    discount_percent: plan?.discountPercent ?? request.discountPercent ?? null,
    direct_price: plan?.directPrice ?? request.directPrice ?? null,
    items_total: plan?.total ?? rows.length,
    planned: executable.length,
    skipped: skipped.length,
    blocked: blockedCount,
    sample_items: sampleItems(itemPreviews),
    blocking_reasons: blockingReasons({ apiIncomplete, fullFetchBlocked, sampleOnlyBlocked, fetchState, fetchInfo, adapterBlocked, typeBlocked, promotionType }),
    candidate_resolution: candidateResolution,
    expected_impact_summary: expectedImpactSummary({ action, executable: executable.length, skipped: skipped.length, blocked: blockedCount, apiIncomplete, fullFetchBlocked, sampleOnly }),
    recheck_method: recheckMethod(action),
    risk_prompts: risks
  };
}

export function buildBatchConfirmationPackage({ account, action, mode = 'real', status, batch, request = {} }) {
  const writeConcurrency = normalizeWriteConcurrency(request.writeConcurrency);
  const packages = (batch?.plans || []).map(({ promotion, plan, blocked, warning, detail_status, fetchState }) => {
    const packageFetchState = fetchState || (blocked ? {
      detail_status: detail_status || 'api_incomplete',
      warning,
      platform_total: null
    } : null);
    return buildConfirmationPackage({
      account,
      campaign: promotion,
      action,
      mode,
      status,
      plan,
      fetchState: packageFetchState,
      request: { ...request, writeConcurrency }
    });
  });
  const blocked = packages.reduce((sum, pkg) => sum + pkg.blocked, 0);
  const planned = packages.reduce((sum, pkg) => sum + pkg.planned, 0);
  const skipped = packages.reduce((sum, pkg) => sum + pkg.skipped, 0);
  const itemsTotal = packages.reduce((sum, pkg) => sum + Number(pkg.items_total || 0), 0);
  const platformTotal = packages.reduce((sum, pkg) => sum + Number(pkg.platform_total || 0), 0);
  const canProceed = packages.length > 0 && blocked === 0 && planned > 0;
  const sites = [...new Set(packages.map((pkg) => pkg.site_id).filter(Boolean))];

  return {
    package_type: 'batch_real_write_precheck',
    status: canProceed ? 'awaiting_supervisor_confirmation' : 'blocked',
    can_request_final_confirmation: canProceed,
    account_id: String(account?.account_id || request.accountId || ''),
    merchant_id: String(account?.account_id || request.accountId || ''),
    action,
    mode,
    item_status: status,
    write_concurrency: writeConcurrency,
    full_fetch_required: Boolean(request.requireFullFetch),
    sample_only: packages.some((pkg) => pkg.sample_only),
    filters: request.filters || {},
    price_rule: {
      mode: request.priceMode || 'discount',
      seller_discount_percent: request.sellerDiscountPercent ?? 5,
      official_discount_percent: request.officialDiscountPercent ?? 6,
      direct_price: request.directPrice ?? null
    },
    sites,
    promotions_total: packages.length,
    items_total: itemsTotal,
    platform_total: platformTotal,
    planned,
    skipped,
    blocked,
    sample_items: packages.flatMap((pkg) => pkg.sample_items.map((item) => ({
      promotion_id: pkg.promotion_id,
      promotion_type: pkg.promotion_type,
      site_id: pkg.site_id,
      ...item
    }))).slice(0, 20),
    blocking_reasons: [...new Set(packages.flatMap((pkg) => pkg.blocking_reasons || []))],
    promotions: packages.map((pkg) => ({
      site_id: pkg.site_id,
      child_user_id: pkg.child_user_id,
      logistic_type: pkg.logistic_type,
      promotion_id: pkg.promotion_id,
      promotion_type: pkg.promotion_type,
      promotion_name: pkg.promotion_name,
      platform_total: pkg.platform_total,
      saved_count: pkg.saved_count,
      is_full_fetch: pkg.is_full_fetch,
      sample_only: pkg.sample_only,
      inventory_fallback_ready: pkg.inventory_fallback_ready,
      inventory_scan_total: pkg.inventory_scan_total,
      inventory_scan_saved: pkg.inventory_scan_saved,
      inventory_added_count: pkg.inventory_added_count,
      inventory_detail_failed: pkg.inventory_detail_failed,
      inventory_excluded_started_pending: pkg.inventory_excluded_started_pending,
      inventory_listing_status: pkg.inventory_listing_status,
      partial_readable_subset: pkg.partial_readable_subset,
      missing_count: pkg.missing_count,
      pages_read: pkg.pages_read,
      empty_page_count: pkg.empty_page_count,
      consecutive_empty_pages: pkg.consecutive_empty_pages,
      unique_count: pkg.unique_count,
      duplicate_count: pkg.duplicate_count,
      stop_reason: pkg.stop_reason,
      items_total: pkg.items_total,
      planned: pkg.planned,
      skipped: pkg.skipped,
      blocked: pkg.blocked,
      status: pkg.status,
      blocking_reasons: pkg.blocking_reasons
    })),
    expected_impact_summary: `批量预检覆盖 ${packages.length} 个活动、${itemsTotal} 个商品；可执行 ${planned}，跳过 ${skipped}，阻断 ${blocked}；最终确认放行后的写入并发为 ${writeConcurrency}。${packages.some((pkg) => pkg.sample_only) ? '当前包含样本数据，不能表述为平台全量。' : ''}${blocked > 0 ? '存在阻断项，不建议进入真实写入确认。' : '仍需主管最终确认后才可考虑真实写入。'}`,
    recheck_method: recheckMethod(action),
    risk_prompts: [
      '真实报名、更新、取消会修改 Mercado Libre 外部状态，本轮不会执行写接口。',
      `写入并发 ${writeConcurrency} 只会在主管最终确认放行后的真实写入中生效；当前预检不会执行。`,
      '批量真实写入必须由主管按账号、站点、活动、商品数、价格规则再次确认。',
      packages.some((pkg) => pkg.sample_only) ? '样本预览只代表已读取商品，不代表平台全量候选。' : null,
      blocked > 0 ? '存在阻断项时不得建议真实执行。' : '确认前仍需抽查样本价格和活动类型。'
    ].filter(Boolean)
  };
}

function priceRuleFromPlan(plan, request) {
  return {
    mode: plan?.priceMode || request.priceMode || 'discount',
    discount_percent: plan?.discountPercent ?? request.discountPercent ?? null,
    direct_price: plan?.directPrice ?? request.directPrice ?? null
  };
}

function sampleItems(itemPreviews) {
  return itemPreviews.slice(0, 10).map(({ row, payloadPreview }) => ({
    item_id: row.item?.item_id || '',
    status: row.item?.status || '',
    plan_status: row.status,
    original_price: row.item?.original_price ?? null,
    current_price: row.item?.price ?? null,
    current_deal_price: row.item?.price ?? null,
    target_deal_price: row.deal_price ?? null,
    min: row.item?.min_discounted_price ?? null,
    max: row.item?.max_discounted_price ?? null,
    skip_or_error_reason: row.status === 'skipped' ? row.reason : payloadPreview.reason || row.reason || '',
    payload_preview_status: payloadPreview.status,
    adapter_state: payloadPreview.adapter_state || payloadPreview.adapter?.adapter_state || null,
    preview_only: payloadPreview.preview_only || false,
    requires_supervisor_final_confirmation: payloadPreview.requires_supervisor_final_confirmation !== false,
    special_fields: payloadPreview.field_summary || null,
    adapter: payloadPreview.adapter || null,
    adapter_missing_fields: payloadPreview.adapter?.missing_fields || null,
    adapter_next_step: payloadPreview.adapter?.next_step || null,
    requires_limited_real_test: payloadPreview.requires_limited_real_test || payloadPreview.adapter?.requires_limited_real_test || false,
    payload_evidence: payloadPreview.adapter?.payload_evidence || null,
    preview_payload: payloadPreview.payload || null
  }));
}

function blockingReasons({ apiIncomplete, fullFetchBlocked, sampleOnlyBlocked, fetchState, fetchInfo, adapterBlocked, typeBlocked, promotionType }) {
  const reasons = [];
  if (apiIncomplete) reasons.push(fetchState?.warning || API_INCOMPLETE_MESSAGE);
  if (fullFetchBlocked) reasons.push(fetchState?.warning || fullFetchRequiredWarning(fetchInfo));
  if (sampleOnlyBlocked) reasons.push('当前只是样本预览，不是平台全量候选；真实预检必须先全量读取 candidate。');
  for (const blocked of adapterBlocked.slice(0, 5)) {
    reasons.push(`${blocked.row.item?.item_id || '-'}：${blocked.payloadPreview.reason}`);
  }
  if (typeBlocked && adapterBlocked.length === 0) reasons.push(`${promotionType || 'UNKNOWN'} 官方写入参数未完整确认，需活动类型专项适配`);
  return [...new Set(reasons)];
}

function expectedImpactSummary({ action, executable, skipped, blocked, apiIncomplete, fullFetchBlocked, sampleOnly }) {
  if (apiIncomplete) return `平台提示存在候选总数但未返回明细；当前阻断，不能建议真实 ${action}。`;
  if (fullFetchBlocked) return `候选未全量读取；当前阻断，不能按“全部报活动”生成真实 ${action} 建议。`;
  if (sampleOnly) return `当前仅为样本预览：可执行 ${executable}，跳过 ${skipped}，阻断 ${blocked}；不代表平台全量候选。`;
  return `本次真实写入预检：可执行 ${executable}，跳过 ${skipped}，阻断 ${blocked}。${blocked > 0 ? '存在阻断项，不建议真实执行。' : '仍需主管最终确认后才可考虑真实执行。'}`;
}

function recheckMethod(action) {
  if (action === 'cancel') return '真实取消后必须重新读取 started 商品，直到剩余 started 为 0 才算完成。';
  if (action === 'update') return '真实更新后需重新读取 started 商品并核对当前活动价是否等于目标价。';
  return '真实报名后需重新读取 candidate/pending/started，核对报名结果和失败原因。';
}

function riskPrompts({ action, campaign, apiIncomplete, adapterBlocked, typeBlocked, canProceed, writeConcurrency }) {
  const risks = [
    '本轮只生成预检包，不执行 Mercado Libre 写接口。',
    `写入并发 ${writeConcurrency} 只会在主管最终确认放行后的真实写入中生效；当前预检不会执行。`,
    `真实 ${action} 会改变外部平台状态，必须等待主管最终确认。`
  ];
  if (apiIncomplete) risks.push(API_INCOMPLETE_MESSAGE);
  if (campaign?.promotion_type === 'SMART') risks.push('SMART 仅生成 offer_id 实验 preview；需单商品真实验证后才能讨论放行，不能批量执行。');
  else if (campaign?.promotion_type === 'LIGHTNING') risks.push('LIGHTNING 官方报名 body 已确认，可生成单商品 limited real test preview；真实写入仍必须先做单商品小样本验证并经主管最终确认，不能批量放行。');
  else if (typeBlocked || adapterBlocked.length > 0) risks.push(`${campaign?.promotion_type || 'UNKNOWN'} 活动类型需专项提交适配。`);
  if (canProceed) risks.push('确认前需要抽查价格边界、站点 child_user_id 和活动类型。');
  return risks;
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
