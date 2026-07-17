const DEFAULT_INITIAL_LIMIT = 6;
const DEFAULT_MAX_LIMIT = 10;
const DEFAULT_PER_ACCOUNT_LIMIT = 4;
const DEFAULT_DETAIL_LIMIT = 4;
const DEFAULT_FALLBACK_LIMIT = 1;
const DEFAULT_SUCCESSES_PER_INCREASE = 20;
const TRANSIENT_RETRY_DELAYS_MS = [1_000, 2_000, 4_000];

export const BALANCED_READ_PROFILES = Object.freeze({
  compatibility: Object.freeze({
    initialLimit: 6,
    maxLimit: 10,
    perAccountLimit: 4,
    detailLimit: 4,
    fallbackPerAccount: 1,
  }),
  balanced: Object.freeze({
    initialLimit: 8,
    maxLimit: 12,
    perAccountLimit: 4,
    detailLimit: 6,
    fallbackPerAccount: 1,
  }),
  prepare: Object.freeze({
    initialLimit: 10,
    maxLimit: 18,
    perAccountLimit: 6,
    detailLimit: 8,
    fallbackPerAccount: 1,
  }),
});

function abortError() {
  const error = new Error('read operation aborted');
  error.name = 'AbortError';
  error.code = 'ABORT_ERR';
  return error;
}

function errorStatus(error) {
  return Number(error?.status || error?.statusCode || error?.response?.status || 0);
}

function isTimeoutOrNetwork(error) {
  const code = String(error?.code || error?.cause?.code || '').toUpperCase();
  const message = String(error?.message || '').toLowerCase();
  return /(ETIMEDOUT|ECONNRESET|ECONNREFUSED|EAI_AGAIN|UND_ERR|SOCKET)/.test(code)
    || /(timeout|timed out|fetch failed|network|socket)/.test(message);
}

function transientKind(error) {
  const status = errorStatus(error);
  if (status === 429) return 'rate_limit';
  if (status >= 500 && status <= 599) return 'service';
  if (isTimeoutOrNetwork(error)) return 'network';
  return null;
}

function retryAfterMs(error) {
  const direct = Number(error?.retryAfterMs ?? error?.retry_after_ms);
  if (Number.isFinite(direct) && direct >= 0) return direct;
  const raw = error?.retryAfter ?? error?.retry_after;
  if (raw == null || raw === '') return 0;
  const seconds = Number(raw);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1_000;
  const date = Date.parse(String(raw));
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : 0;
}

function waitWithSignal(ms, signal, sleep) {
  if (signal?.aborted) return Promise.reject(abortError());
  if (!signal) return sleep(ms);
  return new Promise((resolve, reject) => {
    let settled = false;
    const abort = () => {
      if (settled) return;
      settled = true;
      reject(abortError());
    };
    signal.addEventListener('abort', abort, { once: true });
    Promise.resolve(sleep(ms)).then(() => {
      if (settled) return;
      settled = true;
      signal.removeEventListener('abort', abort);
      resolve();
    }, (error) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener('abort', abort);
      reject(error);
    });
  });
}

export class BalancedReadScheduler {
  constructor({
    initialLimit = DEFAULT_INITIAL_LIMIT,
    maxLimit = DEFAULT_MAX_LIMIT,
    perAccountLimit = DEFAULT_PER_ACCOUNT_LIMIT,
    detailLimit = DEFAULT_DETAIL_LIMIT,
    fallbackPerAccount = DEFAULT_FALLBACK_LIMIT,
    successesPerIncrease = DEFAULT_SUCCESSES_PER_INCREASE,
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    now = () => Date.now(),
    random = Math.random,
  } = {}) {
    this.initialLimit = Math.max(1, Number(initialLimit || DEFAULT_INITIAL_LIMIT));
    this.maxLimit = Math.max(this.initialLimit, Number(maxLimit || DEFAULT_MAX_LIMIT));
    this.dynamicLimit = this.initialLimit;
    this.perAccountLimit = Math.max(1, Number(perAccountLimit || DEFAULT_PER_ACCOUNT_LIMIT));
    this.detailLimit = Math.max(1, Number(detailLimit || DEFAULT_DETAIL_LIMIT));
    this.fallbackPerAccount = Math.max(1, Number(fallbackPerAccount || DEFAULT_FALLBACK_LIMIT));
    this.successesPerIncrease = Math.max(1, Number(successesPerIncrease || DEFAULT_SUCCESSES_PER_INCREASE));
    this.sleep = sleep;
    this.now = now;
    this.random = random;
    this.queues = new Map();
    this.accountOrder = [];
    this.accountCursor = 0;
    this.accountInflight = new Map();
    this.accountCooldownUntil = new Map();
    this.globalCooldownUntil = 0;
    this.inflight = 0;
    this.detailInflight = 0;
    this.peakInflight = 0;
    this.peakDetail = 0;
    this.stableSuccesses = 0;
    this.deduped = new Map();
    this.fallbackTails = new Map();
    this.fallbackActive = new Map();
    this.peakFallbackByAccount = new Map();
    this.pumpQueued = false;
    this.metricStartedAt = null;
    this.metricFinishedAt = null;
    this.metricDurations = [];
    this.metricLogicalRequests = 0;
    this.metricRequestAttempts = 0;
    this.metricCompleted = 0;
    this.metricFailures = 0;
    this.metricRetries = 0;
    this.metricRateLimits = 0;
    this.metricNetworkErrors = 0;
    this.metricServiceErrors = 0;
  }

  schedule({ accountId = '', key = '', kind = 'read', signal = null } = {}, task) {
    if (typeof task !== 'function') return Promise.reject(new TypeError('read task is required'));
    if (signal?.aborted) return Promise.reject(abortError());
    const account = String(accountId || '__global__');
    const requestKey = key ? `${account}|${String(key)}` : '';
    if (requestKey && this.deduped.has(requestKey)) return this.deduped.get(requestKey);

    let resolvePromise;
    let rejectPromise;
    const promise = new Promise((resolve, reject) => {
      resolvePromise = resolve;
      rejectPromise = reject;
    });
    const job = {
      account,
      key: requestKey,
      kind: String(kind || 'read'),
      signal,
      task,
      resolve: resolvePromise,
      reject: rejectPromise,
      abortListener: null,
    };
    if (!this.queues.has(account)) {
      this.queues.set(account, []);
      this.accountOrder.push(account);
    }
    this.queues.get(account).push(job);
    if (requestKey) this.deduped.set(requestKey, promise);
    if (signal) {
      job.abortListener = () => {
        const queue = this.queues.get(account) || [];
        const index = queue.indexOf(job);
        if (index < 0) return;
        queue.splice(index, 1);
        this.#settle(job, null, abortError());
        this.#queuePump();
      };
      signal.addEventListener('abort', job.abortListener, { once: true });
    }
    this.#queuePump();
    return promise;
  }

  withFallback({ accountId = '', key = '', signal = null } = {}, task) {
    if (typeof task !== 'function') return Promise.reject(new TypeError('fallback task is required'));
    const account = String(accountId || '__global__');
    const requestKey = key ? `fallback|${account}|${String(key)}` : '';
    if (requestKey && this.deduped.has(requestKey)) return this.deduped.get(requestKey);
    const prior = this.fallbackTails.get(account) || Promise.resolve();
    const run = prior.catch(() => undefined).then(async () => {
      if (signal?.aborted) throw abortError();
      const active = Number(this.fallbackActive.get(account) || 0) + 1;
      this.fallbackActive.set(account, active);
      this.peakFallbackByAccount.set(account, Math.max(Number(this.peakFallbackByAccount.get(account) || 0), active));
      try {
        return await task();
      } finally {
        this.fallbackActive.set(account, Math.max(0, active - 1));
      }
    });
    const tail = run.finally(() => {
      if (this.fallbackTails.get(account) === tail) this.fallbackTails.delete(account);
      if (requestKey && this.deduped.get(requestKey) === run) this.deduped.delete(requestKey);
    });
    this.fallbackTails.set(account, tail);
    if (requestKey) this.deduped.set(requestKey, run);
    return run;
  }

  snapshot() {
    const durations = [...this.metricDurations].sort((left, right) => left - right);
    const p95Index = durations.length ? Math.min(durations.length - 1, Math.ceil(durations.length * 0.95) - 1) : -1;
    const elapsedMs = this.metricStartedAt == null
      ? 0
      : Math.max(0, Number((this.metricFinishedAt ?? this.now()) - this.metricStartedAt));
    return {
      dynamicLimit: this.dynamicLimit,
      inflight: this.inflight,
      peakInflight: this.peakInflight,
      detailInflight: this.detailInflight,
      peakDetail: this.peakDetail,
      accountInflight: Object.fromEntries(this.accountInflight),
      peakFallbackByAccount: Object.fromEntries(this.peakFallbackByAccount),
      queued: [...this.queues.values()].reduce((sum, queue) => sum + queue.length, 0),
      mercado_outbound_inflight: this.inflight,
      mercado_outbound_peak: this.peakInflight,
      mercado_outbound_logical_request_count: this.metricLogicalRequests,
      mercado_outbound_request_count: this.metricRequestAttempts,
      mercado_outbound_completed_count: this.metricCompleted,
      mercado_outbound_retry_count: this.metricRetries,
      mercado_outbound_rate_limit_count: this.metricRateLimits,
      mercado_outbound_network_error_count: this.metricNetworkErrors,
      mercado_outbound_service_error_count: this.metricServiceErrors,
      mercado_outbound_failure_count: this.metricFailures,
      mercado_outbound_elapsed_ms: elapsedMs,
      mercado_outbound_p95_ms: p95Index >= 0 ? durations[p95Index] : 0,
      mercado_outbound_throughput_per_second: elapsedMs > 0
        ? Number((this.metricCompleted * 1_000 / elapsedMs).toFixed(2))
        : 0,
    };
  }

  #queuePump() {
    if (this.pumpQueued) return;
    this.pumpQueued = true;
    queueMicrotask(() => {
      this.pumpQueued = false;
      this.#pump();
    });
  }

  #pump() {
    while (this.inflight < this.dynamicLimit) {
      const job = this.#nextJob();
      if (!job) break;
      this.#start(job);
    }
  }

  #nextJob() {
    if (!this.accountOrder.length) return null;
    const now = this.now();
    if (this.globalCooldownUntil > now) {
      const delay = this.globalCooldownUntil - now;
      this.sleep(delay).finally(() => this.#queuePump());
      return null;
    }
    const count = this.accountOrder.length;
    for (let offset = 0; offset < count; offset += 1) {
      const index = (this.accountCursor + offset) % count;
      const account = this.accountOrder[index];
      const queue = this.queues.get(account) || [];
      if (!queue.length) continue;
      if (Number(this.accountInflight.get(account) || 0) >= this.perAccountLimit) continue;
      if (Number(this.accountCooldownUntil.get(account) || 0) > now) continue;
      const runnableIndex = queue.findIndex((job) => job.kind !== 'detail' || this.detailInflight < this.detailLimit);
      if (runnableIndex < 0) continue;
      this.accountCursor = (index + 1) % count;
      return queue.splice(runnableIndex, 1)[0];
    }
    const wakeAt = Math.min(...[...this.accountCooldownUntil.values()].filter((value) => value > now));
    if (Number.isFinite(wakeAt)) this.sleep(wakeAt - now).finally(() => this.#queuePump());
    return null;
  }

  #start(job) {
    if (job.abortListener) job.signal?.removeEventListener('abort', job.abortListener);
    if (job.signal?.aborted) {
      this.#settle(job, null, abortError());
      this.#queuePump();
      return;
    }
    this.inflight += 1;
    job.metricStartedAt = this.now();
    if (this.metricStartedAt == null) this.metricStartedAt = job.metricStartedAt;
    this.metricLogicalRequests += 1;
    this.peakInflight = Math.max(this.peakInflight, this.inflight);
    this.accountInflight.set(job.account, Number(this.accountInflight.get(job.account) || 0) + 1);
    if (job.kind === 'detail') {
      this.detailInflight += 1;
      this.peakDetail = Math.max(this.peakDetail, this.detailInflight);
    }
    this.#runWithRetry(job).then((value) => {
      this.#release(job);
      this.#settle(job, value, null);
    }, (error) => {
      this.#release(job);
      this.#settle(job, null, error);
    });
  }

  #release(job) {
      this.inflight = Math.max(0, this.inflight - 1);
      this.accountInflight.set(job.account, Math.max(0, Number(this.accountInflight.get(job.account) || 0) - 1));
      if (job.kind === 'detail') this.detailInflight = Math.max(0, this.detailInflight - 1);
      this.#queuePump();
  }

  async #runWithRetry(job) {
    let attempt = 0;
    while (true) {
      if (job.signal?.aborted) throw abortError();
      try {
        this.metricRequestAttempts += 1;
        const result = await job.task({ attempt: attempt + 1, signal: job.signal });
        this.stableSuccesses += 1;
        if (this.stableSuccesses >= this.successesPerIncrease && this.dynamicLimit < this.maxLimit) {
          this.dynamicLimit += 1;
          this.stableSuccesses = 0;
        }
        return result;
      } catch (error) {
        if (job.signal?.aborted || error?.name === 'AbortError') throw abortError();
        const kind = transientKind(error);
        if (kind === 'rate_limit') this.metricRateLimits += 1;
        if (kind === 'network') this.metricNetworkErrors += 1;
        if (kind === 'service') this.metricServiceErrors += 1;
        if (!kind || attempt >= TRANSIENT_RETRY_DELAYS_MS.length) throw error;
        this.metricRetries += 1;
        let delay = TRANSIENT_RETRY_DELAYS_MS[attempt];
        if (kind === 'rate_limit') {
          delay = Math.max(delay, retryAfterMs(error));
          this.dynamicLimit = Math.max(1, Math.floor(this.dynamicLimit / 2));
          this.globalCooldownUntil = Math.max(this.globalCooldownUntil, this.now() + delay);
        } else {
          this.accountCooldownUntil.set(job.account, Math.max(Number(this.accountCooldownUntil.get(job.account) || 0), this.now() + delay));
        }
        this.stableSuccesses = 0;
        const jitter = Math.floor(delay * 0.2 * Math.max(0, Number(this.random() || 0)));
        await waitWithSignal(delay + jitter, job.signal, this.sleep);
        attempt += 1;
      }
    }
  }

  #settle(job, value, error) {
    if (job.abortListener) job.signal?.removeEventListener('abort', job.abortListener);
    if (job.key && this.deduped.get(job.key)) this.deduped.delete(job.key);
    if (Number.isFinite(job.metricStartedAt)) {
      const finishedAt = this.now();
      this.metricFinishedAt = finishedAt;
      this.metricCompleted += 1;
      if (error && error?.name !== 'AbortError') this.metricFailures += 1;
      this.metricDurations.push(Math.max(0, finishedAt - job.metricStartedAt));
    }
    if (error) job.reject(error);
    else job.resolve(value);
  }
}

export function buildReadConcurrencyReport({
  schedulerSnapshot = {},
  localWorkConcurrency = 1,
  localDbBatchQueries = 0,
} = {}) {
  return {
    local_work_concurrency: Math.max(1, Math.floor(Number(localWorkConcurrency || 1))),
    local_db_batch_queries: Math.max(0, Math.floor(Number(localDbBatchQueries || 0))),
    mercado_outbound_inflight: Math.max(0, Number(schedulerSnapshot.mercado_outbound_inflight ?? schedulerSnapshot.inflight ?? 0)),
    mercado_outbound_peak: Math.max(0, Number(schedulerSnapshot.mercado_outbound_peak ?? schedulerSnapshot.peakInflight ?? 0)),
    mercado_outbound_request_count: Math.max(0, Number(schedulerSnapshot.mercado_outbound_request_count || 0)),
    mercado_outbound_retry_count: Math.max(0, Number(schedulerSnapshot.mercado_outbound_retry_count || 0)),
    mercado_outbound_rate_limit_count: Math.max(0, Number(schedulerSnapshot.mercado_outbound_rate_limit_count || 0)),
    mercado_outbound_failure_count: Math.max(0, Number(schedulerSnapshot.mercado_outbound_failure_count || 0)),
    mercado_outbound_elapsed_ms: Math.max(0, Number(schedulerSnapshot.mercado_outbound_elapsed_ms || 0)),
    mercado_outbound_p95_ms: Math.max(0, Number(schedulerSnapshot.mercado_outbound_p95_ms || 0)),
    mercado_outbound_throughput_per_second: Math.max(0, Number(schedulerSnapshot.mercado_outbound_throughput_per_second || 0)),
  };
}

export function selectReadConcurrencyProfile(benchmarks = {}) {
  const compatibility = normalizeBenchmark(benchmarks.compatibility);
  const balanced = normalizeBenchmark(benchmarks.balanced);
  const high = normalizeBenchmark(benchmarks.high);
  const isNoWorse = (candidate) => candidate.failure_count <= compatibility.failure_count
    && candidate.rate_limit_count <= compatibility.rate_limit_count;
  if (isNoWorse(high) && high.elapsed_ms <= compatibility.elapsed_ms * 0.7) return 'high';
  if (isNoWorse(balanced) && balanced.elapsed_ms <= compatibility.elapsed_ms * 0.85) return 'balanced';
  return 'compatibility';
}

function normalizeBenchmark(value = {}) {
  return {
    elapsed_ms: Number.isFinite(Number(value.elapsed_ms)) ? Math.max(0, Number(value.elapsed_ms)) : Number.POSITIVE_INFINITY,
    rate_limit_count: Math.max(0, Number(value.rate_limit_count || 0)),
    failure_count: Math.max(0, Number(value.failure_count || 0)),
  };
}

export function createBalancedReadScheduler(options = {}) {
  return new BalancedReadScheduler(options);
}
