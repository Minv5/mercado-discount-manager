import { PROMOTION_BUCKETS, promotionBucket } from './promotionDomain.js';

export const ACTIONS = new Set(['enroll', 'update', 'cancel']);
export const CANDIDATE_INCOMPLETE_STATUSES = new Set(['api_incomplete', 'api_incomplete_marketplace_candidate']);
export const PARTIAL_FETCH_STATUSES = new Set(['partial', 'partial_api_sparse_marketplace_candidate']);
export const INVENTORY_FALLBACK_READY_STATUSES = new Set(['inventory_scan_fallback_ready', 'inventory_scan_fallback_partial']);
export const NOT_FULL_FETCH_STATUS = 'not_full_fetch';

const ZERO_DECIMAL_CURRENCIES = new Set(['CLP', 'COP']);
const DEFAULT_CURRENCY_PRECISION = 2;

export function defaultDiscountForPromotion(promotionType) {
  const bucket = promotionBucket(promotionType);
  if (bucket === PROMOTION_BUCKETS.seller) return 5;
  if (bucket === PROMOTION_BUCKETS.official) return 6;
  return null;
}

export function discountForPromotion(promotionType, options = {}) {
  const bucket = promotionBucket(promotionType);
  if (bucket === PROMOTION_BUCKETS.seller) return numberOrNull(options.sellerDiscountPercent) ?? 5;
  if (bucket === PROMOTION_BUCKETS.official) return numberOrNull(options.officialDiscountPercent) ?? 6;
  return null;
}

export function normalizePromotion(raw) {
  return {
    account_id: raw.account_id || raw.accountId || '',
    child_user_id: raw.child_user_id || raw.childUserId || '',
    site_id: raw.site_id || raw.siteId || '',
    logistic_type: raw.logistic_type || raw.logisticType || '',
    promotion_id: raw.promotion_id || raw.id,
    promotion_type: raw.promotion_type || raw.type,
    name: raw.name || raw.title || '',
    status: raw.status || '',
    start_date: raw.start_date || raw.startDate || null,
    finish_date: raw.finish_date || raw.finishDate || raw.deadline_date || null,
    raw
  };
}

export function normalizeItem(raw) {
  const rawJson = parseRawJson(raw?.raw_json);
  const offerId = raw.offer_id ?? raw.offerId ?? rawJson.offer_id ?? rawJson.offerId ?? rawJson.offer?.id ?? rawJson.offer?.offer_id ?? null;
  return {
    item_id: raw.item_id || raw.id,
    status: raw.status || '',
    currency_id: raw.currency_id || null,
    original_price: numberOrNull(raw.original_price),
    price: numberOrNull(raw.price),
    suggested_discounted_price: numberOrNull(raw.suggested_discounted_price),
    min_discounted_price: numberOrNull(raw.min_discounted_price),
    max_discounted_price: numberOrNull(raw.max_discounted_price),
    currency_precision: nonNegativeIntegerOrNull(
      raw.currency_precision
      ?? raw.currencyPrecision
      ?? raw.decimal_places
      ?? raw.currency?.decimal_places
      ?? rawJson.currency_precision
      ?? rawJson.decimal_places
      ?? rawJson.currency?.decimal_places
    ),
    currency_minor_unit: positiveNumberOrNull(
      raw.currency_minor_unit
      ?? raw.currencyMinorUnit
      ?? raw.minor_unit
      ?? rawJson.currency_minor_unit
      ?? rawJson.minor_unit
    ),
    offer_id: offerId,
    raw_json: raw.raw_json || null,
    raw
  };
}

function parseRawJson(value) {
  if (!value || typeof value !== 'string') return {};
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

export function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function roundMoney(value) {
  return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
}

export function moneyRuleForCurrency(currencyId, { precision, minorUnit } = {}) {
  const normalizedCurrency = String(currencyId || '').trim().toUpperCase();
  const explicitMinorUnit = positiveNumberOrNull(minorUnit);
  const resolvedPrecision = nonNegativeIntegerOrNull(precision)
    ?? precisionFromMinorUnit(explicitMinorUnit)
    ?? (ZERO_DECIMAL_CURRENCIES.has(normalizedCurrency) ? 0 : DEFAULT_CURRENCY_PRECISION);
  const resolvedMinorUnit = explicitMinorUnit ?? 10 ** -resolvedPrecision;
  return {
    currency_id: normalizedCurrency || null,
    precision: resolvedPrecision,
    minor_unit: resolvedMinorUnit,
  };
}

export function moneyRuleForItem(item = {}) {
  return moneyRuleForCurrency(item.currency_id, {
    precision: item.currency_precision,
    minorUnit: item.currency_minor_unit,
  });
}

export function normalizeDealPriceForPayload(value, item = {}) {
  const numeric = numberOrNull(value);
  if (numeric === null) return null;
  return quantizeToMinorUnit(numeric, moneyRuleForItem(item), 'floor');
}

export function calculateDealPrice(item, options) {
  if (options.priceMode === 'direct') {
    return normalizeDealPriceForPayload(options.directPrice, item);
  }
  const base = item.original_price ?? item.price;
  if (!Number.isFinite(base) || base <= 0) return null;
  const exactDiscountedPrice = base * (100 - options.discountPercent) / 100;
  return quantizeToMinorUnit(exactDiscountedPrice, moneyRuleForItem(item), 'floor');
}

export function validateDealPrice(item, dealPrice) {
  if (!Number.isFinite(dealPrice) || dealPrice <= 0) return '活动价必须大于 0';
  if (item.min_discounted_price !== null && dealPrice < item.min_discounted_price) {
    return `活动价 ${dealPrice} 低于最低允许价 ${item.min_discounted_price}`;
  }
  if (item.max_discounted_price !== null && dealPrice > item.max_discounted_price) {
    return `活动价 ${dealPrice} 高于最高允许价 ${item.max_discounted_price}`;
  }
  return null;
}

export function isAllowedStatus(action, status) {
  if (action === 'enroll') return status === 'candidate';
  if (action === 'update') return status === 'started';
  if (action === 'cancel') return status === 'started';
  return false;
}

export function buildPlan({ action, promotion, items, priceMode = 'discount', discountPercent, directPrice, skipSamePrice = true }) {
  if (!ACTIONS.has(action)) throw new Error('不支持的动作');
  const normalizedPromotion = normalizePromotion(promotion);
  const effectiveDiscount = numberOrNull(discountPercent) ?? defaultDiscountForPromotion(normalizedPromotion.promotion_type);
  const rows = items.map((source) => {
    const item = normalizeItem(source);
    if (!item.item_id) return planSkip(item, '缺少商品 ID');
    if (!isAllowedStatus(action, item.status)) return planSkip(item, `状态 ${item.status || '未知'} 不适合执行当前动作`);

    if (action === 'cancel') {
      return { item, action, status: 'planned', deal_price: null, reason: '将取消已开始活动商品' };
    }

    const dealPrice = calculateDealPrice(item, { priceMode, discountPercent: effectiveDiscount, directPrice });
    const priceError = validateDealPrice(item, dealPrice);
    if (priceError) return planSkip(item, priceError);
    if (action === 'update' && skipSamePrice && item.price !== null && roundMoney(item.price) === dealPrice) {
      return planSkip(item, '当前活动价已等于目标价');
    }
    return { item, action, status: 'planned', deal_price: dealPrice, reason: '可执行' };
  });

  return {
    promotion: normalizedPromotion,
    action,
    priceMode,
    discountPercent: effectiveDiscount,
    directPrice: numberOrNull(directPrice),
    total: rows.length,
    planned: rows.filter((row) => row.status === 'planned').length,
    skipped: rows.filter((row) => row.status === 'skipped').length,
    rows
  };
}

export function filterPromotions(promotions, filters = {}) {
  const siteIds = splitFilter(filters.siteIds || filters.siteId);
  const childUserIds = splitFilter(filters.childUserIds || filters.childUserId || filters.child_user_id);
  const promotionTypes = splitFilter(filters.promotionTypes || filters.promotionType).map((value) => value.toUpperCase());
  const keywords = splitFilter(filters.keywords || filters.name);
  const sellerActivityNames = splitFilter(filters.sellerActivityNames || filters.sellerActivityName).map(normalizeActivityName);
  const officialActivityNames = splitFilter(filters.officialActivityNames || filters.officialActivityName).map(normalizeActivityName);
  const ordinarySelectorActive = Boolean(filters.excludeSeller || filters.excludeOfficial || sellerActivityNames.length || officialActivityNames.length);
  return promotions.filter((promo) => {
    const type = String(promo.promotion_type || '').toUpperCase();
    const activityName = normalizeActivityName(activityDisplayName(promo));
    const bucket = promotionBucket(type);
    if (siteIds.length && !siteIds.includes(String(promo.site_id || ''))) return false;
    if (childUserIds.length && !childUserIds.includes(String(promo.child_user_id || ''))) return false;
    if (promotionTypes.length && !promotionTypes.includes(type)) return false;
    if (filters.status && promo.status !== filters.status) return false;
    if (ordinarySelectorActive && ![PROMOTION_BUCKETS.seller, PROMOTION_BUCKETS.official].includes(bucket)) return false;
    if (filters.excludeSeller && bucket === PROMOTION_BUCKETS.seller) return false;
    if (filters.excludeOfficial && bucket === PROMOTION_BUCKETS.official) return false;
    if (bucket === PROMOTION_BUCKETS.seller && sellerActivityNames.length && !sellerActivityNames.includes(activityName)) return false;
    if (bucket === PROMOTION_BUCKETS.official && officialActivityNames.length && !officialActivityNames.includes(activityName)) return false;
    if (keywords.length) {
      const text = `${promo.name || ''} ${promo.promotion_id || ''}`.toLowerCase();
      if (!keywords.some((keyword) => text.includes(keyword.toLowerCase()))) return false;
    }
    return true;
  });
}

export function activityDisplayName(promo = {}) {
  return String(promo.name || promo.promotion_name || promo.promotion_id || promo.id || '').trim();
}

export function normalizeActivityName(value) {
  return String(value || '')
    .normalize('NFKC')
    .replace(/[\p{Cf}\p{Cc}]/gu, '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[\s.,;:|/\\，。；：、]+$/u, '')
    .toLowerCase();
}

export function splitFilter(value) {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
  return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
}

export function summarizeSites(promotions) {
  const map = new Map();
  for (const promo of promotions) {
    const key = `${promo.site_id || '-'}|${promo.child_user_id || '-'}|${promo.logistic_type || '-'}`;
    if (!map.has(key)) {
      map.set(key, {
        site_id: promo.site_id || null,
        child_user_id: promo.child_user_id || null,
        logistic_type: promo.logistic_type || null,
        total: 0,
        by_type: {},
        by_status: {}
      });
    }
    const row = map.get(key);
    row.total += 1;
    row.by_type[promo.promotion_type || 'UNKNOWN'] = (row.by_type[promo.promotion_type || 'UNKNOWN'] || 0) + 1;
    row.by_status[promo.status || 'UNKNOWN'] = (row.by_status[promo.status || 'UNKNOWN'] || 0) + 1;
  }
  return [...map.values()].sort((a, b) => String(a.site_id).localeCompare(String(b.site_id)));
}

export function buildBatchPlans({
  action,
  promotions,
  itemsByPromotion,
  fetchStatesByPromotion = new Map(),
  priceMode = 'discount',
  sellerDiscountPercent = 5,
  officialDiscountPercent = 6,
  directPrice,
  requireFullFetch = false,
  sampleOnly = false,
  allowInventoryFallback = false
}) {
  const plans = [];
  const totals = { promotions: 0, total: 0, planned: 0, skipped: 0, empty: 0, blocked: 0 };
  for (const promotion of promotions) {
    const key = promotionKey(promotion);
    const fetchState = fetchStatesByPromotion.get(key);
    const items = itemsByPromotion.get(key) || [];
    const fetchInfo = fetchCompleteness(fetchState, items.length);
    if (isBlockingLiveRead(fetchState)) {
      const liveReadFailure = ['error', 'unreadable'].includes(String(fetchState?.detail_status || '').toLowerCase());
      plans.push({
        promotion,
        blocked: true,
        detail_status: fetchState.detail_status,
        warning: liveReadFailure
          ? `活动商品实时读取失败，已阻断旧缓存进入执行计划：${fetchState?.warning || '无法确认平台当前商品状态'}`
          : candidateIncompleteWarning(fetchState.warning, fetchState.detail_status),
        fetchState,
        fetch_info: fetchInfo,
        plan: { promotion, action, priceMode, discountPercent: null, directPrice: null, total: 0, planned: 0, skipped: 0, rows: [] }
      });
      totals.promotions += 1;
      totals.blocked += 1;
      continue;
    }
    const inventoryFallbackAllowed = Boolean(
      allowInventoryFallback
      && action === 'enroll'
      && String(promotion.promotion_type || '').toUpperCase() === 'SELLER_CAMPAIGN'
      && fetchInfo.inventory_fallback_ready
    );
    if (requireFullFetch && action === 'enroll' && !fetchInfo.is_full_fetch && !inventoryFallbackAllowed) {
      const warning = fullFetchRequiredWarning(fetchInfo);
      const blockedFetchState = {
        ...(fetchState || {}),
        detail_status: NOT_FULL_FETCH_STATUS,
        original_detail_status: fetchState?.detail_status || null,
        warning,
        platform_total: fetchInfo.platform_total,
        saved_count: fetchInfo.saved_count,
        raw_json: fetchState?.raw_json || fetchState?.raw || null
      };
      plans.push({
        promotion,
        blocked: true,
        detail_status: NOT_FULL_FETCH_STATUS,
        warning,
        fetchState: blockedFetchState,
        fetch_info: fetchInfo,
        plan: { promotion, action, priceMode, discountPercent: null, directPrice: null, total: 0, planned: 0, skipped: 0, rows: [] }
      });
      totals.promotions += 1;
      totals.blocked += 1;
      continue;
    }
    const discountPercent = discountForPromotion(promotion.promotion_type, { sellerDiscountPercent, officialDiscountPercent });
    const plan = buildPlan({
      action,
      promotion,
      items,
      priceMode,
      discountPercent,
      directPrice,
      skipSamePrice: true
    });
    plans.push({ promotion, plan, fetchState, fetch_info: fetchInfo, inventory_fallback_allowed: inventoryFallbackAllowed });
    totals.promotions += 1;
    totals.total += plan.total;
    totals.planned += plan.planned;
    totals.skipped += plan.skipped;
    if (plan.total === 0) totals.empty += 1;
  }
  const sampleFromFetch = action === 'enroll' && plans.some((entry) => entry.fetch_info && !entry.fetch_info.is_full_fetch && !entry.fetch_info.inventory_fallback_ready);
  return {
    totals: { ...totals, sample_only: Boolean(sampleOnly || (!requireFullFetch && sampleFromFetch)) },
    plans,
    sample_only: Boolean(sampleOnly || (!requireFullFetch && sampleFromFetch)),
    require_full_fetch: Boolean(requireFullFetch)
  };
}

function isBlockingLiveRead(fetchState) {
  const status = String(fetchState?.detail_status || '').toLowerCase();
  return status === 'error' || status === 'unreadable' || CANDIDATE_INCOMPLETE_STATUSES.has(fetchState?.detail_status);
}

export function promotionKey(promotion) {
  return [
    promotion.account_id || promotion.accountId || '',
    promotion.child_user_id || promotion.childUserId || '',
    String(promotion.site_id || promotion.siteId || '').toUpperCase(),
    promotion.promotion_id || promotion.promotionId || '',
    String(promotion.promotion_type || promotion.promotionType || '').toUpperCase(),
  ].join('|');
}

function planSkip(item, reason) {
  return { item, action: null, status: 'skipped', deal_price: null, reason };
}

function quantizeToMinorUnit(value, rule, mode) {
  const unit = rule.minor_unit;
  const scaled = Number(value) / unit;
  const tolerance = Number.EPSILON * Math.max(1, Math.abs(scaled)) * 8;
  const units = mode === 'floor'
    ? Math.floor(scaled + tolerance)
    : Math.round(scaled + tolerance);
  return Number((units * unit).toFixed(rule.precision));
}

function nonNegativeIntegerOrNull(value) {
  const numeric = numberOrNull(value);
  if (numeric === null || numeric < 0 || !Number.isInteger(numeric)) return null;
  return numeric;
}

function positiveNumberOrNull(value) {
  const numeric = numberOrNull(value);
  return numeric !== null && numeric > 0 ? numeric : null;
}

function precisionFromMinorUnit(minorUnit) {
  if (minorUnit === null) return null;
  let scaled = minorUnit;
  for (let precision = 0; precision <= 8; precision += 1) {
    if (Math.abs(scaled - Math.round(scaled)) <= Number.EPSILON * Math.max(1, Math.abs(scaled)) * 8) {
      return precision;
    }
    scaled *= 10;
  }
  return null;
}

function candidateIncompleteWarning(warning, detailStatus = 'api_incomplete') {
  const base = warning || (detailStatus === 'api_incomplete_marketplace_candidate'
    ? '平台返回 candidate total，但 marketplace child 未返回 candidate 明细；近似 status 会返回 started，已禁止作为 fallback。'
    : '平台返回候选总数但未返回候选明细，需要接口专项处理');
  if (/替代 API|人工导入/.test(base)) return base;
  return `${base}；可等待官方/平台修复、联系 Mercado 支持，或先人工导入 candidate item_id 草案后再用只读明细补齐价格。`;
}

function parseRawFetchState(raw) {
  if (!raw) return {};
  if (typeof raw === 'object') return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

export function fetchCompleteness(fetchState, fallbackSavedCount = 0) {
  const platformTotal = numberOrNull(fetchState?.platform_total);
  const savedCount = Math.max(0, Math.floor(numberOrNull(fetchState?.saved_count) ?? fallbackSavedCount ?? 0));
  const detailStatus = fetchState?.detail_status || null;
  const sourceDetailStatus = fetchState?.original_detail_status || fetchState?.source_detail_status || detailStatus;
  const blocked = CANDIDATE_INCOMPLETE_STATUSES.has(detailStatus);
  const partial = PARTIAL_FETCH_STATUSES.has(sourceDetailStatus);
  const inventoryFallbackReady = INVENTORY_FALLBACK_READY_STATUSES.has(detailStatus);
  const noProducts = platformTotal === 0 || detailStatus === 'empty';
  const hasKnownFullTotal = platformTotal !== null && savedCount >= platformTotal;
  const raw = parseRawFetchState(fetchState?.raw_json || fetchState?.raw || fetchState?.rawSummary);
  return {
    platform_total: platformTotal,
    saved_count: savedCount,
    detail_status: detailStatus,
    is_full_fetch: inventoryFallbackReady || (!blocked && !partial && (noProducts || hasKnownFullTotal)),
    sample_only: !inventoryFallbackReady && !blocked && !noProducts && (partial || !(platformTotal !== null && savedCount >= platformTotal)),
    inventory_fallback_ready: inventoryFallbackReady,
    inventory_fallback_source: inventoryFallbackReady ? raw?.source || 'inventory_scan_fallback' : null,
    inventory_scan_total: numberOrNull(raw?.scan_total),
    inventory_scan_saved: numberOrNull(raw?.scan_saved),
    inventory_detail_success: numberOrNull(raw?.detail_success),
    inventory_detail_failed: numberOrNull(raw?.detail_failed),
    inventory_excluded_started_pending: numberOrNull(raw?.excluded_started_pending),
    inventory_existing_candidate_count: numberOrNull(raw?.existing_candidate_count),
    inventory_added_count: numberOrNull(raw?.added_count),
    inventory_listing_status: raw?.listing_status || null,
    missing_count: platformTotal === null ? null : Math.max(0, platformTotal - savedCount),
    partial_readable_subset: sourceDetailStatus === 'partial_api_sparse_marketplace_candidate',
    pages_read: numberOrNull(raw?.pages_read),
    empty_page_count: numberOrNull(raw?.empty_page_count),
    consecutive_empty_pages: numberOrNull(raw?.consecutive_empty_pages),
    unique_count: numberOrNull(raw?.unique_count) ?? savedCount,
    duplicate_count: numberOrNull(raw?.duplicate_count),
    last_search_after: raw?.last_search_after || null,
    stop_reason: raw?.stop_reason || null
  };
}

export function fullFetchRequiredWarning(fetchInfo) {
  const totalText = fetchInfo.platform_total === null ? '未知' : fetchInfo.platform_total;
  if (fetchInfo.inventory_fallback_ready) {
    return `自建活动已启用库存扫描兜底：扫描 ${fetchInfo.inventory_scan_saved ?? fetchInfo.saved_count} 个站点商品，新增兜底候选 ${fetchInfo.inventory_added_count ?? 0} 个；资格以 Mercado 报名返回为准。`;
  }
  if (fetchInfo.detail_status === 'partial_api_sparse_marketplace_candidate') {
    return `平台 candidate total=${totalText}，本地只读取到 ${fetchInfo.saved_count} 个可见候选子集；分页中出现 ${fetchInfo.empty_page_count ?? 0} 个空页/稀疏页，平台剩余候选未返回明细，不能按“全部报活动”执行。`;
  }
  return `候选未全量读取，不能按“全部报活动”执行；平台 total=${totalText}，本地已读取=${fetchInfo.saved_count}，请先执行全量读取候选。`;
}

export async function cancelUntilEmpty({ fetchStartedItems, cancelItem, maxRounds = 5 }) {
  const rounds = [];
  for (let round = 1; round <= maxRounds; round += 1) {
    const items = await fetchStartedItems();
    rounds.push({ round, remaining: items.length });
    if (items.length === 0) return { completed: true, rounds };
    for (const item of items) {
      await cancelItem(item);
    }
  }
  const finalItems = await fetchStartedItems();
  rounds.push({ round: maxRounds + 1, remaining: finalItems.length });
  return { completed: finalItems.length === 0, rounds };
}
