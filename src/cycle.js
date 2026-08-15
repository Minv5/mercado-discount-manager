import { get, nowIso, run } from './db.js';
import { defaultDiscountForPromotion } from './planner.js';
import { promotionDiscountKind } from './promotionDomain.js';

const LEGACY_MAX_DISCOUNT = 10;

export function classifyPromotionType(promotionType) {
  return promotionDiscountKind(promotionType);
}

export function nextDiscountFor({ promotionType, lastDiscount, lastStatus, advanceAfterIncomplete = false, maxDiscount = LEGACY_MAX_DISCOUNT }) {
  const base = defaultDiscountForPromotion(promotionType);
  if (base === null) return null;
  if (!Number.isFinite(Number(lastDiscount))) return base;
  if (lastStatus !== 'completed' && !advanceAfterIncomplete) return Number(lastDiscount);
  return Math.min(normalizedMaximum(maxDiscount), Number(lastDiscount) + 1);
}

export function decideCycleAction({ promotionType, currentDiscount, lastDiscount, lastUpdatedAt, today = new Date(), hasStartedItems, maxDiscount = LEGACY_MAX_DISCOUNT }) {
  const base = defaultDiscountForPromotion(promotionType);
  if (base === null) return { action: 'excluded', discount: null, reason: '该活动类型不参与普通批量折扣流程' };
  const discount = Number(currentDiscount ?? base);
  const priorDiscount = Number(lastDiscount);
  const maximum = normalizedMaximum(maxDiscount);
  const priorReachedMaximum = Number.isFinite(priorDiscount) && priorDiscount >= maximum;
  const priorCompletedBeforeToday = Boolean(lastUpdatedAt) && localDateNumber(lastUpdatedAt) < localDateNumber(today);
  if (priorReachedMaximum && priorCompletedBeforeToday && hasStartedItems) {
    return { action: 'cancel', discount, reason: `折扣已到最高 ${maximum}%，应进入取消阶段` };
  }
  return { action: 'enroll', discount, reason: '按当前周期折扣报名或补报名' };
}

function normalizedMaximum(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(90, Math.max(1, parsed)) : LEGACY_MAX_DISCOUNT;
}

function localDateNumber(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return Number.NaN;
  return date.getFullYear() * 10000 + (date.getMonth() + 1) * 100 + date.getDate();
}

export function getCycleState(accountId, promotionId, promotionType, { childUserId = '', siteId = '', activityRevision = '' } = {}) {
  return get(
    `SELECT * FROM cycle_states
     WHERE account_id = ? AND child_user_id = ? AND site_id = ?
       AND promotion_id = ? AND promotion_type = ? AND activity_revision = ?`,
    [String(accountId), String(childUserId || ''), String(siteId || '').toUpperCase(), promotionId, promotionType, String(activityRevision || '')]
  );
}

export function upsertCycleState({
  accountId,
  childUserId = '',
  siteId = '',
  activityRevision = '',
  promotionId,
  promotionType,
  sellerDiscountPercent,
  officialDiscountPercent,
  status,
  raw,
}) {
  run(
    `INSERT INTO cycle_states
      (account_id, child_user_id, site_id, promotion_id, promotion_type, activity_revision, identity_state,
       seller_discount_percent, official_discount_percent, status, raw_json, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, 'route_scoped', ?, ?, ?, ?, ?)
      ON CONFLICT(account_id, child_user_id, site_id, promotion_id, promotion_type, activity_revision) DO UPDATE SET
        seller_discount_percent = excluded.seller_discount_percent,
        official_discount_percent = excluded.official_discount_percent,
        status = excluded.status,
        raw_json = excluded.raw_json,
        updated_at = excluded.updated_at`,
    [
      String(accountId),
      String(childUserId || ''),
      String(siteId || '').toUpperCase(),
      promotionId,
      promotionType,
      String(activityRevision || ''),
      sellerDiscountPercent ?? null,
      officialDiscountPercent ?? null,
      status,
      raw ? JSON.stringify(raw) : null,
      nowIso()
    ]
  );
}

export function markCycleAfterTask({ accountId, childUserId = '', siteId = '', activityRevision = '', promotionId, promotionType, action, discountPercent, completed }) {
  const kind = classifyPromotionType(promotionType);
  const existing = getCycleState(accountId, promotionId, promotionType, { childUserId, siteId, activityRevision });
  const status = completed ? (action === 'cancel' ? 'cancelled_complete' : 'completed') : 'partial_or_failed';
  upsertCycleState({
    accountId,
    childUserId,
    siteId,
    activityRevision,
    promotionId,
    promotionType,
    sellerDiscountPercent: kind === 'seller' ? discountPercent : existing?.seller_discount_percent,
    officialDiscountPercent: kind === 'official' ? discountPercent : existing?.official_discount_percent,
    status,
    raw: {
      last_action: action,
      completed,
      last_discount_percent: discountPercent,
      updated_at: nowIso()
    }
  });
}
