import { API_BASE_URL, APP_VERSION, DEFAULT_AUTH_DOMAIN } from './config.js';
import { ApiError } from './errors.js';
import { CANDIDATE_INCOMPLETE_CODE, MARKETPLACE_CANDIDATE_INCOMPLETE_CODE, candidateIncompleteMessage } from './candidateResolution.js';

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
  constructor({ accessToken = null, userId = null, callerId = null, apiBaseUrl = API_BASE_URL, marketplace = false } = {}) {
    this.accessToken = accessToken;
    this.userId = userId;
    this.callerId = callerId || userId;
    this.apiBaseUrl = apiBaseUrl;
    this.marketplace = marketplace;
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

  async getPromotions() {
    return this.request(`/seller-promotions/users/${encodeURIComponent(this.userId)}?app_version=${APP_VERSION}`);
  }

  async getMarketplaceUsers(merchantId) {
    return this.request(`/marketplace/users/${encodeURIComponent(merchantId)}`);
  }

  async getMarketplacePromotions(childUserId) {
    return this.request(`/marketplace/seller-promotions/users/${encodeURIComponent(childUserId)}`, {
      headers: { version: APP_VERSION, 'X-Caller-Id': String(childUserId) }
    });
  }

  async getPromotionItems({ promotionId, promotionType, status, limit = 50, offset = 0, searchAfter = null }) {
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
    return this.request(path, this.marketplace ? { headers: { version: APP_VERSION } } : {});
  }

  async searchMarketplaceUserItems({ userId = this.userId, status = null, limit = 50, scrollId = null, searchType = 'scan' } = {}) {
    const path = new URL(`/marketplace/users/${encodeURIComponent(userId)}/items/search`, this.apiBaseUrl);
    path.searchParams.set('limit', String(clampLimit(limit)));
    if (searchType) path.searchParams.set('search_type', searchType);
    if (status && status !== 'all') path.searchParams.set('status', status);
    if (scrollId) path.searchParams.set('scroll_id', String(scrollId));
    return this.request(path, { headers: { version: APP_VERSION } });
  }

  async scanMarketplaceUserItems({ userId = this.userId, status = 'active', limit = 50, maxItems = 'all', maxPages = 500 } = {}) {
    const maxToCollect = normalizeMaxItems(maxItems);
    const ids = [];
    const seen = new Set();
    let scrollId = null;
    let total = null;
    let pagesRead = 0;
    let duplicateCount = 0;
    let stopReason = null;
    while (ids.length < maxToCollect && pagesRead < maxPages) {
      const data = await this.searchMarketplaceUserItems({ userId, status, limit, scrollId, searchType: 'scan' });
      pagesRead += 1;
      const results = Array.isArray(data?.results) ? data.results : [];
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
        requested_max_items: Number.isFinite(maxToCollect) ? maxToCollect : 'all'
      }
    };
  }

  async getMarketplaceItem(itemId) {
    return this.request(`/marketplace/items/${encodeURIComponent(itemId)}`, {
      headers: { version: APP_VERSION }
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
    maxTotalEmptyPages = 120
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
      const data = await this.getPromotionItems({ promotionId, promotionType, status, limit: pageLimit, offset, searchAfter });
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
          paging: data?.paging ? { total: data.paging.total, limit: data.paging.limit, offset: data.paging.offset, hasSearchAfter: Boolean(data.paging.searchAfter || data.paging.search_after) } : null
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
        sample_only: detailStatus === 'partial' || detailStatus === PARTIAL_SPARSE_MARKETPLACE_CANDIDATE
      }
    };
  }

  async enrollItem({ itemId, promotionId, promotionType, dealPrice }) {
    const prefix = this.marketplace ? '/marketplace/seller-promotions' : '/seller-promotions';
    return this.request(`${prefix}/items/${encodeURIComponent(itemId)}?app_version=${APP_VERSION}`, {
      method: 'POST',
      body: { promotion_id: promotionId, promotion_type: promotionType, deal_price: dealPrice },
      headers: this.marketplace ? { version: APP_VERSION } : {}
    });
  }

  async updateItem({ itemId, promotionId, promotionType, dealPrice }) {
    const prefix = this.marketplace ? '/marketplace/seller-promotions' : '/seller-promotions';
    return this.request(`${prefix}/items/${encodeURIComponent(itemId)}?app_version=${APP_VERSION}`, {
      method: 'PUT',
      body: { promotion_id: promotionId, promotion_type: promotionType, deal_price: dealPrice },
      headers: this.marketplace ? { version: APP_VERSION } : {}
    });
  }

  async cancelItem({ itemId, promotionId, promotionType, offerId }) {
    const prefix = this.marketplace ? '/marketplace/seller-promotions' : '/seller-promotions';
    const path = new URL(`${prefix}/items/${encodeURIComponent(itemId)}`, this.apiBaseUrl);
    path.searchParams.set('promotion_type', promotionType);
    path.searchParams.set('promotion_id', promotionId);
    if (offerId) path.searchParams.set('offer_id', offerId);
    path.searchParams.set('app_version', APP_VERSION);
    return this.request(path, { method: 'DELETE', headers: this.marketplace ? { version: APP_VERSION } : {} });
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

    return requestJson(url, {
      method: options.method || 'GET',
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined
    }, 1, { includeMeta: Boolean(options.includeResponseMeta) });
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
    const response = await fetch(url, { ...options, signal: options.signal || controller.signal });
    const text = await response.text();
    const body = text ? safeJson(text) : null;
    if (!response.ok) {
      if ([429, 500, 502, 503, 504].includes(response.status) && attempt < 3) {
        await delay(300 * attempt);
        return requestJson(url, options, attempt + 1, meta);
      }
      throw new ApiError(`Mercado Libre API ${response.status}`, response.status, body || text);
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
      throw new ApiError('Mercado Libre API 请求超时', 504, { timeout_ms: REQUEST_TIMEOUT_MS });
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

function sparsePartialWarning({ saved, total, emptyPageCount, pagesRead }) {
  return `平台 candidate total=${total}，当前只读取到 ${saved} 个可见候选；分页中出现 ${emptyPageCount} 个空页/稀疏页，已读取 ${pagesRead} 页。平台剩余候选未返回明细，不能标记为全量。`;
}
