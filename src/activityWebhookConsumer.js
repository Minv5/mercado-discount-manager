const SUPPORTED_TOPICS = new Set(['public_offers', 'public_candidates', 'items', 'marketplace_items']);

function activityWebhookError(message, code, status = 422) {
  const error = new Error(message);
  error.code = code;
  error.status = status;
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
  const resourceSiteId = siteIdFromText(event.resource);
  const resourceChildId = resourceRemoteUserId(event.resource);
  if (resourceChildId && resourceChildId !== event.remote_user_id) {
    throw activityWebhookError('活动通知的账号归属与资源不一致，已阻断处理。', 'ACTIVITY_CALLBACK_ROUTE_MISMATCH');
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
  if (publicTopic && !relations.length) {
    throw activityWebhookError('平台通知未返回可确认的活动关系，已保留等待重试。', 'ACTIVITY_CALLBACK_RESOURCE_UNCLASSIFIED', 502);
  }
  return {
    dirty_activities: relations.map((relation) => ({ ...route, ...relation })),
    catalog_dirty: !publicTopic && relations.length === 0,
    resource_status: String(resourceData?.status?.id || resourceData?.status || ''),
  };
}

export function createActivityWebhookConsumer({
  listMarketplaceSites,
  listAccounts,
  createResourceClient,
  markDirty,
  invalidateCatalog,
  onItemMissing = null,
  updateItemPrice = null,
  resolveItemOwner = null,
} = {}) {
  return async (input, { signal = null } = {}) => {
    const event = normalizeActivityWebhookEvent(input);
    const marketplaceSites = await listMarketplaceSites();
    const accounts = await listAccounts();
    let route;
    try {
      route = resolveActivityWebhookRoute({ event, marketplaceSites, accounts });
    } catch (error) {
      if (String(error?.code || '') === 'ACTIVITY_CALLBACK_ROUTE_AMBIGUOUS' && typeof resolveItemOwner === 'function') {
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
        const localOwners = itemId ? await resolveItemOwner(itemId) : [];
        const localMatch = localOwners.filter(registeredRoute).filter(matchesSite);
        if (localMatch.length === 1) {
          route = localMatch[0];
        } else {
          let platformOwner = null;
          try {
            const item = await createResourceClient({ account_id: event.remote_user_id, child_user_id: '', site_id: '' })
              .then((client) => client.getMarketplaceItem(itemId, { signal }));
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
    const client = await createResourceClient(route);
    const resourcePath = buildNotificationResourcePath(event);
    let resourceData;
    try {
      resourceData = await client.getNotificationResource(resourcePath, { signal });
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
      if (notFound && itemId) {
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
      throw error;
    }
    const classified = classifyActivityWebhookResource({ event, route, resourceData });
    if ((event.topic === 'marketplace_items' || event.topic === 'items') && typeof updateItemPrice === 'function') {
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
          });
        }
      } catch {}
    }
    for (const activity of classified.dirty_activities) {
      await markDirty({
        accountId: activity.account_id,
        siteId: activity.site_id,
        promotionId: activity.promotion_id,
        promotionType: activity.promotion_type,
        eventCursor: null,
        gap: false,
      }, {
        accountId: activity.account_id,
        childUserId: activity.child_user_id,
        siteId: activity.site_id,
      });
    }
    if (classified.catalog_dirty) {
      await markDirty({
        accountId: route.account_id,
        siteId: route.site_id,
        eventCursor: null,
        gap: false,
      }, {
        accountId: route.account_id,
        childUserId: route.child_user_id,
        siteId: route.site_id,
      });
      await invalidateCatalog({ accountId: route.account_id, childUserId: route.child_user_id, siteId: route.site_id });
    }
    return {
      account_id: route.account_id,
      child_user_id: route.child_user_id,
      site_id: route.site_id,
      promotion_id: classified.dirty_activities.length === 1 ? classified.dirty_activities[0].promotion_id : '',
      promotion_type: classified.dirty_activities.length === 1 ? classified.dirty_activities[0].promotion_type : '',
      outcome: classified.catalog_dirty ? 'catalog_dirty' : 'activity_dirty',
      resource_status: classified.resource_status,
      dirty_activity_count: classified.dirty_activities.length,
    };
  };
}
