function errorStatus(error = {}) {
  return Number(error.status || error.http_status || error.body?.status || error.details?.status || 0);
}

function errorText(error = {}) {
  const values = [
    error.code,
    error.message,
    error.message_cn,
    error.raw_error_summary,
    error.body?.error,
    error.body?.message,
    error.details?.error,
    error.details?.message,
  ];
  return values.filter(Boolean).join(' ').toLowerCase();
}

export function isTransientOfferLockError(error = {}) {
  if (errorStatus(error) === 423) return true;
  return /lockedentityexception|offer\s+locked|locked\s+entity/.test(errorText(error));
}

export function shouldInvalidateWriteCache(result = {}) {
  if (result.cancelled) return false;
  return result.ok === true
    || result.retryable_failure === true
    || result.interface_failure === true;
}
