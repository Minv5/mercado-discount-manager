const DEFAULT_MAX_ATTEMPTS = 3;
const DEFAULT_RETRY_DELAYS_MS = Object.freeze([1_000, 2_000]);

function numericStatus(error) {
  const value = Number(error?.status ?? error?.statusCode ?? error?.response?.status ?? error?.body?.status);
  return Number.isInteger(value) && value >= 100 && value <= 599 ? value : null;
}

function safeCode(value) {
  const text = String(value || '').trim().toUpperCase();
  return /^[A-Z0-9_]{1,80}$/.test(text) ? text : null;
}

function errorCode(error) {
  return safeCode(error?.code || error?.body?.code || error?.body?.error || error?.body?.message_code);
}

function causeCode(error) {
  return safeCode(error?.cause_code || error?.cause?.code);
}

function errorText(error) {
  return [
    error?.message,
    error?.code,
    error?.cause?.message,
    error?.cause?.code,
  ].filter(Boolean).join(' ').toLowerCase();
}

export function accountAccessErrorKind(error) {
  const status = numericStatus(error);
  const code = String(error?.code || error?.cause?.code || '').toUpperCase();
  const text = errorText(error);
  if (status === 401 || /invalid[_ -]?token|expired.*token|invalid.*access.*token/.test(text)) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 429) return 'rate_limit';
  if (status >= 500 && status <= 599) return 'service';
  if (code.includes('ETIMEDOUT') || code.includes('TIMEOUT') || /timeout|timed out/.test(text)) return 'timeout';
  if (/ECONNRESET|ECONNREFUSED|EAI_AGAIN|UND_ERR|SOCKET|FETCH_FAILED/.test(code)
    || /fetch failed|network|socket|connection reset|connection refused/.test(text)) return 'network';
  if (status >= 400 && status <= 499) return 'business';
  return 'unknown';
}

export function isAccountAccessTransientError(error) {
  return ['network', 'timeout', 'rate_limit', 'service'].includes(accountAccessErrorKind(error));
}

export function isAccountAccessUnauthorizedError(error) {
  return accountAccessErrorKind(error) === 'unauthorized';
}

function attachDiagnostic(error, diagnostic) {
  const target = error && (typeof error === 'object' || typeof error === 'function')
    ? error
    : new Error(String(error || '账号预检失败'));
  target.operation = 'account_access';
  target.endpoint_family = 'users_me';
  target.account_access_diagnostic = diagnostic;
  return target;
}

export function buildAccountAccessDiagnostic({
  attemptCount,
  elapsedMs,
  error,
  errorKind,
  phase = 'initial',
  refreshCount = 0,
} = {}) {
  return {
    operation: 'account_access',
    endpoint_family: 'users_me',
    attempt_count: Math.max(0, Math.floor(Number(attemptCount || 0))),
    elapsed_ms: Math.max(0, Math.floor(Number(elapsedMs || 0))),
    error_kind: String(errorKind || accountAccessErrorKind(error) || 'unknown'),
    http_status: numericStatus(error),
    code: errorCode(error),
    cause_code: causeCode(error),
    phase: String(phase || 'initial'),
    refresh_count: Math.max(0, Math.floor(Number(refreshCount || 0))),
  };
}

function retryDelay(retryDelaysMs, retryIndex, error = null) {
  const values = Array.isArray(retryDelaysMs) && retryDelaysMs.length
    ? retryDelaysMs
    : DEFAULT_RETRY_DELAYS_MS;
  const index = Math.min(values.length - 1, Math.max(0, retryIndex));
  const value = Number(values[index]);
  const retryAfter = Number(error?.retryAfterMs ?? error?.retry_after_ms);
  const boundedValue = Number.isFinite(value) && value >= 0 ? value : 0;
  return Number.isFinite(retryAfter) && retryAfter >= 0
    ? Math.max(boundedValue, retryAfter)
    : boundedValue;
}

function normalizeMaxAttempts(value) {
  const number = Math.floor(Number(value || DEFAULT_MAX_ATTEMPTS));
  return Math.max(1, Math.min(6, Number.isFinite(number) ? number : DEFAULT_MAX_ATTEMPTS));
}

/**
 * Performs a live-account identity preflight without ever falling back to a
 * cached profile. The caller owns scheduling of readProfile; this helper only
 * decides which errors may be retried and when a single token refresh is
 * allowed.
 */
export async function runAccountAccessPreflight({
  accountId = '',
  readProfile,
  refreshToken = null,
  maxAttempts = DEFAULT_MAX_ATTEMPTS,
  retryDelaysMs = DEFAULT_RETRY_DELAYS_MS,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  now = () => Date.now(),
} = {}) {
  if (typeof readProfile !== 'function') throw new TypeError('readProfile is required');
  const attemptsLimit = normalizeMaxAttempts(maxAttempts);
  const startedAt = now();
  let attemptCount = 0;
  let refreshCount = 0;
  let lastPhase = 'initial';
  let lastError = null;

  const readPhase = async (phase) => {
    lastPhase = phase;
    for (let phaseAttempt = 0; phaseAttempt < attemptsLimit; phaseAttempt += 1) {
      attemptCount += 1;
      try {
        return await readProfile({ accountId: String(accountId || ''), attempt: attemptCount, phase });
      } catch (error) {
        lastError = error;
        const kind = accountAccessErrorKind(error);
        const canRetry = isAccountAccessTransientError(error) && phaseAttempt + 1 < attemptsLimit;
        if (!canRetry) throw error;
        await sleep(retryDelay(retryDelaysMs, phaseAttempt, error));
        if (kind === 'unknown') throw error;
      }
    }
    throw lastError || new Error('账号预检失败');
  };

  try {
    try {
      return await readPhase('initial');
    } catch (error) {
      lastError = error;
      if (!isAccountAccessUnauthorizedError(error) || typeof refreshToken !== 'function' || refreshCount >= 1) {
        throw error;
      }
      refreshCount += 1;
      lastPhase = 'refresh';
      try {
        await refreshToken({ accountId: String(accountId || ''), refreshCount });
      } catch (refreshError) {
        lastError = refreshError;
        throw refreshError;
      }
      return await readPhase('after_refresh');
    }
  } catch (error) {
    const finalError = error || lastError || new Error('账号预检失败');
    const diagnostic = buildAccountAccessDiagnostic({
      attemptCount,
      elapsedMs: now() - startedAt,
      error: finalError,
      errorKind: accountAccessErrorKind(finalError),
      phase: lastPhase,
      refreshCount,
    });
    throw attachDiagnostic(finalError, diagnostic);
  }
}

export const ACCOUNT_ACCESS_PREFLIGHT_DEFAULTS = Object.freeze({
  maxAttempts: DEFAULT_MAX_ATTEMPTS,
  retryDelaysMs: DEFAULT_RETRY_DELAYS_MS,
});
