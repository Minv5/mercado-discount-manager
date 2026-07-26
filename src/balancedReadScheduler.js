const DEFAULT_INITIAL_LIMIT = 6;
const DEFAULT_MAX_LIMIT = 10;
const DEFAULT_PER_ACCOUNT_LIMIT = 4;
const DEFAULT_DETAIL_LIMIT = 4;
const DEFAULT_ACTIVITY_LIMIT = 4;
const DEFAULT_DETAIL_PER_ACCOUNT_LIMIT = 4;
const DEFAULT_ACTIVITY_PER_ACCOUNT_LIMIT = 4;
const DEFAULT_FALLBACK_LIMIT = 1;
const DEFAULT_SUCCESSES_PER_INCREASE = 20;
const DEFAULT_MIN_LIMIT = 1;
const DEFAULT_RATE_LIMIT_DECREASE_STEP = 1;
const DEFAULT_SNAPSHOT_THROTTLE_MS = 500;
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
    initialLimit: 192,
    maxLimit: 192,
    perAccountLimit: 64,
    detailLimit: 125,
    detailPerAccountLimit: 42,
    activityLimit: 192,
    activityPerAccountLimit: 64,
    fallbackPerAccount: 2,
    minLimit: 10,
    rateLimitDecreaseStep: 5,
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

function isTimeout(error) {
  const code = String(error?.code || error?.cause?.code || '').toUpperCase();
  const message = String(error?.message || '').toLowerCase();
  return /ETIMEDOUT/.test(code) || /(timeout|timed out)/.test(message);
}

function isNetwork(error) {
  const code = String(error?.code || error?.cause?.code || '').toUpperCase();
  const message = String(error?.message || '').toLowerCase();
  return /(ECONNRESET|ECONNREFUSED|EAI_AGAIN|UND_ERR|SOCKET)/.test(code)
    || /(fetch failed|network|socket)/.test(message);
}

function transientKind(error) {
  const status = errorStatus(error);
  if (status === 429) return 'rate_limit';
  if (status >= 500 && status <= 599) return 'service';
  if (isTimeout(error)) return 'timeout';
  if (isNetwork(error)) return 'network';
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

export class BalancedReadScheduler {
  constructor({
    initialLimit = DEFAULT_INITIAL_LIMIT,
    maxLimit = DEFAULT_MAX_LIMIT,
    perAccountLimit = DEFAULT_PER_ACCOUNT_LIMIT,
    detailLimit = DEFAULT_DETAIL_LIMIT,
    activityLimit = DEFAULT_ACTIVITY_LIMIT,
    detailPerAccountLimit = null,
    activityPerAccountLimit = null,
    fallbackPerAccount = DEFAULT_FALLBACK_LIMIT,
    successesPerIncrease = DEFAULT_SUCCESSES_PER_INCREASE,
    minLimit = DEFAULT_MIN_LIMIT,
    rateLimitDecreaseStep = DEFAULT_RATE_LIMIT_DECREASE_STEP,
    snapshotThrottleMs = DEFAULT_SNAPSHOT_THROTTLE_MS,
    onSnapshot = null,
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    now = () => Date.now(),
    random = Math.random,
  } = {}) {
    this.initialLimit = Math.max(1, Number(initialLimit || DEFAULT_INITIAL_LIMIT));
    this.maxLimit = Math.max(this.initialLimit, Number(maxLimit || DEFAULT_MAX_LIMIT));
    this.dynamicLimit = this.initialLimit;
    this.perAccountLimit = Math.max(1, Number(perAccountLimit || DEFAULT_PER_ACCOUNT_LIMIT));
    this.detailLimit = Math.max(1, Number(detailLimit || DEFAULT_DETAIL_LIMIT));
    this.activityLimit = Math.max(1, Number(activityLimit || DEFAULT_ACTIVITY_LIMIT));
    this.detailPerAccountLimit = Math.max(1, Number(detailPerAccountLimit || perAccountLimit || DEFAULT_DETAIL_PER_ACCOUNT_LIMIT));
    this.activityPerAccountLimit = Math.max(1, Number(activityPerAccountLimit || perAccountLimit || DEFAULT_ACTIVITY_PER_ACCOUNT_LIMIT));
    this.fallbackPerAccount = Math.max(1, Number(fallbackPerAccount || DEFAULT_FALLBACK_LIMIT));
    this.successesPerIncrease = Math.max(1, Number(successesPerIncrease || DEFAULT_SUCCESSES_PER_INCREASE));
    this.minLimit = Math.min(this.initialLimit, Math.max(1, Number(minLimit || DEFAULT_MIN_LIMIT)));
    this.rateLimitDecreaseStep = Math.max(1, Number(rateLimitDecreaseStep || DEFAULT_RATE_LIMIT_DECREASE_STEP));
    this.snapshotThrottleMs = Math.max(0, Number(snapshotThrottleMs ?? DEFAULT_SNAPSHOT_THROTTLE_MS));
    this.sleep = sleep;
    this.now = now;
    this.random = random;
    this.queues = new Map();
    this.accountOrder = [];
    this.accountCursor = 0;
    this.accountInflight = new Map();
    this.accountDetailInflight = new Map();
    this.accountActivityInflight = new Map();
    this.accountCooldownUntil = new Map();
    this.accountCooldownTokens = new Map();
    this.globalCooldownUntil = 0;
    this.globalCooldownToken = null;
    this.inflight = 0;
    this.inflightByGeneration = new Map();
    this.retryGeneration = 0;
    this.overloadGenerations = new Set();
    this.detailInflight = 0;
    this.activityInflight = 0;
    this.peakInflight = 0;
    this.peakDetail = 0;
    this.peakActivity = 0;
    this.stableSuccesses = 0;
    this.deduped = new Map();
    this.fallbackQueues = new Map();
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
    this.metricTimeoutErrors = 0;
    this.snapshotListeners = new Set();
    this.lastSnapshotEmittedAt = Number.NEGATIVE_INFINITY;
    if (typeof onSnapshot === 'function') this.subscribe(onSnapshot);
  }

  subscribe(listener) {
    if (typeof listener !== 'function') return () => {};
    this.snapshotListeners.add(listener);
    listener(this.snapshot());
    return () => this.snapshotListeners.delete(listener);
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
      attempt: 0,
      generation: this.retryGeneration,
    };
    if (requestKey) this.deduped.set(requestKey, promise);
    this.#enqueue(job);
    this.#queuePump();
    this.#emitSnapshot();
    return promise;
  }

  withFallback({ accountId = '', key = '', signal = null } = {}, task) {
    if (typeof task !== 'function') return Promise.reject(new TypeError('fallback task is required'));
    const account = String(accountId || '__global__');
    const requestKey = key ? `fallback|${account}|${String(key)}` : '';
    if (requestKey && this.deduped.has(requestKey)) return this.deduped.get(requestKey);
    let resolvePromise;
    let rejectPromise;
    const run = new Promise((resolve, reject) => {
      resolvePromise = resolve;
      rejectPromise = reject;
    });
    if (!this.fallbackQueues.has(account)) this.fallbackQueues.set(account, []);
    this.fallbackQueues.get(account).push({ account, requestKey, signal, task, resolve: resolvePromise, reject: rejectPromise });
    if (requestKey) this.deduped.set(requestKey, run);
    this.#pumpFallback(account);
    this.#emitSnapshot();
    return run;
  }

  snapshot() {
    const snapshotNow = this.now();
    const durations = [...this.metricDurations].sort((left, right) => left - right);
    const p95Index = durations.length ? Math.min(durations.length - 1, Math.ceil(durations.length * 0.95) - 1) : -1;
    const elapsedMs = this.metricStartedAt == null
      ? 0
      : Math.max(0, Number((this.metricFinishedAt ?? snapshotNow) - this.metricStartedAt));
    const accountCooldownRemainingMs = Object.fromEntries(
      [...this.accountCooldownUntil.entries()]
        .map(([account, until]) => [account, Math.max(0, Number(until || 0) - snapshotNow)])
        .filter(([, remaining]) => remaining > 0),
    );
    const globalCooldownRemainingMs = Math.max(0, this.globalCooldownUntil - snapshotNow);
    const cooldownRemainingMs = Math.max(globalCooldownRemainingMs, 0, ...Object.values(accountCooldownRemainingMs));
    return {
      dynamicLimit: this.dynamicLimit,
      inflight: this.inflight,
      peakInflight: this.peakInflight,
      detailInflight: this.detailInflight,
      peakDetail: this.peakDetail,
      activityInflight: this.activityInflight,
      peakActivity: this.peakActivity,
      accountInflight: Object.fromEntries(this.accountInflight),
      accountDetailInflight: Object.fromEntries(this.accountDetailInflight),
      accountActivityInflight: Object.fromEntries(this.accountActivityInflight),
      fallbackActiveByAccount: Object.fromEntries(this.fallbackActive),
      peakFallbackByAccount: Object.fromEntries(this.peakFallbackByAccount),
      queued: [...this.queues.values()].reduce((sum, queue) => sum + queue.length, 0)
        + [...this.fallbackQueues.values()].reduce((sum, queue) => sum + queue.length, 0),
      initialLimit: this.initialLimit,
      maxLimit: this.maxLimit,
      perAccountLimit: this.perAccountLimit,
      detailLimit: this.detailLimit,
      detailPerAccountLimit: this.detailPerAccountLimit,
      activityLimit: this.activityLimit,
      activityPerAccountLimit: this.activityPerAccountLimit,
      fallbackPerAccount: this.fallbackPerAccount,
      globalCooldownRemainingMs,
      accountCooldownRemainingMs,
      cooldownRemainingMs,
      mercado_outbound_inflight: this.inflight,
      mercado_outbound_peak: this.peakInflight,
      mercado_outbound_logical_request_count: this.metricLogicalRequests,
      mercado_outbound_request_count: this.metricRequestAttempts,
      mercado_outbound_completed_count: this.metricCompleted,
      mercado_outbound_retry_count: this.metricRetries,
      mercado_outbound_rate_limit_count: this.metricRateLimits,
      mercado_outbound_network_error_count: this.metricNetworkErrors,
      mercado_outbound_service_error_count: this.metricServiceErrors,
      mercado_outbound_timeout_error_count: this.metricTimeoutErrors,
      mercado_outbound_failure_count: this.metricFailures,
      mercado_outbound_elapsed_ms: elapsedMs,
      mercado_outbound_p95_ms: p95Index >= 0 ? durations[p95Index] : 0,
      mercado_outbound_throughput_per_second: elapsedMs > 0
        ? Number((this.metricCompleted * 1_000 / elapsedMs).toFixed(2))
        : 0,
    };
  }

  #emitSnapshot(force = false) {
    if (!this.snapshotListeners.size) return;
    const now = this.now();
    if (!force && now - this.lastSnapshotEmittedAt < this.snapshotThrottleMs) return;
    this.lastSnapshotEmittedAt = now;
    const snapshot = this.snapshot();
    for (const listener of this.snapshotListeners) {
      try { listener(snapshot); } catch { /* Observability must not affect reads. */ }
    }
  }

  #pumpFallback(account) {
    const queue = this.fallbackQueues.get(account) || [];
    while (queue.length && Number(this.fallbackActive.get(account) || 0) < this.fallbackPerAccount) {
      const job = queue.shift();
      if (job.signal?.aborted) {
        if (job.requestKey && this.deduped.get(job.requestKey)) this.deduped.delete(job.requestKey);
        job.reject(abortError());
        continue;
      }
      const active = Number(this.fallbackActive.get(account) || 0) + 1;
      this.fallbackActive.set(account, active);
      this.peakFallbackByAccount.set(account, Math.max(Number(this.peakFallbackByAccount.get(account) || 0), active));
      this.#emitSnapshot();
      const finish = () => {
        this.fallbackActive.set(account, Math.max(0, Number(this.fallbackActive.get(account) || 0) - 1));
        if (job.requestKey && this.deduped.get(job.requestKey)) this.deduped.delete(job.requestKey);
        if (!queue.length && Number(this.fallbackActive.get(account) || 0) === 0) this.fallbackQueues.delete(account);
        this.#emitSnapshot();
        this.#pumpFallback(account);
      };
      Promise.resolve().then(job.task).then((value) => {
        finish();
        job.resolve(value);
      }, (error) => {
        finish();
        job.reject(error);
      });
    }
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
    if (this.globalCooldownToken || this.globalCooldownUntil > now) return null;
    const count = this.accountOrder.length;
    for (let offset = 0; offset < count; offset += 1) {
      const index = (this.accountCursor + offset) % count;
      const account = this.accountOrder[index];
      const queue = this.queues.get(account) || [];
      if (!queue.length) continue;
      if (Number(this.accountInflight.get(account) || 0) >= this.perAccountLimit) continue;
      if (this.accountCooldownTokens.has(account) || Number(this.accountCooldownUntil.get(account) || 0) > now) continue;
      const runnableIndex = queue.findIndex((job) => (
        !this.#hasOlderInflight(job.generation)
        &&
        (job.kind !== 'detail' || this.detailInflight < this.detailLimit)
        && (job.kind !== 'detail' || Number(this.accountDetailInflight.get(account) || 0) < this.detailPerAccountLimit)
        && (job.kind !== 'activity' || this.activityInflight < this.activityLimit)
        && (job.kind !== 'activity' || Number(this.accountActivityInflight.get(account) || 0) < this.activityPerAccountLimit)
      ));
      if (runnableIndex < 0) continue;
      this.accountCursor = (index + 1) % count;
      return queue.splice(runnableIndex, 1)[0];
    }
    return null;
  }

  #enqueue(job) {
    if (!this.queues.has(job.account)) {
      this.queues.set(job.account, []);
      this.accountOrder.push(job.account);
    }
    this.queues.get(job.account).push(job);
    if (job.signal) {
      job.abortListener = () => {
        const queue = this.queues.get(job.account) || [];
        const index = queue.indexOf(job);
        if (index < 0) return;
        queue.splice(index, 1);
        this.#settle(job, null, abortError());
        this.#queuePump();
      };
      job.signal.addEventListener('abort', job.abortListener, { once: true });
    }
  }

  #hasOlderInflight(generation) {
    for (const [activeGeneration, count] of this.inflightByGeneration) {
      if (activeGeneration < generation && count > 0) return true;
    }
    return false;
  }

  #setGlobalCooldown(delay) {
    const boundedDelay = Math.max(0, Number(delay || 0));
    const until = this.now() + boundedDelay;
    if (this.globalCooldownToken && this.globalCooldownUntil >= until) return;
    const token = Symbol('global-read-cooldown');
    this.globalCooldownToken = token;
    this.globalCooldownUntil = Math.max(this.globalCooldownUntil, until);
    Promise.resolve(this.sleep(boundedDelay)).finally(() => {
      if (this.globalCooldownToken !== token) return;
      this.globalCooldownToken = null;
      this.globalCooldownUntil = 0;
      this.#emitSnapshot(true);
      this.#queuePump();
    });
  }

  #setAccountCooldown(account, delay) {
    const boundedDelay = Math.max(0, Number(delay || 0));
    const until = this.now() + boundedDelay;
    const currentToken = this.accountCooldownTokens.get(account);
    if (currentToken && Number(this.accountCooldownUntil.get(account) || 0) >= until) return;
    const token = Symbol(`account-read-cooldown:${account}`);
    this.accountCooldownTokens.set(account, token);
    this.accountCooldownUntil.set(account, Math.max(Number(this.accountCooldownUntil.get(account) || 0), until));
    Promise.resolve(this.sleep(boundedDelay)).finally(() => {
      if (this.accountCooldownTokens.get(account) !== token) return;
      this.accountCooldownTokens.delete(account);
      this.accountCooldownUntil.delete(account);
      this.#emitSnapshot(true);
      this.#queuePump();
    });
  }

  #openRetryGeneration(failedGeneration) {
    const nextGeneration = Math.max(this.retryGeneration, failedGeneration + 1);
    this.retryGeneration = nextGeneration;
    for (const queue of this.queues.values()) {
      for (const queuedJob of queue) {
        if (queuedJob.generation <= failedGeneration) queuedJob.generation = nextGeneration;
      }
    }
    if (!this.overloadGenerations.has(failedGeneration)) {
      this.overloadGenerations.add(failedGeneration);
      this.dynamicLimit = Math.max(this.minLimit, this.dynamicLimit - this.rateLimitDecreaseStep);
      this.stableSuccesses = 0;
    }
    return nextGeneration;
  }

  #start(job) {
    if (job.abortListener) job.signal?.removeEventListener('abort', job.abortListener);
    if (job.signal?.aborted) {
      this.#settle(job, null, abortError());
      this.#queuePump();
      return;
    }
    const attemptGeneration = job.generation;
    this.inflight += 1;
    this.inflightByGeneration.set(attemptGeneration, Number(this.inflightByGeneration.get(attemptGeneration) || 0) + 1);
    if (!Number.isFinite(job.metricStartedAt)) {
      job.metricStartedAt = this.now();
      if (this.metricStartedAt == null) this.metricStartedAt = job.metricStartedAt;
      this.metricLogicalRequests += 1;
    }
    this.peakInflight = Math.max(this.peakInflight, this.inflight);
    this.accountInflight.set(job.account, Number(this.accountInflight.get(job.account) || 0) + 1);
    if (job.kind === 'detail') {
      this.detailInflight += 1;
      this.accountDetailInflight.set(job.account, Number(this.accountDetailInflight.get(job.account) || 0) + 1);
      this.peakDetail = Math.max(this.peakDetail, this.detailInflight);
    }
    if (job.kind === 'activity') {
      this.activityInflight += 1;
      this.accountActivityInflight.set(job.account, Number(this.accountActivityInflight.get(job.account) || 0) + 1);
      this.peakActivity = Math.max(this.peakActivity, this.activityInflight);
    }
    this.#emitSnapshot();
    this.metricRequestAttempts += 1;
    Promise.resolve().then(() => job.task({
      attempt: job.attempt + 1,
      signal: job.signal,
    })).then((value) => {
      if (attemptGeneration === this.retryGeneration) {
        this.stableSuccesses += 1;
        if (this.stableSuccesses >= this.successesPerIncrease && this.dynamicLimit < this.maxLimit) {
          this.dynamicLimit += 1;
          this.stableSuccesses = 0;
          this.#emitSnapshot(true);
        }
      }
      this.#release(job, true, attemptGeneration);
      this.#settle(job, value, null);
    }, (error) => {
      if (job.signal?.aborted || error?.name === 'AbortError') {
        this.#release(job, true, attemptGeneration);
        this.#settle(job, null, abortError());
        return;
      }
      const kind = transientKind(error);
      if (kind === 'rate_limit') this.metricRateLimits += 1;
      if (kind === 'network') this.metricNetworkErrors += 1;
      if (kind === 'service') this.metricServiceErrors += 1;
      if (kind === 'timeout') this.metricTimeoutErrors += 1;
      if (!kind || job.attempt >= TRANSIENT_RETRY_DELAYS_MS.length) {
        this.#release(job, true, attemptGeneration);
        this.#settle(job, null, error);
        return;
      }
      this.metricRetries += 1;
      const generation = this.#openRetryGeneration(attemptGeneration);
      let delay = TRANSIENT_RETRY_DELAYS_MS[job.attempt];
      if (kind === 'rate_limit') delay = Math.max(delay, retryAfterMs(error));
      const jitter = Math.floor(delay * 0.2 * Math.max(0, Number(this.random() || 0)));
      if (kind === 'rate_limit') this.#setGlobalCooldown(delay + jitter);
      else this.#setAccountCooldown(job.account, delay + jitter);
      job.attempt += 1;
      this.#release(job, false, attemptGeneration);
      job.generation = generation;
      this.#enqueue(job);
      this.#emitSnapshot(true);
      this.#queuePump();
    });
  }

  #release(job, queuePump = true, generation = job.generation) {
      this.inflightByGeneration.set(generation, Math.max(0, Number(this.inflightByGeneration.get(generation) || 0) - 1));
      if (this.inflightByGeneration.get(generation) === 0) this.inflightByGeneration.delete(generation);
      this.inflight = Math.max(0, this.inflight - 1);
      this.accountInflight.set(job.account, Math.max(0, Number(this.accountInflight.get(job.account) || 0) - 1));
      if (job.kind === 'detail') {
        this.detailInflight = Math.max(0, this.detailInflight - 1);
        this.accountDetailInflight.set(job.account, Math.max(0, Number(this.accountDetailInflight.get(job.account) || 0) - 1));
      }
      if (job.kind === 'activity') {
        this.activityInflight = Math.max(0, this.activityInflight - 1);
        this.accountActivityInflight.set(job.account, Math.max(0, Number(this.accountActivityInflight.get(job.account) || 0) - 1));
      }
      this.#emitSnapshot();
      if (queuePump) this.#queuePump();
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
    mercado_outbound_dynamic_limit: Math.max(0, Number(schedulerSnapshot.dynamicLimit || 0)),
    mercado_outbound_max_limit: Math.max(0, Number(schedulerSnapshot.maxLimit || 0)),
    mercado_outbound_per_account_limit: Math.max(0, Number(schedulerSnapshot.perAccountLimit || 0)),
    mercado_outbound_detail_limit: Math.max(0, Number(schedulerSnapshot.detailLimit || 0)),
    mercado_outbound_detail_per_account_limit: Math.max(0, Number(schedulerSnapshot.detailPerAccountLimit || 0)),
    mercado_outbound_activity_limit: Math.max(0, Number(schedulerSnapshot.activityLimit || 0)),
    mercado_outbound_activity_per_account_limit: Math.max(0, Number(schedulerSnapshot.activityPerAccountLimit || 0)),
    mercado_outbound_fallback_per_account: Math.max(0, Number(schedulerSnapshot.fallbackPerAccount || 0)),
    mercado_outbound_inflight: Math.max(0, Number(schedulerSnapshot.mercado_outbound_inflight ?? schedulerSnapshot.inflight ?? 0)),
    mercado_outbound_peak: Math.max(0, Number(schedulerSnapshot.mercado_outbound_peak ?? schedulerSnapshot.peakInflight ?? 0)),
    mercado_outbound_activity_inflight: Math.max(0, Number(schedulerSnapshot.activityInflight || 0)),
    mercado_outbound_activity_peak: Math.max(0, Number(schedulerSnapshot.peakActivity || 0)),
    mercado_outbound_request_count: Math.max(0, Number(schedulerSnapshot.mercado_outbound_request_count || 0)),
    mercado_outbound_retry_count: Math.max(0, Number(schedulerSnapshot.mercado_outbound_retry_count || 0)),
    mercado_outbound_rate_limit_count: Math.max(0, Number(schedulerSnapshot.mercado_outbound_rate_limit_count || 0)),
    mercado_outbound_network_error_count: Math.max(0, Number(schedulerSnapshot.mercado_outbound_network_error_count || 0)),
    mercado_outbound_service_error_count: Math.max(0, Number(schedulerSnapshot.mercado_outbound_service_error_count || 0)),
    mercado_outbound_timeout_error_count: Math.max(0, Number(schedulerSnapshot.mercado_outbound_timeout_error_count || 0)),
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

export function prepareReadSchedulerProfile(settings = {}) {
  const bounded = (value, fallback, maximum) => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.min(maximum, Math.max(1, Math.floor(number))) : fallback;
  };
  const detailLimit = bounded(settings.readConcurrency, 125, 125);
  const activityLimit = bounded(settings.previewConcurrency, BALANCED_READ_PROFILES.prepare.activityLimit, BALANCED_READ_PROFILES.prepare.activityLimit);
  const globalLimit = Math.max(detailLimit, activityLimit);
  const detailPerAccountLimit = Math.min(42, detailLimit);
  const activityPerAccountLimit = Math.min(64, activityLimit);
  return {
    ...BALANCED_READ_PROFILES.prepare,
    initialLimit: globalLimit,
    maxLimit: globalLimit,
    perAccountLimit: Math.max(detailPerAccountLimit, activityPerAccountLimit),
    detailLimit,
    detailPerAccountLimit,
    activityLimit,
    activityPerAccountLimit,
  };
}
