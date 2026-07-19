export const ADAPTIVE_WRITE_PROFILE = Object.freeze({
  initialGlobal: 8,
  maxGlobal: 24,
  perRoute: 6,
  minGlobal: 2,
  successWindow: 40,
  defaultRateLimitCooldownMs: 5_000,
});

export function createAdaptiveWriteScheduler(options = {}) {
  const profile = { ...ADAPTIVE_WRITE_PROFILE, ...(options.profile || {}) };
  const now = options.now || (() => Date.now());
  const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  let limit = clamp(profile.initialGlobal, profile.minGlobal, profile.maxGlobal);
  let active = 0;
  let maxActive = 0;
  let stableSuccesses = 0;
  let cooldownUntil = 0;
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
      if (isRateLimited(error)) {
        limit = Math.max(profile.minGlobal, Math.floor(limit * 0.5));
        stableSuccesses = 0;
        cooldownUntil = Math.max(cooldownUntil, now() + retryAfterMs(error, profile.defaultRateLimitCooldownMs));
      }
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

function isRateLimited(error) {
  return Number(error?.status || error?.statusCode) === 429 || /429|rate.?limit|too many|限流/i.test(String(error?.message || ''));
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, Math.floor(Number(value) || min)));
}
