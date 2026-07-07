const mappings = [
  [/invalid_token|expired|unauthorized|401/i, '授权失效或 token 已过期'],
  [/under_review|item status is not allowed/i, '商品正在审核中，平台不允许报名'],
  [/forbidden|permission|403|not authorized|access denied|caller.*not/i, '账号权限不足或应用权限不足'],
  [/not found|404/i, '活动或商品不存在'],
  [/too many|rate|429/i, '接口限流，请稍后重试'],
  [/credible|discounted price/i, '折扣价不被平台认可'],
  [/price.*minimum|min_discount/i, '活动价低于平台允许最低价'],
  [/price.*maximum|max_discount/i, '活动价高于平台允许最高价'],
  [/campaign|promotion.*finished|ended/i, '活动已结束或不可用'],
  [/stock|inventory/i, '库存数量不符合活动要求'],
  [/offer/i, '缺少或无效的活动报价信息'],
  [/bad request|invalid|400/i, '请求参数不符合平台要求'],
  [/server|temporarily|timeout|5\d\d/i, '平台接口临时异常']
];

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
