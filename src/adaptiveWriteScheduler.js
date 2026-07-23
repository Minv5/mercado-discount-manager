const WRITE_PROFILE_BASE = Object.freeze({
  minGlobal: 10,
  successWindow: 40,
  overloadDecreaseStep: 16,
  defaultRateLimitCooldownMs: 5_000,
  defaultTransientCooldownMs: 2_000,
});

export const ADAPTIVE_WRITE_ACTION_PROFILES = Object.freeze({
  cancel: Object.freeze({
    ...WRITE_PROFILE_BASE,
    initialGlobal: 160,
    maxGlobal: 160,
    perRoute: 54,
  }),
  enroll: Object.freeze({
    ...WRITE_PROFILE_BASE,
    initialGlobal: 160,
    maxGlobal: 160,
    perRoute: 28,
    successWindow: 1000,
    defaultRateLimitCooldownMs: 15_000,
  }),
  update: Object.freeze({
    ...WRITE_PROFILE_BASE,
    initialGlobal: 128,
    maxGlobal: 128,
    perRoute: 28,
  }),
});

export const ADAPTIVE_WRITE_PROFILE = ADAPTIVE_WRITE_ACTION_PROFILES.enroll;

export function adaptiveWriteProfileForAction(action, value) {
  const normalizedAction = String(action || '').toLowerCase();
  const source = ADAPTIVE_WRITE_ACTION_PROFILES[normalizedAction] || ADAPTIVE_WRITE_PROFILE;
  return profileForLimit(source, value);
}

export function adaptiveWriteProfileForLimit(value) {
  return profileForLimit(ADAPTIVE_WRITE_PROFILE, value);
}

function profileForLimit(source, value) {
  const requested = Number(value);
  const maxGlobal = Number.isFinite(requested)
    ? Math.min(source.maxGlobal, Math.max(1, Math.floor(requested)))
    : source.maxGlobal;
  return {
    ...source,
    initialGlobal: maxGlobal,
    perRoute: Math.min(source.perRoute, maxGlobal),
    minGlobal: Math.min(source.minGlobal, maxGlobal),
    maxGlobal,
  };
}

export function createAdaptiveWriteScheduler(options = {}) {
  const profile = { ...ADAPTIVE_WRITE_PROFILE, ...(options.profile || {}) };
  const now = options.now || (() => Date.now());
  const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  let limit = clamp(profile.initialGlobal, profile.minGlobal, profile.maxGlobal);
  let active = 0;
  let maxActive = 0;
  let stableSuccesses = 0;
  let cooldownUntil = 0;
  let decreaseAllowedAt = 0;
  let overloadCount = 0;
  let rateLimitCount = 0;
  let networkErrorCount = 0;
  let serviceErrorCount = 0;
  let timeoutErrorCount = 0;
  let lastOverloadKind = null;
  const routeActive = new Map();
  const routePeaks = new Map();
  const queues = new Map();
  const routeOrder = [];
  let routeCursor = 0;

  function snapshot() {
    return {
      limit,
      active,
      maxActive,
      cooldown_until: cooldownUntil ? new Date(cooldownUntil).toISOString() : null,
      route_active: Object.fromEntries(routeActive),
      route_peaks: Object.fromEntries(routePeaks),
      queued: [...queues.values()].reduce((sum, rows) => sum + rows.length, 0),
      overload_count: overloadCount,
      rate_limit_count: rateLimitCount,
      network_error_count: networkErrorCount,
      service_error_count: serviceErrorCount,
      timeout_error_count: timeoutErrorCount,
      last_overload_kind: lastOverloadKind,
    };
  }

  function emit() {
    options.onStateChange?.(snapshot());
  }

  function routeKey(meta = {}) {
    return [String(meta.accountId || meta.account_id || ''), String(meta.siteId || meta.site_id || '').toUpperCase()].join('|');
  }

  function schedule(fn, meta = {}) {
    const key = routeKey(meta);
    if (!queues.has(key)) {
      queues.set(key, []);
      routeOrder.push(key);
    }
    return new Promise((resolve, reject) => {
      queues.get(key).push({ fn, meta, resolve, reject });
      drain();
    });
  }

  function nextRunnable() {
    if (!routeOrder.length) return null;
    for (let offset = 0; offset < routeOrder.length; offset += 1) {
      const index = (routeCursor + offset) % routeOrder.length;
      const key = routeOrder[index];
      const queue = queues.get(key) || [];
      if (!queue.length || Number(routeActive.get(key) || 0) >= profile.perRoute) continue;
      routeCursor = (index + 1) % routeOrder.length;
      return { key, item: queue.shift() };
    }
    return null;
  }

  function drain() {
    if (now() < cooldownUntil) {
      const timer = setTimeout(drain, Math.max(1, cooldownUntil - now()));
      timer.unref?.();
      return;
    }
    while (active < limit) {
      const next = nextRunnable();
      if (!next) break;
      run(next.key, next.item);
    }
    emit();
  }

  function recordOverload(kind, error) {
    overloadCount += 1;
    lastOverloadKind = kind;
    if (kind === 'rate_limit') rateLimitCount += 1;
    if (kind === 'network') networkErrorCount += 1;
    if (kind === 'service') serviceErrorCount += 1;
    if (kind === 'timeout') timeoutErrorCount += 1;
    const cooldownMs = kind === 'rate_limit'
      ? retryAfterMs(error, profile.defaultRateLimitCooldownMs)
      : Math.max(0, Number(profile.defaultTransientCooldownMs || 0));
    const currentTime = now();
    if (currentTime >= decreaseAllowedAt) {
      const step = Number(profile.overloadDecreaseStep);
      limit = Number.isFinite(step) && step > 0
        ? Math.max(profile.minGlobal, limit - Math.floor(step))
        : Math.max(profile.minGlobal, Math.floor(limit * 0.5));
      decreaseAllowedAt = currentTime + Math.max(1, cooldownMs);
    }
    stableSuccesses = 0;
    cooldownUntil = Math.max(cooldownUntil, currentTime + cooldownMs);
  }

  async function run(key, item) {
    active += 1;
    maxActive = Math.max(maxActive, active);
    const nextRouteActive = Number(routeActive.get(key) || 0) + 1;
    routeActive.set(key, nextRouteActive);
    routePeaks.set(key, Math.max(Number(routePeaks.get(key) || 0), nextRouteActive));
    emit();
    try {
      const value = await item.fn();
      stableSuccesses += 1;
      if (stableSuccesses >= profile.successWindow && limit < profile.maxGlobal) {
        limit += 1;
        stableSuccesses = 0;
      }
      item.resolve(value);
    } catch (error) {
      const kind = transientOverloadKind(error);
      if (kind) recordOverload(kind, error);
      item.reject(error);
    } finally {
      active -= 1;
      routeActive.set(key, Math.max(0, Number(routeActive.get(key) || 1) - 1));
      emit();
      if (now() < cooldownUntil) await sleep(Math.max(1, cooldownUntil - now()));
      drain();
    }
  }

  return {
    run: schedule,
    snapshot,
    get active() { return active; },
    get maxActive() { return maxActive; },
    get limit() { return limit; },
    get maxLimit() { return profile.maxGlobal; },
  };
}

export function retryAfterMs(error, fallbackMs = 5_000) {
  const direct = Number(error?.retryAfterMs ?? error?.retry_after_ms);
  if (Number.isFinite(direct) && direct >= 0) return direct;
  const raw = error?.headers?.get?.('retry-after') ?? error?.headers?.['retry-after'] ?? error?.retryAfter;
  const seconds = Number(raw);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1_000;
  const date = Date.parse(String(raw || ''));
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : fallbackMs;
}

function transientOverloadKind(error) {
  const status = Number(error?.status || error?.statusCode || error?.response?.status || 0);
  const code = String(error?.code || error?.cause?.code || '').toUpperCase();
  const message = String(error?.message || '').toLowerCase();
  if (status === 429 || /429|rate.?limit|too many|限流/i.test(message)) return 'rate_limit';
  if (status >= 500 && status <= 599) return 'service';
  if (/ETIMEDOUT/.test(code) || /(timeout|timed out)/.test(message)) return 'timeout';
  if (/(ECONNRESET|ECONNREFUSED|EAI_AGAIN|UND_ERR|SOCKET)/.test(code)
      || /(fetch failed|network|socket)/.test(message)) return 'network';
  return null;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, Math.floor(Number(value) || min)));
}
