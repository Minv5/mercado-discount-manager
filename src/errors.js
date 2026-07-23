const mappings = [
  [/invalid_token|expired|unauthorized|\b401\b/i, '授权失效或 token 已过期'],
  [/under_review|item status is not allowed/i, '商品正在审核中，平台不允许报名'],
  [/forbidden|permission|\b403\b|not authorized|access denied|caller.*not/i, '账号权限不足或应用权限不足'],
  [/not found|\b404\b/i, '活动或商品不存在'],
  [/too many|rate|\b429\b/i, '接口限流，请稍后重试'],
  [/credible|discounted price/i, '折扣价不被平台认可'],
  [/price.*minimum|min_discount/i, '活动价低于平台允许最低价'],
  [/price.*maximum|max_discount/i, '活动价高于平台允许最高价'],
  [/campaign|promotion.*finished|ended/i, '活动已结束或不可用'],
  [/stock|inventory/i, '库存数量不符合活动要求'],
  [/offer/i, '缺少或无效的活动报价信息'],
  [/bad request|invalid|400/i, '请求参数不符合平台要求'],
  [/server|temporarily|timeout|5\d\d/i, '平台接口临时异常']
];

const TIMEOUT_CODES = new Set([
  'ETIMEDOUT', 'ESOCKETTIMEDOUT', 'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT',
]);
const NETWORK_CODES = new Set([
  'ECONNRESET', 'ECONNREFUSED', 'ECONNABORTED', 'EHOSTUNREACH', 'ENETUNREACH', 'ENETDOWN', 'EAI_AGAIN',
  'ENOTFOUND', 'UND_ERR_SOCKET', 'UND_ERR_CONNECT',
]);
const LOCAL_STORAGE_CODES = new Set(['EPERM', 'EACCES', 'EBUSY']);

function safeCode(value) {
  const text = String(value || '').trim();
  return /^[A-Za-z0-9_.-]{1,80}$/.test(text) ? text : null;
}

function numericStatus(error) {
  for (const value of [error?.status, error?.statusCode, error?.response?.status, error?.cause?.status]) {
    const number = Number(value || 0);
    if (number >= 100 && number <= 599) return number;
  }
  return null;
}

export function classifyPrepareError(error) {
  const httpStatus = numericStatus(error);
  const code = safeCode(error?.code);
  const causeCode = safeCode(error?.cause?.code);
  const name = String(error?.name || '');
  const message = String(error?.message || error || '');
  const codes = new Set([code, causeCode].filter(Boolean));
  let errorKind = 'unknown';
  if (httpStatus === 429) errorKind = 'rate_limit';
  else if (httpStatus != null && httpStatus >= 500) errorKind = 'service';
  else if ([...codes].some((value) => TIMEOUT_CODES.has(value)) || /timeout|timed out/i.test(`${name} ${message}`)) errorKind = 'timeout';
  else if ([...codes].some((value) => NETWORK_CODES.has(value)) || /fetch failed|network error|socket hang up/i.test(message)) errorKind = 'network';
  else if ([...codes].some((value) => LOCAL_STORAGE_CODES.has(value))) errorKind = 'local_storage';
  else if (error instanceof TypeError || error instanceof SyntaxError || error instanceof RangeError
      || /CONTRACT|SCHEMA|MALFORMED|INVALID_SHAPE|ACTIVITY_ACCOUNT_ROUTE|PREPARATION_STATE/i.test(String(code || ''))) errorKind = 'local_contract';
  return {
    error_kind: errorKind,
    http_status: httpStatus,
    code,
    cause_code: causeCode,
    operation: safeCode(error?.operation || error?.prepare_context?.operation),
    storage_operation: safeCode(error?.storage_operation || error?.prepare_context?.storage_operation),
    storage_syscall: safeCode(error?.storage_syscall || error?.prepare_context?.storage_syscall),
    storage_target: safeCode(error?.storage_target || error?.prepare_context?.storage_target),
  };
}

export function prepareErrorMessage(classification = {}) {
  return ({
    rate_limit: '平台读取触发限流，请稍后重新核对。',
    service: '平台服务暂时异常，请稍后重新核对。',
    timeout: '平台读取超时，请稍后重新核对。',
    network: '网络连接暂时异常，请检查网络后重新核对。',
    local_contract: '程序处理平台数据时发现格式异常，已安全停止准备。',
    local_storage: '本地状态暂时无法保存，已安全停止准备，请稍后重新核对。',
    unknown: '准备范围时发生未分类异常，已安全停止。',
  })[String(classification.error_kind || 'unknown')] || '准备范围时发生未分类异常，已安全停止。';
}

export function toChineseError(error) {
  if (error && error.status >= 400 && error.status < 500 && error.message && !error.body && !error.details) {
    return error.message;
  }
  const raw = typeof error === 'string'
    ? error
    : JSON.stringify(error?.details || error?.body || error?.message || error || '');
  for (const [pattern, label] of mappings) {
    if (pattern.test(raw)) return label;
  }
  return raw ? `平台返回未分类错误：${raw.slice(0, 160)}` : '未知错误';
}

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
    this.details = body;
  }
}
