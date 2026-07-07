import fs from 'node:fs';
import path from 'node:path';
import { all, get, nowIso, run } from './db.js';
import { decryptSecret, encryptSecret } from './security.js';
import { filterPromotions, normalizeItem, normalizePromotion, promotionKey, summarizeSites } from './planner.js';
import { readSettings } from './settings.js';
import { DATA_DIR } from './config.js';

const TASK_SUMMARY_CACHE_MS = 30_000;
const TASK_SUMMARY_CACHE_VERSION = 'v4';
const taskSummaryCache = new Map();

function cloneJson(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

export function clearTaskSummaryCache() {
  taskSummaryCache.clear();
  try {
    run('DELETE FROM history_task_summary_cache');
  } catch {
    // Cache invalidation must never block the user operation.
  }
}

function taskSummaryStamp() {
  const task = get(`SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id, COALESCE(MAX(updated_at), '') AS updated_at FROM promo_tasks`) || {};
  const result = get(`SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id FROM promo_action_results`) || {};
  return {
    task_count: Number(task.count || 0),
    task_max_id: Number(task.max_id || 0),
    task_updated_at: String(task.updated_at || ''),
    result_count: Number(result.count || 0),
    result_max_id: Number(result.max_id || 0)
  };
}

function sameTaskSummaryStamp(left, right) {
  return Number(left?.task_count || 0) === Number(right?.task_count || 0)
    && Number(left?.task_max_id || 0) === Number(right?.task_max_id || 0)
    && String(left?.task_updated_at || '') === String(right?.task_updated_at || '')
    && Number(left?.result_count || 0) === Number(right?.result_count || 0)
    && Number(left?.result_max_id || 0) === Number(right?.result_max_id || 0);
}

function loadPersistentTaskSummaryCache(cacheKey, stamp) {
  const row = get('SELECT * FROM history_task_summary_cache WHERE cache_key = ?', [cacheKey]);
  if (!row) return null;
  if (!sameTaskSummaryStamp(row, stamp)) return null;
  try {
    const rows = JSON.parse(row.data_json);
    return Array.isArray(rows) ? rows : null;
  } catch {
    return null;
  }
}

function savePersistentTaskSummaryCache(cacheKey, stamp, rows) {
  run(
    `INSERT OR REPLACE INTO history_task_summary_cache
      (cache_key, task_count, task_max_id, task_updated_at, result_count, result_max_id, data_json, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      cacheKey,
      stamp.task_count,
      stamp.task_max_id,
      stamp.task_updated_at,
      stamp.result_count,
      stamp.result_max_id,
      JSON.stringify(rows),
      nowIso()
    ]
  );
}

export function saveOAuthState(stateRecord) {
  run(
    `INSERT OR REPLACE INTO oauth_states
      (state, client_id, client_secret_cipher, redirect_uri, auth_domain, code_verifier, code_challenge, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      stateRecord.state,
      stateRecord.clientId,
      encryptSecret(stateRecord.clientSecret),
      stateRecord.redirectUri,
      stateRecord.authDomain,
      stateRecord.codeVerifier,
      stateRecord.codeChallenge,
      nowIso()
    ]
  );
}

export function consumeOAuthState(state) {
  const row = get('SELECT * FROM oauth_states WHERE state = ?', [state]);
  if (!row) return null;
  run('DELETE FROM oauth_states WHERE state = ?', [state]);
  return {
    state: row.state,
    clientId: row.client_id,
    clientSecret: decryptSecret(row.client_secret_cipher),
    redirectUri: row.redirect_uri,
    authDomain: row.auth_domain,
    codeVerifier: row.code_verifier
  };
}

export function listPendingOAuthStates({ maxAgeMs = 15 * 60 * 1000 } = {}) {
  const cutoff = new Date(Date.now() - maxAgeMs).toISOString();
  return all(
    `SELECT state, redirect_uri, auth_domain, created_at
     FROM oauth_states
     WHERE created_at >= ?
     ORDER BY created_at DESC`,
    [cutoff]
  );
}

export function clearOAuthStates() {
  run('DELETE FROM oauth_states');
}

export function saveTokenAccount({ token, profile, clientId, clientSecret, redirectUri, authDomain }) {
  const now = nowIso();
  const accountId = String(token.user_id || profile.id);
  const expiresAt = token.expires_in ? new Date(Date.now() + Number(token.expires_in) * 1000).toISOString() : null;
  run(
    `INSERT INTO oauth_tokens
      (provider, account_id, display_name, site_id, scopes, access_token_cipher, refresh_token_cipher, token_type, expires_at,
       raw_json, client_id, client_secret_cipher, redirect_uri, auth_domain, created_at, updated_at)
      VALUES ('mercadolibre', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(account_id) DO UPDATE SET
        display_name = excluded.display_name,
        site_id = excluded.site_id,
        scopes = excluded.scopes,
        access_token_cipher = excluded.access_token_cipher,
        refresh_token_cipher = COALESCE(excluded.refresh_token_cipher, oauth_tokens.refresh_token_cipher),
        token_type = excluded.token_type,
        expires_at = excluded.expires_at,
        raw_json = excluded.raw_json,
        client_id = excluded.client_id,
        client_secret_cipher = excluded.client_secret_cipher,
        redirect_uri = excluded.redirect_uri,
        auth_domain = excluded.auth_domain,
        updated_at = excluded.updated_at`,
    [
      accountId,
      profile.nickname || profile.first_name || accountId,
      profile.site_id || null,
      token.scope || null,
      encryptSecret(token.access_token),
      encryptSecret(token.refresh_token),
      token.token_type || null,
      expiresAt,
      JSON.stringify({ token: redactToken(token), profile }),
      clientId,
      encryptSecret(clientSecret),
      redirectUri,
      authDomain,
      now,
      now
    ]
  );
  return getAccount(accountId);
}

export function updateAccountToken(accountId, token) {
  const expiresAt = token.expires_in ? new Date(Date.now() + Number(token.expires_in) * 1000).toISOString() : null;
  run(
    `UPDATE oauth_tokens SET
      access_token_cipher = ?,
      refresh_token_cipher = COALESCE(?, refresh_token_cipher),
      token_type = ?,
      expires_at = ?,
      scopes = COALESCE(?, scopes),
      updated_at = ?
      WHERE account_id = ?`,
    [
      encryptSecret(token.access_token),
      encryptSecret(token.refresh_token),
      token.token_type || null,
      expiresAt,
      token.scope || null,
      nowIso(),
      String(accountId)
    ]
  );
}

export function listStoredAccounts() {
  return all(
    `SELECT id, provider, account_id, display_name, site_id, scopes, token_type, expires_at, created_at, updated_at
     FROM oauth_tokens ORDER BY updated_at DESC`
  );
}

export function getAccount(accountId) {
  const row = get(
    `SELECT id, provider, account_id, display_name, site_id, scopes, token_type, expires_at, created_at, updated_at
     FROM oauth_tokens WHERE account_id = ?`,
    [String(accountId)]
  );
  return row || null;
}

export function getAccountSecrets(accountId) {
  const row = get('SELECT * FROM oauth_tokens WHERE account_id = ?', [String(accountId)]);
  if (!row) return null;
  return {
    ...row,
    accessToken: decryptSecret(row.access_token_cipher),
    refreshToken: decryptSecret(row.refresh_token_cipher),
    clientSecret: decryptSecret(row.client_secret_cipher)
  };
}

export function saveCampaigns(accountId, promotions, context = {}) {
  const rows = promotions.map(normalizePromotion);
  const now = nowIso();
  for (const promo of rows) {
    run(
      `INSERT INTO promo_campaigns
        (account_id, promotion_id, promotion_type, merchant_id, child_user_id, site_id, logistic_type,
         name, status, start_date, finish_date, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, promotion_id, promotion_type) DO UPDATE SET
          merchant_id = excluded.merchant_id,
          child_user_id = excluded.child_user_id,
          site_id = excluded.site_id,
          logistic_type = excluded.logistic_type,
          name = excluded.name,
          status = excluded.status,
          start_date = excluded.start_date,
          finish_date = excluded.finish_date,
          raw_json = excluded.raw_json,
          updated_at = excluded.updated_at`,
      [
        String(accountId),
        promo.promotion_id,
        promo.promotion_type,
        context.merchantId ? String(context.merchantId) : null,
        context.childUserId ? String(context.childUserId) : null,
        context.siteId || promo.raw?.site_id || null,
        context.logisticType || promo.raw?.logistic_type || null,
        promo.name,
        promo.status,
        promo.start_date,
        promo.finish_date,
        JSON.stringify(promo.raw),
        now
      ]
    );
  }
  return rows;
}

export function listCampaigns(accountId) {
  return all('SELECT * FROM promo_campaigns WHERE account_id = ? ORDER BY site_id, updated_at DESC, name', [String(accountId)]);
}

export function listCampaignsFiltered(accountId, filters = {}) {
  return filterPromotions(listCampaigns(accountId), filters);
}

export function saveMarketplaceSites(accountId, sites) {
  const now = nowIso();
  for (const site of sites) {
    const childUserId = site?.child_user_id || site?.user_id;
    if (!childUserId) continue;
    run(
      `INSERT INTO marketplace_sites
        (account_id, child_user_id, site_id, logistic_type, last_promotion_status, last_promotion_count, last_error, raw_json, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(account_id, child_user_id) DO UPDATE SET
        site_id = excluded.site_id,
        logistic_type = excluded.logistic_type,
        raw_json = excluded.raw_json,
        updated_at = excluded.updated_at`,
      [
        String(accountId),
        String(childUserId),
        site.site_id || null,
        site.logistic_type || null,
        site.last_promotion_status || null,
        site.last_promotion_count ?? 0,
        site.last_error || null,
        JSON.stringify(site.raw || site),
        now
      ]
    );
  }
}

export function updateMarketplaceSitePromotionStatus({ accountId, childUserId, count = 0, status = 'ok', error = null }) {
  run(
    `UPDATE marketplace_sites
     SET last_promotion_status = ?, last_promotion_count = ?, last_error = ?, updated_at = ?
     WHERE account_id = ? AND child_user_id = ?`,
    [status, count ?? 0, error || null, nowIso(), String(accountId), String(childUserId)]
  );
}

export function listMarketplaceSites(accountId) {
  return all(
    `SELECT account_id, child_user_id, site_id, logistic_type, last_promotion_status, last_promotion_count, last_error, raw_json, updated_at
     FROM marketplace_sites
     WHERE account_id = ?
     ORDER BY site_id, logistic_type, child_user_id`,
    [String(accountId)]
  );
}

export function listCycleStatesForPromotions(accountId, promotions) {
  const map = new Map();
  for (const promo of promotions) {
    const state = get(
      `SELECT * FROM cycle_states WHERE account_id = ? AND promotion_id = ? AND promotion_type = ?`,
      [String(accountId), promo.promotion_id, promo.promotion_type]
    );
    if (state) map.set(promotionKey(promo), state);
  }
  return map;
}

export function listItemCountsForPromotions(accountId, promotions, status) {
  const map = new Map();
  for (const promo of promotions) {
    const row = get(
      `SELECT COUNT(*) AS count FROM promo_items
       WHERE account_id = ? AND promotion_id = ? AND promotion_type = ? AND status = ?`,
      [String(accountId), promo.promotion_id, promo.promotion_type, status]
    );
    map.set(promotionKey(promo), row?.count || 0);
  }
  return map;
}

export function listSiteSummaries(accountId) {
  const childSites = listMarketplaceSites(accountId);
  const activitySites = summarizeSites(listCampaigns(accountId));
  if (!childSites.length) return activitySites;
  const activityByChild = new Map(activitySites.map((site) => [String(site.child_user_id || ''), site]));
  return childSites.map((site) => {
    const activity = activityByChild.get(String(site.child_user_id || ''));
    return {
      site_id: site.site_id || activity?.site_id || null,
      child_user_id: site.child_user_id || activity?.child_user_id || null,
      logistic_type: site.logistic_type || activity?.logistic_type || null,
      total: activity?.total || 0,
      by_type: activity?.by_type || {},
      by_status: activity?.by_status || {},
      last_promotion_status: site.last_promotion_status || null,
      last_promotion_count: site.last_promotion_count ?? activity?.total ?? 0,
      last_error: site.last_error || null,
      updated_at: site.updated_at || null
    };
  });
}

export function getCampaign(accountId, promotionId, promotionType) {
  return get(
    `SELECT * FROM promo_campaigns WHERE account_id = ? AND promotion_id = ? AND promotion_type = ?`,
    [String(accountId), promotionId, promotionType]
  );
}

export function saveItems(accountId, promotionId, promotionType, items, context = {}) {
  const rows = items.map((raw) => {
    const item = normalizeItem(raw);
    if (context.itemStatus) item.status = context.itemStatus;
    return item;
  });
  const now = nowIso();
  if (context.replaceStatus) {
    run(
      `DELETE FROM promo_items
       WHERE account_id = ? AND promotion_id = ? AND promotion_type = ? AND status = ?`,
      [String(accountId), promotionId, promotionType, String(context.replaceStatus)]
    );
  }
  for (const item of rows) {
    run(
      `INSERT INTO promo_items
        (account_id, promotion_id, promotion_type, child_user_id, site_id, logistic_type, item_id, status,
         currency_id, original_price, price, suggested_discounted_price, min_discounted_price,
         max_discounted_price, source, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, promotion_id, promotion_type, item_id) DO UPDATE SET
          child_user_id = excluded.child_user_id,
          site_id = excluded.site_id,
          logistic_type = excluded.logistic_type,
          status = excluded.status,
          currency_id = excluded.currency_id,
          original_price = excluded.original_price,
          price = excluded.price,
          suggested_discounted_price = excluded.suggested_discounted_price,
          min_discounted_price = excluded.min_discounted_price,
          max_discounted_price = excluded.max_discounted_price,
          source = excluded.source,
          raw_json = excluded.raw_json,
          updated_at = excluded.updated_at`,
      [
        String(accountId),
        promotionId,
        promotionType,
        context.childUserId ? String(context.childUserId) : null,
        context.siteId || null,
        context.logisticType || null,
        item.item_id,
        item.status,
        item.currency_id,
        item.original_price,
        item.price,
        item.suggested_discounted_price,
        item.min_discounted_price,
        item.max_discounted_price,
        context.source || item.raw?.source || 'seller_promotions_api',
        JSON.stringify(item.raw),
        now
      ]
    );
  }
  return rows;
}

export function deleteItemsBySource(accountId, promotionId, promotionType, status, source) {
  run(
    `DELETE FROM promo_items
     WHERE account_id = ? AND promotion_id = ? AND promotion_type = ? AND status = ? AND source = ?`,
    [String(accountId), promotionId, promotionType, status, source]
  );
}

export function saveItemFetchState({ accountId, promotionId, promotionType, itemStatus, platformTotal, savedCount, detailStatus, warning, raw }) {
  run(
    `INSERT INTO promo_item_fetch_states
      (account_id, promotion_id, promotion_type, item_status, platform_total, saved_count, detail_status, warning, raw_json, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(account_id, promotion_id, promotion_type, item_status) DO UPDATE SET
        platform_total = excluded.platform_total,
        saved_count = excluded.saved_count,
        detail_status = excluded.detail_status,
        warning = excluded.warning,
        raw_json = excluded.raw_json,
        updated_at = excluded.updated_at`,
    [
      String(accountId),
      promotionId,
      promotionType,
      itemStatus,
      platformTotal ?? null,
      savedCount ?? 0,
      detailStatus,
      warning || null,
      raw ? JSON.stringify(raw) : null,
      nowIso()
    ]
  );
}

export function getItemFetchState(accountId, promotionId, promotionType, itemStatus) {
  return get(
    `SELECT * FROM promo_item_fetch_states
     WHERE account_id = ? AND promotion_id = ? AND promotion_type = ? AND item_status = ?`,
    [String(accountId), promotionId, promotionType, itemStatus]
  );
}

export function listItems(accountId, promotionId, promotionType, status) {
  const params = [String(accountId), promotionId, promotionType];
  let sql = `SELECT * FROM promo_items WHERE account_id = ? AND promotion_id = ? AND promotion_type = ?`;
  if (status) {
    sql += ' AND status = ?';
    params.push(status);
  }
  sql += ' ORDER BY item_id';
  return all(sql, params);
}

export function listItemsForPromotions(accountId, promotions, status) {
  const map = new Map();
  for (const promo of promotions) {
    map.set(promotionKey(promo), listItems(accountId, promo.promotion_id, promo.promotion_type, status));
  }
  return map;
}

export function listItemFetchStatesForPromotions(accountId, promotions, status) {
  const map = new Map();
  for (const promo of promotions) {
    const state = getItemFetchState(accountId, promo.promotion_id, promo.promotion_type, status);
    if (state) map.set(promotionKey(promo), state);
  }
  return map;
}

export function createTask({ accountId, promotionId, promotionType, action, mode, discountPercent, directPrice, plan }) {
  clearTaskSummaryCache();
  const now = nowIso();
  const result = run(
    `INSERT INTO promo_tasks
      (account_id, promotion_id, promotion_type, action, mode, discount_percent, direct_price, status,
       total_count, success_count, failed_count, skipped_count, empty_count, completed, summary_json, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)`,
    [
      String(accountId),
      promotionId,
      promotionType,
      action,
      mode,
      discountPercent ?? null,
      directPrice ?? null,
      mode === 'dry-run' ? 'planned' : 'running',
      plan.total,
      plan.skipped,
      plan.total === 0 ? 1 : 0,
      mode === 'dry-run' ? 1 : 0,
      JSON.stringify({
        total: plan.total,
        planned: plan.planned,
        skipped: plan.skipped,
        priceMode: plan.priceMode
      }),
      now,
      now
    ]
  );
  return result.lastInsertRowid;
}

export function savePlanResults({ taskId, accountId, promotionId, promotionType, action, mode, plan }) {
  clearTaskSummaryCache();
  for (const row of plan.rows) {
    run(
      `INSERT INTO promo_action_results
        (task_id, account_id, promotion_id, promotion_type, item_id, action, mode, status, deal_price,
         top_deal_price, error_cn, error_raw, response_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)`,
      [
        taskId,
        String(accountId),
        promotionId,
        promotionType,
        row.item.item_id || '',
        row.action || action,
        mode,
        row.status,
        row.deal_price,
        row.status === 'skipped' ? row.reason : null,
        JSON.stringify({ reason: row.reason, item: row.item.raw }),
        nowIso()
      ]
    );
  }
}

export function saveExecutionResult({ taskId, accountId, promotionId, promotionType, itemId, action, mode, status, dealPrice, errorCn, errorRaw, response }) {
  clearTaskSummaryCache();
  run(
    `INSERT INTO promo_action_results
      (task_id, account_id, promotion_id, promotion_type, item_id, action, mode, status, deal_price,
       top_deal_price, error_cn, error_raw, response_json, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)`,
    [
      taskId,
      String(accountId),
      promotionId,
      promotionType,
      itemId,
      action,
      mode,
      status,
      dealPrice ?? null,
      errorCn ?? null,
      errorRaw ? String(errorRaw).slice(0, 1000) : null,
      response ? JSON.stringify(response) : null,
      nowIso()
    ]
  );
}

export function finishTask(taskId, counts, status = 'completed', completed = true) {
  clearTaskSummaryCache();
  run(
    `UPDATE promo_tasks SET status = ?, success_count = ?, failed_count = ?, skipped_count = ?,
      completed = ?, summary_json = ?, updated_at = ? WHERE id = ?`,
    [
      status,
      counts.success || 0,
      counts.failed || 0,
      counts.skipped || 0,
      completed ? 1 : 0,
      JSON.stringify(counts),
      nowIso(),
      taskId
    ]
  );
}

export function deleteTasks(taskIds = []) {
  const ids = [...new Set(taskIds.map((id) => Number(id)).filter((id) => Number.isInteger(id) && id > 0))];
  if (!ids.length) return { deleted: 0 };
  clearTaskSummaryCache();
  const placeholders = ids.map(() => '?').join(',');
  run(`DELETE FROM promo_action_results WHERE task_id IN (${placeholders})`, ids);
  const result = run(`DELETE FROM promo_tasks WHERE id IN (${placeholders})`, ids);
  return { deleted: result.changes || 0 };
}

export function listResults(limit = 300) {
  return all(
    `SELECT r.*, t.created_at AS task_created_at
     FROM promo_action_results r
     JOIN promo_tasks t ON t.id = r.task_id
     ORDER BY r.id DESC LIMIT ?`,
    [Number(limit)]
  );
}

export function listTaskSummaries(limit = 300, options = {}) {
  const settings = readSettings();
  const requested = Math.max(Number(limit) || 20, 1);
  const includeDetails = options.includeDetails !== false;
  const cacheKey = includeDetails ? '' : `main:${requested}:${TASK_SUMMARY_CACHE_VERSION}`;
  const stamp = cacheKey ? taskSummaryStamp() : null;
  if (cacheKey) {
    const cached = taskSummaryCache.get(cacheKey);
    if (cached
      && Date.now() - cached.createdAt <= TASK_SUMMARY_CACHE_MS
      && sameTaskSummaryStamp(cached.stamp, stamp)) {
      return cloneJson(cached.rows);
    }
    const persistent = loadPersistentTaskSummaryCache(cacheKey, stamp);
    if (persistent) {
      taskSummaryCache.set(cacheKey, { createdAt: Date.now(), stamp, rows: cloneJson(persistent) });
      return cloneJson(persistent);
    }
  }
  let fetchLimit = Math.max(requested * 24, 480);
  let summaries = [];
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const rows = fetchTaskSummaryRows(fetchLimit, settings);
    summaries = buildLegacyTaskSummaries(rows, requested, { includeDetails });
    if (summaries.length >= requested || rows.length < fetchLimit) {
      if (cacheKey) {
        taskSummaryCache.set(cacheKey, { createdAt: Date.now(), stamp, rows: cloneJson(summaries) });
        savePersistentTaskSummaryCache(cacheKey, stamp, summaries);
      }
      return summaries;
    }
    fetchLimit *= 2;
  }
  if (cacheKey) {
    taskSummaryCache.set(cacheKey, { createdAt: Date.now(), stamp, rows: cloneJson(summaries) });
    savePersistentTaskSummaryCache(cacheKey, stamp, summaries);
  }
  return summaries;
}

function fetchTaskSummaryRows(fetchLimit, settings) {
  return all(
    `SELECT
       t.*,
       o.display_name AS account_display_name,
       p.name AS promotion_name,
       p.site_id AS site_id,
       p.logistic_type AS logistic_type,
       COALESCE(json_extract(t.summary_json, '$.planned'), 0) AS planned_count,
       COALESCE(json_extract(t.summary_json, '$.blocked'), 0) AS blocked_count,
       COALESCE(json_extract(t.summary_json, '$.promotions_total'), 0) AS promotions_total
     FROM promo_tasks t
     LEFT JOIN promo_campaigns p
       ON p.account_id = t.account_id
      AND p.promotion_id = t.promotion_id
      AND p.promotion_type = t.promotion_type
     LEFT JOIN oauth_tokens o
       ON o.account_id = t.account_id
     ORDER BY t.id DESC LIMIT ?`,
    [fetchLimit]
  ).map((row) => ({
    ...row,
    store_name: storeDisplayName(row.account_id, row.account_display_name, settings.storeAliases),
    site_name: siteDisplayName(row.site_id)
  }));
}

export function buildLegacyTaskSummaries(rows = [], limit = 300, options = {}) {
  const includeDetails = options.includeDetails !== false;
  const ordered = [...rows].sort((a, b) => Number(b.id || 0) - Number(a.id || 0));
  const batchRows = ordered.filter(isBatchTaskRow);
  if (!batchRows.length) return ordered.slice(0, limit);

  const nonBatchRows = ordered.filter((row) => !isBatchTaskRow(row));
  const windows = batchRows
    .map((batch) => buildBatchWindow(batch, batchRows, nonBatchRows))
    .sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs);
  const groups = [];
  for (const window of windows) {
    const last = groups[groups.length - 1];
    if (last && canMergeBatchWindows(last, window)) {
      last.windows.push(window);
      last.startMs = Math.min(last.startMs, window.startMs);
      last.endMs = Math.max(last.endMs, window.endMs);
      continue;
    }
    groups.push({ windows: [window], startMs: window.startMs, endMs: window.endMs });
  }
  attachOrphanDetailsToBatchGroups(groups, nonBatchRows);

  const coveredTaskIds = new Set();
  const summaryRows = groups.map((group) => {
    const row = buildBatchSummaryRow(group, { includeDetails });
    for (const id of row.task_ids || []) coveredTaskIds.add(Number(id));
    return row;
  });
  const uncovered = nonBatchRows.filter((row) => !coveredTaskIds.has(Number(row.id || 0)));
  const outputRows = applyLatestEnrollLiveVerification([...summaryRows, ...uncovered]);
  return outputRows
    .sort((a, b) => dateMs(b.created_at) - dateMs(a.created_at) || Number(b.id || 0) - Number(a.id || 0))
    .slice(0, limit)
    .map(decorateTaskSummaryRow);
}

export function listTaskDetails(taskIds = []) {
  const ids = [...new Set((Array.isArray(taskIds) ? taskIds : [])
    .map((id) => Number(id))
    .filter((id) => Number.isInteger(id) && id > 0))];
  if (!ids.length) return [];
  const settings = readSettings();
  const placeholders = ids.map(() => '?').join(',');
  return all(
    `SELECT
       t.*,
       o.display_name AS account_display_name,
       p.name AS promotion_name,
       p.site_id AS site_id,
       p.logistic_type AS logistic_type
     FROM promo_tasks t
     LEFT JOIN promo_campaigns p
       ON p.account_id = t.account_id
      AND p.promotion_id = t.promotion_id
      AND p.promotion_type = t.promotion_type
     LEFT JOIN oauth_tokens o
       ON o.account_id = t.account_id
     WHERE t.id IN (${placeholders})
     ORDER BY t.id ASC`,
    ids
  ).map((row) => taskDetail({
    ...row,
    store_name: storeDisplayName(row.account_id, row.account_display_name, settings.storeAliases),
    site_name: siteDisplayName(row.site_id)
  }));
}

function isBatchTaskRow(row) {
  return String(row?.promotion_id || '').toUpperCase() === '__BATCH__'
    || String(row?.promotion_type || '').toUpperCase() === 'BATCH';
}

function buildBatchWindow(batch, allBatchRows, allDetailRows) {
  const batchMs = dateMs(batch.created_at);
  const batchDay = dayKey(batchMs);
  const previousBatchMs = allBatchRows
    .filter((row) => row !== batch
      && String(row.account_id || '') === String(batch.account_id || '')
      && String(row.action || '') === String(batch.action || '')
      && String(row.mode || '') === String(batch.mode || '')
      && dateMs(row.created_at) < batchMs)
    .map((row) => dateMs(row.created_at))
    .sort((a, b) => b - a)[0] || 0;
  const details = allDetailRows.filter((row) => {
    const rowMs = dateMs(row.created_at);
    return String(row.account_id || '') === String(batch.account_id || '')
      && String(row.action || '') === String(batch.action || '')
      && String(row.mode || '') === String(batch.mode || '')
      && dayKey(rowMs) === batchDay
      && rowMs <= batchMs
      && rowMs > previousBatchMs;
  });
  const startMs = Math.min(batchMs, ...details.map((row) => dateMs(row.created_at)).filter(Boolean));
  return { batch, details, startMs: Number.isFinite(startMs) ? startMs : batchMs, endMs: batchMs };
}

function canMergeBatchWindows(left, right) {
  const leftBatch = left.windows[0]?.batch || {};
  const rightBatch = right.batch || {};
  if (String(leftBatch.action || '') !== String(rightBatch.action || '')) return false;
  if (String(leftBatch.mode || '') !== String(rightBatch.mode || '')) return false;
  if (dayKey(left.endMs) !== dayKey(right.endMs)) return false;
  return right.startMs <= left.endMs + 10 * 60 * 1000;
}

function buildBatchSummaryRow(group, options = {}) {
  const includeDetails = options.includeDetails !== false;
  const windows = group.windows;
  const batchRows = windows.map((window) => window.batch);
  const orphanDetails = group.orphanDetails || [];
  const coverageOnlyDetails = group.coverageOnlyDetails || [];
  const details = [...windows.flatMap((window) => window.details), ...orphanDetails];
  const first = batchRows[0] || details[0] || {};
  const primaryMode = String(first.mode || '');
  const primaryDetails = details.filter((row) => String(row.mode || '') === primaryMode);
  const summaryDetails = primaryDetails.length ? primaryDetails : details;
  const taskIds = [...new Set([...batchRows, ...details, ...coverageOnlyDetails].map((row) => Number(row.id || 0)).filter(Boolean))];
  const uniqueWriteSummary = summarizeUniqueActionResultsForTaskIds(summaryDetails.map((row) => row.id));
  const useUniqueWriteSummary = uniqueWriteSummary.hasRows && uniqueWriteSummary.total > 0;
  const failureReasons = useUniqueWriteSummary
    ? uniqueWriteSummary.failure_reasons
    : summarizeFailureReasonsForTaskIds(summaryDetails.map((row) => row.id));
  const skippedReasons = useUniqueWriteSummary
    ? uniqueWriteSummary.skipped_reasons
    : summarizeSkippedReasonsForTaskIds(summaryDetails.map((row) => row.id));
  const fallbackSkippedReasons = mergeSkippedReasons(batchRows.flatMap((row) => parseSummary(row.summary_json).skipped_reasons || []));
  const topSkippedReasons = skippedReasons.length ? skippedReasons : fallbackSkippedReasons;
  const fallbackReasons = mergeFailureReasons(batchRows.flatMap((row) => parseSummary(row.summary_json).failure_reasons || []));
  const topReasons = failureReasons.length ? failureReasons : fallbackReasons;
  const primaryOrphanDetails = orphanDetails.filter((row) => String(row.mode || '') === primaryMode);
  const rawSuccess = sum(batchRows, 'success_count') + sum(primaryOrphanDetails, 'success_count');
  const rawFailed = sum(batchRows, 'failed_count') + sum(primaryOrphanDetails, 'failed_count');
  const rawSkipped = sum(batchRows, 'skipped_count') + sum(primaryOrphanDetails, 'skipped_count');
  const rawTotal = sum(batchRows, 'total_count') + sum(primaryOrphanDetails, 'total_count');
  const countedSummary = useUniqueWriteSummary ? {
    success: uniqueWriteSummary.success,
    failed: uniqueWriteSummary.failed,
    skipped: uniqueWriteSummary.skipped,
    planned: uniqueWriteSummary.total,
    total: uniqueWriteSummary.total
  } : {
    success: rawSuccess,
    failed: rawFailed,
    skipped: rawSkipped,
    planned: sumSummary(batchRows, 'planned') + sum(primaryOrphanDetails, 'success_count'),
    total: rawSuccess + rawFailed + rawSkipped
  };
  const processedTotal = countedSummary.success + countedSummary.failed + countedSummary.skipped;
  const candidatePoolTotal = Math.max(rawTotal, countedSummary.total, processedTotal);
  const summary = {
    ...countedSummary,
    total: processedTotal,
    processed_total: processedTotal,
    candidate_pool_total: candidatePoolTotal,
    api_success_count: countedSummary.success,
    enrolled_count: String(first.action || '') === 'enroll' ? countedSummary.success : null,
    main_quantity_type: String(first.action || '') === 'enroll' ? '已报名商品数' : '实际处理数',
    main_quantity_note: String(first.action || '') === 'enroll'
      ? '主表商品数按已报名/上架成功数显示；候选池、处理数、失败和跳过在详情中查看。'
      : '主表商品数按实际处理结论合计显示。',
    blocked: sumSummary(batchRows, 'blocked'),
    promotions_total: sumSummary(batchRows, 'promotions_total') + primaryOrphanDetails.length || summaryDetails.length,
    failure_reasons: topReasons,
    skipped_reasons: topSkippedReasons,
    seller_activity_text: discountText(summaryDetails.filter(isSellerCampaignTask).map((row) => row.discount_percent)),
    official_activity_text: discountText(summaryDetails.filter((row) => !isSellerCampaignTask(row)).map((row) => row.discount_percent))
  };
  const stores = [...new Set([...batchRows, ...details].map((row) => row.store_name).filter(Boolean))];
  const sites = [...new Set(details.map((row) => row.site_name).filter(Boolean))];
  return {
    ...first,
    id: Math.max(...taskIds),
    task_ids: taskIds,
    promotion_id: '__BATCH__',
    promotion_type: 'BATCH',
    promotion_name: null,
    site_id: null,
    site_name: '',
    store_name: scopeText(stores, '多个店铺'),
    action: first.action,
    mode: first.mode,
    status: deriveBatchStatus(batchRows),
    total_count: String(first.action || '') === 'enroll' ? summary.enrolled_count : summary.total,
    success_count: String(first.action || '') === 'enroll' ? summary.enrolled_count : summary.success,
    failed_count: summary.failed,
    skipped_count: summary.skipped,
    empty_count: sum(batchRows, 'empty_count'),
    completed: batchRows.every((row) => Number(row.completed || 0) === 1) ? 1 : 0,
    summary_json: JSON.stringify(summary),
    short_failure_reason: shortFailureReason(topReasons, summary.skipped, summary.blocked, summary.failed, topSkippedReasons),
    full_failure_reasons: fullFailureReasonRows(topReasons),
    planned_count: summary.planned,
    blocked_count: summary.blocked,
    promotions_total: summary.promotions_total,
    seller_activity_text: summary.seller_activity_text,
    official_activity_text: summary.official_activity_text,
    detail_count: details.length,
    ...(includeDetails ? { details: details.map(taskDetail) } : {})
  };
}

function applyLatestEnrollLiveVerification(rows) {
  const verification = loadLatestEnrollLiveVerification();
  if (!verification || verification.enrolled_count <= 0) return rows;
  const latestEnroll = rows
    .filter((row) => String(row.action || '') === 'enroll' && String(row.mode || '') === 'real')
    .sort((a, b) => dateMs(b.created_at) - dateMs(a.created_at) || Number(b.id || 0) - Number(a.id || 0))[0];
  if (!latestEnroll) return rows;
  const summary = parseSummary(latestEnroll.summary_json);
  const apiSuccess = Number(latestEnroll.success_count || summary.api_success_count || summary.success || 0);
  latestEnroll.total_count = verification.enrolled_count;
  latestEnroll.success_count = verification.enrolled_count;
  latestEnroll.summary_json = JSON.stringify({
    ...summary,
    api_success_count: apiSuccess,
    enrolled_count: verification.enrolled_count,
    live_verified_enrolled_count: verification.enrolled_count,
    live_started_pending_before: verification.before_count,
    live_started_pending_after: verification.after_count,
    live_verification_source: verification.source,
    live_verification_note: `主表商品数使用 live started+pending 增量 ${verification.enrolled_count}；接口成功数 ${apiSuccess}。`,
    main_quantity_type: '已报名商品数',
    main_quantity_note: '主表商品数按 live 回查确认的已报名/上架成功数显示；候选池、处理数、失败和跳过在详情中查看。'
  });
  return rows;
}

function loadLatestEnrollLiveVerification() {
  try {
    const refreshPath = path.join(DATA_DIR, 'tmp-live-enroll-refresh-summary.json');
    const postPath = path.join(DATA_DIR, 'tmp-live-enroll-post-summary.json');
    if (!fs.existsSync(refreshPath) || !fs.existsSync(postPath)) return null;
    const before = sumStartedPending(readJsonFile(refreshPath));
    const after = sumStartedPending(readJsonFile(postPath));
    const enrolled = after - before;
    if (!Number.isFinite(enrolled) || enrolled <= 0) return null;
    return {
      before_count: before,
      after_count: after,
      enrolled_count: enrolled,
      source: 'tmp-live-enroll-refresh-summary.json -> tmp-live-enroll-post-summary.json'
    };
  } catch {
    return null;
  }
}

function readJsonFile(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8').replace(/^\uFEFF/, ''));
}

function sumStartedPending(rows) {
  if (!Array.isArray(rows)) return 0;
  return rows
    .filter((row) => ['started', 'pending'].includes(String(row.status || '')))
    .reduce((total, row) => total + Number(row.saved_count || 0), 0);
}

function decorateTaskSummaryRow(row) {
  const summary = parseSummary(row.summary_json);
  const reasons = mergeFailureReasons(summary.failure_reasons || []);
  const skippedReasons = summary.skipped_reasons || summarizeSkippedReasonsForTaskIds([row.id]);
  return {
    ...row,
    short_failure_reason: row.short_failure_reason || shortFailureReason(
      reasons,
      Number(row.skipped_count || summary.skipped || 0),
      Number(row.blocked_count || summary.blocked || 0),
      Number(row.failed_count || summary.failed || 0),
      skippedReasons
    ),
    full_failure_reasons: row.full_failure_reasons || fullFailureReasonRows(reasons)
  };
}

function attachOrphanDetailsToBatchGroups(groups, nonBatchRows) {
  const assigned = new Set(groups.flatMap((group) => group.windows.flatMap((window) => window.details.map((row) => Number(row.id || 0)))));
  for (const row of nonBatchRows) {
    const rowId = Number(row.id || 0);
    if (!rowId || assigned.has(rowId)) continue;
    const rowMs = dateMs(row.created_at);
    const group = groups.find((candidate) => {
      const batch = candidate.windows[0]?.batch || {};
      return String(batch.action || '') === String(row.action || '')
        && canAttachDetailToBatch(batch, row)
        && dayKey(rowMs) === dayKey(candidate.endMs)
        && rowMs >= candidate.startMs - 10 * 60 * 1000
        && rowMs <= candidate.endMs + 10 * 60 * 1000;
    });
    if (!group) continue;
    const batch = group.windows[0]?.batch || {};
    const coverageOnly = String(batch.mode || '') === 'real' && String(row.mode || '') === 'dry-run';
    const bucket = coverageOnly ? 'coverageOnlyDetails' : 'orphanDetails';
    group[bucket] ||= [];
    group[bucket].push(row);
    group.startMs = Math.min(group.startMs, rowMs);
    group.endMs = Math.max(group.endMs, dateMs(row.updated_at) || rowMs);
    assigned.add(rowId);
  }
}

function canAttachDetailToBatch(batch, row) {
  const batchMode = String(batch.mode || '');
  const rowMode = String(row.mode || '');
  return batchMode === rowMode || (batchMode === 'real' && rowMode === 'dry-run');
}

function taskDetail(row) {
  const summary = parseSummary(row.summary_json);
  const success = Number(summary.success ?? row.success_count ?? 0);
  const failed = Number(summary.failed ?? row.failed_count ?? 0);
  const skipped = Number(summary.skipped ?? row.skipped_count ?? 0);
  const processedTotal = Number(success || 0) + Number(failed || 0) + Number(skipped || 0);
  const detailTotal = String(row.action || '') === 'enroll'
    ? success
    : (processedTotal > 0 ? processedTotal : Number(summary.total ?? row.total_count ?? 0));
  return {
    id: row.id,
    account_id: row.account_id,
    store_name: row.store_name,
    site_id: row.site_id,
    site_name: row.site_name,
    promotion_id: row.promotion_id,
    promotion_type: row.promotion_type,
    promotion_name: row.promotion_name,
    action: row.action,
    mode: row.mode,
    total_count: detailTotal,
    success_count: success,
    failed_count: failed,
    skipped_count: skipped,
    status: row.status,
    summary_json: row.summary_json
  };
}

function summarizeFailureReasonsForTaskIds(taskIds = [], limit = 3) {
  const ids = [...new Set(taskIds.map((id) => Number(id)).filter((id) => Number.isInteger(id) && id > 0))];
  if (!ids.length) return [];
  const placeholders = ids.map(() => '?').join(',');
  const rows = all(
    `SELECT error_cn, error_raw, response_json, COUNT(*) AS count
     FROM promo_action_results
     WHERE task_id IN (${placeholders}) AND status = 'failed'
     GROUP BY error_cn, error_raw, response_json
     ORDER BY count DESC`,
    ids
  );
  return mergeFailureReasons(rows.map((row) => ({ ...classifyFailureReason(row.error_raw || row.response_json || row.error_cn), count: Number(row.count || 0) })), limit);
}

function summarizeSkippedReasonsForTaskIds(taskIds = [], limit = 2) {
  const ids = [...new Set(taskIds.map((id) => Number(id)).filter((id) => Number.isInteger(id) && id > 0))];
  if (!ids.length) return [];
  const placeholders = ids.map(() => '?').join(',');
  const rows = all(
    `SELECT error_cn, error_raw, response_json, COUNT(*) AS count
     FROM promo_action_results
     WHERE task_id IN (${placeholders}) AND status = 'skipped'
     GROUP BY error_cn, error_raw, response_json
     ORDER BY count DESC`,
    ids
  );
  return mergeSkippedReasons(rows.map((row) => ({ ...classifySkippedReason(row.error_raw || row.response_json || row.error_cn), count: Number(row.count || 0) })), limit);
}

function summarizeUniqueActionResultsForTaskIds(taskIds = []) {
  const ids = [...new Set(taskIds.map((id) => Number(id)).filter((id) => Number.isInteger(id) && id > 0))];
  if (!ids.length) return emptyUniqueActionSummary(false);
  const placeholders = ids.map(() => '?').join(',');
  const finalItemSql = `
    WITH final_items AS (
      SELECT account_id, promotion_id, promotion_type, item_id, action, status, error_cn, error_raw, response_json
      FROM (
        SELECT account_id, promotion_id, promotion_type, item_id, action, status, error_cn, error_raw, response_json,
               ROW_NUMBER() OVER (
                 PARTITION BY account_id, promotion_id, promotion_type, action, item_id
                 ORDER BY id DESC
               ) AS rn
        FROM promo_action_results
        WHERE task_id IN (${placeholders})
          AND status IN ('success', 'failed', 'skipped')
          AND TRIM(COALESCE(item_id, '')) <> ''
      )
      WHERE rn = 1
    )`;
  const statusRows = all(
    `${finalItemSql}
     SELECT status, COUNT(*) AS count
     FROM final_items
     GROUP BY status`,
    ids
  );
  const reasonRows = all(
    `${finalItemSql}
     SELECT status, error_cn, error_raw, response_json, COUNT(*) AS count
     FROM final_items
     WHERE status IN ('failed', 'skipped')
     GROUP BY status, error_cn, error_raw, response_json`,
    ids
  );
  const nonItemRows = all(
    `SELECT status, error_cn, error_raw, response_json
     FROM promo_action_results
     WHERE task_id IN (${placeholders})
       AND status IN ('failed', 'skipped')
       AND TRIM(COALESCE(item_id, '')) = ''`,
    ids
  );
  const summary = emptyUniqueActionSummary(statusRows.length > 0 || nonItemRows.length > 0);
  for (const row of statusRows) {
    const count = Number(row.count || 0);
    summary.total += count;
    const status = String(row.status || '').toLowerCase();
    if (status === 'success') summary.success += count;
    else if (status === 'skipped') summary.skipped += count;
    else if (status === 'failed') summary.failed += count;
  }
  const failureReasons = [];
  const skippedReasons = [];
  for (const row of reasonRows) {
    const count = Number(row.count || 0);
    if (String(row.status || '').toLowerCase() === 'skipped') {
      skippedReasons.push({ ...classifySkippedReason(row.error_raw || row.response_json || row.error_cn), count });
    } else {
      failureReasons.push({ ...classifyFailureReason(row.error_raw || row.response_json || row.error_cn), count });
    }
  }
  for (const row of nonItemRows) {
    const count = nonItemResultCount(row);
    summary.total += count;
    if (String(row.status || '').toLowerCase() === 'skipped') {
      summary.skipped += count;
      skippedReasons.push({ ...classifySkippedReason(row.error_raw || row.response_json || row.error_cn), count });
    } else {
      summary.failed += count;
      failureReasons.push({ ...classifyFailureReason(row.error_raw || row.response_json || row.error_cn), count });
    }
  }
  summary.failure_reasons = mergeFailureReasons(failureReasons);
  summary.skipped_reasons = mergeSkippedReasons(skippedReasons);
  return summary;
}

export function summarizeUniqueFinalActionResults(rows = []) {
  const finalByItem = new Map();
  const summary = emptyUniqueActionSummary(false);
  const failureReasons = [];
  const skippedReasons = [];
  let hasRows = false;
  for (const row of rows || []) {
    hasRows = true;
    if (!isWriteResultStatus(row?.status)) continue;
    if (!String(row?.item_id || '').trim()) {
      const count = nonItemResultCount(row);
      const status = String(row.status || '').toLowerCase();
      summary.total += count;
      if (status === 'skipped') {
        summary.skipped += count;
        skippedReasons.push({ ...classifySkippedReason(row.error_raw || row.response_json || row.error_cn), count });
      } else if (status === 'failed') {
        summary.failed += count;
        failureReasons.push({ ...classifyFailureReason(row.error_raw || row.response_json || row.error_cn), count });
      }
      continue;
    }
    const key = [
      row.account_id || '',
      row.promotion_id || '',
      row.promotion_type || '',
      row.action || '',
      row.item_id || ''
    ].join('|');
    finalByItem.set(key, row);
  }
  summary.hasRows = hasRows;
  summary.total += finalByItem.size;
  for (const row of finalByItem.values()) {
    const status = String(row.status || '').toLowerCase();
    if (status === 'success') {
      summary.success += 1;
    } else if (status === 'skipped') {
      summary.skipped += 1;
      skippedReasons.push({ ...classifySkippedReason(row.error_raw || row.response_json || row.error_cn), count: 1 });
    } else {
      summary.failed += 1;
      failureReasons.push({ ...classifyFailureReason(row.error_raw || row.response_json || row.error_cn), count: 1 });
    }
  }
  summary.failure_reasons = mergeFailureReasons(failureReasons);
  summary.skipped_reasons = mergeSkippedReasons(skippedReasons);
  return summary;
}

function nonItemResultCount(row = {}) {
  const text = [row.error_cn, row.error_raw, row.response_json].filter(Boolean).join(' ');
  const patterns = [
    /平台还有\s*(\d+)\s*个候选未返回明细/,
    /还有\s*(\d+)\s*个候选/,
    /(\d+)\s*个候选未返回明细/
  ];
  for (const pattern of patterns) {
    const match = String(text || '').match(pattern);
    if (match) {
      const count = Number(match[1]);
      if (Number.isFinite(count) && count > 0) return count;
    }
  }
  return 1;
}

function emptyUniqueActionSummary(hasRows = false) {
  return {
    hasRows,
    total: 0,
    success: 0,
    failed: 0,
    skipped: 0,
    failure_reasons: [],
    skipped_reasons: []
  };
}

function isWriteResultStatus(status) {
  return ['success', 'failed', 'skipped'].includes(String(status || '').toLowerCase());
}

function mergeSkippedReasons(reasons = [], limit = 2) {
  const counts = new Map();
  for (const reason of reasons) {
    const classified = typeof reason === 'string' ? classifySkippedReason(reason) : { ...classifySkippedReason(reason), ...reason };
    const text = classified.reason || '其他跳过';
    const current = counts.get(text) || { reason: text, count: 0 };
    current.count += Number(classified.count || 1);
    counts.set(text, current);
  }
  return [...counts.values()].sort((a, b) => b.count - a.count).slice(0, limit);
}

function mergeFailureReasons(reasons = [], limit = 3) {
  const counts = new Map();
  for (const reason of reasons) {
    const classified = typeof reason === 'string' ? classifyFailureReason(reason) : { ...classifyFailureReason(reason), ...reason };
    const canonical = classifyFailureReason(classified.reason || reason);
    const text = canonical.reason || classified.reason || '其他失败';
    if (!text) continue;
    const current = counts.get(text) || {
      reason: text,
      count: 0,
      sent_to_api: classified.sent_to_api ?? canonical.sent_to_api ?? true,
      suggestion: classified.suggestion || canonical.suggestion || ''
    };
    current.count += Number(classified.count || 1);
    if (classified.sent_to_api === false) current.sent_to_api = false;
    if (!current.suggestion && (classified.suggestion || canonical.suggestion)) current.suggestion = classified.suggestion || canonical.suggestion;
    counts.set(text, current);
  }
  return [...counts.values()]
    .sort((a, b) => b.count - a.count)
    .slice(0, limit)
    .map((reason) => ({
      reason: reason.reason,
      count: reason.count,
      sent_to_api: reason.sent_to_api,
      suggestion: reason.suggestion
    }));
}

function shortFailureReason(reasons = [], skipped = 0, blocked = 0, failed = 0, skippedReasons = []) {
  const parts = [];
  for (const reason of reasons.slice(0, 3)) {
    const text = shortFailureReasonName(reason.reason);
    const count = Number(reason.count || 0);
    if (text) parts.push(count > 0 ? `${text}${count}` : text);
  }
  const skippedTotal = Number(skipped || 0);
  if (skippedTotal > 0) {
    const shownSkipped = [...(skippedReasons || [])].slice(0, 1);
    let remainingSkipped = skippedTotal;
    for (const reason of shownSkipped) {
      const count = Math.min(Number(reason.count || 0), remainingSkipped);
      if (count > 0) {
        parts.push(`${reason.reason}${count}`);
        remainingSkipped -= count;
      }
    }
    const other = Math.max(0, remainingSkipped);
    if (other > 0 || shownSkipped.length === 0) parts.push(`其他跳过${other || skippedTotal}`);
  }
  if (Number(blocked || 0) > 0) parts.push(`阻断${Number(blocked || 0)}`);
  if (!parts.length && Number(failed || 0) > 0) parts.push(`其他失败${Number(failed || 0)}`);
  return parts.join('，');
}

function fullFailureReasonRows(reasons = []) {
  return reasons.map((reason) => ({
    reason: fullFailureReasonName(reason.reason),
    count: Number(reason.count || 0),
    sent_to_api: reason.sent_to_api !== false,
    suggestion: reason.suggestion || ''
  }));
}

function shortFailureReasonName(reason = '') {
  const text = String(reason || '').trim();
  if (text === '授权中途失效') return '账号授权需刷新';
  if (text === '商品审核中') return '商品审核中';
  if (text === 'SMART未发送') return 'SMART未报名';
  if (text === 'LIGHTNING未发送') return 'LIGHTNING未报名';
  if (text === '请求参数不符合平台要求') return '参数错误';
  if (text === '平台限流') return '平台限流';
  if (text === '网络失败') return '网络失败';
  if (text === '未读取到可处理候选商品') return '无候选商品';
  if (text === '折扣价不被平台认可') return '价格不认可';
  if (text === '缺少活动报价ID') return '缺报价ID';
  if (text === '平台接口超时') return '超时';
  if (text === '候选明细不完整') return '候选不完整';
  if (text === '折扣比例不符合要求') return '折扣比例';
  return text.replace(/\s+/g, '').slice(0, 8);
}

function fullFailureReasonName(reason = '') {
  const text = String(reason || '').trim();
  if (text === 'SMART未发送') return 'SMART未参与批量报名';
  if (text === 'LIGHTNING未发送') return 'LIGHTNING未参与批量报名';
  return text;
}

function classifySkippedReason(reason) {
  const text = cleanFailureText(reason);
  const raw = text.toLowerCase();
  if (/当前活动价已等于目标价|已是目标价格|already matches target/.test(raw)) {
    return { reason: '已是目标价格' };
  }
  if (/高于最高允许价|低于最低允许价|超出平台范围|min_discounted_price|max_discounted_price|price range/.test(raw)) {
    return { reason: '活动价超出平台范围' };
  }
  if (/候选明细不完整|候选未返回明细/.test(raw)) {
    return { reason: '候选明细不完整' };
  }
  if (/无可处理|未读取到/.test(raw)) {
    return { reason: '无可处理商品' };
  }
  return { reason: '其他跳过' };
}

export function classifyFailureReason(reason) {
  const text = cleanFailureText(reason);
  const raw = text.toLowerCase();
  if (/invalid access token|unauthorized|\"status\":401|\b401\b/.test(raw)) {
    return { reason: '授权中途失效', sent_to_api: true, suggestion: '刷新授权后重跑失败商品' };
  }
  if (/under_review|item status is not allowed/.test(raw)) {
    return { reason: '商品审核中', sent_to_api: true, suggestion: '商品审核结束后再重跑' };
  }
  if (/smart 实验预览|smart.*不能批量真实放行|smart 未参与/.test(raw)) {
    return { reason: 'SMART未发送', sent_to_api: false, suggestion: '需先做 SMART 小样本验证或放开 SMART 批量策略' };
  }
  if (/lightning 官方 body|lightning.*不能批量/.test(raw)) {
    return { reason: 'LIGHTNING未发送', sent_to_api: false, suggestion: '需先做 LIGHTNING 小样本验证或放开批量策略' };
  }
  if (/timeout|请求超时|\"status\":504|\b504\b/.test(raw)) {
    return { reason: '平台接口超时', sent_to_api: true, suggestion: '降低并发或稍后重跑失败商品' };
  }
  if (/rate limit|rate_limited|too many requests|ratelimi|\b429\b/.test(raw)) {
    return { reason: '平台限流', sent_to_api: true, suggestion: '降低并发或稍后重跑失败商品' };
  }
  if (/fetch failed|fetchfai|network|socket|econnreset|etimedout|und_err|aborted/.test(raw)) {
    return { reason: '网络失败', sent_to_api: true, suggestion: '网络或平台连接失败，稍后重跑失败商品' };
  }
  if (/invalid_parameter|bad_request|请求参数不符合平台要求/.test(raw)) {
    return { reason: '请求参数不符合平台要求', sent_to_api: true, suggestion: '刷新活动商品后重跑，仍失败则检查活动报价参数' };
  }
  if (/credible|discounted price is not credible|折扣价不被平台认可/.test(raw)) {
    return { reason: '折扣价不被平台认可', sent_to_api: true, suggestion: '调整折扣或价格边界后重跑' };
  }
  if (/minimum_discount_percent|discount final price must be more than/.test(raw)) {
    return { reason: '折扣比例不符合要求', sent_to_api: true, suggestion: '提高折扣幅度或按平台要求调整活动价' };
  }
  if (/offer|活动报价/.test(raw)) {
    return { reason: '缺少活动报价ID', sent_to_api: true, suggestion: '刷新候选明细/活动报价后重跑' };
  }
  if (/candidate 商品未读取到|无可处理商品/.test(raw)) {
    return { reason: '未读取到可处理候选商品', sent_to_api: false, suggestion: '先刷新活动商品或检查筛选范围' };
  }
  if (/平台还有.*候选未返回明细|候选未返回明细|候选明细不完整/.test(raw)) {
    return { reason: '候选明细不完整', sent_to_api: false, suggestion: '等待平台返回明细或用人工导入/库存兜底' };
  }
  if (!text) return { reason: '其他失败', sent_to_api: true, suggestion: '查看详情后处理' };
  if (raw.includes('账号权限不足或应用权限不足')) {
    return { reason: '账号/应用权限不足', sent_to_api: true, suggestion: '核对账号、child caller 和 App 权限后重跑' };
  }
  if (text.startsWith('平台返回未分类错误：')) {
    return { reason: sanitizeDisplayFailure(text.replace(/^平台返回未分类错误："?/, '').replace(/"$/, '')) || '其他失败', sent_to_api: true, suggestion: '查看详情后按原始错误处理' };
  }
  return { reason: sanitizeDisplayFailure(text) || '其他失败', sent_to_api: true, suggestion: '查看详情后处理' };
}

function cleanFailureText(reason) {
  const input = typeof reason === 'string' ? reason : String(reason?.reason || reason?.message || reason?.error || '');
  const trimmed = input.trim();
  if (!trimmed) return '';
  const parsed = parseFailureJson(trimmed);
  if (!parsed) return trimmed;
  const body = parsed.body && typeof parsed.body === 'object' ? parsed.body : {};
  return [
    body.message,
    body.error,
    body.message_code,
    body.code,
    body.status,
    body.cause,
    parsed.message,
    parsed.error,
    parsed.message_code,
    parsed.code,
    parsed.status,
    parsed.cause
  ].filter((part) => part !== undefined && part !== null && String(part).trim()).map((part) => {
    if (Array.isArray(part) || typeof part === 'object') return JSON.stringify(part);
    return String(part);
  }).join(' ');
}

function parseFailureJson(text) {
  const candidates = [text];
  const firstBrace = text.indexOf('{');
  const lastBrace = text.lastIndexOf('}');
  if (firstBrace >= 0 && lastBrace > firstBrace) candidates.push(text.slice(firstBrace, lastBrace + 1));
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === 'object') return parsed;
    } catch {
      // keep trying narrower candidates
    }
  }
  return null;
}

function sanitizeDisplayFailure(text) {
  return text
    .replace(/[{}"]/g, '')
    .replace(/\bmessage\s*:\s*/ig, '')
    .replace(/\bMercado\s*/ig, '')
    .replace(/\btoken\b/ig, '授权')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 24);
}

function parseSummary(summaryJson) {
  if (!summaryJson) return {};
  try {
    return JSON.parse(summaryJson);
  } catch {
    return {};
  }
}

function isSellerCampaignTask(row) {
  return String(row?.promotion_type || '').toUpperCase() === 'SELLER_CAMPAIGN'
    || String(row?.promotion_id || '').toUpperCase().startsWith('C-');
}

function discountText(values = []) {
  const unique = [...new Set(values.map((value) => Number(value)).filter((value) => Number.isFinite(value) && value > 0))]
    .sort((a, b) => a - b);
  if (!unique.length) return '';
  if (unique.length === 1) return `${formatPercent(unique[0])}%`;
  return unique.map((value) => `${formatPercent(value)}%`).join('/');
}

function formatPercent(value) {
  return Number.isInteger(value) ? String(value) : String(value).replace(/\.0+$/, '');
}

function deriveBatchStatus(batchRows) {
  if (batchRows.some((row) => String(row.status || '') === 'running')) return 'running';
  if (batchRows.some((row) => String(row.status || '') === 'cancelled' || String(row.status || '') === 'canceled')) return 'cancelled';
  return batchRows.every((row) => Number(row.completed || 0) === 1 && Number(row.failed_count || 0) === 0) ? 'completed' : 'partial_or_failed';
}

function scopeText(names, multiLabel) {
  const clean = [...new Set(names.filter(Boolean))].sort();
  if (!clean.length) return '';
  return clean.length === 1 ? clean[0] : `${multiLabel}（${clean.length}个）`;
}

function sum(rows, field) {
  return rows.reduce((total, row) => total + Number(row?.[field] || 0), 0);
}

function sumSummary(rows, field) {
  return rows.reduce((total, row) => total + Number(parseSummary(row.summary_json)?.[field] || 0), 0);
}

function dateMs(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (value instanceof Date) {
    const ms = value.getTime();
    return Number.isFinite(ms) ? ms : 0;
  }
  const ms = Date.parse(value || '');
  return Number.isFinite(ms) ? ms : 0;
}

function dayKey(ms) {
  return ms ? new Date(ms).toISOString().slice(0, 10) : '';
}

function storeDisplayName(accountId, displayName, aliases = {}) {
  const alias = aliases?.[String(accountId || '')];
  if (alias && String(alias).trim()) return String(alias).trim();
  if (String(accountId || '') === '2651442567') return '湖北店';
  if (String(accountId || '') === '3332096437') return '湖南店';
  if (String(accountId || '') === '3408885754') return '广东店';
  const raw = String(displayName || '').trim();
  const upper = raw.toUpperCase();
  if (upper.includes('HUBEI') || raw.includes('湖北')) return '湖北店';
  if (upper.includes('HUNAN') || raw.includes('湖南')) return '湖南店';
  if (upper.includes('GUANGDONG') || upper.includes('GUANGZHOU') || upper.includes('GD') || raw.includes('广东')) return '广东店';
  return raw && raw !== String(accountId || '') ? raw : `账号 ${accountId || ''}`.trim();
}

function siteDisplayName(siteId) {
  const id = String(siteId || '').toUpperCase();
  return {
    MLB: '巴西站',
    MLM: '墨西哥站',
    MLA: '阿根廷站',
    MLC: '智利站',
    MCO: '哥伦比亚站',
    MPE: '秘鲁站',
    MEC: '厄瓜多尔站',
    MLU: '乌拉圭站',
    CBT: '跨境店'
  }[id] || (id ? `站点 ${id}` : '');
}

function redactToken(token) {
  const clone = { ...token };
  if (clone.access_token) clone.access_token = '[encrypted]';
  if (clone.refresh_token) clone.refresh_token = '[encrypted]';
  return clone;
}
