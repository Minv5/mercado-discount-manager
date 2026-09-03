import { decideCycleAction, nextDiscountFor } from './cycle.js';
import { promotionKey } from './planner.js';
import { ordinaryPromotions } from './promotionDomain.js';

const INCOMPLETE_FINAL_STATUSES = new Set(['partial_or_failed', 'cancelled', 'canceled']);

export function decideToday({
  promotions,
  cycleStatesByPromotion = new Map(),
  startedCountsByPromotion = new Map(),
  candidateCountsByPromotion = new Map(),
  repriceCountsByPromotion = new Map(),
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
    const repricePendingCount = Number(repriceCountsByPromotion.get(key) || 0);
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
    const scheduledDiscount = hasGlobalCycle
      ? Number(String(promotion.promotion_type || '').toUpperCase() === 'SELLER_CAMPAIGN'
        ? globalCycle.seller_discount : globalCycle.official_discount)
      : activityDiscount;
    const repricingRequired = repricePendingCount > 0 && startedCount > 0 && !globalCancelCycle;
    const discount = repricingRequired && Number.isFinite(Number(lastDiscount))
      ? Number(lastDiscount)
      : scheduledDiscount;
    const isSellerCampaign = String(promotion.promotion_type || '').toUpperCase() === 'SELLER_CAMPAIGN';
    const freshSellerCampaign = isSellerCampaign
      && startedCount === 0
      && !globalCancelCycle;
    const incompleteLastAction = cycleLastAction(state);
    const incompleteToday = stateIncompleteToday(state, today);
    const resumeIncompleteCancel = incompleteToday
      && startedCount > 0
      && incompleteLastAction === 'cancel';
    const candidateCount = Number(candidateCountsByPromotion.get(key) || 0);
    const action = freshSellerCampaign
      ? 'enroll'
      : resumeIncompleteCancel
        ? 'cancel'
        : startedCount === 0 && !globalCancelCycle
          ? 'enroll'
          : hasGlobalCycle
            ? globalCancelCycle ? 'cancel' : repricingRequired ? 'update' : candidateCount > 0 ? 'enroll' : 'update'
            : cycleDecision.action === 'cancel' ? 'cancel'
              : repricingRequired ? 'update'
                : candidateCount > 0 ? 'enroll'
                : startedCount > 0 ? 'update' : 'enroll';
    const baseCompletedToday = stateCompletedToday(state, today, action);
    return {
      promotion,
      state,
      startedCount,
      candidateCount,
      repricePendingCount,
      repricingRequired,
      baseCompletedToday,
      action,
      discount,
      completedToday: baseCompletedToday && !repricingRequired,
      incompleteToday,
      reason: resumeIncompleteCancel
        ? `今天取消未完成（${state?.status}），本次继续取消剩余商品`
        : repricingRequired
          ? `商品基础售价已变化，${repricePendingCount} 个活动商品价格待按现有 ${discount}% 重算`
        : hasGlobalCycle
          ? globalCancelCycle
            ? `上一有效真实报名或更新已达到自建${configuredSellerMaximum}%/官方${configuredOfficialMaximum}%，且当前仍有 started 商品，本次应批量取消折扣`
            : '按上一有效真实报名或更新批次推进，本次应批量更新折扣'
          : cycleDecision.action === 'cancel'
            ? '最近完整折扣已到设置上限，本次应批量取消折扣'
            : candidateCount > 0
              ? '仍有候选商品未报名，本次应批量报名'
              : startedCount > 0
                ? '候选商品已全部报名，本次应批量更新折扣'
                : '新周期: 批量报折扣'
    };
  });

  const incompleteRows = rows.filter((row) => row.incompleteToday);
  const activeRows = rows.filter((row) => !row.completedToday);
  const candidates = activeRows.length ? activeRows : rows;
  const priorityAction = chooseAction(candidates);
  const selectedRows = rows.filter((row) => row.action === priorityAction);
  const selectedOutstanding = selectedRows.filter((row) => !row.completedToday);
  const repricePendingOnly = priorityAction === 'update'
    && selectedOutstanding.length > 0
    && selectedOutstanding.every((row) => row.repricingRequired && row.baseCompletedToday);
  const allSelectedCompleted = selectedRows.length > 0 && selectedRows.every((row) => row.completedToday);
  const maxDiscount = selectedRows.reduce((max, row) => Math.max(max, Number(row.discount || 0)), 0);

  return {
    today_action: allSelectedCompleted ? 'completed' : priorityAction,
    action: priorityAction,
    discount: maxDiscount || null,
    already_completed: allSelectedCompleted,
    needs_resume: incompleteRows.length > 0,
    reprice_pending_count: rows.reduce((sum, row) => sum + row.repricePendingCount, 0),
    reprice_pending_only: repricePendingOnly,
    seller_discount: selectedRows.find((row) => String(row.promotion.promotion_type || '').toUpperCase() === 'SELLER_CAMPAIGN')?.discount ?? null,
    official_discount: selectedRows.find((row) => String(row.promotion.promotion_type || '').toUpperCase() === 'DEAL')?.discount ?? null,
    promotions_total: promotions.length,
    selected_promotions: selectedRows.length,
    rows: rows.map((row) => ({
      account_id: row.promotion.account_id,
      child_user_id: row.promotion.child_user_id,
      site_id: row.promotion.site_id,
      promotion_id: row.promotion.promotion_id,
      promotion_type: row.promotion.promotion_type,
      name: row.promotion.name,
      action: row.action,
      discount: row.discount,
      started_count: row.startedCount,
      candidate_count: row.candidateCount,
      reprice_pending_count: row.repricePendingCount,
      reprice_required: row.repricingRequired,
      completed_today: row.completedToday,
      incomplete_today: row.incompleteToday,
      reason: row.reason,
      cycle_status: row.state?.status || null,
      cycle_updated_at: row.state?.updated_at || null
    })),
    reason: summaryReason({ allSelectedCompleted, incompleteRows, priorityAction, maxDiscount, repricePendingOnly })
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

function summaryReason({ allSelectedCompleted, incompleteRows, priorityAction, maxDiscount, repricePendingOnly = false }) {
  if (allSelectedCompleted) return '今天已完整执行，默认不重复提交。';
  if (incompleteRows.length) return '今天存在未完成任务，建议继续/补跑当前动作。';
  if (repricePendingOnly) return '商品基础售价已变化，本次只更新待重算的活动商品价格。';
  if (priorityAction === 'cancel') return '最近完整折扣已到设置上限，本次应批量取消折扣。';
  if (priorityAction === 'update') return `以上次完整折扣为基准递增，本次应批量更新折扣，建议折扣 ${maxDiscount}%。`;
  return `新周期: 批量报折扣，建议折扣 ${maxDiscount}%。`;
}

function finiteMaximum(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(90, Math.max(1, parsed)) : fallback;
}

function cycleLastAction(state) {
  if (!state) return '';
  const raw = state.raw && typeof state.raw === 'object'
    ? state.raw
    : typeof state.raw_json === 'string' && state.raw_json
      ? safeParse(state.raw_json)
      : null;
  return String(raw?.last_action || '').toLowerCase();
}

function safeParse(value) {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function actionDisplayName(action) {
  return { enroll: '报名', update: '更新', cancel: '取消' }[String(action || '')] || String(action || '');
}
