import { decideCycleAction, nextDiscountFor } from './cycle.js';
import { promotionKey } from './planner.js';
import { ordinaryPromotions } from './promotionDomain.js';

const INCOMPLETE_FINAL_STATUSES = new Set(['partial_or_failed', 'cancelled', 'canceled']);

export function decideToday({
  promotions,
  cycleStatesByPromotion = new Map(),
  startedCountsByPromotion = new Map(),
  globalCycle = null,
  sellerMaxDiscount = 10,
  officialMaxDiscount = 10,
  today = new Date()
}) {
  const ordinary = ordinaryPromotions(promotions);
  const configuredSellerMaximum = finiteMaximum(globalCycle?.seller_max_discount ?? sellerMaxDiscount, 10);
  const configuredOfficialMaximum = finiteMaximum(globalCycle?.official_max_discount ?? officialMaxDiscount, 10);
  const hasGlobalCycle = ['latest_effective_discount', 'latest_effective_update'].includes(globalCycle?.source)
    && Boolean(globalCycle?.source_time);
  const globalCancelCycle = hasGlobalCycle
    && Number(globalCycle.base_seller_discount) >= configuredSellerMaximum
    && Number(globalCycle.base_official_discount) >= configuredOfficialMaximum
    && localDateNumber(globalCycle.source_time) < localDateNumber(today)
    && ordinary.some((promotion) => Number(startedCountsByPromotion.get(promotionKey(promotion)) || 0) > 0);
  const rows = ordinary.map((promotion) => {
    const key = promotionKey(promotion);
    const state = cycleStatesByPromotion.get(key) || null;
    const startedCount = Number(startedCountsByPromotion.get(key) || 0);
    const lastDiscount = state?.seller_discount_percent ?? state?.official_discount_percent;
    const activityDiscount = nextDiscountFor({
      promotionType: promotion.promotion_type,
      lastDiscount,
      lastStatus: state?.status === 'completed' ? 'completed' : state?.status,
      advanceAfterIncomplete: Boolean(INCOMPLETE_FINAL_STATUSES.has(state?.status) && state?.updated_at && !sameLocalDate(state.updated_at, today)),
      maxDiscount: String(promotion.promotion_type || '').toUpperCase() === 'SELLER_CAMPAIGN'
        ? configuredSellerMaximum : configuredOfficialMaximum
    });
    const cycleDecision = decideCycleAction({
      promotionType: promotion.promotion_type,
      currentDiscount: activityDiscount,
      lastDiscount,
      lastUpdatedAt: state?.updated_at,
      today,
      hasStartedItems: startedCount > 0,
      maxDiscount: String(promotion.promotion_type || '').toUpperCase() === 'SELLER_CAMPAIGN'
        ? configuredSellerMaximum : configuredOfficialMaximum
    });
    const discount = hasGlobalCycle
      ? Number(String(promotion.promotion_type || '').toUpperCase() === 'SELLER_CAMPAIGN'
        ? globalCycle.seller_discount : globalCycle.official_discount)
      : activityDiscount;
    const action = hasGlobalCycle
      ? globalCancelCycle ? 'cancel' : 'update'
      : cycleDecision.action === 'cancel' ? 'cancel' : startedCount > 0 ? 'update' : 'enroll';
    return {
      promotion,
      state,
      startedCount,
      action,
      discount,
      completedToday: stateCompletedToday(state, today, action),
      incompleteToday: stateIncompleteToday(state, today),
      reason: hasGlobalCycle
        ? globalCancelCycle
          ? `上一有效真实报名或更新已达到自建${configuredSellerMaximum}%/官方${configuredOfficialMaximum}%，且当前仍有 started 商品，本次应批量取消折扣`
          : '按上一有效真实报名或更新批次推进，本次应批量更新折扣'
        : cycleDecision.action === 'cancel'
        ? '最近完整折扣已到设置上限，本次应批量取消折扣'
        : startedCount > 0
          ? '有 started 商品，本次应批量更新折扣'
          : '新周期: 批量报折扣'
    };
  });

  const incompleteRows = rows.filter((row) => row.incompleteToday);
  const activeRows = rows.filter((row) => !row.completedToday);
  const candidates = activeRows.length ? activeRows : rows;
  const priorityAction = chooseAction(candidates);
  const selectedRows = rows.filter((row) => row.action === priorityAction);
  const allSelectedCompleted = selectedRows.length > 0 && selectedRows.every((row) => row.completedToday);
  const maxDiscount = selectedRows.reduce((max, row) => Math.max(max, Number(row.discount || 0)), 0);

  return {
    today_action: allSelectedCompleted ? 'completed' : priorityAction,
    action: priorityAction,
    discount: maxDiscount || null,
    already_completed: allSelectedCompleted,
    needs_resume: incompleteRows.length > 0,
    promotions_total: promotions.length,
    selected_promotions: selectedRows.length,
    rows: rows.map((row) => ({
      site_id: row.promotion.site_id,
      promotion_id: row.promotion.promotion_id,
      promotion_type: row.promotion.promotion_type,
      name: row.promotion.name,
      action: row.action,
      discount: row.discount,
      started_count: row.startedCount,
      completed_today: row.completedToday,
      incomplete_today: row.incompleteToday,
      reason: row.reason,
      cycle_status: row.state?.status || null,
      cycle_updated_at: row.state?.updated_at || null
    })),
    reason: summaryReason({ allSelectedCompleted, incompleteRows, priorityAction, maxDiscount })
  };
}

function chooseAction(rows) {
  if (rows.some((row) => row.action === 'cancel')) return 'cancel';
  if (rows.some((row) => row.action === 'update')) return 'update';
  return 'enroll';
}

function stateCompletedToday(state, today, action) {
  if (!state?.updated_at) return false;
  if (!sameLocalDate(state.updated_at, today)) return false;
  if (state.status === 'cancelled_complete') return true;
  if (action === 'cancel') return state.status === 'cancelled_complete';
  return state.status === 'completed';
}

function stateIncompleteToday(state, today) {
  return Boolean(state?.updated_at && sameLocalDate(state.updated_at, today) && INCOMPLETE_FINAL_STATUSES.has(state.status));
}

function sameLocalDate(value, today) {
  const a = new Date(value);
  const b = new Date(today);
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function localDateNumber(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return Number.NaN;
  return date.getFullYear() * 10000 + (date.getMonth() + 1) * 100 + date.getDate();
}

function summaryReason({ allSelectedCompleted, incompleteRows, priorityAction, maxDiscount }) {
  if (allSelectedCompleted) return '今天已完整执行，默认不重复提交。';
  if (incompleteRows.length) return '今天存在未完成任务，建议继续/补跑当前动作。';
  if (priorityAction === 'cancel') return '最近完整折扣已到设置上限，本次应批量取消折扣。';
  if (priorityAction === 'update') return `以上次完整折扣为基准递增，本次应批量更新折扣，建议折扣 ${maxDiscount}%。`;
  return `新周期: 批量报折扣，建议折扣 ${maxDiscount}%。`;
}

function finiteMaximum(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(90, Math.max(1, parsed)) : fallback;
}
