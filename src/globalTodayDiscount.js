import { nextDiscountFor } from './cycle.js';

const NON_FINAL_STATUSES = new Set(['', 'planned', 'running', 'preparing', 'stopping']);
const INCOMPLETE_FINAL_STATUSES = new Set(['partial_or_failed', 'cancelled', 'canceled']);

export function findLatestEffectiveUpdate(tasks = []) {
  return [...tasks]
    .filter(isEffectiveUpdate)
    .sort((a, b) => effectiveTime(b) - effectiveTime(a) || numericId(b) - numericId(a))[0] || null;
}

export function buildGlobalTodayDiscount({ tasks = [], settings = {}, today = new Date() } = {}) {
  const fallbackSeller = finiteDiscount(settings.sellerDefaultDiscount, 5);
  const fallbackOfficial = finiteDiscount(settings.officialDefaultDiscount, 6);
  const latest = findLatestEffectiveUpdate(tasks);
  if (!latest) {
    return {
      source: 'settings_fallback',
      source_task_id: null,
      source_time: null,
      source_status: null,
      base_seller_discount: fallbackSeller,
      base_official_discount: fallbackOfficial,
      seller_discount: fallbackSeller,
      official_discount: fallbackOfficial,
      same_local_day: null,
      message: '未找到可用更新历史，已使用保存设置。'
    };
  }

  const baseSeller = sellerDiscount(latest);
  const baseOfficial = officialDiscount(latest);
  const sourceTime = latest.updated_at || latest.created_at;
  const sameDay = sameLocalDate(sourceTime, today);
  const sourceBeforeToday = localDateNumber(sourceTime) < localDateNumber(today);
  const status = String(latest.status || '').toLowerCase();
  const advanceAfterIncomplete = sourceBeforeToday && INCOMPLETE_FINAL_STATUSES.has(status);
  const seller = sourceBeforeToday
    ? nextDiscountFor({ promotionType: 'SELLER_CAMPAIGN', lastDiscount: baseSeller, lastStatus: status, advanceAfterIncomplete })
    : baseSeller;
  const official = sourceBeforeToday
    ? nextDiscountFor({ promotionType: 'DEAL', lastDiscount: baseOfficial, lastStatus: status, advanceAfterIncomplete })
    : baseOfficial;

  return {
    source: 'latest_effective_update',
    source_task_id: numericId(latest),
    source_time: sourceTime,
    source_status: status,
    base_seller_discount: baseSeller,
    base_official_discount: baseOfficial,
    seller_discount: seller,
    official_discount: official,
    same_local_day: sameDay,
    message: sameDay
      ? `今日已有更新记录，沿用自建${baseSeller}%、官方${baseOfficial}%。`
      : `以上次有效更新为基准，今日自建${seller}%、官方${official}%。`
  };
}

function isEffectiveUpdate(task) {
  if (String(task?.action || '').toLowerCase() !== 'update') return false;
  if (String(task?.mode || '').toLowerCase() !== 'real') return false;
  if (NON_FINAL_STATUSES.has(String(task?.status || '').toLowerCase())) return false;
  if (!Number.isFinite(sellerDiscount(task)) || !Number.isFinite(officialDiscount(task))) return false;
  if (!Number.isFinite(effectiveTime(task))) return false;
  return ['total_count', 'success_count', 'failed_count', 'skipped_count']
    .some((key) => Number(task?.[key] || 0) > 0);
}

function sellerDiscount(task) {
  return percentage(task?.seller_activity_text ?? task?.seller_discount_percent ?? task?.sellerDiscountPercent);
}

function officialDiscount(task) {
  return percentage(task?.official_activity_text ?? task?.official_discount_percent ?? task?.officialDiscountPercent);
}

function percentage(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : Number.NaN;
  const match = String(value ?? '').match(/\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : Number.NaN;
}

function finiteDiscount(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function effectiveTime(task) {
  const value = task?.updated_at || task?.created_at;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : Number.NaN;
}

function numericId(task) {
  const value = Number(task?.id || 0);
  return Number.isFinite(value) ? value : 0;
}

function sameLocalDate(a, b) {
  return localDateNumber(a) === localDateNumber(b);
}

function localDateNumber(value) {
  const date = new Date(value);
  return date.getFullYear() * 10000 + (date.getMonth() + 1) * 100 + date.getDate();
}
