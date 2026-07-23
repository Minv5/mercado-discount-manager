export function shanghaiDayStartIso(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return new Date(`${values.year}-${values.month}-${values.day}T00:00:00+08:00`).toISOString();
}

export function applyWriteRepeatGuards(plan = {}, guards = [], action = '') {
  const guardByRelation = new Map();
  for (const guard of guards || []) {
    const key = repeatGuardKey(guard);
    if (key) guardByRelation.set(key, guard);
  }
  let guarded = 0;
  const rows = (plan.rows || []).map((row) => {
    if (String(row.status || '') !== 'planned') return row;
    const guard = guardByRelation.get(repeatGuardKey({
      promotion_id: plan.promotionId || plan.promotion_id,
      promotion_type: plan.promotionType || plan.promotion_type,
      item_id: row.item?.item_id,
    }));
    if (!guard || !guardApplies(guard, row, action)) return row;
    guarded += 1;
    const pending = String(guard.status || '') === 'pending_verification';
    return {
      ...row,
      status: 'skipped',
      reason: pending
        ? '此前写入请求已成功，平台状态仍待确认，本次不重复提交'
        : '该商品今天已执行且未达到目标，本次不重复提交',
      repeat_guard_status: String(guard.status || ''),
    };
  });
  const planned = rows.filter((row) => String(row.status || '') === 'planned').length;
  const skipped = rows.length - planned;
  return {
    ...plan,
    rows,
    total: rows.length,
    planned,
    skipped,
    repeat_guard_skipped: guarded,
  };
}

function guardApplies(guard, row, action) {
  const status = String(guard.status || '').toLowerCase();
  if (status === 'pending_verification') return true;
  if (!['failed', 'live_still_started'].includes(status)) return false;
  if (String(action || '').toLowerCase() === 'cancel') return true;
  const previousPrice = Number(guard.deal_price);
  const nextPrice = Number(row.deal_price);
  return Number.isFinite(previousPrice) && Number.isFinite(nextPrice)
    && Math.round(previousPrice * 100) === Math.round(nextPrice * 100);
}

function repeatGuardKey(value = {}) {
  const promotionId = String(value.promotion_id || value.promotionId || '').trim();
  const promotionType = String(value.promotion_type || value.promotionType || '').trim().toUpperCase();
  const itemId = String(value.item_id || value.itemId || '').trim();
  return promotionId && promotionType && itemId ? `${promotionId}|${promotionType}|${itemId}` : '';
}
