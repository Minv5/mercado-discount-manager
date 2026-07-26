import { API_BASE_URL, APP_VERSION, DEFAULT_AUTH_DOMAIN } from './config.js';
import { ApiError } from './errors.js';
import { CANDIDATE_INCOMPLETE_CODE, MARKETPLACE_CANDIDATE_INCOMPLETE_CODE, candidateIncompleteMessage } from './candidateResolution.js';
import { buildItemIdentitySummary } from './activityChangeCache.js';

export const PARTIAL_SPARSE_MARKETPLACE_CANDIDATE = 'partial_api_sparse_marketplace_candidate';
const REQUEST_TIMEOUT_MS = Math.max(5000, Number(process.env.ML_REQUEST_TIMEOUT_MS || 45000));

export function buildAuthorizationUrl({ authDomain = DEFAULT_AUTH_DOMAIN, clientId, redirectUri, state, codeChallenge }) {
  const url = new URL('/authorization', authDomain);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('client_id', clientId);
  url.searchParams.set('redirect_uri', redirectUri);
  url.searchParams.set('state', state);
  url.searchParams.set('code_challenge', codeChallenge);
  url.searchParams.set('code_challenge_method', 'S256');
  return url.toString();
}

export class MercadoLibreClient {
  constructor({
    accessToken = null,
    userId = null,
    callerId = null,
    apiBaseUrl = API_BASE_URL,
    marketplace = false,
    readScheduler = null,
    readAccountId = null,
  } = {}) {
    this.accessToken = accessToken;
    this.userId = userId;
    this.callerId = callerId || userId;
    this.apiBaseUrl = apiBaseUrl;
    this.marketplace = marketplace;
    this.readScheduler = readScheduler;
    this.readAccountId = String(readAccountId || userId || callerId || '__global__');
  }

  async exchangeCode({ clientId, clientSecret, code, redirectUri, codeVerifier }) {
    return postForm(`${API_BASE_URL}/oauth/token`, {
      grant_type: 'authorization_code',
      client_id: clientId,
      client_secret: clientSecret,
      code,
      redirect_uri: redirectUri,
      code_verifier: codeVerifier
    });
  }

  async refreshToken({ clientId, clientSecret, refreshToken }) {
    return postForm(`${API_BASE_URL}/oauth/token`, {
      grant_type: 'refresh_token',
      client_id: clientId,
      client_secret: clientSecret,
      refresh_token: refreshToken
    });
  }

  async getMe() {
    return this.request('/users/me');
  }

  async getPromotions({ userId = this.userId, callerId = this.callerId, includeVersionHeader = false, signal = null } = {}) {
    const headers = {};
    if (callerId) headers['X-Caller-Id'] = String(callerId);
    if (includeVersionHeader) headers.version = APP_VERSION;
    return this.request(`/seller-promotions/users/${encodeURIComponent(userId)}?app_version=${APP_VERSION}`, {
      headers,
      signal,
      readKind: 'activity',
    });
  }

  async getMarketplaceUsers(merchantId, { signal = null } = {}) {
    return this.request(`/marketplace/users/${encodeURIComponent(merchantId)}`, { signal, readKind: 'activity' });
  }

  async getMarketplacePromotions(childUserId, {
    callerId = this.callerId || childUserId,
    limit = 50,
    maxPages = 100,
    signal = null,
  } = {}) {
    const safeLimit = Math.max(1, Math.min(50, Math.floor(Number(limit) || 50)));
    const safeMaxPages = Math.max(1, Math.floor(Number(maxPages) || 100));
    const promotions = new Map();
    const seenSearchAfter = new Set();
    let offset = 0;
    let searchAfter = null;
    let total = null;
    let pages = 0;
    let fetchedRows = 0;
    let lastPaging = {};
    let lastData = {};

    while (pages < safeMaxPages) {
      const path = new URL(`/marketplace/seller-promotions/users/${encodeURIComponent(childUserId)}`, this.apiBaseUrl);
      path.searchParams.set('limit', String(safeLimit));
      if (searchAfter) path.searchParams.set('searchAfter', searchAfter);
      else path.searchParams.set('offset', String(offset));
      const data = await this.request(path, {
        headers: { version: APP_VERSION, 'X-Caller-Id': String(callerId || childUserId) },
        signal,
        readKind: 'activity',
      });
      lastData = data && typeof data === 'object' ? data : {};
      const rows = extractPromotions(data);
      const paging = data?.paging && typeof data.paging === 'object' ? data.paging : {};
      lastPaging = paging;
      pages += 1;
      fetchedRows += rows.length;
      for (const promotion of rows) {
        const key = marketplacePromotionIdentity(promotion);
        if (!promotions.has(key)) promotions.set(key, promotion);
      }

      const pageTotal = Number(paging.total);
      if (Number.isFinite(pageTotal) && pageTotal >= 0) total = pageTotal;
      if (total !== null && fetchedRows >= total) break;
      if (!rows.length) break;

      const nextSearchAfter = String(paging.searchAfter ?? paging.search_after ?? '').trim();
      if (nextSearchAfter && !seenSearchAfter.has(nextSearchAfter)) {
        seenSearchAfter.add(nextSearchAfter);
        searchAfter = nextSearchAfter;
        continue;
      }

      const pageOffset = Number(paging.offset);
      const pageLimit = Number(paging.limit);
      const nextOffset = (Number.isFinite(pageOffset) ? pageOffset : offset)
        + (Number.isFinite(pageLimit) && pageLimit > 0 ? pageLimit : safeLimit);
      if (nextOffset <= offset) break;
      offset = nextOffset;
      searchAfter = null;
      if (total === null && rows.length < safeLimit) break;
    }

    return {
      ...lastData,
      results: [...promotions.values()],
      paging: {
        ...lastPaging,
        offset: 0,
        limit: safeLimit,
        total: total ?? promotions.size,
        fetched: promotions.size,
        pages,
        complete: total === null ? true : fetchedRows >= total
      }
    };
  }

  async getPromotionDetail({
    promotionId,
    promotionType,
    userId = this.userId,
    signal = null,
  } = {}) {
    const prefix = this.marketplace ? '/marketplace/seller-promotions' : '/seller-promotions';
    const path = new URL(`${prefix}/promotions/${encodeURIComponent(promotionId)}`, this.apiBaseUrl);
    path.searchParams.set('promotion_type', String(promotionType || '').toUpperCase());
    path.searchParams.set('app_version', APP_VERSION);
    if (this.marketplace && userId) path.searchParams.set('user_id', String(userId));
    return this.request(path, {
      headers: this.marketplace ? { version: APP_VERSION } : {},
      signal,
      readKind: 'detail',
      readKey: `promotion-detail|${String(userId || '')}|${String(promotionType || '').toUpperCase()}|${String(promotionId || '')}`,
    });
  }

  async getPromotionItems({ promotionId, promotionType, status, limit = 50, offset = 0, searchAfter = null, signal = null }) {
    const safeLimit = clampPromotionItemLimit(limit, promotionType);
    const prefix = this.marketplace ? '/marketplace/seller-promotions' : '/seller-promotions';
    const path = new URL(`${prefix}/promotions/${encodeURIComponent(promotionId)}/items`, this.apiBaseUrl);
    path.searchParams.set('user_id', this.userId);
    path.searchParams.set('promotion_type', promotionType);
    path.searchParams.set('limit', String(safeLimit));
    if (searchAfter) path.searchParams.set('searchAfter', String(searchAfter));
    else path.searchParams.set('offset', String(offset));
    path.searchParams.set('app_version', APP_VERSION);
    if (status) path.searchParams.set('status', status);
    return this.request(path, this.marketplace ? { headers: { version: APP_VERSION }, signal, readKind: 'detail' } : { signal, readKind: 'detail' });
  }

  async probePromotionItems({
    promotionId,
    promotionType,
    status,
    limit = 50,
    signal = null,
  }) {
    const page = await this.getPromotionItems({
      promotionId,
      promotionType,
      status,
      limit,
      offset: 0,
      searchAfter: null,
      signal,
    });
    const results = Array.isArray(page?.results) ? page.results : Array.isArray(page) ? page : [];
    const total = Number(page?.paging?.total);
    return {
      platform_total: Number.isFinite(total) ? total : null,
      first_page_item_ids: stableItemIds(results),
      identity_summary: null,
      identity_summary_complete: false,
      probe_scope: 'first_page_only',
      results_type: Array.isArray(page?.results) ? 'array' : page?.results === null ? 'null' : typeof page?.results,
      has_search_after: Boolean(page?.paging?.searchAfter || page?.paging?.search_after || page?.searchAfter || page?.search_after),
      page,
    };
  }

  async searchMarketplaceUserItems({ userId = this.userId, status = null, limit = 50, scrollId = null, searchType = 'scan', signal = null } = {}) {
    const path = new URL(`/marketplace/users/${encodeURIComponent(userId)}/items/search`, this.apiBaseUrl);
    path.searchParams.set('limit', String(clampLimit(limit)));
    if (searchType) path.searchParams.set('search_type', searchType);
    if (status && status !== 'all') path.searchParams.set('status', status);
    if (scrollId) path.searchParams.set('scroll_id', String(scrollId));
    return this.request(path, { headers: { version: APP_VERSION }, signal, readKind: 'detail' });
  }

  async probeMarketplaceUserItems({
    userId = this.userId,
    status = 'all',
    limit = 50,
    signal = null,
  } = {}) {
    const page = await this.searchMarketplaceUserItems({
      userId,
      status,
      limit,
      scrollId: null,
      searchType: 'scan',
      signal,
    });
    const results = Array.isArray(page?.results) ? page.results : [];
    const total = Number(page?.paging?.total);
    return {
      platform_total: Number.isFinite(total) ? total : null,
      first_page_item_ids: stableItemIds(results),
      page,
    };
  }

  async scanMarketplaceUserItems({ userId = this.userId, status = 'active', limit = 50, maxItems = 'all', maxPages = 500, signal = null } = {}) {
    const maxToCollect = normalizeMaxItems(maxItems);
    const ids = [];
    const seen = new Set();
    let scrollId = null;
    let total = null;
    let pagesRead = 0;
    let duplicateCount = 0;
    let stopReason = null;
    let firstPageItemIds = null;
    while (ids.length < maxToCollect && pagesRead < maxPages) {
      const data = await this.searchMarketplaceUserItems({ userId, status, limit, scrollId, searchType: 'scan', signal });
      pagesRead += 1;
      const results = Array.isArray(data?.results) ? data.results : [];
      if (firstPageItemIds === null) firstPageItemIds = stableItemIds(results);
      total = data?.paging?.total ?? total;
      for (const id of results) {
        const key = String(id);
        if (seen.has(key)) {
          duplicateCount += 1;
          continue;
        }
        seen.add(key);
        ids.push(key);
        if (ids.length >= maxToCollect) break;
      }
      const nextScrollId = data?.scroll_id || data?.scrollId || null;
      if (!nextScrollId || nextScrollId === scrollId) {
        stopReason = nextScrollId === scrollId ? 'scroll_id_stalled' : 'no_next_scroll_id';
        break;
      }
      scrollId = nextScrollId;
      if (total !== null && ids.length >= total) break;
      if (results.length === 0) {
        stopReason = 'empty_results';
        break;
      }
    }
    if (pagesRead >= maxPages && total !== null && ids.length < total) stopReason = 'max_pages';
    return {
      ids,
      total: total ?? ids.length,
      saved: ids.length,
      isFullFetch: total === null ? true : ids.length >= total,
      sampleOnly: total !== null && ids.length < total,
      rawSummary: {
        source: 'marketplace_users_items_search_scan',
        listing_status: status || 'all',
        pages_read: pagesRead,
        duplicate_count: duplicateCount,
        stop_reason: stopReason,
        requested_max_items: Number.isFinite(maxToCollect) ? maxToCollect : 'all',
        inventory_first_page_item_ids: firstPageItemIds || [],
      }
    };
  }

  async getMarketplaceItem(itemId, { signal = null } = {}) {
    return this.request(`/marketplace/items/${encodeURIComponent(itemId)}`, {
      headers: { version: APP_VERSION },
      signal,
      readKind: 'detail',
    });
  }

  async getNotificationResource(resourcePath, { signal = null } = {}) {
    const safePath = String(resourcePath || '').trim();
    if (!safePath.startsWith('/') || safePath.includes('://') || safePath.includes('..')) {
      throw new Error('活动通知资源地址无效');
    }
    if (!safePath.startsWith('/marketplace/seller-promotions/promotions/')
        && !safePath.startsWith('/marketplace/items/')) {
      throw new Error('活动通知资源类型不受支持');
    }
    return this.request(safePath, {
      headers: { version: APP_VERSION },
      signal,
      readKind: 'detail',
      readKey: `activity-webhook|${safePath}`,
    });
  }

  async fetchAllPromotionItems({
    promotionId,
    promotionType,
    status,
    limit = 50,
    maxItems = 5000,
    maxPages = 500,
    maxConsecutiveEmptyPages = 25,
    maxTotalEmptyPages = 120,
    initialPage = null,
    signal = null,
  }) {
    const pageLimit = clampPromotionItemLimit(limit, promotionType);
    const maxToCollect = normalizeMaxItems(maxItems);
    const collected = [];
    const seenItemIds = new Set();
    let offset = 0;
    let searchAfter = null;
    let total = null;
    let detailStatus = 'empty';
    let warning = null;
    let rawFirstPageSummary = null;
    let pagesRead = 0;
    let emptyPageCount = 0;
    let consecutiveEmptyPages = 0;
    let duplicateCount = 0;
    let lastSearchAfter = null;
    let stopReason = null;
    while (collected.length < maxToCollect && pagesRead < maxPages) {
      const data = pagesRead === 0 && initialPage
        ? initialPage
        : await this.getPromotionItems({ promotionId, promotionType, status, limit: pageLimit, offset, searchAfter, signal });
      pagesRead += 1;
      const resultValue = data?.results;
      const results = Array.isArray(resultValue) ? resultValue : Array.isArray(data) ? data : [];
      total = data.paging?.total ?? total;
      const nextSearchAfter = data?.paging?.searchAfter || data?.paging?.search_after || data?.searchAfter || data?.search_after || null;
      if (nextSearchAfter) lastSearchAfter = String(nextSearchAfter);
      const uniqueResults = [];
      for (const row of results) {
        const itemId = row?.item_id || row?.id;
        const key = itemId ? String(itemId) : JSON.stringify(row);
        if (seenItemIds.has(key)) {
          duplicateCount += 1;
          continue;
        }
        seenItemIds.add(key);
        uniqueResults.push(row);
      }
      if (results.length === 0) {
        emptyPageCount += 1;
        consecutiveEmptyPages += 1;
      } else {
        consecutiveEmptyPages = 0;
      }
      const remainingByMax = maxToCollect - collected.length;
      const remainingByTotal = total === null ? remainingByMax : Math.max(0, total - collected.length);
      const remaining = Math.min(remainingByMax, remainingByTotal);
      collected.push(...uniqueResults.slice(0, remaining));
      if (!rawFirstPageSummary) {
        rawFirstPageSummary = {
          keys: data && typeof data === 'object' ? Object.keys(data) : [],
          hasResultsKey: Boolean(data && Object.hasOwn(data, 'results')),
          resultsType: Array.isArray(resultValue) ? 'array' : resultValue === null ? 'null' : typeof resultValue,
          paging: data?.paging ? { total: data.paging.total, limit: data.paging.limit, offset: data.paging.offset, hasSearchAfter: Boolean(data.paging.searchAfter || data.paging.search_after) } : null,
          first_page_item_ids: stableItemIds(results),
        };
      }
      const marketplaceCandidate = this.marketplace && status === 'candidate';
      const canContinueSparse = marketplaceCandidate
        && (total ?? 0) > collected.length
        && nextSearchAfter
        && nextSearchAfter !== searchAfter
        && consecutiveEmptyPages < maxConsecutiveEmptyPages
        && emptyPageCount < maxTotalEmptyPages;
      if ((total ?? 0) > 0 && results.length === 0 && canContinueSparse) {
        searchAfter = nextSearchAfter;
        continue;
      }
      if ((total ?? 0) > 0 && (results.length === 0 || (uniqueResults.length === 0 && collected.length < total))) {
        const marketplaceCandidate = this.marketplace && status === 'candidate';
        if (marketplaceCandidate && collected.length > 0) {
          detailStatus = PARTIAL_SPARSE_MARKETPLACE_CANDIDATE;
          warning = sparsePartialWarning({ saved: collected.length, total, emptyPageCount, pagesRead });
        } else {
          detailStatus = marketplaceCandidate ? MARKETPLACE_CANDIDATE_INCOMPLETE_CODE : CANDIDATE_INCOMPLETE_CODE;
          warning = results.length === 0
            ? `${candidateIncompleteMessage({ marketplaceCandidate })}；可等待官方/平台修复、联系 Mercado 支持，或先人工导入 candidate item_id 草案后再用只读明细补齐价格。`
            : '平台分页未返回新的商品明细，疑似分页游标异常；不能标记为全量，需使用 searchAfter 或等待平台修复。';
        }
        stopReason = results.length === 0 ? 'empty_page_limit_or_no_next_results' : 'duplicate_page_stalled';
        break;
      }
      if (results.length === 0) break;
      detailStatus = 'ok';
      if (nextSearchAfter && nextSearchAfter !== searchAfter) searchAfter = nextSearchAfter;
      else {
        if (searchAfter && nextSearchAfter === searchAfter && collected.length < (total ?? collected.length)) {
          detailStatus = marketplaceCandidate && collected.length > 0 ? PARTIAL_SPARSE_MARKETPLACE_CANDIDATE : CANDIDATE_INCOMPLETE_CODE;
          warning = marketplaceCandidate && collected.length > 0
            ? sparsePartialWarning({ saved: collected.length, total, emptyPageCount, pagesRead })
            : '平台分页 token 未推进，无法继续读取商品明细。';
          stopReason = 'search_after_stalled';
          break;
        }
        offset += results.length;
      }
      if (total !== null && collected.length >= total) break;
      if (results.length < pageLimit && !nextSearchAfter) break;
    }
    if (pagesRead >= maxPages && total !== null && collected.length < total) {
      detailStatus = this.marketplace && status === 'candidate' && collected.length > 0 ? PARTIAL_SPARSE_MARKETPLACE_CANDIDATE : CANDIDATE_INCOMPLETE_CODE;
      warning = detailStatus === PARTIAL_SPARSE_MARKETPLACE_CANDIDATE
        ? sparsePartialWarning({ saved: collected.length, total, emptyPageCount, pagesRead })
        : '达到最大分页数，仍未读完商品明细。';
      stopReason = 'max_pages';
    }
    if ((total ?? collected.length) === 0) detailStatus = 'empty';
    const saved = collected.length;
    if (detailStatus === 'ok' && total !== null && saved < total) {
      if (Number.isFinite(maxToCollect) && saved >= maxToCollect) {
        detailStatus = 'partial';
        warning = `样本读取已保存 ${saved}/${total} 个商品；这不是平台全量候选，不能按“全部报活动”执行。`;
      } else if (this.marketplace && status === 'candidate') {
        detailStatus = PARTIAL_SPARSE_MARKETPLACE_CANDIDATE;
        warning = sparsePartialWarning({ saved, total, emptyPageCount, pagesRead });
      } else {
        detailStatus = 'partial';
        warning = `已保存 ${saved}/${total} 个商品，未达到平台 total。`;
      }
    }
    const full = (total ?? saved) <= saved && ![CANDIDATE_INCOMPLETE_CODE, MARKETPLACE_CANDIDATE_INCOMPLETE_CODE, PARTIAL_SPARSE_MARKETPLACE_CANDIDATE, 'partial'].includes(detailStatus);
    return {
      results: collected,
      total: total ?? collected.length,
      saved,
      detailStatus,
      warning,
      blocked: detailStatus === CANDIDATE_INCOMPLETE_CODE || detailStatus === MARKETPLACE_CANDIDATE_INCOMPLETE_CODE,
      isFullFetch: full,
      sampleOnly: detailStatus === 'partial' || detailStatus === PARTIAL_SPARSE_MARKETPLACE_CANDIDATE,
      rawSummary: {
        ...rawFirstPageSummary,
        pages_read: pagesRead,
        empty_page_count: emptyPageCount,
        consecutive_empty_pages: consecutiveEmptyPages,
        unique_count: collected.length,
        duplicate_count: duplicateCount,
        last_search_after: lastSearchAfter ? '[present]' : null,
        stop_reason: stopReason,
        requested_max_items: Number.isFinite(maxToCollect) ? maxToCollect : 'all',
        is_full_fetch: full,
        sample_only: detailStatus === 'partial' || detailStatus === PARTIAL_SPARSE_MARKETPLACE_CANDIDATE,
        identity_summary: buildItemIdentitySummary(collected, { complete: full }),
      }
    };
  }

  async enrollItem({ itemId, promotionId, promotionType, dealPrice }) {
    const prefix = this.marketplace ? '/marketplace/seller-promotions' : '/seller-promotions';
    const path = this.promotionItemWritePath(prefix, itemId);
    return this.request(path, {
      method: 'POST',
      body: { promotion_id: promotionId, promotion_type: promotionType, deal_price: dealPrice },
      headers: this.promotionItemWriteHeaders()
    });
  }

  async updateItem({ itemId, promotionId, promotionType, dealPrice }) {
    const prefix = this.marketplace ? '/marketplace/seller-promotions' : '/seller-promotions';
    const path = this.promotionItemWritePath(prefix, itemId);
    return this.request(path, {
      method: 'PUT',
      body: { promotion_id: promotionId, promotion_type: promotionType, deal_price: dealPrice },
      headers: this.promotionItemWriteHeaders()
    });
  }

  async cancelItem({ itemId, promotionId, promotionType, offerId }) {
    const prefix = this.marketplace ? '/marketplace/seller-promotions' : '/seller-promotions';
    const path = this.promotionItemWritePath(prefix, itemId);
    path.searchParams.set('promotion_type', promotionType);
    path.searchParams.set('promotion_id', promotionId);
    if (offerId) path.searchParams.set('offer_id', offerId);
    return this.request(path, { method: 'DELETE', headers: this.promotionItemWriteHeaders() });
  }

  promotionItemWritePath(prefix, itemId) {
    const path = new URL(`${prefix}/items/${encodeURIComponent(itemId)}`, this.apiBaseUrl);
    if (this.userId) path.searchParams.set('user_id', String(this.userId));
    path.searchParams.set('app_version', APP_VERSION);
    return path;
  }

  promotionItemWriteHeaders() {
    if (!this.marketplace) return {};
    const headers = { version: APP_VERSION };
    if (this.callerId) headers['X-Client-Id'] = String(this.callerId);
    return headers;
  }

  async createSellerCampaign({
    childUserId = this.userId,
    callerId = this.callerId,
    clientUserId = callerId,
    name,
    startDate,
    finishDate,
    subType = 'FLEXIBLE_PERCENTAGE',
    signal = null,
  }) {
    const path = `/marketplace/seller-promotions/seller-campaign/${encodeURIComponent(childUserId)}`;
    const headers = { version: APP_VERSION };
    if (callerId) headers['X-Caller-Id'] = String(callerId);
    if (clientUserId) headers['X-Client-Id'] = String(clientUserId);
    return this.request(path, {
      method: 'POST',
      body: {
        promotion_type: 'SELLER_CAMPAIGN',
        name,
        sub_type: subType,
        start_date: startDate,
        finish_date: finishDate
      },
      headers,
      signal,
      includeResponseMeta: true
    });
  }

  async request(pathOrUrl, options = {}) {
    const url = typeof pathOrUrl === 'string' && pathOrUrl.startsWith('http')
      ? pathOrUrl
      : new URL(pathOrUrl, this.apiBaseUrl).toString();
    const headers = {
      Authorization: `Bearer ${this.accessToken}`,
      Accept: 'application/json'
    };
    if (this.callerId) headers['X-Caller-Id'] = String(this.callerId);
    Object.assign(headers, options.headers || {});
    if (options.body) headers['Content-Type'] = 'application/json';

    const requestOptions = {
      method: options.method || 'GET',
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: options.signal || undefined,
    };
    const method = String(requestOptions.method || 'GET').toUpperCase();
    if (method === 'GET' && this.readScheduler) {
      const caller = String(headers['X-Caller-Id'] || this.callerId || '');
      const key = String(options.readKey || `${method}|${url}|caller=${caller}`);
      return this.readScheduler.schedule({
        accountId: this.readAccountId,
        key,
        kind: options.readKind || 'read',
        signal: options.signal || null,
      }, () => requestJson(url, requestOptions, 1, { includeMeta: Boolean(options.includeResponseMeta), externalRetry: true }));
    }
    return requestJson(url, requestOptions, 1, { includeMeta: Boolean(options.includeResponseMeta) });
  }
}

export function extractMarketplaceUsers(data) {
  const candidates = Array.isArray(data)
    ? data
    : data?.users || data?.marketplace_users || data?.results || data?.marketplaces || [];
  return candidates
    .map((entry) => ({
      user_id: entry.user_id || entry.id || entry.remote_user_id || entry.seller_id,
      site_id: entry.site_id || entry.site || entry.marketplace || entry.country_id || null,
      logistic_type: entry.logistic_type || entry.logistics_type || entry.fulfillment_type || entry.type || null,
      raw: entry
    }))
    .filter((entry) => entry.user_id);
}

export function extractPromotions(data) {
  return Array.isArray(data?.results)
    ? data.results
    : Array.isArray(data?.promotions)
      ? data.promotions
      : Array.isArray(data)
        ? data
        : [];
}

export function mergePromotionsByIdentity(...groups) {
  const merged = new Map();
  for (const group of groups) {
    for (const promotion of Array.isArray(group) ? group : []) {
      const key = marketplacePromotionIdentity(promotion);
      if (!merged.has(key)) merged.set(key, promotion);
    }
  }
  return [...merged.values()];
}

function marketplacePromotionIdentity(promotion = {}) {
  const id = String(promotion.id || promotion.promotion_id || promotion.deal_id || '').trim();
  const type = String(promotion.promotion_type || promotion.type || '').trim().toUpperCase();
  if (id) return `${type || 'UNKNOWN'}|${id}`;
  return `${type || 'UNKNOWN'}|${JSON.stringify(promotion)}`;
}

async function postForm(url, fields) {
  const body = new URLSearchParams();
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== null) body.set(key, value);
  }
  return requestJson(url, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/x-www-form-urlencoded'
    },
    body
  });
}

async function requestJson(url, options, attempt = 1, meta = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal;
    const response = await fetch(url, { ...options, signal });
    const text = await response.text();
    const body = text ? safeJson(text) : null;
    if (!response.ok) {
      if (!meta.externalRetry && [429, 500, 502, 503, 504].includes(response.status) && attempt < 3) {
        await delay(300 * attempt);
        return requestJson(url, options, attempt + 1, meta);
      }
      const error = new ApiError(`Mercado Libre API ${response.status}`, response.status, body || text);
      const retryAfter = response.headers.get('retry-after');
      if (retryAfter) {
        const seconds = Number(retryAfter);
        const date = Date.parse(retryAfter);
        error.retryAfterMs = Number.isFinite(seconds)
          ? Math.max(0, seconds * 1_000)
          : Number.isFinite(date) ? Math.max(0, date - Date.now()) : 0;
      }
      throw error;
    }
    if (meta.includeMeta) {
      return {
        http_status: response.status,
        headers: safeResponseHeaders(response.headers),
        body,
        raw_text_length: text.length
      };
    }
    return body;
  } catch (error) {
    if (error?.name === 'AbortError') {
      if (options.signal?.aborted) throw error;
      const timeoutError = new ApiError('Mercado Libre API 请求超时', 504, { timeout_ms: REQUEST_TIMEOUT_MS });
      timeoutError.code = 'ETIMEDOUT';
      throw timeoutError;
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function safeResponseHeaders(headers) {
  const allowed = ['content-type', 'x-request-id', 'x-trace-id', 'x-flow-starter', 'x-meli-trace-site'];
  const result = {};
  for (const key of allowed) {
    const value = headers.get(key);
    if (value) result[key] = String(value).slice(0, 200);
  }
  return result;
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizeMaxItems(maxItems) {
  if (maxItems === null || maxItems === undefined || maxItems === '' || maxItems === 'all' || maxItems === 'full') return Number.POSITIVE_INFINITY;
  const n = Number(maxItems);
  if (!Number.isFinite(n) || n <= 0) return Number.POSITIVE_INFINITY;
  return Math.max(1, Math.floor(n));
}

function clampLimit(limit) {
  const n = Number(limit);
  if (!Number.isFinite(n) || n <= 0) return 50;
  return Math.min(50, Math.max(1, Math.floor(n)));
}

function clampPromotionItemLimit(limit, promotionType) {
  const safeLimit = clampLimit(limit);
  return String(promotionType || '').toUpperCase() === 'SMART'
    ? Math.min(49, safeLimit)
    : safeLimit;
}

function stableItemIds(rows = []) {
  return [...new Set((Array.isArray(rows) ? rows : [])
    .map((row) => typeof row === 'string' ? row : row?.item_id || row?.id)
    .map((value) => String(value || '').trim())
    .filter(Boolean))]
    .sort();
}

function sparsePartialWarning({ saved, total, emptyPageCount, pagesRead }) {
  return `平台 candidate total=${total}，当前只读取到 ${saved} 个可见候选；分页中出现 ${emptyPageCount} 个空页/稀疏页，已读取 ${pagesRead} 页。平台剩余候选未返回明细，不能标记为全量。`;
}
