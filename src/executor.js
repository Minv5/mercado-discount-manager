import { MAX_WRITE_CONCURRENCY, normalizeWriteConcurrency, mapLimitedWithCap } from './concurrency.js';

const DEFAULT_RETRY_OPTIONS = {
  maxImmediateRetries: 3,
  retryBackoffMs: [1000, 2000, 4000],
  deferredFinalRetry: true,
  deferredConcurrency: 20,
  maxFinalFailureRetries: 0,
  finalFailureBackoffMs: [1000, 2000, 4000],
  finalFailureConcurrency: 20,
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
  mode,
  writeConcurrency = 2,
  schedule,
  executeOne,
  saveResult,
  toErrorText = (error) => error?.message || String(error),
  shouldCancel,
  onItemEvent,
  onStopRequested,
  onPending,
  beforeExecuteRow,
  onCheckpoint,
  chunkSize = 2000,
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
    deferred_skipped: 0,
    deferred_pending: 0,
    final_failure_retry_rows: 0,
    final_failure_retry_attempts: 0,
    final_failure_retry_success: 0,
    final_failure_retry_pending: 0,
    final_failure_retry_failed: 0,
    final_failure_retry_skipped: 0,
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

  async function executeRow(inputRow, {
    finalRetry = false,
    finalFailureRetryRound = 0,
    finalFailureRetryLimit = 0,
  } = {}) {
    let row = inputRow;
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
    if (typeof beforeExecuteRow === 'function') {
      try {
        const prepared = await beforeExecuteRow(row, { action, promotionId, promotionType, accountId, taskId, mode, finalRetry });
        if (prepared?.row) row = prepared.row;
        if (prepared?.changed) {
          await onItemEvent?.({
            type: 'item_revalidated',
            row,
            status: 'revalidated',
            changedFields: prepared.changed_fields || [],
            finalRetry,
          });
        }
      } catch (error) {
        const errorCn = toErrorText(error);
        if (!isPolicyBlockedError(error)) throw error;
        await saveResult?.({
          taskId, accountId, promotionId, promotionType,
          itemId: row.item?.item_id || '', action, mode, status: 'skipped',
          dealPrice: row.deal_price, errorCn,
          errorRaw: JSON.stringify({ code: error?.code || null, policyBlocked: true }),
        });
        await onItemEvent?.({ type: 'item_finish', row, status: 'skipped', error, errorCn, policyBlocked: true, finalRetry });
        return { itemId: row.item?.item_id || '', status: 'skipped', reason: errorCn, policyBlocked: true };
      }
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
        // The Mercado write already succeeded. Mark the relation as successful
        // BEFORE local persistence so a storage failure can never cause a
        // duplicate submission of the same item (repeat guard + same-batch dedupe).
        successfulItemKeys.add(itemKey);
        try {
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
        } catch (storageError) {
          // Interface write succeeded but the local result row could not be
          // persisted. Do NOT classify this as a business failure: retrying
          // would re-submit an already-enrolled item. Report it separately.
          await onItemEvent?.({
            type: 'item_storage_error',
            row,
            status: 'success',
            startedAt,
            finishedAt,
            durationMs: Date.now() - startedMs,
            error: storageError,
            errorCn: toErrorText(storageError),
            attempt,
            retryCount,
            finalRetry
          });
          return { itemId: row.item?.item_id || '', status: 'success', response, row, finalRetry, storageError: true };
        }
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
        return { itemId: row.item?.item_id || '', status: 'success', response, row, finalRetry };
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
        if (classifiedError.ambiguousWrite === true) {
          const pending = {
            taskId, accountId, promotionId, promotionType,
            itemId: row.item?.item_id || '', action, mode, status: 'pending',
            dealPrice: row.deal_price, errorCn,
            errorRaw: JSON.stringify({ message: error?.message, status: error?.status, ambiguous_write: true }),
          };
          await saveResult?.(pending);
          await onPending?.({ row, error, errorCn, classifiedError, attempt, retryCount, finalRetry });
          await onItemEvent?.({ type: 'item_pending', row, status: 'pending', startedAt, finishedAt, error, errorCn, attempt, retryCount, finalRetry, ambiguousWrite: true });
          return { itemId: row.item?.item_id || '', status: 'pending', row, errorCn, classifiedError, ambiguousWrite: true };
        }
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
        if (retryable && finalRetry) {
          const pending = {
            taskId,
            accountId,
            promotionId,
            promotionType,
            itemId: row.item?.item_id || '',
            action,
            mode,
            status: 'pending',
            dealPrice: row.deal_price,
            errorCn,
            errorRaw: JSON.stringify({ message: error?.message, status: error?.status, body: error?.body || null, retryable: true })
          };
          await saveResult?.(pending);
          await onPending?.({ row, error, errorCn, classifiedError, attempt, retryCount, finalRetry });
          await onItemEvent?.({
            type: 'item_pending',
            row,
            status: 'pending',
            startedAt,
            finishedAt,
            durationMs: Date.now() - startedMs,
            error,
            errorCn,
            isInterfaceFailure: true,
            attempt,
            retryCount,
            finalRetry,
          });
          return { itemId: row.item?.item_id || '', status: 'pending', row, errorCn, classifiedError, finalRetry: true };
        }
        const exhaustedFinalFailureRetry = finalFailureRetryRound > 0
          && finalFailureRetryRound >= finalFailureRetryLimit;
        if (exhaustedFinalFailureRetry) {
          const exhaustedReason = `明确失败末尾重试 ${finalFailureRetryLimit} 次后最终失败：${errorCn}`;
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
            errorCn: exhaustedReason,
            errorRaw: JSON.stringify({ message: error?.message, status: error?.status, retry_exhausted: true }),
          });
          await onItemEvent?.({
            type: 'item_finish',
            row,
            status: 'failed',
            startedAt,
            finishedAt,
            durationMs: Date.now() - startedMs,
            error,
            errorCn: exhaustedReason,
            isInterfaceFailure: Boolean(classifiedError.interfaceFailure),
            attempt,
            retryCount,
            retryRound: finalFailureRetryRound,
            finalRetry: true,
            retryExhausted: true,
          });
          return {
            itemId: row.item?.item_id || '',
            status: 'failed',
            errorCn: exhaustedReason,
            row,
            error,
            classifiedError,
            finalRetry: true,
            retryExhausted: true,
          };
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
        if (classifiedError.authFailure) onStopRequested?.({ error, errorCn, row, classifiedError });
        return { itemId: row.item?.item_id || '', status: 'failed', errorCn, row, error, classifiedError, finalRetry };
      }
    }
    return { itemId: row.item?.item_id || '', status: 'failed', errorCn: '未知失败' };
  }

  const normalizedChunkSize = Math.max(1, Math.min(10_000, Math.floor(Number(chunkSize) || 2000)));
  const executed = [];
  let executedMaxActive = 0;
  let checkpointProcessed = 0;
  const chunkCount = Math.ceil(plannedRows.length / normalizedChunkSize);
  for (let offset = 0, chunkIndex = 0; offset < plannedRows.length; offset += normalizedChunkSize, chunkIndex += 1) {
    const chunk = plannedRows.slice(offset, offset + normalizedChunkSize);
    await onItemEvent?.({ type: 'chunk_start', chunkIndex, chunkCount, chunkSize: chunk.length, processed: checkpointProcessed, total: plannedRows.length });
    const chunkResults = await mapLimitedWithCap(chunk, normalizedWriteConcurrency, MAX_WRITE_CONCURRENCY, async (row) => {
      const result = await executeRow(row);
      checkpointProcessed += 1;
      await onCheckpoint?.({ type: 'item', processed: checkpointProcessed, total: plannedRows.length, chunkIndex, chunkCount, result });
      return result;
    });
    executed.push(...chunkResults);
    executedMaxActive = Math.max(executedMaxActive, Number(chunkResults.maxActive || 0));
    await onCheckpoint?.({ type: 'chunk', processed: checkpointProcessed, total: plannedRows.length, chunkIndex, chunkCount });
    await onItemEvent?.({ type: 'chunk_done', chunkIndex, chunkCount, chunkSize: chunk.length, processed: checkpointProcessed, total: plannedRows.length });
    if (shouldCancel?.()) break;
  }
  Object.defineProperty(executed, 'maxActive', { value: executedMaxActive, configurable: true });

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
    retrySummary.deferred_pending = deferredExecuted.filter((result) => result?.status === 'pending').length;
    await onItemEvent?.({
      type: 'deferred_retry_done',
      status: 'completed',
      count: deferredRows.length,
      success: retrySummary.deferred_success,
      failed: retrySummary.deferred_failed,
      skipped: retrySummary.deferred_skipped,
      pending: retrySummary.deferred_pending,
      deferred: true
    });
  } else if (deferredRows.length > 0 && shouldCancel?.()) {
    deferredExecuted = await Promise.all(deferredRows.map(async (row) => {
      const reason = '用户已停止执行，留置商品未再提交。';
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
        errorCn: reason,
      });
      await onItemEvent?.({ type: 'item_cancelled_before_start', row, status: 'skipped', reason, deferred: true });
      return { itemId: row.item?.item_id || '', status: 'skipped', reason, cancelled: true };
    }));
    retrySummary.deferred_skipped = deferredExecuted.length;
  }

  let finalResults = [
    ...executed.filter((result) => result?.status !== 'deferred'),
    ...deferredExecuted
  ];
  let finalFailureRows = finalResults
    .filter((result) => result?.status === 'failed' && result?.row && isRetryableInterfaceFailure(result?.classifiedError))
    .map((result) => result.row);
  if (finalFailureRows.length > 0 && retryConfig.maxFinalFailureRetries > 0 && !shouldCancel?.()) {
    retrySummary.final_failure_retry_rows = finalFailureRows.length;
    const retained = finalResults.filter((result) => !(
      result?.status === 'failed' && result?.row && isRetryableInterfaceFailure(result?.classifiedError)
    ));
    const completedRetries = [];
    for (let round = 1; round <= retryConfig.maxFinalFailureRetries && finalFailureRows.length > 0 && !shouldCancel?.(); round += 1) {
      const backoff = retryConfig.finalFailureBackoffMs[round - 1] || 0;
      if (backoff > 0) await delay(backoff);
      await onItemEvent?.({
        type: 'final_failure_retry_start',
        status: 'started',
        round,
        maxRounds: retryConfig.maxFinalFailureRetries,
        count: finalFailureRows.length,
      });
      const roundResults = await mapLimitedWithCap(
        finalFailureRows,
        Math.min(normalizedWriteConcurrency, retryConfig.finalFailureConcurrency),
        MAX_WRITE_CONCURRENCY,
        async (row) => executeRow(row, {
          finalRetry: true,
          finalFailureRetryRound: round,
          finalFailureRetryLimit: retryConfig.maxFinalFailureRetries,
        }),
      );
      retrySummary.final_failure_retry_attempts += roundResults.length;
      const remaining = roundResults.filter((result) => result?.status === 'failed');
      completedRetries.push(...roundResults.filter((result) => result?.status !== 'failed'));
      await onItemEvent?.({
        type: 'final_failure_retry_done',
        status: 'completed',
        round,
        maxRounds: retryConfig.maxFinalFailureRetries,
        count: roundResults.length,
        success: roundResults.filter((result) => result?.status === 'success').length,
        pending: roundResults.filter((result) => result?.status === 'pending').length,
        skipped: roundResults.filter((result) => result?.status === 'skipped').length,
        failed: remaining.length,
      });
      finalFailureRows = remaining.map((result) => result.row).filter(Boolean);
      if (round === retryConfig.maxFinalFailureRetries) completedRetries.push(...remaining);
    }
    finalResults = [...retained, ...completedRetries];
    retrySummary.final_failure_retry_success = completedRetries.filter((result) => result?.status === 'success').length;
    retrySummary.final_failure_retry_pending = completedRetries.filter((result) => result?.status === 'pending').length;
    retrySummary.final_failure_retry_failed = completedRetries.filter((result) => result?.status === 'failed').length;
    retrySummary.final_failure_retry_skipped = completedRetries.filter((result) => result?.status === 'skipped').length;
  }
  for (const result of finalResults) {
    if (result?.status === 'success') counts.success += 1;
    else if (result?.status === 'skipped') counts.skipped += 1;
    else if (result?.status === 'pending') counts.pending = Number(counts.pending || 0) + 1;
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
  const maxFinalFailureRetries = Number.isFinite(Number(options.maxFinalFailureRetries))
    ? Math.max(0, Math.min(3, Math.floor(Number(options.maxFinalFailureRetries))))
    : DEFAULT_RETRY_OPTIONS.maxFinalFailureRetries;
  const finalFailureBackoffMs = Array.isArray(options.finalFailureBackoffMs)
    ? options.finalFailureBackoffMs.map((value) => Math.max(0, Math.floor(Number(value) || 0)))
    : DEFAULT_RETRY_OPTIONS.finalFailureBackoffMs;
  const finalFailureConcurrency = Number.isFinite(Number(options.finalFailureConcurrency))
    ? Math.max(1, Math.floor(Number(options.finalFailureConcurrency)))
    : DEFAULT_RETRY_OPTIONS.finalFailureConcurrency;
  return {
    maxImmediateRetries,
    retryBackoffMs,
    deferredFinalRetry: options.deferredFinalRetry !== false,
    deferredConcurrency,
    maxFinalFailureRetries,
    finalFailureBackoffMs,
    finalFailureConcurrency,
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
