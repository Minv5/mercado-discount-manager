import { PHYSICAL_GET_BUDGET_EXHAUSTED, readMarketplaceItemWithBudget } from './physicalGetBudget.js';
import { classifyCbtUnresolved } from './cbtUnresolvedClassification.js';
import { readItemResourceAfterEvent } from './itemSnapshot.js';

const SUPPORTED_TOPICS = new Set(['public_offers', 'public_candidates', 'items', 'marketplace_items']);

function activityWebhookError(message, code, status = 422, diagnosticCode = null, signalPresence = null) {
  const error = new Error(message);
  error.code = code;
  error.status = status;
  if (diagnosticCode) error.diagnostic_code = diagnosticCode;
  if (signalPresence && typeof signalPresence === 'object') {
    error.resolver_diagnostic = {
      operation: 'cbt_route_resolution',
      endpoint_family: 'items_cbt',
      code: diagnosticCode || code,
      signal_presence: {
        seller_signal_count: Math.max(0, Number(signalPresence.seller_signal_count || 0)),
        site_signal_count: Math.max(0, Number(signalPresence.site_signal_count || 0)),
        status_present: Boolean(signalPresence.status_present),
        route_candidate_count: Math.max(0, Number(signalPresence.route_candidate_count || 0)),
      },
    };
  }
  return error;
}

function siteIdFromText(value) {
  const match = String(value || '').toUpperCase().match(/(?:^|[^A-Z0-9])(ML[A-Z])(?=[A-Z0-9])/);
  return match ? match[1] : '';
}

function resourceRemoteUserId(resource) {
  const match = String(resource || '').match(/\/(\d+)(?:\?.*)?$/);
  return match ? match[1] : '';
}

function normalizedRoute(input = {}) {
  return {
    account_id: String(input.account_id || '').trim(),
    child_user_id: String(input.child_user_id || '').trim(),
    site_id: String(input.site_id || '').trim().toUpperCase(),
  };
}

export function normalizeActivityWebhookEvent(input = {}) {
  const event = {
    schema_version: String(input.schema_version || '').trim(),
    event_id: String(input.event_id || '').trim(),
    topic: String(input.topic || '').trim().toLowerCase(),
    resource: String(input.resource || '').trim(),
    remote_user_id: String(input.remote_user_id || '').trim(),
    application_id: String(input.application_id || '').trim(),
    received_at: String(input.received_at || '').trim(),
  };
  if (event.schema_version !== '2') throw activityWebhookError('活动通知版本不受支持。', 'ACTIVITY_CALLBACK_SCHEMA_UNSUPPORTED', 400);
  if (!event.event_id || !event.resource || !event.remote_user_id || !event.application_id) {
    throw activityWebhookError('活动通知缺少必要字段。', 'ACTIVITY_CALLBACK_FIELDS_MISSING', 400);
  }
  if (!SUPPORTED_TOPICS.has(event.topic)) {
    throw activityWebhookError('该类活动通知暂不支持。', 'ACTIVITY_CALLBACK_TOPIC_UNSUPPORTED', 422);
  }
  if (!event.resource.startsWith('/') || event.resource.includes('://') || event.resource.includes('..')) {
    throw activityWebhookError('活动通知资源地址无效。', 'ACTIVITY_CALLBACK_RESOURCE_INVALID', 400);
  }
  return event;
}

export function resolveActivityWebhookRoute({ event: input, marketplaceSites = [], accounts = [] } = {}) {
  const event = normalizeActivityWebhookEvent(input);
  const routes = (marketplaceSites || []).map(normalizedRoute)
    .filter((route) => route.account_id && route.child_user_id && route.site_id);
  const cbtItemId = cbtItemIdFromEvent(event);
  const resourceSiteId = siteIdFromText(event.resource);
  const resourceChildId = resourceRemoteUserId(event.resource);
  if (resourceChildId && resourceChildId !== event.remote_user_id) {
    throw activityWebhookError('活动通知的账号归属与资源不一致，已阻断处理。', 'ACTIVITY_CALLBACK_ROUTE_MISMATCH');
  }
  if (cbtItemId && routes.some((route) => route.account_id === event.remote_user_id)) {
    throw activityWebhookError('CBT 父账号商品通知必须通过官方商品资源确认经营路由。', 'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED');
  }

  let candidates = routes.filter((route) => route.child_user_id === event.remote_user_id);
  if (candidates.length && resourceSiteId && candidates.every((route) => route.site_id !== resourceSiteId)) {
    throw activityWebhookError('活动通知的账号站点与资源不一致，已阻断处理。', 'ACTIVITY_CALLBACK_ROUTE_MISMATCH');
  }
  if (!candidates.length && (accounts || []).some((account) => String(account.account_id || '') === event.remote_user_id)) {
    candidates = routes.filter((route) => route.account_id === event.remote_user_id);
  }
  if (resourceSiteId) candidates = candidates.filter((route) => route.site_id === resourceSiteId);
  const identities = new Map(candidates.map((route) => [`${route.account_id}|${route.child_user_id}|${route.site_id}`, route]));
  if (identities.size !== 1) {
    throw activityWebhookError(
      identities.size ? '活动通知对应多个账号站点，无法安全确认归属。' : '活动通知无法映射到已授权的经营账号站点。',
      identities.size ? 'ACTIVITY_CALLBACK_ROUTE_AMBIGUOUS' : 'ACTIVITY_CALLBACK_ROUTE_UNRESOLVED',
    );
  }
  return [...identities.values()][0];
}

export function buildNotificationResourcePath(input) {
  const event = normalizeActivityWebhookEvent(input);
  if (event.topic === 'public_offers' || event.topic === 'public_candidates') {
    if (!/^\/seller-promotions\/promotions\/(?:offer|candidate)\/[A-Za-z0-9._:-]+\/\d+$/.test(event.resource)) {
      throw activityWebhookError('活动通知资源地址与活动类型不匹配。', 'ACTIVITY_CALLBACK_RESOURCE_INVALID', 400);
    }
    return `/marketplace${event.resource}`;
  }
  if (/^\/marketplace\/items\/[A-Za-z0-9._:-]+$/.test(event.resource)) return event.resource;
  if (/^\/items\/[A-Za-z0-9._:-]+$/.test(event.resource)) return `/marketplace${event.resource}`;
  throw activityWebhookError('商品通知资源地址无效。', 'ACTIVITY_CALLBACK_RESOURCE_INVALID', 400);
}

function promotionRelation(value, allowGenericType = false) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const id = String(value.promotion_id || value.id || '').trim();
  const type = String(value.promotion_type || (allowGenericType ? value.type : '') || '').trim().toUpperCase();
  if (!id || !type) return null;
  return { promotion_id: id, promotion_type: type };
}

function statusId(value) {
  if (value && typeof value === 'object') return String(value.id || value.status || '').trim().toLowerCase();
  return String(value || '').trim().toLowerCase();
}

function resourceItemPromotionRelations(resourceData, fallbackRelations = []) {
  const values = [];
  if (resourceData?.promotion && typeof resourceData.promotion === 'object') values.push(resourceData.promotion);
  for (const value of Array.isArray(resourceData?.promotions) ? resourceData.promotions : []) values.push(value);
  const relations = [];
  for (const value of values) {
    const relation = promotionRelation(value, true);
    if (!relation) continue;
    relations.push({
      ...relation,
      status: statusId(value.status || value.item_status || value.itemStatus),
      raw: value,
    });
  }
  if (!relations.length && Array.isArray(fallbackRelations) && fallbackRelations.length) {
    return fallbackRelations.map((relation) => ({
      ...relation,
      status: statusId(resourceData?.status),
      raw: resourceData,
    }));
  }
  return [...new Map(relations.map((relation) => [`${relation.promotion_type}|${relation.promotion_id}`, relation])).values()];
}

function resourcePromotionRelations(event, resourceData) {
  const relations = [];
  const publicTopic = event.topic === 'public_offers' || event.topic === 'public_candidates';
  const direct = promotionRelation(resourceData, publicTopic);
  if (direct) relations.push(direct);
  const nested = promotionRelation(resourceData?.promotion, true);
  if (nested) relations.push(nested);
  for (const value of Array.isArray(resourceData?.promotions) ? resourceData.promotions : []) {
    const relation = promotionRelation(value, true);
    if (relation) relations.push(relation);
  }
  const unique = new Map(relations.map((relation) => [`${relation.promotion_type}|${relation.promotion_id}`, relation]));
  return [...unique.values()];
}

export function classifyActivityWebhookResource({ event: input, route: inputRoute, resourceData } = {}) {
  const event = normalizeActivityWebhookEvent(input);
  const route = normalizedRoute(inputRoute);
  if (!resourceData || typeof resourceData !== 'object' || Array.isArray(resourceData)) {
    throw activityWebhookError('平台未返回可识别的活动通知资源。', 'ACTIVITY_CALLBACK_RESOURCE_UNREADABLE', 502);
  }
  const resourceSiteId = siteIdFromText(resourceData.item_id || resourceData.id || event.resource);
  if (resourceSiteId && route.site_id !== resourceSiteId) {
    throw activityWebhookError('平台资源与通知账号站点不一致，已阻断处理。', 'ACTIVITY_CALLBACK_ROUTE_MISMATCH');
  }
  const relations = resourcePromotionRelations(event, resourceData);
  const publicTopic = event.topic === 'public_offers' || event.topic === 'public_candidates';
  const itemTopic = event.topic === 'marketplace_items' || event.topic === 'items';
  if (publicTopic && !relations.length) {
    throw activityWebhookError('平台通知未返回可确认的活动关系，已保留等待重试。', 'ACTIVITY_CALLBACK_RESOURCE_UNCLASSIFIED', 502);
  }
  // Item-level events carry a single item id (change / add / remove). They must
  // update only that item, never mark the whole site catalog dirty: marking
  // the catalog dirty would force a full re-read of every activity on the next
  // refresh, defeating the event-driven incremental sync.
  return {
    dirty_activities: publicTopic ? relations.map((relation) => ({ ...route, ...relation })) : [],
    catalog_dirty: false,
    item_only: itemTopic,
    item_relations: (itemTopic || publicTopic) ? resourceItemPromotionRelations(resourceData, relations) : [],
    item_relations_complete: Boolean(itemTopic && Array.isArray(resourceData?.promotions)),
    resource_status: String(resourceData?.status?.id || resourceData?.status || ''),
  };
}

function scalarSignals(values = []) {
  return [...new Set(values
    .map((value) => (value === null || value === undefined ? '' : String(value).trim()))
    .filter(Boolean))];
}

function siteSignal(value) {
  const text = String(value || '').trim().toUpperCase();
  return /^(?:ML[A-Z]|MCO|MPE|MEC)$/.test(text) ? text : '';
}

function documentedCbtSiteItems(resourceData = {}) {
  // Global Selling item detail documents the parent CBT item separately from
  // site_items child listings; marketplace_items is the documented mapping
  // shape. Only child entries are used for child seller/site routing.
  const values = [
    ...(Array.isArray(resourceData.site_items) ? resourceData.site_items : []),
    ...(Array.isArray(resourceData.siteItems) ? resourceData.siteItems : []),
    ...(Array.isArray(resourceData.marketplace_items) ? resourceData.marketplace_items : []),
    ...(Array.isArray(resourceData.marketplaceItems) ? resourceData.marketplaceItems : []),
  ];
  return values.filter((value) => value && typeof value === 'object' && !Array.isArray(value));
}

function resourceStatusValues(resourceData = {}, childItems = []) {
  const values = [];
  if (Object.hasOwn(resourceData, 'status')) values.push(statusId(resourceData.status));
  for (const item of childItems) {
    if (Object.hasOwn(item, 'status')) values.push(statusId(item.status));
    if (Object.hasOwn(item, 'sub_status')) values.push(statusId(item.sub_status));
  }
  return scalarSignals(values.map((value) => String(value || '').toLowerCase()));
}

function officialItemRouteSignals(resourceData = {}) {
  const seller = resourceData?.seller && typeof resourceData.seller === 'object' ? resourceData.seller : {};
  const owner = resourceData?.owner && typeof resourceData.owner === 'object' ? resourceData.owner : {};
  const marketplace = resourceData?.marketplace && typeof resourceData.marketplace === 'object'
    ? resourceData.marketplace
    : {};
  const childItems = documentedCbtSiteItems(resourceData);
  const childSellerIds = scalarSignals(childItems.flatMap((item) => [
    item.seller_id,
    item.owner_id,
    item.user_id,
    item.marketplace_user_id,
  ]));
  const childSiteIds = scalarSignals(childItems.flatMap((item) => [
    siteSignal(item.site_id),
    siteSignal(item.site),
    siteSignal(item.country_id),
  ]));
  const statusValues = resourceStatusValues(resourceData, childItems);
  return {
    account_ids: scalarSignals([
      resourceData.account_id,
      resourceData.accountId,
      resourceData.merchant_id,
      resourceData.merchantId,
      resourceData.seller_account_id,
      seller.account_id,
      seller.merchant_id,
    ]),
    seller_ids: childItems.length ? childSellerIds : scalarSignals([
      resourceData.seller_id,
      resourceData.owner_id,
      resourceData.user_id,
      resourceData.marketplace_user_id,
      seller.id,
      seller.user_id,
      seller.seller_id,
      owner.id,
      owner.user_id,
    ]),
    site_ids: childItems.length ? childSiteIds : scalarSignals([
      siteSignal(resourceData.site_id),
      siteSignal(resourceData.site),
      siteSignal(resourceData.country_id),
      siteSignal(resourceData.marketplace_id),
      siteSignal(seller.site_id),
      siteSignal(seller.site),
      siteSignal(marketplace.site_id),
      siteSignal(marketplace.site),
      siteSignal(marketplace.country_id),
      siteSignal(marketplace.id),
      siteSignal(marketplace.code),
    ]),
    status_values: statusValues,
    status_present: statusValues.length > 0,
    child_item_count: childItems.length,
  };
}

function cbtItemIdFromEvent(event = {}) {
  const value = String(event.resource || '')
    .replace(/^\/marketplace\/items\//, '')
    .replace(/^\/items\//, '')
    .split(/[?#]/)[0];
  return /^CBT[A-Za-z0-9._:-]+$/.test(value) ? value : '';
}

function cbtResolverSignalPresence({ sellerCount = 0, siteCount = 0, statusPresent = false, routeCandidateCount = 0, ownedIdentityProven = false, foreignRouteCandidateCount = 0, rootSiteId = '', childItemCount = 0 } = {}) {
  return {
    seller_signal_count: Math.max(0, Number(sellerCount || 0)),
    site_signal_count: Math.max(0, Number(siteCount || 0)),
    status_present: Boolean(statusPresent),
    route_candidate_count: Math.max(0, Number(routeCandidateCount || 0)),
    owned_identity_proven: Boolean(ownedIdentityProven),
    foreign_route_candidate_count: Math.max(0, Number(foreignRouteCandidateCount || 0)),
    ...(rootSiteId ? { root_site_id: String(rootSiteId).trim().toUpperCase() } : {}),
    child_item_count: Math.max(0, Number(childItemCount || 0)),
  };
}

function resolverExpansionDiagnostic(code, signalPresence = {}) {
  const classification = classifyCbtUnresolved({ code, signal_presence: signalPresence });
  return {
    code,
    classification: classification.category,
    signal_presence: cbtResolverSignalPresence(signalPresence),
  };
}

export function resolveCbtItemRoutes({ event: input, resourceData, marketplaceSites = [], accounts = [], allowRootFallback = false } = {}) {
  const event = normalizeActivityWebhookEvent(input);
  const itemId = cbtItemIdFromEvent(event);
  if ((event.topic !== 'items' && event.topic !== 'marketplace_items') || !itemId) {
    return { targets: [], diagnostics: { target_count: 0, codes: [{ code: 'ROUTE_NOT_OWNED', count: 1 }], unmatched_skipped: 1, foreign_skipped: 0, unusable_skipped: 0, ambiguous_skipped: 0 } };
  }
  const allRoutes = (marketplaceSites || []).map(normalizedRoute)
    .filter((route) => route.account_id && route.child_user_id && route.site_id);
  const ownedRoutes = allRoutes.filter((route) => route.account_id === event.remote_user_id);
  const children = documentedCbtSiteItems(resourceData || {});
  const diagnostics = {
    target_count: 0,
    foreign_skipped: 0,
    unmatched_skipped: 0,
    unusable_skipped: 0,
    ambiguous_skipped: 0,
    missing_skipped: 0,
    deduplicated_count: 0,
    classification_counts: {},
    codes: [],
  };
  const codeCounts = new Map();
  const addSkip = (code, kind, signalPresence = {}) => {
    diagnostics[kind] = Number(diagnostics[kind] || 0) + 1;
    codeCounts.set(code, Number(codeCounts.get(code) || 0) + 1);
    const diagnostic = resolverExpansionDiagnostic(code, signalPresence);
    diagnostics.classification_counts[diagnostic.classification] = Number(diagnostics.classification_counts[diagnostic.classification] || 0) + 1;
    return diagnostic;
  };
  const skipped = [];
  const targets = [];
  const targetKeys = new Set();
  const terminalStatuses = new Set(['deleted', 'closed', 'inactive']);
  const parentAuthorized = (accounts || []).some((account) => String(account.account_id || '') === event.remote_user_id);

  if (!parentAuthorized) {
    const skipCount = Math.max(1, children.length || 1);
    diagnostics.foreign_skipped = skipCount;
    codeCounts.set('ROUTE_NOT_OWNED', skipCount);
    diagnostics.target_count = 0;
    diagnostics.codes = [{ code: 'ROUTE_NOT_OWNED', count: skipCount }];
    return { targets, diagnostics, skipped: [resolverExpansionDiagnostic('ROUTE_NOT_OWNED', { routeCandidateCount: 0 })] };
  }

  if (!resourceData || typeof resourceData !== 'object' || Array.isArray(resourceData)) {
    skipped.push(addSkip('OFFICIAL_SELLER_MISSING', 'missing_skipped'));
  } else if (!children.length && allowRootFallback) {
    try {
      const route = resolveCbtItemRoute({ event, resourceData, marketplaceSites, accounts });
      const key = `${route.account_id}|${route.child_user_id}|${route.site_id}|${itemId}`;
      targets.push({ ...route, marketplace_item_id: itemId, parent_item_id: itemId });
      targetKeys.add(key);
    } catch (error) {
      skipped.push(error?.resolver_diagnostic || resolverExpansionDiagnostic(error?.diagnostic_code || error?.code || 'ROUTE_NOT_OWNED'));
    }
  } else if (!children.length) {
    skipped.push(addSkip('OFFICIAL_SITE_MISSING', 'missing_skipped', {
      rootSiteId: String(resourceData?.site_id || '').trim().toUpperCase() === 'CBT' ? 'CBT' : '',
      childItemCount: 0,
    }));
  } else {
    for (const child of children) {
      const childItemId = String(child.item_id || child.id || '').trim();
      const sellerIds = scalarSignals([child.seller_id, child.owner_id, child.user_id, child.marketplace_user_id]);
      const siteIds = scalarSignals([siteSignal(child.site_id), siteSignal(child.site), siteSignal(child.country_id)]);
      const statuses = resourceStatusValues({}, [child]);
      const signalPresence = cbtResolverSignalPresence({
        sellerCount: sellerIds.length,
        siteCount: siteIds.length,
        statusPresent: statuses.length > 0,
      });
      if (!childItemId) {
        skipped.push(addSkip('MARKETPLACE_ITEM_MISSING', 'missing_skipped', signalPresence));
        continue;
      }
      if (sellerIds.length === 0) {
        skipped.push(addSkip('OFFICIAL_SELLER_MISSING', 'missing_skipped', signalPresence));
        continue;
      }
      if (sellerIds.length > 1) {
        skipped.push(addSkip('OFFICIAL_SELLER_AMBIGUOUS', 'ambiguous_skipped', signalPresence));
        continue;
      }
      if (siteIds.length === 0) {
        skipped.push(addSkip('OFFICIAL_SITE_MISSING', 'missing_skipped', signalPresence));
        continue;
      }
      if (siteIds.length > 1) {
        skipped.push(addSkip('OFFICIAL_SITE_AMBIGUOUS', 'ambiguous_skipped', signalPresence));
        continue;
      }
      if (statuses.length && statuses.every((status) => terminalStatuses.has(status))) {
        skipped.push(addSkip('RESOURCE_STATUS_UNUSABLE', 'unusable_skipped', signalPresence));
        continue;
      }
      const sellerId = sellerIds[0];
      const siteId = siteIds[0];
      const routeCandidates = ownedRoutes.filter((route) => route.child_user_id === sellerId && route.site_id === siteId);
      const foreignCandidates = allRoutes.filter((route) => route.account_id !== event.remote_user_id && route.child_user_id === sellerId && route.site_id === siteId);
      signalPresence.route_candidate_count = routeCandidates.length;
      signalPresence.owned_identity_proven = ownedRoutes.some((route) => route.child_user_id === sellerId);
      signalPresence.foreign_route_candidate_count = foreignCandidates.length;
      if (routeCandidates.length > 1) {
        skipped.push(addSkip('ROUTE_AMBIGUOUS', 'ambiguous_skipped', signalPresence));
        continue;
      }
      if (!routeCandidates.length) {
        skipped.push(addSkip(foreignCandidates.length ? 'ROUTE_NOT_OWNED' : 'ROUTE_NOT_OWNED', foreignCandidates.length ? 'foreign_skipped' : 'unmatched_skipped', signalPresence));
        continue;
      }
      const route = routeCandidates[0];
      const key = `${route.account_id}|${route.child_user_id}|${route.site_id}|${childItemId}`;
      if (targetKeys.has(key)) {
        diagnostics.deduplicated_count += 1;
        continue;
      }
      targetKeys.add(key);
      targets.push({
        ...route,
        marketplace_item_id: childItemId,
        parent_item_id: itemId,
        child_status: statuses[0] || '',
      });
    }
  }
  diagnostics.target_count = targets.length;
  diagnostics.codes = [...codeCounts.entries()].map(([code, count]) => ({ code, count }));
  return { targets, diagnostics, skipped };
}

export function resolveCbtItemRoute({ event: input, resourceData, marketplaceSites = [], accounts = [] } = {}) {
  const event = normalizeActivityWebhookEvent(input);
  const itemId = cbtItemIdFromEvent(event);
  if ((event.topic !== 'items' && event.topic !== 'marketplace_items') || !itemId) {
    throw activityWebhookError('该通知不是可补偿的 CBT 商品通知。', 'ACTIVITY_CALLBACK_CBT_ROUTE_NOT_APPLICABLE');
  }
  const signals = officialItemRouteSignals(resourceData || {});
  const signalPresence = (routeCandidateCount = 0) => ({
    seller_signal_count: signals.seller_ids.length,
    site_signal_count: signals.site_ids.length,
    status_present: signals.status_present,
    route_candidate_count: routeCandidateCount,
  });
  if (!resourceData || typeof resourceData !== 'object' || Array.isArray(resourceData)) {
    throw activityWebhookError(
      'CBT 商品官方资源不可读取，无法确认经营路由。',
      'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED',
      422,
      'OFFICIAL_SELLER_MISSING',
      signalPresence(),
    );
  }
  const routes = (marketplaceSites || []).map(normalizedRoute)
    .filter((route) => route.account_id && route.child_user_id && route.site_id)
    .filter((route) => route.account_id === event.remote_user_id);
  const accountIds = scalarSignals(officialItemRouteSignals(resourceData).account_ids);
  if (accountIds.length && (accountIds.length !== 1 || accountIds[0] !== event.remote_user_id)) {
    throw activityWebhookError(
      'CBT 商品官方资源与通知父账号不一致，已阻断处理。',
      'ACTIVITY_CALLBACK_ROUTE_MISMATCH',
      422,
      'ROUTE_NOT_OWNED',
      signalPresence(),
    );
  }
  const terminalStatuses = new Set(['deleted', 'closed', 'inactive']);
  if (signals.status_values.length && signals.status_values.every((value) => terminalStatuses.has(value))) {
    throw activityWebhookError(
      'CBT 商品官方资源状态不可用于经营路由确认。',
      'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED',
      422,
      'RESOURCE_STATUS_UNUSABLE',
      signalPresence(),
    );
  }
  if (signals.seller_ids.length === 0) {
    throw activityWebhookError(
      'CBT 商品官方资源缺少卖家身份，已阻断处理。',
      'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED',
      422,
      'OFFICIAL_SELLER_MISSING',
      signalPresence(),
    );
  }
  if (signals.seller_ids.length > 1) {
    throw activityWebhookError(
      'CBT 商品官方资源包含多个卖家身份，已阻断处理。',
      'ACTIVITY_CALLBACK_ROUTE_AMBIGUOUS',
      422,
      'OFFICIAL_SELLER_AMBIGUOUS',
      signalPresence(),
    );
  }
  if (signals.site_ids.length === 0) {
    throw activityWebhookError(
      'CBT 商品官方资源缺少站点身份，已阻断处理。',
      'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED',
      422,
      'OFFICIAL_SITE_MISSING',
      signalPresence(),
    );
  }
  if (signals.site_ids.length > 1) {
    throw activityWebhookError(
      'CBT 商品官方资源包含多个站点身份，已阻断处理。',
      'ACTIVITY_CALLBACK_ROUTE_AMBIGUOUS',
      422,
      'OFFICIAL_SITE_AMBIGUOUS',
      signalPresence(),
    );
  }
  const siteId = signals.site_ids[0];
  const sellerId = signals.seller_ids[0];
  const siteRoutes = routes.filter((route) => route.site_id === siteId);
  if (!siteRoutes.length) {
    throw activityWebhookError(
      'CBT 商品官方站点不在已验证经营路由内，已阻断处理。',
      'ACTIVITY_CALLBACK_ROUTE_MISMATCH',
      422,
      'ROUTE_NOT_OWNED',
      signalPresence(0),
    );
  }
  const matched = siteRoutes.filter((route) => route.child_user_id === sellerId);
  const parentMatched = siteRoutes.filter((route) => route.account_id === sellerId);
  const candidates = matched.length ? matched : parentMatched;
  const identities = new Map(candidates.map((route) => [`${route.account_id}|${route.child_user_id}|${route.site_id}`, route]));
  if (identities.size !== 1) {
    throw activityWebhookError(
      identities.size ? 'CBT 商品官方资源对应多个经营路由，无法安全确认归属。' : 'CBT 商品官方卖家不在已验证经营路由内，已阻断处理。',
      identities.size ? 'ACTIVITY_CALLBACK_ROUTE_AMBIGUOUS' : 'ACTIVITY_CALLBACK_ROUTE_MISMATCH',
      422,
      identities.size ? 'ROUTE_AMBIGUOUS' : 'ROUTE_NOT_OWNED',
      signalPresence(candidates.length),
    );
  }
  const accountMatches = (accounts || []).filter((account) => String(account.account_id || '') === event.remote_user_id);
  if (!accountMatches.length) {
    throw activityWebhookError(
      'CBT 商品通知父账号未在本地授权账号范围内，已阻断处理。',
      'ACTIVITY_CALLBACK_ROUTE_UNRESOLVED',
      422,
      'ROUTE_NOT_OWNED',
      signalPresence(candidates.length),
    );
  }
  return [...identities.values()][0];
}

/**
 * Build the CBT route resolver with an explicit resource-client dependency.
 * Keeping the factory outside the server closure makes the GET boundary
 * testable and prevents a free variable from being captured by replay code.
 */
export function createCbtItemRoutesResolver({
  createResourceClient,
} = {}) {
  if (typeof createResourceClient !== 'function') {
    throw new TypeError('CBT item route resolver requires an explicit resource client factory');
  }
  return async ({ event, marketplaceSites, accounts, itemId, signal, physicalGetBudget = null } = {}) => {
    if (event?.topic !== 'items' && event?.topic !== 'marketplace_items') return null;
    if (!/^\/items\/CBT[A-Za-z0-9._:-]+(?:[?#].*)?$/.test(String(event?.resource || ''))) return null;
    const client = await createResourceClient({
      account_id: event.remote_user_id,
      child_user_id: '',
      site_id: '',
    });
    let resourceData;
    const budgetBefore = physicalGetBudget?.stats?.() || null;
    try {
      resourceData = await readMarketplaceItemWithBudget({ client, itemId, signal, physicalGetBudget, kind: 'cbt_parent' });
    } catch (error) {
      if (String(error?.code || '') === PHYSICAL_GET_BUDGET_EXHAUSTED) {
        error.physical_get_count = 0;
        error.budget_remaining = true;
      } else {
        error.physical_get_count = Math.max(1, Number(error.physical_get_count || 0));
      }
      throw error;
    }
    const budgetDelta = physicalBudgetDelta(physicalGetBudget, budgetBefore);
    return {
      ...resolveCbtItemRoutes({ event, resourceData, marketplaceSites, accounts }),
      resourceData,
      parent_get_count: budgetDelta ? budgetDelta.physical_get_count : 1,
      physical_get_stats: budgetDelta,
    };
  };
}

export function createCbtItemRouteResolver({
  createResourceClient,
  routeResolver = resolveCbtItemRoute,
} = {}) {
  if (typeof createResourceClient !== 'function') {
    throw new TypeError('CBT item route resolver requires an explicit resource client factory');
  }
  if (typeof routeResolver !== 'function') {
    throw new TypeError('CBT item route resolver requires a route resolver');
  }
  return async ({ event, marketplaceSites, accounts, itemId, signal, physicalGetBudget = null } = {}) => {
    if (event?.topic !== 'items' && event?.topic !== 'marketplace_items') return null;
    if (!/^\/items\/CBT[A-Za-z0-9._:-]+(?:[?#].*)?$/.test(String(event?.resource || ''))) return null;
    const client = await createResourceClient({ account_id: event.remote_user_id, child_user_id: '', site_id: '' });
    const budgetBefore = physicalGetBudget?.stats?.() || null;
    const resourceData = await readMarketplaceItemWithBudget({ client, itemId, signal, physicalGetBudget, kind: 'cbt_parent' });
    const budgetDelta = physicalBudgetDelta(physicalGetBudget, budgetBefore);
    const expansion = resolveCbtItemRoutes({ event, resourceData, marketplaceSites, accounts, allowRootFallback: true });
    if (expansion.targets.length !== 1) {
      const error = activityWebhookError('CBT 商品无法确认唯一经营路由。', 'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED');
      error.diagnostic_code = expansion.diagnostics.codes?.[0]?.code || 'ROUTE_NOT_OWNED';
      const category = Object.keys(expansion.diagnostics.classification_counts || {})[0] || classifyCbtUnresolved({ code: error.diagnostic_code }).category;
      error.classification = category;
      error.resolver_diagnostic = { operation: 'cbt_route_resolution', endpoint_family: 'items_cbt', classification: category, ...expansion.diagnostics };
      throw error;
    }
    return {
      route: routeResolver({ event, resourceData, marketplaceSites, accounts }),
      ...expansion,
      resourceData,
      parent_get_count: budgetDelta ? budgetDelta.physical_get_count : 1,
      physical_get_stats: budgetDelta,
    };
  };
}

function cbtTargetKey(target = {}) {
  return [target.account_id, target.child_user_id, target.site_id, target.marketplace_item_id]
    .map((value) => String(value || '').trim())
    .join('|');
}

function physicalBudgetDelta(budget, before) {
  if (!budget || typeof budget.stats !== 'function') return null;
  const after = budget.stats();
  return {
    ...after,
    physical_get_count: Math.max(0, Number(after.completed || 0) - Number(before?.completed || 0)),
  };
}

function numericPrice(value) {
  if (value === null || value === undefined || String(value).trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function itemStatusText(value) {
  if (value && typeof value === 'object') return String(value.id || value.status || '').trim().toLowerCase();
  return String(value || '').trim().toLowerCase();
}

function cbtFanoutError(message, code) {
  const error = activityWebhookError(message, 'ACTIVITY_CALLBACK_CBT_FANOUT_FAILED', 422, code);
  error.operation = 'cbt_child_item_read';
  error.endpoint_family = 'marketplace_item_resource';
  return error;
}

export function createActivityWebhookConsumer({
  listMarketplaceSites,
  listAccounts,
  createResourceClient,
  markDirty,
  invalidateCatalog,
  onItemMissing = null,
  markItemUnconfirmed = null,
  updateItemPrice = null,
  updateItemRelations = null,
  applyActivityChange = null,
  resolveItemOwner = null,
  resolveItemRoute = null,
  resolveItemRoutes = null,
  itemSyncRetryDelaysMs = [2000, 5000, 10000],
  sleepFn = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  const consumeCbtFanout = async ({ event, targets, successKeys = [], parentGetCount = 1, signal, physicalGetBudget = null }) => {
    const alreadySucceeded = new Set((Array.isArray(successKeys) ? successKeys : []).map(String));
    const successes = new Set(alreadySucceeded);
    const failures = [];
    let childGetCount = 0;
    let cacheUpdatedCount = 0;
    const budgetBefore = physicalGetBudget?.stats?.() || null;
    for (const target of targets) {
      const key = cbtTargetKey(target);
      if (alreadySucceeded.has(key)) continue;
      try {
        const client = await createResourceClient(target);
        const child = await readMarketplaceItemWithBudget({
          client,
          itemId: target.marketplace_item_id,
          signal,
          physicalGetBudget,
          kind: 'cbt_child',
        });
        childGetCount += 1;
        const childSite = siteSignal(child?.site_id || child?.site || child?.country_id);
        const childSeller = String(child?.seller_id || child?.owner_id || child?.user_id || '').trim();
        if (childSite && childSite !== target.site_id) throw cbtFanoutError('子商品站点与精确经营路由不一致。', 'ROUTE_NOT_OWNED');
        if (childSeller && childSeller !== target.child_user_id) throw cbtFanoutError('子商品卖家与精确经营路由不一致。', 'ROUTE_NOT_OWNED');
        const childStatus = itemStatusText(child?.status);
        if (['deleted', 'closed', 'inactive'].includes(childStatus)) throw cbtFanoutError('子商品状态不可用于缓存更新。', 'RESOURCE_STATUS_UNUSABLE');
        const price = numericPrice(child?.price);
        if (price === null) throw cbtFanoutError('子商品官方资源缺少价格字段。', 'CHILD_PRICE_MISSING');
        if (typeof updateItemPrice !== 'function') throw cbtFanoutError('商品价格缓存写入器未就绪。', 'CHILD_CACHE_WRITER_UNAVAILABLE');
        await updateItemPrice({
          accountId: target.account_id,
          childUserId: target.child_user_id,
          siteId: target.site_id,
          itemId: target.marketplace_item_id,
          price,
          originalPrice: numericPrice(child?.original_price),
          status: childStatus,
          raw: child,
          observedAt: event.received_at,
          sourceRevision: child?.last_updated || child?.date_modified || child?.updated_at || '',
        });
        cacheUpdatedCount += 1;
        successes.add(key);
      } catch (error) {
        failures.push({ key, code: String(error?.diagnostic_code || error?.code || 'CHILD_GET_FAILED') });
      }
    }
    const failedKeys = failures.map((failure) => failure.key);
    const diagnostics = [...new Map(failures.map((failure) => [failure.code, { code: failure.code, count: failures.filter((item) => item.code === failure.code).length }])).values()];
    const budgetDelta = physicalBudgetDelta(physicalGetBudget, budgetBefore);
    const physicalGetCount = budgetDelta
      ? budgetDelta.physical_get_count
      : Number(parentGetCount || 0) + childGetCount;
    const budgetRemaining = failures.some((failure) => failure.code === PHYSICAL_GET_BUDGET_EXHAUSTED);
    const base = {
      account_id: targets[0]?.account_id || event.remote_user_id,
      child_user_id: '',
      site_id: '',
      route_target_count: targets.length,
      route_target_attempted_count: Math.max(0, targets.length - alreadySucceeded.size),
      child_get_count: childGetCount,
      parent_get_count: Number(parentGetCount || 0),
      physical_get_count: physicalGetCount,
      physical_get_stats: budgetDelta,
      budget_remaining: budgetRemaining,
      cache_updated_count: cacheUpdatedCount,
      fanout_success_keys: [...successes],
      fanout_failed_keys: failedKeys,
      fanout_diagnostics: diagnostics,
      fanout_success_count: successes.size,
      fanout_failed_count: failedKeys.length,
    };
    if (failures.length && successes.size === alreadySucceeded.size) {
      const error = cbtFanoutError('所有匹配子商品读取或缓存更新失败。', diagnostics[0]?.code || 'CHILD_GET_FAILED');
      error.fanout = base;
      error.physical_get_count = base.physical_get_count;
      throw error;
    }
    if (failures.length) return {
      ...base,
      outcome: 'partial',
      retryable_failed_count: budgetRemaining ? 0 : failures.length,
    };
    return { ...base, outcome: 'item_updated' };
  };
  return async (input, { signal = null, physicalGetBudget = null } = {}) => {
    const event = normalizeActivityWebhookEvent(input);
    const marketplaceSites = await listMarketplaceSites();
    const accounts = await listAccounts();
    let route;
    let preloadedResourceData = null;
    let cbtTargets = null;
    let cbtDiagnostics = null;
    let cbtFanoutSuccessKeys = [];
    let cbtParentGetCount = 0;
    try {
      route = resolveActivityWebhookRoute({ event, marketplaceSites, accounts });
    } catch (error) {
      const code = String(error?.code || '');
      if ((code === 'ACTIVITY_CALLBACK_ROUTE_AMBIGUOUS'
        || code === 'ACTIVITY_CALLBACK_ROUTE_UNRESOLVED'
        || code === 'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED')
        && (typeof resolveItemOwner === 'function' || typeof resolveItemRoute === 'function' || typeof resolveItemRoutes === 'function')) {
        const itemId = String(event.resource || '')
          .replace(/^\/marketplace\/items\//, '')
          .replace(/^\/items\//, '')
          .split(/[?#]/)[0];
        const resourceSiteId = siteIdFromText(event.resource);
        const registeredRoute = (candidate) => marketplaceSites.some((site) => (
          String(site.account_id || '') === candidate.account_id
          && String(site.child_user_id || '') === candidate.child_user_id
          && String(site.site_id || '').toUpperCase() === candidate.site_id
        ));
        const matchesSite = (candidate) => !resourceSiteId || String(candidate.site_id || '').toUpperCase() === resourceSiteId;
        let cbtResolved = null;
        if (itemId && typeof resolveItemRoutes === 'function') {
          try {
            cbtResolved = await resolveItemRoutes({ event, marketplaceSites, accounts, itemId, signal, physicalGetBudget });
          } catch (routeError) {
            if (cbtItemIdFromEvent(event)) throw routeError;
            cbtResolved = null;
          }
        } else if (itemId && typeof resolveItemRoute === 'function') {
          try {
            cbtResolved = await resolveItemRoute({ event, marketplaceSites, accounts, itemId, signal, physicalGetBudget });
          } catch (routeError) {
            if (cbtItemIdFromEvent(event)) throw routeError;
            cbtResolved = null;
          }
        }
        if (Array.isArray(cbtResolved?.targets)) {
          cbtTargets = cbtResolved.targets;
          cbtDiagnostics = cbtResolved.diagnostics || null;
          preloadedResourceData = cbtResolved.resourceData || null;
          cbtParentGetCount = Number(cbtResolved.parent_get_count || 0);
          if (!cbtTargets.length) {
            const routeError = activityWebhookError('CBT 商品未匹配到可验证经营路由。', 'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED');
            routeError.diagnostic_code = cbtDiagnostics?.codes?.[0]?.code || 'ROUTE_NOT_OWNED';
            routeError.resolver_diagnostic = { operation: 'cbt_route_resolution', endpoint_family: 'items_cbt', ...(cbtDiagnostics || {}) };
            routeError.physical_get_count = Number(cbtResolved.parent_get_count || 0);
            throw routeError;
          }
          route = cbtTargets[0];
          cbtResolved = null;
        } else if (cbtResolved?.route) {
          route = cbtResolved.route;
          preloadedResourceData = cbtResolved.resourceData || null;
          cbtResolved = null;
        }
        const localOwners = route ? [] : (itemId ? await resolveItemOwner(itemId) : []);
        const localMatch = localOwners.filter(registeredRoute).filter(matchesSite);
        if (route) {
          // The official CBT item resource already supplied the route and can
          // be reused below, avoiding a second identical resource read.
        } else if (localMatch.length === 1) {
          route = localMatch[0];
        } else {
          let platformOwner = null;
          try {
            const client = await createResourceClient({ account_id: event.remote_user_id, child_user_id: '', site_id: '' });
            const item = await readMarketplaceItemWithBudget({ client, itemId, signal, physicalGetBudget, kind: 'cbt_parent_fallback' });
            const ownerId = String(item?.seller_id || item?.owner_id || '');
            if (ownerId) {
              const platformCandidates = marketplaceSites.filter((site) => (
                String(site.account_id || '') === event.remote_user_id
                && String(site.child_user_id || '') === ownerId
                && matchesSite(site)
              ));
              if (platformCandidates.length === 1) platformOwner = platformCandidates[0];
            }
          } catch {}
          if (platformOwner) {
            route = platformOwner;
          } else {
            throw error;
          }
        }
      } else {
        throw error;
      }
    }
    if (Array.isArray(cbtTargets) && cbtTargets.length) {
      const storedOutcome = input?.fanout_success_keys || input?.fanout?.fanout_success_keys || [];
      cbtFanoutSuccessKeys = Array.isArray(storedOutcome) ? storedOutcome : [];
      return consumeCbtFanout({ event, targets: cbtTargets, successKeys: cbtFanoutSuccessKeys, parentGetCount: cbtParentGetCount, signal, physicalGetBudget });
    }
    const client = await createResourceClient(route);
    const resourcePath = buildNotificationResourcePath(event);
    let resourceData = preloadedResourceData;
    const itemTopic = event.topic === 'marketplace_items' || event.topic === 'items';
    let itemSyncFresh = true;
    let itemSyncAttempts = 0;
    try {
      if (!resourceData && itemTopic) {
        const refreshed = await readItemResourceAfterEvent({
          read: () => client.getNotificationResource(resourcePath, { signal }),
          eventReceivedAt: event.received_at,
          retryDelaysMs: itemSyncRetryDelaysMs,
          sleepFn,
        });
        resourceData = refreshed.resource;
        itemSyncFresh = refreshed.fresh;
        itemSyncAttempts = refreshed.attempts;
      } else if (!resourceData) {
        resourceData = await client.getNotificationResource(resourcePath, { signal });
      }
    } catch (error) {
      const status = Number(error?.status || error?.httpStatus || 0);
      // Only definitive 404/410 responses prove the resource is gone. Text
      // matching across all 4xx/5xx statuses is too broad: auth failures
      // (401/403) or gateway errors (5xx) whose body happens to contain
      // "not found" would wrongly delete cached candidate rows.
      const notFound = status === 404 || status === 410;
      const itemId = String(event.resource || '')
        .replace(/^\/marketplace\/items\//, '')
        .replace(/^\/items\//, '')
        .split(/[?#]/)[0];
      if (notFound && itemId && itemTopic) {
        try {
          await onItemMissing?.({ ...route, item_id: itemId });
        } catch {}
        return {
          account_id: route.account_id,
          child_user_id: route.child_user_id,
          site_id: route.site_id,
          promotion_id: '',
          promotion_type: '',
          outcome: 'item_missing_cleaned',
          resource_status: 'missing',
        };
      }
      if (notFound && !itemTopic) {
        try {
          await markDirty({
            accountId: route.account_id,
            childUserId: route.child_user_id,
            siteId: route.site_id,
            promotionId: '',
            promotionType: '',
            eventCursor: event.event_id,
            gap: false,
          }, route);
        } catch {}
        return {
          account_id: route.account_id,
          child_user_id: route.child_user_id,
          site_id: route.site_id,
          promotion_id: '',
          promotion_type: '',
          outcome: 'activity_resource_missing',
          resource_status: 'missing',
          catalog_dirty: true,
        };
      }
      if (itemTopic && itemId && typeof markItemUnconfirmed === 'function') {
        try {
          await markItemUnconfirmed({
            accountId: route.account_id,
            childUserId: route.child_user_id,
            siteId: route.site_id,
            itemId,
            reason: String(error?.code || error?.message || 'resource_read_failed'),
          });
        } catch {}
      }
      throw error;
    }
    const classified = classifyActivityWebhookResource({ event, route, resourceData });
    if (itemTopic && typeof updateItemPrice === 'function') {
      try {
        const itemId = String(resourceData?.id || resourceData?.item_id || '').trim();
        const parsePrice = (value) => {
          if (value === null || value === undefined) return null;
          const text = String(value).trim();
          if (!text) return null;
          const parsed = Number(text);
          return Number.isFinite(parsed) ? parsed : null;
        };
        const price = parsePrice(resourceData?.price);
        // Only an explicitly provided original_price may update the cached
        // original price. Falling back to the current price would overwrite
        // the true pre-discount baseline and corrupt discount calculations.
        const originalPrice = parsePrice(resourceData?.original_price);
        if (itemId) {
          await updateItemPrice({
            accountId: route.account_id,
            childUserId: route.child_user_id,
            siteId: route.site_id,
            itemId,
            price,
            originalPrice,
            status: String(resourceData?.status || ''),
            raw: resourceData,
            observedAt: event.received_at,
            sourceRevision: resourceData?.last_updated || resourceData?.date_modified || resourceData?.updated_at || '',
            confirmed: itemSyncFresh,
          });
        }
      } catch {}
    }
    if (itemTopic && !itemSyncFresh) {
      const itemId = String(resourceData?.id || resourceData?.item_id || '').trim();
      if (itemId && typeof markItemUnconfirmed === 'function') {
        await markItemUnconfirmed({
          accountId: route.account_id,
          childUserId: route.child_user_id,
          siteId: route.site_id,
          itemId,
          reason: 'resource_revision_behind_event',
          observedAt: event.received_at,
        });
      }
      return {
        account_id: route.account_id,
        child_user_id: route.child_user_id,
        site_id: route.site_id,
        promotion_id: '',
        promotion_type: '',
        outcome: 'item_sync_pending',
        resource_status: classified.resource_status,
        item_sync_attempts: itemSyncAttempts,
      };
    }
    if (typeof updateItemRelations === 'function') {
      const itemId = String(resourceData?.id || resourceData?.item_id || '').trim();
      if (itemId && (classified.item_relations?.length || classified.item_relations_complete)) {
        await updateItemRelations({
          accountId: route.account_id,
          childUserId: route.child_user_id,
          siteId: route.site_id,
          itemId,
          relations: classified.item_relations,
          relationsComplete: classified.item_relations_complete,
          raw: resourceData,
          event,
        });
      }
    }
    let appliedActivityCount = 0;
    if (classified.dirty_activities.length && typeof applyActivityChange === 'function') {
      for (const activity of classified.dirty_activities) {
        let detail = null;
        let removed = false;
        try {
          if (typeof client.getPromotionDetail !== 'function') throw new Error('活动详情读取器未就绪。');
          detail = await client.getPromotionDetail({
            promotionId: activity.promotion_id,
            promotionType: activity.promotion_type,
            userId: route.child_user_id,
            signal,
          });
        } catch (error) {
          const status = Number(error?.status || error?.httpStatus || 0);
          if (status === 404 || status === 410) removed = true;
          else {
            try {
              await markDirty({
                accountId: activity.account_id,
                childUserId: activity.child_user_id,
                siteId: activity.site_id,
                promotionId: activity.promotion_id,
                promotionType: activity.promotion_type,
                eventCursor: event.event_id,
                gap: false,
              }, {
                accountId: activity.account_id,
                childUserId: activity.child_user_id,
                siteId: activity.site_id,
              });
            } catch {}
            throw error;
          }
        }
        try {
          await applyActivityChange({ ...activity, detail, resourceData, event, removed });
          appliedActivityCount += 1;
        } catch (error) {
          try {
            await markDirty({
              accountId: activity.account_id,
              childUserId: activity.child_user_id,
              siteId: activity.site_id,
              promotionId: activity.promotion_id,
              promotionType: activity.promotion_type,
              eventCursor: event.event_id,
              gap: false,
            }, {
              accountId: activity.account_id,
              childUserId: activity.child_user_id,
              siteId: activity.site_id,
            });
          } catch {}
          throw error;
        }
      }
    } else {
      for (const activity of classified.dirty_activities) {
        await markDirty({
          accountId: activity.account_id,
          siteId: activity.site_id,
          promotionId: activity.promotion_id,
          promotionType: activity.promotion_type,
          eventCursor: event.event_id,
          gap: false,
        }, {
          accountId: activity.account_id,
          childUserId: activity.child_user_id,
          siteId: activity.site_id,
        });
      }
    }
    return {
      account_id: route.account_id,
      child_user_id: route.child_user_id,
      site_id: route.site_id,
      promotion_id: classified.dirty_activities.length === 1 ? classified.dirty_activities[0].promotion_id : '',
      promotion_type: classified.dirty_activities.length === 1 ? classified.dirty_activities[0].promotion_type : '',
      outcome: classified.item_only ? 'item_updated' : appliedActivityCount ? 'activity_updated' : 'activity_dirty',
      resource_status: classified.resource_status,
      dirty_activity_count: classified.dirty_activities.length,
      applied_activity_count: appliedActivityCount,
      ...(itemTopic ? { item_sync_attempts: itemSyncAttempts || 1 } : {}),
    };
  };
}
