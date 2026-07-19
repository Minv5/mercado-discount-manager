import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const TERMINAL_GROUP_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
const AUTO_ACTIONS = new Set(['auto', 'automatic']);
const DEFAULT_TTL_MS = 5 * 60 * 1000;

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]));
}

function stableHash(value) {
  return crypto.createHash('sha256').update(JSON.stringify(stable(value))).digest('hex').toUpperCase();
}

function normalizeText(value) {
  return String(value || '')
    .normalize('NFKC')
    .replace(/[\u200B-\u200D\uFEFF]/g, '')
    .trim()
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase('en-US');
}

function normalizedTextList(values) {
  return [...new Set((Array.isArray(values) ? values : [])
    .map(normalizeText)
    .filter(Boolean))]
    .sort((left, right) => left.localeCompare(right));
}

function normalizedAccountIds(request = {}) {
  const values = request.accountIds ?? request.account_ids ?? [];
  return [...new Set((Array.isArray(values) ? values : [])
    .map((value) => String(value || '').trim())
    .filter(Boolean))]
    .sort((left, right) => left.localeCompare(right));
}

function normalizedSiteScope(filters = {}) {
  const direct = String(filters.siteId ?? filters.site_id ?? '').trim().toUpperCase();
  if (direct) return direct;
  const sites = [...new Set((filters.siteIds ?? filters.site_ids ?? [])
    .map((value) => String(value || '').trim().toUpperCase())
    .filter(Boolean))]
    .sort((left, right) => left.localeCompare(right));
  return sites.join(',');
}

function groupRequest(group = {}) {
  const request = group.request && typeof group.request === 'object' ? group.request : {};
  if ((request.accountIds || request.account_ids)?.length) return request;
  const accountIds = (group.children || [])
    .map((child) => child.account_id || child.request_summary?.accountId)
    .filter(Boolean);
  return { ...request, accountIds };
}

function groupFinishedAt(group = {}) {
  return group.finished_at || group.completed_at || group.updated_at || null;
}

function groupIsReal(group = {}) {
  const request = group.request || {};
  const mode = String(group.mode || request.mode || 'real').toLowerCase();
  return !request.prepareOnly
    && !request.prepare_only
    && !request.dryRun
    && !request.dry_run
    && !['fake', 'dry-run', 'dry_run', 'preview', 'prepare'].includes(mode);
}

function actionOf(value = {}) {
  return String(value.action || value.resolved_action || value.request?.action || value.request?.requested_action || '').trim().toLowerCase();
}

function completedSummary(group = {}) {
  const source = group.result || group.summary || group.result_summary || {};
  const success = Math.max(0, Number(source.success ?? source.success_count ?? 0));
  const failed = Math.max(0, Number(source.failed ?? source.failed_count ?? 0));
  const skipped = Math.max(0, Number(source.skipped ?? source.skipped_count ?? 0));
  const total = Math.max(Number(source.total ?? source.total_count ?? 0), success + failed + skipped);
  const request = group.request || {};
  const scope = {
    seller_discount_percent: request.sellerDiscountPercent ?? request.seller_discount_percent ?? null,
    official_discount_percent: request.officialDiscountPercent ?? request.official_discount_percent ?? null,
  };
  return {
    group_id: String(group.id || group.group_id || ''),
    action: actionOf(group),
    status: String(group.status || ''),
    finished_at: groupFinishedAt(group),
    total,
    success,
    failed,
    skipped,
    scope,
    result: { total, success, failed, skipped },
  };
}

function gateError(message, code, details = null, status = 409) {
  const error = new Error(message);
  error.code = code;
  error.status = status;
  error.details = details;
  return error;
}

function sameBinding(left = {}, right = {}) {
  return stableHash(left) === stableHash(right);
}

function safeId(value) {
  return String(value || '').replace(/[^A-Za-z0-9_.-]/g, '');
}

function confirmationError(code) {
  const messages = {
    SAME_DAY_CONFIRMATION_INVALID: '本次二次确认凭证无效，请重新操作。',
    SAME_DAY_CONFIRMATION_MISMATCH: '本次二次确认与当前店铺、范围或动作不一致，请重新确认。',
    SAME_DAY_CONFIRMATION_USED: '本次二次确认已经使用，不能重复提交。',
    SAME_DAY_CONFIRMATION_CANCELLED: '本次二次确认已取消，未创建准备任务。',
    SAME_DAY_CONFIRMATION_EXPIRED: '本次二次确认已过期，请重新确认。',
  };
  return gateError(messages[code] || messages.SAME_DAY_CONFIRMATION_INVALID, code);
}

export function businessDateInShanghai(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(date);
  const mapped = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${mapped.year}-${mapped.month}-${mapped.day}`;
}

export function executionRequestScope(request = {}) {
  const filters = request.filters && typeof request.filters === 'object' ? request.filters : {};
  return {
    account_ids: normalizedAccountIds(request),
    site_id: normalizedSiteScope(filters),
    seller_activity_names: normalizedTextList(filters.sellerActivityNames ?? filters.seller_activity_names),
    official_activity_names: normalizedTextList(filters.officialActivityNames ?? filters.official_activity_names),
    exclude_seller: Boolean(filters.excludeSeller ?? filters.exclude_seller),
    exclude_official: Boolean(filters.excludeOfficial ?? filters.exclude_official),
  };
}

export function executionScopeKey(scopeOrRequest = {}) {
  const scope = Array.isArray(scopeOrRequest.account_ids)
    ? scopeOrRequest
    : executionRequestScope(scopeOrRequest);
  return JSON.stringify([
    normalizedTextList(scope.account_ids),
    String(scope.site_id || '').trim().toUpperCase(),
    normalizedTextList(scope.seller_activity_names),
    normalizedTextList(scope.official_activity_names),
    Boolean(scope.exclude_seller),
    Boolean(scope.exclude_official),
  ]);
}

export function findSameDayTerminalGroup(groups = [], request = {}, now = new Date()) {
  const businessDate = businessDateInShanghai(now);
  const wantedScope = executionScopeKey(executionRequestScope(request));
  return (groups || [])
    .filter((group) => TERMINAL_GROUP_STATUSES.has(String(group?.status || '').toLowerCase()))
    .filter(groupIsReal)
    .filter((group) => businessDateInShanghai(groupFinishedAt(group)) === businessDate)
    .filter((group) => executionScopeKey(executionRequestScope(groupRequest(group))) === wantedScope)
    .sort((left, right) => String(groupFinishedAt(right) || '').localeCompare(String(groupFinishedAt(left) || '')))[0] || null;
}

export function createSameDayConfirmationStore({ stateDir, now = () => new Date().toISOString(), ttlMs = DEFAULT_TTL_MS }) {
  function statePath(id) {
    return path.join(stateDir, `${safeId(id)}.json`);
  }

  function persist(record) {
    fs.mkdirSync(stateDir, { recursive: true });
    record.updated_at = now();
    const target = statePath(record.id);
    const temporary = `${target}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(clone(record)), 'utf8');
    fs.renameSync(temporary, target);
    return record;
  }

  function load(id) {
    const target = statePath(id);
    if (!fs.existsSync(target)) return null;
    try {
      const record = JSON.parse(fs.readFileSync(target, 'utf8'));
      return record && String(record.id || '') === String(id || '') ? record : null;
    } catch {
      return null;
    }
  }

  function loadAll() {
    fs.mkdirSync(stateDir, { recursive: true });
    return fs.readdirSync(stateDir)
      .filter((name) => name.endsWith('.json'))
      .map((name) => load(name.slice(0, -5)))
      .filter(Boolean)
      .sort((left, right) => String(left.created_at || '').localeCompare(String(right.created_at || '')));
  }

  function splitToken(token) {
    const [id, secret, extra] = String(token || '').split('.');
    if (!id || !secret || extra !== undefined) throw confirmationError('SAME_DAY_CONFIRMATION_INVALID');
    return { id: safeId(id), secret };
  }

  function verifyToken(record, secret) {
    if (!record?.token_hash) throw confirmationError('SAME_DAY_CONFIRMATION_INVALID');
    const actual = Buffer.from(crypto.createHash('sha256').update(secret).digest('hex'), 'utf8');
    const expected = Buffer.from(String(record.token_hash), 'utf8');
    if (actual.length !== expected.length || !crypto.timingSafeEqual(actual, expected)) {
      throw confirmationError('SAME_DAY_CONFIRMATION_INVALID');
    }
  }

  function issue(binding, completed) {
    const issuedAt = now();
    const id = `same-day-${Date.now()}-${crypto.randomBytes(6).toString('hex')}`;
    const secret = crypto.randomBytes(24).toString('base64url');
    const token = `${id}.${secret}`;
    const record = {
      id,
      state: 'issued',
      binding: clone(binding),
      binding_hash: stableHash(binding),
      completed: clone(completed),
      token_hash: crypto.createHash('sha256').update(secret).digest('hex'),
      created_at: issuedAt,
      updated_at: issuedAt,
      expires_at: new Date(new Date(issuedAt).getTime() + ttlMs).toISOString(),
      events: [{ type: 'warning_issued', at: issuedAt }],
    };
    persist(record);
    return { token, record };
  }

  function consume(token, binding) {
    const { id, secret } = splitToken(token);
    const record = load(id);
    verifyToken(record, secret);
    if (record.state === 'consumed') throw confirmationError('SAME_DAY_CONFIRMATION_USED');
    if (record.state === 'cancelled') throw confirmationError('SAME_DAY_CONFIRMATION_CANCELLED');
    if (record.state === 'expired') throw confirmationError('SAME_DAY_CONFIRMATION_EXPIRED');
    if (new Date(record.expires_at).getTime() <= new Date(now()).getTime()) {
      record.state = 'expired';
      persist(record);
      throw confirmationError('SAME_DAY_CONFIRMATION_EXPIRED');
    }
    if (!sameBinding(record.binding, binding)) throw confirmationError('SAME_DAY_CONFIRMATION_MISMATCH');
    record.state = 'consumed';
    record.consumed_at = now();
    record.events.push({ type: 'accepted', at: record.consumed_at });
    return persist(record);
  }

  function cancel(token) {
    const { id, secret } = splitToken(token);
    const record = load(id);
    verifyToken(record, secret);
    if (record.state === 'consumed') throw confirmationError('SAME_DAY_CONFIRMATION_USED');
    if (record.state === 'cancelled') return record;
    if (new Date(record.expires_at).getTime() <= new Date(now()).getTime()) {
      record.state = 'expired';
      persist(record);
      throw confirmationError('SAME_DAY_CONFIRMATION_EXPIRED');
    }
    record.state = 'cancelled';
    record.cancelled_at = now();
    record.events.push({ type: 'cancelled', at: record.cancelled_at });
    return persist(record);
  }

  return { cancel, consume, issue, load, loadAll, statePath };
}

export function sameDayCompletionGate({
  groups = [], request = {}, confirmationStore, now = () => new Date().toISOString(), deferManualConfirmation = false,
}) {
  const timestamp = typeof now === 'function' ? now() : now;
  const completedGroup = findSameDayTerminalGroup(groups, request, timestamp);
  if (!completedGroup) return { allowed: true, confirmed: false, completed: null, binding: null };

  const completed = completedSummary(completedGroup);
  const requestedAction = actionOf(request);
  if (AUTO_ACTIONS.has(requestedAction)) {
    throw gateError('今天当前范围已有真实任务完成，自动模式不会重复准备。', 'TODAY_COMPLETED', { completed });
  }

  const scope = executionRequestScope(request);
  const binding = {
    client_submission_id: String(request.client_submission_id || ''),
    scope_key: executionScopeKey(scope),
    scope_hash: stableHash(scope),
    requested_action: requestedAction,
    completed_group_id: completed.group_id,
    business_date: businessDateInShanghai(timestamp),
  };
  if (deferManualConfirmation) {
    return {
      allowed: true,
      confirmed: false,
      completed,
      binding,
      warning: {
        same_action: completed.action === requestedAction,
        completed,
      },
    };
  }
  const token = request.same_day_confirmation_token || request.sameDayConfirmationToken || '';
  if (token) {
    confirmationStore.consume(token, binding);
    return { allowed: true, confirmed: true, completed, binding };
  }

  const issued = confirmationStore.issue(binding, completed);
  throw gateError('今天当前范围已执行过真实任务，请确认是否继续另一项真实操作。', 'CONFIRM_SAME_DAY_ACTION', {
    confirmation_token: issued.token,
    expires_at: issued.record.expires_at,
    same_action: completed.action === requestedAction,
    completed,
  });
}
