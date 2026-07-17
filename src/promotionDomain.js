export const PROMOTION_BUCKETS = Object.freeze({
  seller: 'seller',
  official: 'official',
  smart: 'smart',
  lightning: 'lightning',
  other: 'other',
});

export function normalizePromotionType(value) {
  return String(value || '').trim().toUpperCase();
}

export function promotionBucket(value) {
  const type = normalizePromotionType(value);
  if (type === 'SELLER_CAMPAIGN') return PROMOTION_BUCKETS.seller;
  if (type === 'DEAL') return PROMOTION_BUCKETS.official;
  if (type === 'SMART') return PROMOTION_BUCKETS.smart;
  if (type === 'LIGHTNING') return PROMOTION_BUCKETS.lightning;
  return PROMOTION_BUCKETS.other;
}

export function isOrdinaryPromotion(value) {
  const bucket = promotionBucket(value?.promotion_type ?? value?.promotionType ?? value);
  return bucket === PROMOTION_BUCKETS.seller || bucket === PROMOTION_BUCKETS.official;
}

export function ordinaryPromotions(promotions = []) {
  return promotions.filter(isOrdinaryPromotion);
}

export function partitionPromotions(promotions = []) {
  const result = Object.fromEntries(Object.values(PROMOTION_BUCKETS).map((bucket) => [bucket, []]));
  for (const promotion of promotions) result[promotionBucket(promotion?.promotion_type ?? promotion?.promotionType)].push(promotion);
  return result;
}

export function promotionBucketCounts(promotions = []) {
  return Object.fromEntries(
    Object.entries(partitionPromotions(promotions)).map(([bucket, rows]) => [bucket, rows.length]),
  );
}

export function promotionDiscountKind(value) {
  const bucket = promotionBucket(value);
  return bucket === PROMOTION_BUCKETS.seller || bucket === PROMOTION_BUCKETS.official ? bucket : null;
}
