import crypto from 'node:crypto';

import {
  accountRouteKey,
  bindActivitiesToAccountRoute,
  bindActivityToAccountRoute,
  normalizeAccountRoute,
} from './accountRouteIdentity.js';
import { activityIdentityKey } from './submissionScopeFreeze.js';

function text(value) {
  return String(value ?? '').trim();
}

function upper(value) {
  return text(value).toUpperCase();
}

function rawValue(value = {}) {
  if (value.raw && typeof value.raw === 'object') return value.raw;
  if (value.raw_json && typeof value.raw_json === 'string') {
    try {
      const parsed = JSON.parse(value.raw_json);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {}
  }
  return {};
}

function sourceRevision(value = {}) {
  const raw = rawValue(value);
  return text(
    value.source_revision ?? value.revision ?? value.catalog_revision
    ?? raw.source_revision ?? raw.revision ?? raw.catalog_revision
    ?? raw.last_updated ?? raw.updated_at ?? raw.update_date,
  );
}

function metadata(value = {}) {
  return {
    status: upper(value.status),
    start_date: text(value.start_date ?? value.startDate),
    finish_date: text(value.finish_date ?? value.finishDate),
    name: text(value.name ?? value.promotion_name),
    source_revision: sourceRevision(value),
  };
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]));
}

function sameMetadata(left, right) {
  return JSON.stringify(metadata(left)) === JSON.stringify(metadata(right));
}

function sorted(values = []) {
  return [...values].sort((left, right) => activityIdentityKey(left).localeCompare(activityIdentityKey(right)));
}

export function reconcileActivityCatalog({ route, cachedPromotions = [], livePromotions = [] } = {}) {
  const expectedRoute = normalizeAccountRoute(route);
  const cached = bindActivitiesToAccountRoute(cachedPromotions, expectedRoute);
  const live = bindActivitiesToAccountRoute(livePromotions, expectedRoute);
  const cachedByIdentity = new Map(cached.map((row) => [activityIdentityKey(row), row]));
  const liveByIdentity = new Map(live.map((row) => [activityIdentityKey(row), row]));
  const added = [];
  const changed = [];
  const unchanged = [];
  const removed = [];

  for (const [key, liveRow] of liveByIdentity) {
    const cachedRow = cachedByIdentity.get(key);
    if (!cachedRow) added.push(liveRow);
    else if (sameMetadata(cachedRow, liveRow)) unchanged.push(liveRow);
    else changed.push(liveRow);
  }
  for (const [key, cachedRow] of cachedByIdentity) {
    if (!liveByIdentity.has(key)) removed.push(cachedRow);
  }

  const dirtyIdentities = [
    ...added.map((promotion) => ({ key: activityIdentityKey(promotion), reason: 'added', promotion })),
    ...changed.map((promotion) => ({ key: activityIdentityKey(promotion), reason: 'metadata_changed', promotion })),
    ...removed.map((promotion) => ({ key: activityIdentityKey(promotion), reason: 'removed', promotion })),
  ].sort((left, right) => left.key.localeCompare(right.key));
  const uniqueCached = [...cachedByIdentity.values()];
  const uniqueLive = [...liveByIdentity.values()];
  const revisionFacts = sorted(uniqueLive).map((promotion) => ({
    identity: activityIdentityKey(promotion),
    ...metadata(promotion),
  }));

  return {
    route: expectedRoute,
    cached_total: uniqueCached.length,
    live_total: uniqueLive.length,
    revision: crypto.createHash('sha256').update(JSON.stringify(stable(revisionFacts)), 'utf8').digest('hex').toUpperCase(),
    live_promotions: sorted(uniqueLive),
    added: sorted(added),
    changed: sorted(changed),
    removed: sorted(removed),
    unchanged: sorted(unchanged),
    dirty_identities: dirtyIdentities,
  };
}

export function summarizeActivityCatalogRouteReads({ expectedRoutes = [], results = [], error = null } = {}) {
  const expected = new Map((Array.isArray(expectedRoutes) ? expectedRoutes : []).map((route) => {
    const normalized = normalizeAccountRoute(route);
    return [accountRouteKey(normalized), normalized];
  }));
  const refreshedRouteKeys = new Set();
  const blockedRouteKeys = new Set();
  const errorsByRoute = new Map();
  for (const result of Array.isArray(results) ? results : []) {
    const route = normalizeAccountRoute({
      account_id: result.account_id,
      child_user_id: result.child_user_id,
      site_id: result.site_id,
    }, { requireComplete: false });
    if (!route.account_id || !route.child_user_id || !route.site_id) continue;
    const key = accountRouteKey(route);
    if (String(result.status || '') === 'ok') refreshedRouteKeys.add(key);
    else {
      blockedRouteKeys.add(key);
      errorsByRoute.set(key, text(result.error) || '活动目录读取失败，本次不使用旧缓存。');
    }
  }
  for (const [key] of expected) {
    if (error || (!refreshedRouteKeys.has(key) && !blockedRouteKeys.has(key))) {
      blockedRouteKeys.add(key);
      errorsByRoute.set(key, text(error) || '活动目录未返回该店铺站点，本次不使用旧缓存。');
    }
  }
  return { refreshed_route_keys: refreshedRouteKeys, blocked_route_keys: blockedRouteKeys, errors_by_route: errorsByRoute };
}

export function selectMarketplaceUsersForCatalogRoutes({ accountId, marketplaceUsers = [], routes = [], siteIds = [] } = {}) {
  const rows = Array.isArray(marketplaceUsers) ? marketplaceUsers : [];
  const routeKeys = new Set((Array.isArray(routes) ? routes : []).map((route) => accountRouteKey(normalizeAccountRoute(route))));
  const wantedSites = new Set((Array.isArray(siteIds) ? siteIds : []).map(upper).filter(Boolean));
  if (!routeKeys.size && !wantedSites.size) return rows;
  return rows.filter((child) => {
    const route = normalizeAccountRoute({
      account_id: accountId,
      child_user_id: child?.child_user_id ?? child?.user_id ?? child?.id,
      site_id: child?.site_id ?? child?.site,
    }, { requireComplete: false });
    if (!route.account_id || !route.child_user_id || !route.site_id) return false;
    return routeKeys.size ? routeKeys.has(accountRouteKey(route)) : wantedSites.has(route.site_id);
  });
}

export function requireAuthoritativeActivityCatalogRead(results = []) {
  const rows = Array.isArray(results) ? results : [];
  if (rows.some((row) => row?.ok === true && row?.authoritative === true)) return true;
  const error = new Error('平台活动目录暂时无法确认，本次已阻断该店铺站点且未使用旧目录。');
  error.code = 'ACTIVITY_DIRECTORY_UNREADABLE';
  error.status = 422;
  throw error;
}

export function sellerCampaignWriteThroughPromotion({
  route,
  promotionId,
  name,
  startDate,
  finishDate,
  response = {},
} = {}) {
  const body = response?.body && typeof response.body === 'object' ? response.body : response;
  const raw = body && typeof body === 'object' ? body : {};
  const id = text(promotionId ?? raw.id ?? raw.promotion_id);
  if (!id) {
    const error = new Error('平台创建响应未返回活动编号，已停止写入本地活动目录并等待实时回查。');
    error.code = 'SELLER_CAMPAIGN_ID_MISSING';
    error.status = 422;
    throw error;
  }
  return bindActivityToAccountRoute({
    promotion_id: id,
    promotion_type: 'SELLER_CAMPAIGN',
    name: text(raw.name) || text(name),
    status: text(raw.status) || 'pending',
    start_date: text(raw.start_date ?? raw.startDate) || text(startDate),
    finish_date: text(raw.finish_date ?? raw.finishDate) || text(finishDate),
    raw,
  }, route);
}
