function text(value) {
  return String(value ?? '').trim();
}

function upper(value) {
  return text(value).toUpperCase();
}

function routeError(message, code = 'ACTIVITY_ACCOUNT_ROUTE_INVALID', details = null) {
  const error = new Error(message);
  error.code = code;
  error.status = 422;
  error.details = details;
  return error;
}

function observedRoute(activity = {}) {
  const raw = activity?.raw && typeof activity.raw === 'object' ? activity.raw : {};
  return {
    account_id: text(
      activity.account_id ?? activity.accountId ?? activity.merchant_id ?? activity.merchantId
      ?? raw.account_id ?? raw.accountId ?? raw.merchant_id ?? raw.merchantId,
    ),
    child_user_id: text(
      activity.child_user_id ?? activity.childUserId ?? activity.user_id ?? activity.userId
      ?? raw.child_user_id ?? raw.childUserId ?? raw.user_id ?? raw.userId,
    ),
    site_id: upper(activity.site_id ?? activity.siteId ?? raw.site_id ?? raw.siteId),
  };
}

export function normalizeAccountRoute(value = {}, { requireComplete = true } = {}) {
  const route = {
    account_id: text(value.account_id ?? value.accountId ?? value.merchant_id ?? value.merchantId),
    child_user_id: text(value.child_user_id ?? value.childUserId ?? value.user_id ?? value.userId),
    site_id: upper(value.site_id ?? value.siteId),
  };
  if (requireComplete && (!route.account_id || !route.child_user_id || !route.site_id)) {
    throw routeError('店铺、子账号或站点身份不完整，已停止准备且未执行商品操作。', 'ACTIVITY_ACCOUNT_ROUTE_INCOMPLETE', {
      has_account_id: Boolean(route.account_id),
      has_child_user_id: Boolean(route.child_user_id),
      has_site_id: Boolean(route.site_id),
    });
  }
  return route;
}

export function accountRouteKey(value = {}) {
  const route = normalizeAccountRoute(value, { requireComplete: false });
  return `${route.account_id}|${route.child_user_id}|${route.site_id}`;
}

export function bindActivityToAccountRoute(activity = {}, expectedRoute = {}) {
  const expected = normalizeAccountRoute(expectedRoute);
  const observed = observedRoute(activity);
  const mismatches = [];
  for (const field of ['account_id', 'child_user_id', 'site_id']) {
    if (observed[field] && observed[field] !== expected[field]) mismatches.push(field);
  }
  if (mismatches.length) {
    throw routeError('活动归属与当前店铺、子账号或站点不一致，已停止准备且未执行商品操作。', 'ACTIVITY_ACCOUNT_ROUTE_MISMATCH', {
      mismatched_fields: mismatches,
      expected_route: accountRouteKey(expected),
      observed_route: accountRouteKey(observed),
    });
  }
  const promotionId = text(activity.promotion_id ?? activity.promotionId ?? activity.id ?? activity.deal_id);
  const promotionType = upper(activity.promotion_type ?? activity.promotionType ?? activity.type);
  return {
    ...activity,
    ...(promotionId ? { promotion_id: promotionId } : {}),
    ...(promotionType ? { promotion_type: promotionType } : {}),
    ...expected,
    route_key: accountRouteKey(expected),
  };
}

export function bindActivitiesToAccountRoute(activities = [], expectedRoute = {}) {
  if (!Array.isArray(activities)) {
    throw routeError('活动目录返回格式异常，已停止准备且未执行商品操作。', 'ACTIVITY_DIRECTORY_FORMAT_INVALID');
  }
  return activities.map((activity) => bindActivityToAccountRoute(activity, expectedRoute));
}

export function assertAccountRouteAllowed(routeValue = {}, allowedRoutes = []) {
  const route = normalizeAccountRoute(routeValue);
  const allowed = new Set((Array.isArray(allowedRoutes) ? allowedRoutes : []).map(accountRouteKey));
  if (!allowed.has(accountRouteKey(route))) {
    throw routeError('活动归属不在当前账号已验证的子账号和站点范围内，已停止准备且未执行商品操作。', 'ACTIVITY_ACCOUNT_ROUTE_NOT_ALLOWED', {
      route_key: accountRouteKey(route),
    });
  }
  return route;
}
