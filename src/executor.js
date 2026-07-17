import { MAX_WRITE_CONCURRENCY, normalizeWriteConcurrency, mapLimitedWithCap } from './concurrency.js';

const DEFAULT_RETRY_OPTIONS = {
  maxImmediateRetries: 3,
  retryBackoffMs: [1000, 2000, 4000],
  deferredFinalRetry: true,
  deferredConcurrency: 20
};

export function createAsyncLimiter(limit, options = {}) {
  const normalizedLimit = normalizeWriteConcurrency(limit);
  let active = 0;
  let maxActive = 0;
  const queue = [];

  function emitActiveChange() {
    options.onActiveChange?.({ active, maxActive, limit: normalizedLimit });
  }

  async function run(fn) {
    if (active >= normalizedLimit) {
      await new Promise((resolve) => queue.push(resolve));
    }
    active += 1;
    maxActive = Math.max(maxActive, active);
    emitActiveChange();
    try {
      return await fn();
    } finally {
      active -= 1;
      emitActiveChange();
      const next = queue.shift();
      if (next) next();
    }
  }

  return {
    run,
    get maxActive() {
      return maxActive;
    },
    get active() {
      return active;
    },
    get limit() {
      return normalizedLimit;
    }
  };
}

export async function executePlannedRowsWithConcurrency({
  plan,
  action,
  promotionId,
  promotionType,
  accountId,
  taskId,
  mode = 'real',
  writeConcurrency = 2,
  schedule,
  executeOne,
  saveResult,
  toErrorText = (error) => error?.message || String(error),
  shouldCancel,
  onItemEvent,
  onStopRequested,
  classifyError = () => ({ interfaceFailure: false }),
  retryOptions = {}
}) {
  const rows = plan?.rows || [];
  const counts = { success: 0, failed: 0, skipped: 0 };
  const normalizedWriteConcurrency = normalizeWriteConcurrency(writeConcurrency);
  const retryConfig = normalizeRetryOptions(retryOptions);
  const retrySummary = {
    immediate_retries: 0,
    deferred: 0,
    deferred_success: 0,
    deferred_failed: 0,
    deferred_skipped: 0
  };
  const successfulItemKeys = new Set();

  const skippedRows = [];
  const plannedRows = [];
  for (const row of rows) {
    if (row.status === 'planned') plannedRows.push(row);
    else skippedRows.push(row);
  }

  for (const row of skippedRows) {
    counts.skipped += 1;
    await onItemEvent?.({ type: 'item_skipped', row, status: 'skipped', reason: row.reason });
    await saveResult?.({
      taskId,
      accountId,
      promotionId,
      promotionType,
      itemId: row.item?.item_id || '',
      action,
      mode,
      status: 'skipped',
      dealPrice: row.deal_price,
      errorCn: row.reason
    });
  }

  async function executeRow(row, { finalRetry = false } = {}) {
    if (shouldCancel?.()) {
      const reason = '执行任务已停止，未开始的商品留待下次继续';
      await onItemEvent?.({ type: 'item_cancelled_before_start', row, status: 'skipped', reason });
      await saveResult?.({
        taskId,
        accountId,
        promotionId,
        promotionType,
        itemId: row.item?.item_id || '',
        action,
        mode,
        status: 'skipped',
        dealPrice: row.deal_price,
        errorCn: reason
      });
      return { itemId: row.item?.item_id || '', status: 'skipped', reason, cancelled: true };
    }
    const itemKey = resultItemKey(row, accountId, promotionId, promotionType, action);
    if (successfulItemKeys.has(itemKey)) {
      const reason = '本批次内该商品已成功，跳过重复提交';
      await onItemEvent?.({ type: 'item_skipped', row, status: 'skipped', reason, finalRetry });
      return { itemId: row.item?.item_id || '', status: 'skipped', reason, duplicateSuccess: true };
    }
    const maxRetries = finalRetry ? 0 : retryConfig.maxImmediateRetries;
    for (let retryCount = 0; retryCount <= maxRetries; retryCount += 1) {
      const attempt = retryCount + 1;
      const startedAt = new Date().toISOString();
      await onItemEvent?.({ type: 'item_start', row, status: 'started', startedAt, attempt, retryCount, finalRetry });
      const startedMs = Date.now();
      try {
        const runWrite = () => executeOne({
          row,
          action,
          itemId: row.item?.item_id,
          promotionId,
          promotionType,
          dealPrice: row.deal_price,
          attempt,
          retryCount,
          finalRetry
        });
        const response = schedule ? await schedule(runWrite) : await runWrite();
        const finishedAt = new Date().toISOString();
        await saveResult?.({
          taskId,
          accountId,
          promotionId,
          promotionType,
          itemId: row.item?.item_id || '',
          action,
          mode,
          status: 'success',
          dealPrice: row.deal_price,
          response
        });
        await onItemEvent?.({
          type: 'item_finish',
          row,
          status: 'success',
          startedAt,
          finishedAt,
          durationMs: Date.now() - startedMs,
          response,
          attempt,
          retryCount,
          finalRetry
        });
        successfulItemKeys.add(itemKey);
        return { itemId: row.item?.item_id || '', status: 'success', response, finalRetry };
      } catch (error) {
        const errorCn = toErrorText(error);
        if (isPolicyBlockedError(error)) {
          const finishedAt = new Date().toISOString();
          await saveResult?.({
            taskId,
            accountId,
            promotionId,
            promotionType,
            itemId: row.item?.item_id || '',
            action,
            mode,
            status: 'skipped',
            dealPrice: row.deal_price,
            errorCn,
            errorRaw: JSON.stringify({ message: error?.message, code: error?.code || null, policyBlocked: true })
          });
          await onItemEvent?.({
            type: 'item_finish',
            row,
            status: 'skipped',
            startedAt,
            finishedAt,
            durationMs: Date.now() - startedMs,
            error,
            errorCn,
            policyBlocked: true,
            attempt,
            retryCount,
            finalRetry
          });
          return { itemId: row.item?.item_id || '', status: 'skipped', reason: errorCn, policyBlocked: true };
        }
        const classifiedError = classifyError(error) || {};
        const retryable = isRetryableInterfaceFailure(classifiedError);
        const finishedAt = new Date().toISOString();
        if (retryable && retryCount < maxRetries) {
          retrySummary.immediate_retries += 1;
          const retryNotice = retryNoticeText(classifiedError, retryCount + 1);
          await onItemEvent?.({
            type: 'item_retry',
            row,
            status: 'retrying',
            startedAt,
            finishedAt,
            durationMs: Date.now() - startedMs,
            error,
            errorCn,
            isInterfaceFailure: true,
            attempt,
            retryCount,
            nextRetry: retryCount + 1,
            reason: retryNotice,
            finalRetry
          });
          await delay(retryBackoffMs(retryConfig, classifiedError, retryCount));
          continue;
        }
        if (retryable && !finalRetry && retryConfig.deferredFinalRetry) {
          retrySummary.deferred += 1;
          const reason = `${retryableReasonName(classifiedError)}，已留到本批末尾补跑。`;
          await onItemEvent?.({
            type: 'item_deferred',
            row,
            status: 'deferred',
            startedAt,
            finishedAt,
            durationMs: Date.now() - startedMs,
            error,
            errorCn,
            isInterfaceFailure: true,
            attempt,
            retryCount,
            deferred: true,
            reason
          });
          return { itemId: row.item?.item_id || '', status: 'deferred', row, error, errorCn, classifiedError };
        }
        await saveResult?.({
          taskId,
          accountId,
          promotionId,
          promotionType,
          itemId: row.item?.item_id || '',
          action,
          mode,
          status: 'failed',
          dealPrice: row.deal_price,
          errorCn,
          errorRaw: JSON.stringify({ message: error?.message, status: error?.status, body: error?.body || null })
        });
        await onItemEvent?.({
          type: 'item_finish',
          row,
          status: 'failed',
          startedAt,
          finishedAt,
          durationMs: Date.now() - startedMs,
          error,
          errorCn,
          isInterfaceFailure: Boolean(classifiedError.interfaceFailure),
          attempt,
          retryCount,
          finalRetry
        });
        if (classifiedError.interfaceFailure) onStopRequested?.({ error, errorCn, row, classifiedError });
        return { itemId: row.item?.item_id || '', status: 'failed', errorCn, finalRetry };
      }
    }
    return { itemId: row.item?.item_id || '', status: 'failed', errorCn: '未知失败' };
  }

  const executed = await mapLimitedWithCap(plannedRows, normalizedWriteConcurrency, MAX_WRITE_CONCURRENCY, async (row) => {
    return executeRow(row);
  });

  const deferredRows = executed.filter((result) => result?.status === 'deferred').map((result) => result.row);
  let deferredExecuted = [];
  if (deferredRows.length > 0 && retryConfig.deferredFinalRetry && !shouldCancel?.()) {
    await onItemEvent?.({ type: 'deferred_retry_start', status: 'started', count: deferredRows.length, deferred: true });
    deferredExecuted = await mapLimitedWithCap(
      deferredRows,
      Math.min(normalizedWriteConcurrency, retryConfig.deferredConcurrency),
      MAX_WRITE_CONCURRENCY,
      async (row) => executeRow(row, { finalRetry: true })
    );
    retrySummary.deferred_success = deferredExecuted.filter((result) => result?.status === 'success').length;
    retrySummary.deferred_failed = deferredExecuted.filter((result) => result?.status === 'failed').length;
    retrySummary.deferred_skipped = deferredExecuted.filter((result) => result?.status === 'skipped').length;
    await onItemEvent?.({
      type: 'deferred_retry_done',
      status: 'completed',
      count: deferredRows.length,
      success: retrySummary.deferred_success,
      failed: retrySummary.deferred_failed,
      skipped: retrySummary.deferred_skipped,
      deferred: true
    });
  }

  const finalResults = [
    ...executed.filter((result) => result?.status !== 'deferred'),
    ...deferredExecuted
  ];
  for (const result of finalResults) {
    if (result?.status === 'success') counts.success += 1;
    else if (result?.status === 'skipped') counts.skipped += 1;
    else counts.failed += 1;
  }

  return {
    counts,
    writeConcurrency: normalizedWriteConcurrency,
    maxActive: Math.max(executed.maxActive || 0, deferredExecuted.maxActive || 0),
    retrySummary,
    results: [
      ...skippedRows.map((row) => ({ itemId: row.item?.item_id || '', status: 'skipped', reason: row.reason })),
      ...finalResults
    ]
  };
}

function isPolicyBlockedError(error) {
  return Boolean(
    error?.policyBlocked
    || error?.code === 'submit_payload_blocked'
    || /不能批量|尚未允许批量真实提交|缺少可提交 payload/.test(error?.message || '')
  );
}

function normalizeRetryOptions(options = {}) {
  const maxImmediateRetries = Number.isFinite(Number(options.maxImmediateRetries))
    ? Math.max(0, Math.min(5, Math.floor(Number(options.maxImmediateRetries))))
    : DEFAULT_RETRY_OPTIONS.maxImmediateRetries;
  const retryBackoffMs = Array.isArray(options.retryBackoffMs)
    ? options.retryBackoffMs.map((value) => Math.max(0, Math.floor(Number(value) || 0)))
    : DEFAULT_RETRY_OPTIONS.retryBackoffMs;
  const deferredConcurrency = Number.isFinite(Number(options.deferredConcurrency))
    ? Math.max(1, Math.floor(Number(options.deferredConcurrency)))
    : DEFAULT_RETRY_OPTIONS.deferredConcurrency;
  return {
    maxImmediateRetries,
    retryBackoffMs,
    deferredFinalRetry: options.deferredFinalRetry !== false,
    deferredConcurrency
  };
}

function retryBackoffMs(config, classifiedError, retryIndex) {
  const fromConfig = config.retryBackoffMs[retryIndex];
  if (Number.isFinite(fromConfig)) return Math.max(0, fromConfig);
  if (classifiedError?.rateLimited) return [1000, 2000, 4000][retryIndex] || 4000;
  return [500, 1000, 2000][retryIndex] || 2000;
}

function isRetryableInterfaceFailure(classifiedError = {}) {
  return Boolean(
    classifiedError.interfaceFailure
    && !classifiedError.authFailure
    && (classifiedError.rateLimited || classifiedError.transientFailure || classifiedError.category === 'rate_limited' || classifiedError.category === 'transient_interface_failure')
  );
}

function retryNoticeText(classifiedError = {}, retryNumber) {
  return `${retryableReasonName(classifiedError)}，正在第 ${retryNumber} 次重试...`;
}

function retryableReasonName(classifiedError = {}) {
  if (classifiedError.rateLimited || classifiedError.category === 'rate_limited') return '平台限流';
  return '网络失败';
}

function resultItemKey(row, accountId, promotionId, promotionType, action) {
  return [
    accountId || '',
    promotionId || '',
    promotionType || '',
    action || '',
    row?.item?.item_id || row?.item?.id || ''
  ].join('|');
}

function delay(ms) {
  if (!ms) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms));
}
