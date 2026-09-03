function errorStatus(error = {}) {
  return Number(error.status || error.http_status || error.body?.status || error.details?.status || 0);
}

function errorText(error = {}) {
  const safeJson = (value) => {
    try { return value && typeof value === 'object' ? JSON.stringify(value).slice(0, 500) : ''; } catch { return ''; }
  };
  const values = [
    error.code,
    error.message,
    error.message_cn,
    error.raw_error_summary,
    error.body?.error,
    error.body?.message,
    error.details?.error,
    error.details?.message,
    safeJson(error.body),
    safeJson(error.details),
  ];
  return values.filter(Boolean).join(' ').toLowerCase();
}

export function isTransientOfferLockError(error = {}) {
  if (errorStatus(error) === 423) return true;
  return /lockedentityexception|offer\s+locked|locked\s+entity/.test(errorText(error));
}

export function classifyWriteFailure(error = {}, { toErrorText = (value) => value?.message || String(value || '') } = {}) {
  const status = errorStatus(error);
  const raw = errorText(error);
  const errorCn = String(toErrorText(error) || '');
  const rateLimited = status === 429 || /too many|rate.?limit|\b429\b|限流/.test(raw);
  const authFailure = status === 401 || /invalid_token|unauthorized|\b401\b/.test(raw);
  const transientOfferLock = isTransientOfferLockError(error);
  const businessSignal = /offer_id|offer id|invalid_parameter|bad_request|under_review|price/.test(raw)
    || /活动报价|报价|参数|审核|价格|缺少或无效/.test(errorCn);
  const businessFailure = !rateLimited && !authFailure && !transientOfferLock && (
    (status >= 400 && status < 500)
    || (status === 0 && businessSignal)
  );
  const serverFailure = !businessFailure && (status >= 500 || /5\d\d|server|temporarily|service unavailable/.test(raw));
  const timeoutFailure = /timeout|timed out|504|fetch failed|socket|network|econnreset|etimedout|und_err|aborted/.test(raw);
  const transientFailure = !businessFailure && (transientOfferLock || serverFailure || timeoutFailure);
  return {
    status: status || null,
    category: rateLimited ? 'rate_limited' : authFailure ? 'auth_failure' : transientOfferLock ? 'transient_offer_lock' : transientFailure ? 'transient_interface_failure' : businessFailure ? 'business_failure' : null,
    interfaceFailure: !businessFailure && (rateLimited || authFailure || transientFailure),
    rateLimited,
    authFailure,
    transientFailure,
    transientOfferLock,
    ambiguousWrite: (timeoutFailure || serverFailure) && !transientOfferLock,
    businessFailure,
    errorCn,
  };
}

export function shouldInvalidateWriteCache(result = {}) {
  if (result.cancelled) return false;
  return result.ok === true
    || result.retryable_failure === true
    || result.interface_failure === true;
}
