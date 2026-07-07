import { decideCycleAction, nextDiscountFor } from './cycle.js';
import { promotionKey } from './planner.js';

export function decideToday({ promotions, cycleStatesByPromotion = new Map(), startedCountsByPromotion = new Map(), today = new Date() }) {
  const rows = promotions.map((promotion) => {
    const key = promotionKey(promotion);
    const state = cycleStatesByPromotion.get(key) || null;
    const startedCount = Number(startedCountsByPromotion.get(key) || 0);
    const lastDiscount = state?.seller_discount_percent ?? state?.official_discount_percent;
    const discount = nextDiscountFor({
      promotionType: promotion.promotion_type,
      lastDiscount,
      lastStatus: state?.status === 'completed' ? 'completed' : state?.status
    });
    const cycleDecision = decideCycleAction({ promotionType: promotion.promotion_type, currentDiscount: discount, hasStartedItems: startedCount > 0 });
    const action = cycleDecision.action === 'cancel' ? 'cancel' : startedCount > 0 ? 'update' : 'enroll';
    return {
      promotion,
      state,
      startedCount,
      action,
      discount,
      completedToday: stateCompletedToday(state, today, action),
      incompleteToday: stateIncompleteToday(state, today),
      reason: cycleDecision.action === 'cancel'
        ? '最近完整折扣已到 10，本次应批量取消折扣'
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
  if (action === 'cancel') return state.status === 'cancelled_complete';
  return state.status === 'completed';
}

function stateIncompleteToday(state, today) {
  return Boolean(state?.updated_at && sameLocalDate(state.updated_at, today) && state.status === 'partial_or_failed');
}

function sameLocalDate(value, today) {
  const a = new Date(value);
  const b = new Date(today);
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function summaryReason({ allSelectedCompleted, incompleteRows, priorityAction, maxDiscount }) {
  if (allSelectedCompleted) return '今天已完整执行，默认不重复提交。';
  if (incompleteRows.length) return '今天存在未完成任务，建议继续/补跑当前动作。';
  if (priorityAction === 'cancel') return '最近完整折扣已到 10，本次应批量取消折扣。';
  if (priorityAction === 'update') return `以上次完整折扣为基准递增，本次应批量更新折扣，建议折扣 ${maxDiscount}%。`;
  return `新周期: 批量报折扣，建议折扣 ${maxDiscount}%。`;
}
