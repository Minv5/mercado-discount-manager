import { mapLimited, normalizeConcurrency } from './concurrency.js';
import { roundMoney } from './planner.js';

export const INVENTORY_FALLBACK_SOURCE = 'inventory_scan_fallback';
export const INVENTORY_FALLBACK_READY_STATUS = 'inventory_scan_fallback_ready';
export const INVENTORY_FALLBACK_PARTIAL_STATUS = 'inventory_scan_fallback_partial';
export const INVENTORY_FALLBACK_ERROR_STATUS = 'inventory_scan_fallback_error';
export const INVENTORY_FALLBACK_ITEM_STATUS = 'candidate_inventory_fallback';

export function isSellerCampaign(promotion) {
  return String(promotion?.promotion_type || promotion?.type || '').toUpperCase() === 'SELLER_CAMPAIGN';
}

export function buildInventoryFallbackCandidateRows({ itemDetails, promotion, discountPercent = 5 }) {
  return itemDetails
    .map((detail) => inventoryDetailToCandidateRow({ detail, promotion, discountPercent }))
    .filter(Boolean);
}

export function inventoryDetailToCandidateRow({ detail, promotion, discountPercent = 5 }) {
  const itemId = detail?.id || detail?.item_id;
  const basePrice = numberOrNull(detail?.price);
  if (!itemId || !Number.isFinite(basePrice) || basePrice <= 0) return null;
  return {
    id: String(itemId),
    status: 'candidate',
    currency_id: detail.currency_id || null,
    original_price: basePrice,
    price: basePrice,
    suggested_discounted_price: roundMoney(basePrice * (100 - Number(discountPercent || 5)) / 100),
    min_discounted_price: null,
    max_discounted_price: null,
    source: INVENTORY_FALLBACK_SOURCE,
    inventory_scan_source: INVENTORY_FALLBACK_SOURCE,
    candidate_source_quality: 'eligibility_unconfirmed_until_mercado_write',
    promotion_id: promotion?.promotion_id || promotion?.id || null,
    promotion_type: 'SELLER_CAMPAIGN',
    marketplace_item_status: detail.status || null,
    marketplace_original_price: numberOrNull(detail.original_price),
    marketplace_price: basePrice,
    raw_marketplace_item: detail
  };
}

export async function buildSellerCampaignInventoryFallback({
  client,
  promotion,
  startedItems = [],
  pendingItems = [],
  existingCandidateItems = [],
  listingStatus = 'all',
  detailConcurrency = 5,
  discountPercent = 5,
  maxScanItems = 'all'
}) {
  if (!isSellerCampaign(promotion)) {
    return skippedFallbackRow(promotion, '仅 SELLER_CAMPAIGN 自建活动支持库存扫描兜底');
  }
  const childUserId = promotion.child_user_id || client.userId;
  const scan = await client.scanMarketplaceUserItems({
    userId: childUserId,
    status: listingStatus || 'all',
    maxItems: maxScanItems
  });
  const excludedIds = new Set([
    ...startedItems.map(itemIdFromRow),
    ...pendingItems.map(itemIdFromRow)
  ].filter(Boolean));
  const existingCandidateIds = new Set(existingCandidateItems.map(itemIdFromRow).filter(Boolean));
  const detailTargetIds = scan.ids.filter((id) => !excludedIds.has(id) && !existingCandidateIds.has(id));
  const details = await mapLimited(detailTargetIds, normalizeConcurrency(detailConcurrency), async (itemId) => {
    try {
      return { ok: true, itemId, detail: await client.getMarketplaceItem(itemId) };
    } catch (error) {
      return { ok: false, itemId, error: safeError(error) };
    }
  });
  const successfulDetails = details.filter((row) => row?.ok && row.detail).map((row) => row.detail);
  const failedDetails = details.filter((row) => row && !row.ok);
  const candidateRows = buildInventoryFallbackCandidateRows({ itemDetails: successfulDetails, promotion, discountPercent });
  const detailStatus = scan.isFullFetch ? INVENTORY_FALLBACK_READY_STATUS : INVENTORY_FALLBACK_PARTIAL_STATUS;
  return {
    promotion,
    source: INVENTORY_FALLBACK_SOURCE,
    listing_status: listingStatus || 'all',
    scan_total: scan.total,
    scan_saved: scan.saved,
    scan_is_full_fetch: scan.isFullFetch,
    scan_sample_only: scan.sampleOnly,
    scanned_ids: scan.ids.length,
    excluded_started_pending: excludedIds.size,
    existing_candidate_count: existingCandidateIds.size,
    detail_targets: detailTargetIds.length,
    detail_success: successfulDetails.length,
    detail_failed: failedDetails.length,
    fallback_rows: candidateRows,
    added_count: candidateRows.length,
    detail_status: detailStatus,
    is_full_fetch: scan.isFullFetch,
    blocked: false,
    note: `库存扫描兜底生成 ${candidateRows.length} 个候选草案；资格以 Mercado 报名返回为准。`,
    raw: {
      source: INVENTORY_FALLBACK_SOURCE,
      listing_status: listingStatus || 'all',
      scan: scan.rawSummary,
      failed_detail_sample: failedDetails.slice(0, 10)
    }
  };
}

function skippedFallbackRow(promotion, reason) {
  return {
    promotion,
    source: INVENTORY_FALLBACK_SOURCE,
    detail_status: 'skipped',
    blocked: true,
    fallback_rows: [],
    added_count: 0,
    note: reason,
    raw: { source: INVENTORY_FALLBACK_SOURCE, reason }
  };
}

function itemIdFromRow(row) {
  return row?.item_id || row?.id || null;
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function safeError(error) {
  return {
    message: error?.message || String(error),
    status: error?.status,
    body: error?.body ? JSON.stringify(error.body).slice(0, 500) : null
  };
}
