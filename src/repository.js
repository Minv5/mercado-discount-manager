import crypto from 'node:crypto';

import { all, get, nowIso, run, transaction } from './db.js';
import { decryptSecret, encryptSecret } from './security.js';
import { filterPromotions, normalizeItem, normalizePromotion, promotionKey, summarizeSites } from './planner.js';
import { readSettings } from './settings.js';
import { storeNameForAccount } from './storeNameDomain.js';
import { RESULT_CONTRACT_VERSION, summarizeResultContractRows } from './executionResultContract.js';

const TASK_SUMMARY_CANONICAL_LIMIT = 300;
const HISTORY_SUMMARY_SCHEMA_VERSION = 1;
const DEFAULT_OAUTH_STATE_TTL_MS = 15 * 60 * 1000;
const DEFAULT_OAUTH_CLAIM_LEASE_MS = 5 * 60 * 1000;
const DAILY_ITEM_SNAPSHOT_RETENTION_DAYS = 90;

function routeIdentityError(message = '多路由场景缺少完整店铺、子账号和站点身份。') {
  const error = new Error(message);
  error.code = 'ROUTE_IDENTITY_REQUIRED';
  error.status = 422;
  return error;
}

function routeFields(value = {}) {
  const child = value?.childUserId ?? value?.child_user_id;
  const site = value?.siteId ?? value?.site_id;
  return {
    childUserId: child == null ? '' : String(child).trim(),
    siteId: site == null ? '' : String(site).trim().toUpperCase(),
  };
}

function routeFromPromotion(promotion = {}, context = {}) {
  const contextRoute = routeFields(context);
  if (contextRoute.childUserId || contextRoute.siteId) return contextRoute;
  return routeFields(promotion);
}

function completeRoute(route = {}) {
  return Boolean(route.childUserId && route.siteId);
}

function distinctCompleteRoutes(rows = []) {
  const map = new Map();
  for (const row of rows) {
    const route = routeFields(row);
    if (!completeRoute(route)) continue;
    map.set(`${route.childUserId}|${route.siteId}`, route);
  }
  return [...map.values()];
}

function resolveRouteForIdentity({ accountId, promotionId, promotionType, route = null } = {}) {
  const requested = routeFields(route || {});
  if (requested.childUserId || requested.siteId) {
    if (!completeRoute(requested)) throw routeIdentityError();
    return requested;
  }
  const params = [String(accountId || ''), String(promotionId || ''), String(promotionType || '')];
  const candidates = [
    ...all(
      `SELECT child_user_id, site_id FROM promo_campaigns
       WHERE account_id = ? AND promotion_id = ? AND promotion_type = ?`,
      params,
    ),
    ...all(
      `SELECT child_user_id, site_id FROM promo_items
       WHERE account_id = ? AND promotion_id = ? AND promotion_type = ?`,
      params,
    ),
    ...all(
      `SELECT child_user_id, site_id FROM promo_item_fetch_states
       WHERE account_id = ? AND promotion_id = ? AND promotion_type = ?`,
      params,
    ),
  ];
  const routes = distinctCompleteRoutes(candidates);
  if (routes.length > 1) throw routeIdentityError();
  return routes[0] || { childUserId: '', siteId: '' };
}

function routeSql(route, startIndex = 0) {
  return {
    sql: 'child_user_id = ? AND site_id = ?',
    params: [route.childUserId, route.siteId],
    startIndex,
  };
}

function cloneJson(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
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

export function saveOAuthState(stateRecord) {
  run(
    `INSERT INTO oauth_states
      (state, client_id, client_secret_cipher, redirect_uri, auth_domain,
       code_verifier, code_challenge, created_at, processing_state,
       claim_token, claimed_at, claim_expires_at, consumed_at, last_error_code, attempt_count)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, NULL, NULL, 0)
     ON CONFLICT(state) DO UPDATE SET
       client_id = excluded.client_id,
       client_secret_cipher = excluded.client_secret_cipher,
       redirect_uri = excluded.redirect_uri,
       auth_domain = excluded.auth_domain,
       code_verifier = excluded.code_verifier,
       code_challenge = excluded.code_challenge,
       created_at = excluded.created_at,
       processing_state = 'pending',
       claim_token = NULL,
       claimed_at = NULL,
       claim_expires_at = NULL,
       consumed_at = NULL,
       last_error_code = NULL,
       attempt_count = 0`,
    [
      stateRecord.state,
      stateRecord.clientId,
      encryptSecret(stateRecord.clientSecret),
      stateRecord.redirectUri,
      stateRecord.authDomain,
      stateRecord.codeVerifier,
      stateRecord.codeChallenge,
      stateRecord.createdAt || nowIso()
    ]
  );
}

function oauthStateRecord(row) {
  if (!row) return null;
  return {
    state: row.state,
    clientId: row.client_id,
    clientSecret: decryptSecret(row.client_secret_cipher),
    redirectUri: row.redirect_uri,
    authDomain: row.auth_domain,
    codeVerifier: row.code_verifier,
    createdAt: row.created_at,
  };
}

function normalizedDate(value = new Date()) {
  const result = value instanceof Date ? new Date(value.getTime()) : new Date(value);
  if (!Number.isFinite(result.getTime())) throw new Error('时间参数无效。');
  return result;
}

function oauthStateLifecycle(row, { now, maxAgeMs }) {
  if (!row) return 'missing';
  if (String(row.processing_state || '') === 'consumed') return 'consumed';
  if (String(row.processing_state || '') === 'expired') return 'expired';
  const createdAt = Date.parse(String(row.created_at || ''));
  if (!Number.isFinite(createdAt)
    || now.getTime() - createdAt > Math.max(1, Number(maxAgeMs) || DEFAULT_OAUTH_STATE_TTL_MS)) {
    return 'expired';
  }
  return String(row.processing_state || 'pending');
}

export function peekOAuthState(state, {
  maxAgeMs = DEFAULT_OAUTH_STATE_TTL_MS,
  now = new Date(),
} = {}) {
  const nowDate = normalizedDate(now);
  const row = get('SELECT * FROM oauth_states WHERE state = ?', [String(state || '')]);
  const status = oauthStateLifecycle(row, { now: nowDate, maxAgeMs });
  return {
    status,
    state: String(state || ''),
    record: ['pending', 'processing'].includes(status) ? oauthStateRecord(row) : null,
    claim_expires_at: row?.claim_expires_at || null,
  };
}

export function claimOAuthState(state, {
  maxAgeMs = DEFAULT_OAUTH_STATE_TTL_MS,
  leaseMs = DEFAULT_OAUTH_CLAIM_LEASE_MS,
  now = new Date(),
} = {}) {
  const normalizedState = String(state || '');
  const nowDate = normalizedDate(now);
  const claimedAt = nowDate.toISOString();
  const claimExpiresAt = new Date(
    nowDate.getTime() + Math.max(1_000, Number(leaseMs) || DEFAULT_OAUTH_CLAIM_LEASE_MS),
  ).toISOString();
  const claimToken = crypto.randomUUID();
  return transaction((database) => {
    const row = database.prepare('SELECT * FROM oauth_states WHERE state = ?').get(normalizedState);
    const lifecycle = oauthStateLifecycle(row, { now: nowDate, maxAgeMs });
    if (lifecycle === 'missing') {
      return { status: 'missing', state: normalizedState, claim_token: null, record: null };
    }
    if (lifecycle === 'consumed') {
      return { status: 'consumed', state: normalizedState, claim_token: null, record: null };
    }
    if (lifecycle === 'expired') {
      database.prepare(
        `UPDATE oauth_states
         SET processing_state = 'expired',
             claim_token = NULL,
             claimed_at = NULL,
             claim_expires_at = NULL
         WHERE state = ? AND processing_state <> 'consumed'`,
      ).run(normalizedState);
      return { status: 'expired', state: normalizedState, claim_token: null, record: null };
    }
    if (lifecycle === 'processing'
      && Date.parse(String(row.claim_expires_at || '')) > nowDate.getTime()) {
      return {
        status: 'in_progress',
        state: normalizedState,
        claim_token: null,
        record: null,
        claim_expires_at: row.claim_expires_at,
      };
    }
    const result = database.prepare(
      `UPDATE oauth_states
       SET processing_state = 'processing',
           claim_token = ?,
           claimed_at = ?,
           claim_expires_at = ?,
           consumed_at = NULL,
           last_error_code = NULL,
           attempt_count = COALESCE(attempt_count, 0) + 1
       WHERE state = ? AND processing_state <> 'consumed'`,
    ).run(claimToken, claimedAt, claimExpiresAt, normalizedState);
    if (Number(result.changes || 0) !== 1) {
      return { status: 'consumed', state: normalizedState, claim_token: null, record: null };
    }
    return {
      status: 'claimed',
      state: normalizedState,
      claim_token: claimToken,
      claim_expires_at: claimExpiresAt,
      record: oauthStateRecord(row),
    };
  });
}

export function renewOAuthStateClaim({
  state,
  claimToken,
  leaseMs = DEFAULT_OAUTH_CLAIM_LEASE_MS,
  maxAgeMs = DEFAULT_OAUTH_STATE_TTL_MS,
  now = new Date(),
} = {}) {
  const normalizedState = String(state || '');
  const normalizedClaimToken = String(claimToken || '');
  const nowDate = normalizedDate(now);
  const nowText = nowDate.toISOString();
  const claimExpiresAt = new Date(
    nowDate.getTime() + Math.max(1_000, Number(leaseMs) || DEFAULT_OAUTH_CLAIM_LEASE_MS),
  ).toISOString();
  return transaction((database) => {
    const row = database.prepare('SELECT * FROM oauth_states WHERE state = ?').get(normalizedState);
    const lifecycle = oauthStateLifecycle(row, { now: nowDate, maxAgeMs });
    if (lifecycle === 'missing') {
      return { status: 'missing', state: normalizedState, claim_token: null };
    }
    if (lifecycle === 'consumed') {
      return { status: 'consumed', state: normalizedState, claim_token: null };
    }
    if (lifecycle === 'expired') {
      database.prepare(
        `UPDATE oauth_states
         SET processing_state = 'expired',
             claim_token = NULL,
             claimed_at = NULL,
             claim_expires_at = NULL
         WHERE state = ? AND processing_state <> 'consumed'`,
      ).run(normalizedState);
      return { status: 'expired', state: normalizedState, claim_token: null };
    }
    if (String(row.processing_state || '') !== 'processing'
      || String(row.claim_token || '') !== normalizedClaimToken) {
      return { status: 'claim_mismatch', state: normalizedState, claim_token: null };
    }
    if (Date.parse(String(row.claim_expires_at || '')) <= nowDate.getTime()) {
      return { status: 'claim_expired', state: normalizedState, claim_token: null };
    }
    const renewed = database.prepare(
      `UPDATE oauth_states
       SET claim_expires_at = ?
       WHERE state = ?
         AND processing_state = 'processing'
         AND claim_token = ?
         AND claim_expires_at > ?`,
    ).run(claimExpiresAt, normalizedState, normalizedClaimToken, nowText);
    if (Number(renewed.changes || 0) !== 1) {
      return { status: 'claim_mismatch', state: normalizedState, claim_token: null };
    }
    return {
      status: 'renewed',
      state: normalizedState,
      claim_token: normalizedClaimToken,
      claim_expires_at: claimExpiresAt,
    };
  });
}

export function releaseOAuthStateClaim({
  state,
  claimToken,
  errorCode = 'TOKEN_EXCHANGE_FAILED',
  now = new Date(),
} = {}) {
  const releasedAt = normalizedDate(now).toISOString();
  return transaction((database) => {
    const result = database.prepare(
      `UPDATE oauth_states
       SET processing_state = 'pending',
           claim_token = NULL,
           claimed_at = NULL,
           claim_expires_at = NULL,
           last_error_code = ?
       WHERE state = ? AND processing_state = 'processing' AND claim_token = ?`,
    ).run(
      String(errorCode || 'TOKEN_EXCHANGE_FAILED').replace(/[^A-Z0-9_.-]+/gi, '_').slice(0, 100),
      String(state || ''),
      String(claimToken || ''),
    );
    return {
      status: Number(result.changes || 0) === 1 ? 'released' : 'claim_mismatch',
      state: String(state || ''),
      claim_token: null,
      released_at: releasedAt,
    };
  });
}

export function consumeClaimedOAuthState({
  state,
  claimToken,
  now = new Date(),
} = {}) {
  const consumedAt = normalizedDate(now).toISOString();
  return transaction((database) => {
    const row = database.prepare('SELECT * FROM oauth_states WHERE state = ?').get(String(state || ''));
    if (!row) return { status: 'missing', state: String(state || ''), record: null };
    if (String(row.processing_state || '') === 'consumed') {
      return { status: 'consumed', state: String(state || ''), record: null };
    }
    const result = database.prepare(
      `UPDATE oauth_states
       SET processing_state = 'consumed',
           claim_token = NULL,
           claimed_at = NULL,
           claim_expires_at = NULL,
           consumed_at = ?,
           last_error_code = NULL
       WHERE state = ? AND processing_state = 'processing' AND claim_token = ?`,
    ).run(consumedAt, String(state || ''), String(claimToken || ''));
    if (Number(result.changes || 0) !== 1) {
      return { status: 'claim_mismatch', state: String(state || ''), record: null };
    }
    return { status: 'consumed', state: String(state || ''), record: oauthStateRecord(row) };
  });
}

/** @deprecated Server integration uses atomic token persistence and state consumption. */
export function consumeOAuthState(state) {
  const claim = claimOAuthState(state);
  if (claim.status !== 'claimed') return null;
  const consumed = consumeClaimedOAuthState({ state, claimToken: claim.claim_token });
  return consumed.status === 'consumed' ? consumed.record : null;
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

function normalizedBusinessDate(value) {
  const text = String(value || '').trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) throw new Error('业务日期必须使用 YYYY-MM-DD。');
  const parsed = new Date(`${text}T00:00:00.000Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== text) {
    throw new Error('业务日期无效。');
  }
  return text;
}

function previousBusinessDate(value) {
  const current = new Date(`${normalizedBusinessDate(value)}T00:00:00.000Z`);
  current.setUTCDate(current.getUTCDate() - 1);
  return current.toISOString().slice(0, 10);
}

function normalizedDailySnapshotIdentity(input = {}) {
  const identity = {
    businessDate: normalizedBusinessDate(input.businessDate ?? input.business_date),
    accountId: String(input.accountId ?? input.account_id ?? '').trim(),
    childUserId: String(input.childUserId ?? input.child_user_id ?? '').trim(),
    siteId: String(input.siteId ?? input.site_id ?? '').trim().toUpperCase(),
  };
  if (!identity.accountId || !identity.childUserId || !identity.siteId) {
    throw new Error('每日商品身份快照必须包含账号、子账号和站点。');
  }
  return identity;
}

function normalizedItemIds(itemIds = []) {
  if (!Array.isArray(itemIds)) throw new Error('商品身份集合必须是数组。');
  return [...new Set(itemIds.map((itemId) => String(itemId || '').trim()).filter(Boolean))].sort();
}

function publicDailyItemIdentitySnapshot(row) {
  if (!row) return null;
  let itemIds = [];
  let parseError = false;
  try {
    const parsed = JSON.parse(String(row.item_ids_json || '[]'));
    itemIds = normalizedItemIds(parsed);
  } catch {
    parseError = true;
  }
  const storedCount = Number(row.item_count || 0);
  const storedHash = String(row.item_ids_hash || '');
  const actualHash = crypto.createHash('sha256').update(JSON.stringify(itemIds)).digest('hex').toUpperCase();
  const identityValid = !parseError && storedCount === itemIds.length && storedHash === actualHash;
  return {
    business_date: String(row.business_date),
    account_id: String(row.account_id),
    child_user_id: String(row.child_user_id),
    site_id: String(row.site_id),
    complete: Boolean(row.complete) && identityValid,
    item_count: itemIds.length,
    item_ids_hash: actualHash,
    item_ids: itemIds,
    source: row.source || '',
    captured_at: row.captured_at,
    updated_at: row.updated_at,
    integrity_status: identityValid ? 'ok' : 'invalid',
  };
}

export function saveDailyItemIdentitySnapshot({
  businessDate,
  business_date,
  accountId,
  account_id,
  childUserId,
  child_user_id,
  siteId,
  site_id,
  itemIds = [],
  item_ids,
  complete = false,
  source = '',
  capturedAt,
  captured_at,
} = {}) {
  const identity = normalizedDailySnapshotIdentity({
    businessDate: businessDate ?? business_date,
    accountId: accountId ?? account_id,
    childUserId: childUserId ?? child_user_id,
    siteId: siteId ?? site_id,
  });
  const ids = normalizedItemIds(item_ids ?? itemIds);
  const itemIdsJson = JSON.stringify(ids);
  const itemIdsHash = crypto.createHash('sha256').update(itemIdsJson).digest('hex').toUpperCase();
  const timestamp = normalizedDate(capturedAt ?? captured_at ?? new Date()).toISOString();
  return transaction((database) => {
    database.prepare(
      `INSERT INTO daily_item_identity_snapshots
        (business_date, account_id, child_user_id, site_id, complete, item_count,
         item_ids_hash, item_ids_json, source, captured_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(business_date, account_id, child_user_id, site_id) DO UPDATE SET
         complete = excluded.complete,
         item_count = excluded.item_count,
         item_ids_hash = excluded.item_ids_hash,
         item_ids_json = excluded.item_ids_json,
         source = excluded.source,
         captured_at = excluded.captured_at,
         updated_at = excluded.updated_at`,
    ).run(
      identity.businessDate,
      identity.accountId,
      identity.childUserId,
      identity.siteId,
      Number(Boolean(complete)),
      ids.length,
      itemIdsHash,
      itemIdsJson,
      String(source || ''),
      timestamp,
      nowIso(),
    );
    const latest = database.prepare(
      'SELECT MAX(business_date) AS business_date FROM daily_item_identity_snapshots',
    ).get()?.business_date;
    if (latest) {
      const cutoff = new Date(`${normalizedBusinessDate(latest)}T00:00:00.000Z`);
      cutoff.setUTCDate(cutoff.getUTCDate() - (DAILY_ITEM_SNAPSHOT_RETENTION_DAYS - 1));
      database.prepare(
        'DELETE FROM daily_item_identity_snapshots WHERE business_date < ?',
      ).run(cutoff.toISOString().slice(0, 10));
    }
    const row = database.prepare(
      `SELECT *
       FROM daily_item_identity_snapshots
       WHERE business_date = ? AND account_id = ? AND child_user_id = ? AND site_id = ?`,
    ).get(identity.businessDate, identity.accountId, identity.childUserId, identity.siteId);
    return publicDailyItemIdentitySnapshot(row);
  });
}

export function getDailyItemIdentitySnapshot(input = {}) {
  const identity = normalizedDailySnapshotIdentity(input);
  const row = get(
    `SELECT *
     FROM daily_item_identity_snapshots
     WHERE business_date = ? AND account_id = ? AND child_user_id = ? AND site_id = ?`,
    [identity.businessDate, identity.accountId, identity.childUserId, identity.siteId],
  );
  return publicDailyItemIdentitySnapshot(row);
}

function insufficientDailyDelta(identity, baselineDate, reason) {
  return {
    status: 'insufficient',
    baseline_date: baselineDate,
    current_date: identity.businessDate,
    account_id: identity.accountId,
    child_user_id: identity.childUserId,
    site_id: identity.siteId,
    added_count: null,
    removed_count: null,
    added_item_ids: [],
    removed_item_ids: [],
    reason,
  };
}

export function getDailyItemIdentityDelta(input = {}) {
  const identity = normalizedDailySnapshotIdentity(input);
  const baselineDate = previousBusinessDate(identity.businessDate);
  const current = getDailyItemIdentitySnapshot(identity);
  if (!current) return insufficientDailyDelta(identity, baselineDate, 'current_snapshot_missing');
  if (current.integrity_status !== 'ok') {
    return insufficientDailyDelta(identity, baselineDate, 'current_snapshot_invalid');
  }
  if (!current.complete) return insufficientDailyDelta(identity, baselineDate, 'current_snapshot_incomplete');
  const baseline = getDailyItemIdentitySnapshot({ ...identity, businessDate: baselineDate });
  if (!baseline) return insufficientDailyDelta(identity, baselineDate, 'baseline_snapshot_missing');
  if (baseline.integrity_status !== 'ok') {
    return insufficientDailyDelta(identity, baselineDate, 'baseline_snapshot_invalid');
  }
  if (!baseline.complete) return insufficientDailyDelta(identity, baselineDate, 'baseline_snapshot_incomplete');

  const currentIds = new Set(current.item_ids);
  const baselineIds = new Set(baseline.item_ids);
  const addedItemIds = current.item_ids.filter((itemId) => !baselineIds.has(itemId));
  const removedItemIds = baseline.item_ids.filter((itemId) => !currentIds.has(itemId));
  return {
    status: 'ready',
    baseline_date: baselineDate,
    current_date: identity.businessDate,
    account_id: identity.accountId,
    child_user_id: identity.childUserId,
    site_id: identity.siteId,
    added_count: addedItemIds.length,
    removed_count: removedItemIds.length,
    added_item_ids: addedItemIds,
    removed_item_ids: removedItemIds,
    reason: '',
  };
}

export function summarizeDailyItemIdentityDeltas({
  businessDate,
  business_date,
  routes = [],
} = {}) {
  const currentDate = normalizedBusinessDate(businessDate ?? business_date);
  const baselineDate = previousBusinessDate(currentDate);
  const routeDeltas = routes.map((route) => getDailyItemIdentityDelta({
    ...route,
    businessDate: currentDate,
  }));
  const ready = routeDeltas.filter((row) => row.status === 'ready');
  const insufficient = routeDeltas.filter((row) => row.status !== 'ready');
  return {
    status: routeDeltas.length > 0 && insufficient.length === 0 ? 'ready' : 'insufficient',
    baseline_date: baselineDate,
    current_date: currentDate,
    added_count: insufficient.length === 0
      ? ready.reduce((sum, row) => sum + row.added_count, 0)
      : null,
    removed_count: insufficient.length === 0
      ? ready.reduce((sum, row) => sum + row.removed_count, 0)
      : null,
    route_count: routeDeltas.length,
    ready_route_count: ready.length,
    insufficient_route_count: insufficient.length,
    reason: routeDeltas.length === 0
      ? 'no_routes'
      : insufficient.map((row) => row.reason).filter(Boolean).join(','),
  };
}

function accountRecordFromDatabase(database, accountId) {
  return database.prepare(
    `SELECT id, provider, account_id, display_name, site_id, scopes, token_type,
            expires_at, created_at, updated_at
     FROM oauth_tokens WHERE account_id = ?`,
  ).get(String(accountId)) || null;
}

function upsertTokenAccount(database, {
  token,
  profile,
  clientId,
  clientSecret,
  redirectUri,
  authDomain,
  now = new Date(),
}) {
  const nowDate = normalizedDate(now);
  const nowText = nowDate.toISOString();
  const accountId = String(token.user_id || profile.id);
  const expiresAt = refreshTokenExpiresAt(token, nowDate);
  database.prepare(
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
  ).run(
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
    nowText,
    nowText,
  );
  return accountRecordFromDatabase(database, accountId);
}

export function saveTokenAccount({ token, profile, clientId, clientSecret, redirectUri, authDomain }) {
  return transaction((database) => upsertTokenAccount(database, {
    token,
    profile,
    clientId,
    clientSecret,
    redirectUri,
    authDomain,
  }));
}

export function saveTokenAccountAndConsumeOAuthState({
  state,
  claimToken,
  token,
  profile,
  clientId,
  clientSecret,
  redirectUri,
  authDomain,
  now = new Date(),
  maxAgeMs = DEFAULT_OAUTH_STATE_TTL_MS,
} = {}) {
  const normalizedState = String(state || '');
  const normalizedClaimToken = String(claimToken || '');
  const nowDate = normalizedDate(now);
  const consumedAt = nowDate.toISOString();
  return transaction((database) => {
    const oauth = database.prepare(
      `SELECT processing_state, claim_token, created_at, claim_expires_at
       FROM oauth_states WHERE state = ?`,
    ).get(normalizedState);
    if (!oauth) return { status: 'missing', state: normalizedState, account: null };
    if (String(oauth.processing_state || '') === 'consumed') {
      const accountId = String(token?.user_id || profile?.id || '');
      return {
        status: 'consumed',
        state: normalizedState,
        account: accountId ? accountRecordFromDatabase(database, accountId) : null,
      };
    }
    const lifecycle = oauthStateLifecycle(oauth, {
      now: nowDate,
      maxAgeMs,
    });
    if (lifecycle === 'expired') {
      database.prepare(
        `UPDATE oauth_states
         SET processing_state = 'expired',
             claim_token = NULL,
             claimed_at = NULL,
             claim_expires_at = NULL
         WHERE state = ? AND processing_state <> 'consumed'`,
      ).run(normalizedState);
      return { status: 'expired', state: normalizedState, account: null };
    }
    if (String(oauth.processing_state || '') !== 'processing'
      || String(oauth.claim_token || '') !== normalizedClaimToken) {
      return { status: 'claim_mismatch', state: normalizedState, account: null };
    }
    if (Date.parse(String(oauth.claim_expires_at || '')) <= nowDate.getTime()) {
      database.prepare(
        `UPDATE oauth_states
         SET processing_state = 'pending',
             claim_token = NULL,
             claimed_at = NULL,
             claim_expires_at = NULL,
             last_error_code = 'OAUTH_STATE_CLAIM_EXPIRED'
         WHERE state = ? AND processing_state = 'processing' AND claim_token = ?`,
      ).run(normalizedState, normalizedClaimToken);
      return { status: 'claim_expired', state: normalizedState, account: null };
    }

    const account = upsertTokenAccount(database, {
      token,
      profile,
      clientId,
      clientSecret,
      redirectUri,
      authDomain,
      now,
    });
    const consumed = database.prepare(
      `UPDATE oauth_states
       SET processing_state = 'consumed',
           claim_token = NULL,
           claimed_at = NULL,
           claim_expires_at = NULL,
           consumed_at = ?,
           last_error_code = NULL
       WHERE state = ? AND processing_state = 'processing' AND claim_token = ?`,
    ).run(consumedAt, normalizedState, normalizedClaimToken);
    if (Number(consumed.changes || 0) !== 1) {
      const error = new Error('OAuth状态认领已变化，token与状态均未保存。');
      error.code = 'OAUTH_STATE_CLAIM_MISMATCH';
      throw error;
    }
    return { status: 'consumed', state: normalizedState, account };
  });
}

function refreshTokenExpiresAt(token = {}, now = new Date()) {
  const absolute = token.expires_at;
  if (absolute !== undefined && absolute !== null && String(absolute).trim()) {
    const text = String(absolute).trim();
    if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(text)) return null;
    const parsed = Date.parse(text);
    return Number.isFinite(parsed) ? new Date(parsed).toISOString() : null;
  }
  const legacyExpiry = token.expiry;
  if (typeof legacyExpiry === 'string' && legacyExpiry.trim()) {
    const text = legacyExpiry.trim();
    if (/(?:Z|[+-]\d{2}:\d{2})$/i.test(text)) {
      const parsed = Date.parse(text);
      return Number.isFinite(parsed) ? new Date(parsed).toISOString() : null;
    }
    return null;
  }
  const seconds = Number(token.expires_in ?? legacyExpiry);
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  const parsed = new Date(now.getTime() + seconds * 1000);
  return Number.isFinite(parsed.getTime()) ? parsed.toISOString() : null;
}

function invalidRefreshExpiryError() {
  const error = new Error('刷新授权缺少有效的到期时间，未更新授权。');
  error.code = 'TOKEN_EXPIRY_INVALID';
  error.status = 422;
  return error;
}

export function updateAccountToken(accountId, token, { now = new Date() } = {}) {
  const expiresAt = refreshTokenExpiresAt(token, now);
  if (!expiresAt) throw invalidRefreshExpiryError();
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

export function getAccountProfile(accountId) {
  return get(
    `SELECT account_id, provider, display_name, site_id, fetched_at, source
     FROM account_profiles WHERE account_id = ?`,
    [String(accountId)]
  ) || null;
}

export function listAccountProfiles() {
  return all(
    `SELECT account_id, provider, display_name, site_id, fetched_at, source
     FROM account_profiles ORDER BY fetched_at DESC`
  );
}

export function saveAccountProfile(profile) {
  if (!profile?.account_id || !profile?.display_name) return null;
  run(
    `INSERT INTO account_profiles (account_id, provider, display_name, site_id, fetched_at, source)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(account_id) DO UPDATE SET
       provider = excluded.provider,
       display_name = excluded.display_name,
       site_id = excluded.site_id,
       fetched_at = excluded.fetched_at,
       source = excluded.source`,
    [
      String(profile.account_id),
      String(profile.provider || 'mercadolibre'),
      String(profile.display_name),
      profile.site_id ? String(profile.site_id) : null,
      String(profile.fetched_at || nowIso()),
      String(profile.source || 'users_me')
    ]
  );
  return getAccountProfile(profile.account_id);
}

export function saveCampaigns(accountId, promotions, context = {}) {
  const rows = promotions.map(normalizePromotion);
  const now = nowIso();
  transaction((database) => {
    const statement = database.prepare(
      `INSERT INTO promo_campaigns
        (account_id, promotion_id, promotion_type, merchant_id, child_user_id, site_id, logistic_type,
         name, status, start_date, finish_date, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, child_user_id, site_id, promotion_id, promotion_type) DO UPDATE SET
          merchant_id = excluded.merchant_id,
          child_user_id = excluded.child_user_id,
          site_id = excluded.site_id,
          logistic_type = excluded.logistic_type,
          name = excluded.name,
          status = excluded.status,
          start_date = excluded.start_date,
          finish_date = excluded.finish_date,
          raw_json = excluded.raw_json,
          updated_at = excluded.updated_at`
    );
    for (const promo of rows) {
      const route = resolveRouteForIdentity({
        accountId,
        promotionId: promo.promotion_id,
        promotionType: promo.promotion_type,
        route: routeFromPromotion(promo, context),
      });
      statement.run(
        String(accountId),
        promo.promotion_id,
        promo.promotion_type,
        context.merchantId ? String(context.merchantId) : null,
        route.childUserId,
        route.siteId,
        context.logisticType || promo.raw?.logistic_type || null,
        promo.name,
        promo.status,
        promo.start_date,
        promo.finish_date,
        JSON.stringify(promo.raw),
        now
      );
    }
  });
  return rows;
}

export function markCampaignsCatalogRemoved({ accountId, childUserId, siteId, promotions = [] } = {}) {
  const rows = Array.isArray(promotions) ? promotions : [];
  if (!accountId || !childUserId || !siteId || !rows.length) return 0;
  const updatedAt = nowIso();
  let changed = 0;
  transaction((database) => {
    const statement = database.prepare(
      `UPDATE promo_campaigns
       SET status = 'catalog_removed', updated_at = ?
       WHERE account_id = ? AND child_user_id = ? AND site_id = ?
         AND promotion_id = ? AND promotion_type = ?`
    );
    for (const promotion of rows) {
      changed += Number(statement.run(
        updatedAt,
        String(accountId),
        String(childUserId),
        String(siteId).toUpperCase(),
        String(promotion.promotion_id || promotion.id || ''),
        String(promotion.promotion_type || promotion.type || '').toUpperCase(),
      ).changes || 0);
    }
  });
  return changed;
}

export function listCampaignsAll(accountId) {
  return all('SELECT * FROM promo_campaigns WHERE account_id = ? ORDER BY site_id, child_user_id, updated_at DESC, name', [String(accountId)]);
}

export function listCampaigns(accountId, route = null) {
  const requested = routeFields(route || {});
  if (requested.childUserId || requested.siteId) {
    if (!completeRoute(requested)) throw routeIdentityError();
    return all(
      `SELECT * FROM promo_campaigns
       WHERE account_id = ? AND child_user_id = ? AND site_id = ?
       ORDER BY updated_at DESC, name`,
      [String(accountId), requested.childUserId, requested.siteId],
    );
  }
  const rows = listCampaignsAll(accountId);
  const routes = distinctCompleteRoutes(rows);
  if (routes.length > 1) throw routeIdentityError();
  if (routes.length === 1) return rows.filter((row) => {
    const rowRoute = routeFields(row);
    return rowRoute.childUserId === routes[0].childUserId && rowRoute.siteId === routes[0].siteId;
  });
  return rows;
}

export function listCampaignsFiltered(accountId, filters = {}) {
  const requested = routeFields(filters.route || filters);
  const campaigns = requested.childUserId && requested.siteId
    ? listCampaigns(accountId, requested)
    : listCampaignsAll(accountId);
  return filterPromotions(campaigns, filters);
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
     SET last_promotion_status = ?,
         last_promotion_count = CASE WHEN ? IS NULL THEN last_promotion_count ELSE ? END,
         last_error = ?, updated_at = ?
     WHERE account_id = ? AND child_user_id = ?`,
    [status, count == null ? null : count, count == null ? null : count, error || null, nowIso(), String(accountId), String(childUserId)]
  );
}

export function invalidateMarketplaceSiteCatalog({ accountId, childUserId, siteId } = {}) {
  if (!accountId || !childUserId || !siteId) return 0;
  return Number(run(
    `UPDATE marketplace_sites
     SET last_promotion_status = 'dirty', last_error = NULL, updated_at = ?
     WHERE account_id = ? AND child_user_id = ? AND site_id = ?`,
    [nowIso(), String(accountId), String(childUserId), String(siteId).toUpperCase()]
  ).changes || 0);
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

export function listAllMarketplaceSites() {
  return all(
    `SELECT account_id, child_user_id, site_id, logistic_type, last_promotion_status, last_promotion_count, last_error, raw_json, updated_at
     FROM marketplace_sites
     ORDER BY account_id, site_id, logistic_type, child_user_id`
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
    const route = resolveRouteForIdentity({
      accountId,
      promotionId: promo.promotion_id,
      promotionType: promo.promotion_type,
      route: routeFromPromotion(promo),
    });
    const row = get(
      `SELECT COUNT(*) AS count FROM promo_items
       WHERE account_id = ? AND child_user_id = ? AND site_id = ?
         AND promotion_id = ? AND promotion_type = ? AND status = ?`,
      [String(accountId), route.childUserId, route.siteId, promo.promotion_id, promo.promotion_type, status]
    );
    map.set(promotionKey(promo), row?.count || 0);
  }
  return map;
}

export function listSiteSummaries(accountId) {
  const childSites = listMarketplaceSites(accountId);
  const activitySites = summarizeSites(listCampaignsAll(accountId));
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

export function getCampaign(accountId, promotionId, promotionType, route = null) {
  const resolvedRoute = resolveRouteForIdentity({ accountId, promotionId, promotionType, route });
  return get(
    `SELECT * FROM promo_campaigns
     WHERE account_id = ? AND child_user_id = ? AND site_id = ?
       AND promotion_id = ? AND promotion_type = ?`,
    [String(accountId), resolvedRoute.childUserId, resolvedRoute.siteId, promotionId, promotionType]
  );
}

export function saveItems(accountId, promotionId, promotionType, items, context = {}) {
  const rows = items.map((raw) => {
    const item = normalizeItem(raw);
    if (context.itemStatus) item.status = context.itemStatus;
    return item;
  });
  const now = nowIso();
  const route = resolveRouteForIdentity({
    accountId,
    promotionId,
    promotionType,
    route: routeFields(context),
  });
  transaction((database) => {
    if (context.replaceStatus) {
      database.prepare(
        `DELETE FROM promo_items
       WHERE account_id = ? AND child_user_id = ? AND site_id = ?
         AND promotion_id = ? AND promotion_type = ? AND status = ?`,
      ).run(String(accountId), route.childUserId, route.siteId, promotionId, promotionType, String(context.replaceStatus));
    }
    const statement = database.prepare(
      `INSERT INTO promo_items
        (account_id, promotion_id, promotion_type, child_user_id, site_id, logistic_type, item_id, status,
         currency_id, original_price, price, suggested_discounted_price, min_discounted_price,
         max_discounted_price, source, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, child_user_id, site_id, promotion_id, promotion_type, item_id) DO UPDATE SET
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
          updated_at = excluded.updated_at`
    );
    for (const item of rows) {
      statement.run(
        String(accountId),
        promotionId,
        promotionType,
        route.childUserId,
        route.siteId,
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
      );
    }
  });
  return rows;
}

export function applySuccessfulPromotionItemWrites({ accountId, promotionId, promotionType, action, items = [], childUserId, siteId }) {
  const normalizedAction = String(action || '').toLowerCase();
  const route = resolveRouteForIdentity({ accountId, promotionId, promotionType, route: { childUserId, siteId } });
  const identity = [String(accountId), route.childUserId, route.siteId, String(promotionId), String(promotionType)];
  const rows = (items || [])
    .map((item) => ({
      itemId: String(item?.itemId || item?.item_id || '').trim(),
      dealPrice: item?.dealPrice ?? item?.deal_price ?? null,
    }))
    .filter((item) => item.itemId);
  if (!rows.length) return;
  transaction((database) => {
    if (normalizedAction === 'cancel') {
      const statement = database.prepare(
        `DELETE FROM promo_items
         WHERE account_id = ? AND child_user_id = ? AND site_id = ?
           AND promotion_id = ? AND promotion_type = ? AND item_id = ?`,
      );
      for (const row of rows) statement.run(...identity, row.itemId);
      return;
    }
    if (!['enroll', 'update'].includes(normalizedAction)) return;
    const statement = database.prepare(
      `UPDATE promo_items
       SET status = 'started', price = COALESCE(?, price), updated_at = ?
       WHERE account_id = ? AND child_user_id = ? AND site_id = ?
         AND promotion_id = ? AND promotion_type = ? AND item_id = ?`,
    );
    const updatedAt = nowIso();
    for (const row of rows) {
      statement.run(
        row.dealPrice == null ? null : Number(row.dealPrice),
        updatedAt,
        ...identity,
        row.itemId,
      );
    }
  });
}

export function reconcilePromotionItemFetchCounts({ accountId, promotionId, promotionType, childUserId, siteId }) {
  const route = resolveRouteForIdentity({ accountId, promotionId, promotionType, route: { childUserId, siteId } });
  const identity = [String(accountId), route.childUserId, route.siteId, String(promotionId), String(promotionType)];
  transaction((database) => {
    const countStatement = database.prepare(
      `SELECT COUNT(*) AS count FROM promo_items
       WHERE account_id = ? AND child_user_id = ? AND site_id = ?
         AND promotion_id = ? AND promotion_type = ? AND status = ?`,
    );
    const updateStatement = database.prepare(
      `UPDATE promo_item_fetch_states
       SET saved_count = ?,
           platform_total = CASE
             WHEN detail_status IN ('ok', 'full', 'empty') THEN ?
             ELSE platform_total
           END
       WHERE account_id = ? AND child_user_id = ? AND site_id = ?
         AND promotion_id = ? AND promotion_type = ? AND item_status = ?`,
    );
    for (const status of ['candidate', 'started']) {
      const count = Number(countStatement.get(...identity, status)?.count || 0);
      updateStatement.run(count, count, ...identity, status);
    }
  });
}

export function deleteItemsBySource(accountId, promotionId, promotionType, status, source, route = null) {
  const resolvedRoute = resolveRouteForIdentity({ accountId, promotionId, promotionType, route });
  run(
    `DELETE FROM promo_items
     WHERE account_id = ? AND child_user_id = ? AND site_id = ?
       AND promotion_id = ? AND promotion_type = ? AND status = ? AND source = ?`,
    [String(accountId), resolvedRoute.childUserId, resolvedRoute.siteId, promotionId, promotionType, status, source]
  );
}

export function saveItemFetchState({ accountId, promotionId, promotionType, itemStatus, platformTotal, savedCount, detailStatus, warning, raw, childUserId, siteId }) {
  const route = resolveRouteForIdentity({ accountId, promotionId, promotionType, route: { childUserId, siteId } });
  run(
    `INSERT INTO promo_item_fetch_states
      (account_id, child_user_id, site_id, promotion_id, promotion_type, item_status, platform_total, saved_count, detail_status, warning, raw_json, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(account_id, child_user_id, site_id, promotion_id, promotion_type, item_status) DO UPDATE SET
        platform_total = excluded.platform_total,
        saved_count = excluded.saved_count,
        detail_status = excluded.detail_status,
        warning = excluded.warning,
        raw_json = excluded.raw_json,
        updated_at = excluded.updated_at`,
    [
      String(accountId),
      route.childUserId,
      route.siteId,
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

export function getItemFetchState(accountId, promotionId, promotionType, itemStatus, route = null) {
  const resolvedRoute = resolveRouteForIdentity({ accountId, promotionId, promotionType, route });
  return get(
    `SELECT * FROM promo_item_fetch_states
     WHERE account_id = ? AND child_user_id = ? AND site_id = ?
       AND promotion_id = ? AND promotion_type = ? AND item_status = ?`,
    [String(accountId), resolvedRoute.childUserId, resolvedRoute.siteId, promotionId, promotionType, itemStatus]
  );
}

export function invalidatePromotionItemFetchStates({ accountId, promotionId, promotionType, childUserId, siteId }) {
  const route = resolveRouteForIdentity({ accountId, promotionId, promotionType, route: { childUserId, siteId } });
  run(
    `DELETE FROM promo_item_fetch_states
     WHERE account_id = ? AND child_user_id = ? AND site_id = ?
       AND promotion_id = ? AND promotion_type = ?`,
    [String(accountId), route.childUserId, route.siteId, String(promotionId), String(promotionType)]
  );
}

function activityCacheIdentity(input, siteId = '', promotionId = '', promotionType = '') {
  if (input && typeof input === 'object' && !Array.isArray(input)) {
    return {
      accountId: String(input.accountId ?? input.account_id ?? ''),
      childUserId: String(input.childUserId ?? input.child_user_id ?? ''),
      siteId: String(input.siteId ?? input.site_id ?? '').toUpperCase(),
      promotionId: String(input.promotionId ?? input.promotion_id ?? ''),
      promotionType: String(input.promotionType ?? input.promotion_type ?? '').toUpperCase(),
    };
  }
  return {
    accountId: String(input ?? ''),
    childUserId: '',
    siteId: String(siteId || '').toUpperCase(),
    promotionId: String(promotionId || ''),
    promotionType: String(promotionType || '').toUpperCase(),
  };
}

export function getActivityCacheState(accountOrIdentity, siteId = '', promotionId = '', promotionType = '') {
  const identity = activityCacheIdentity(accountOrIdentity, siteId, promotionId, promotionType);
  return get(
    `SELECT * FROM activity_cache_states
     WHERE account_id = ? AND child_user_id = ? AND site_id = ? AND promotion_id = ? AND promotion_type = ?`,
    [identity.accountId, identity.childUserId, identity.siteId, identity.promotionId, identity.promotionType]
  );
}

export function listPreparationReadStates(accountIds = []) {
  const ids = [...new Set((accountIds || []).map((value) => String(value || '').trim()).filter(Boolean))];
  if (!ids.length) return { activity_cache_states: [], item_fetch_states: [], db_batch_queries: 0 };
  const placeholders = ids.map(() => '?').join(', ');
  return {
    activity_cache_states: all(
      `SELECT * FROM activity_cache_states WHERE account_id IN (${placeholders})`,
      ids,
    ),
    item_fetch_states: all(
      `SELECT * FROM promo_item_fetch_states WHERE account_id IN (${placeholders})`,
      ids,
    ),
    db_batch_queries: 2,
  };
}

export function saveActivityCacheState({
  accountId,
  childUserId = '',
  siteId = '',
  promotionId = '',
  promotionType = '',
  ...changes
}) {
  const identity = activityCacheIdentity({ accountId, childUserId, siteId, promotionId, promotionType });
  const current = getActivityCacheState(identity) || {};
  const row = {
    catalog_checked_at: changes.catalogCheckedAt ?? current.catalog_checked_at ?? null,
    items_full_checked_at: changes.itemsFullCheckedAt ?? current.items_full_checked_at ?? null,
    dirty: changes.dirty == null ? Number(current.dirty || 0) : Number(Boolean(changes.dirty)),
    expired: changes.expired == null ? Number(current.expired || 0) : Number(Boolean(changes.expired)),
    continuity: String(changes.continuity ?? current.continuity ?? 'continuous'),
    event_cursor: changes.eventCursor ?? current.event_cursor ?? null,
    last_error: Object.hasOwn(changes, 'lastError') ? changes.lastError : current.last_error ?? null,
  };
  run(
    `INSERT INTO activity_cache_states
      (account_id, child_user_id, site_id, promotion_id, promotion_type, catalog_checked_at, items_full_checked_at,
       dirty, expired, continuity, event_cursor, last_error, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(account_id, child_user_id, site_id, promotion_id, promotion_type) DO UPDATE SET
       catalog_checked_at = excluded.catalog_checked_at,
       items_full_checked_at = excluded.items_full_checked_at,
       dirty = excluded.dirty,
       expired = excluded.expired,
       continuity = excluded.continuity,
       event_cursor = excluded.event_cursor,
       last_error = excluded.last_error,
       updated_at = excluded.updated_at`,
    [identity.accountId, identity.childUserId, identity.siteId, identity.promotionId, identity.promotionType,
      row.catalog_checked_at, row.items_full_checked_at, row.dirty, row.expired, row.continuity,
      row.event_cursor, row.last_error, nowIso()]
  );
  return getActivityCacheState(identity);
}

export function markActivityCacheDirty({
  accountId,
  childUserId = '',
  siteId = '',
  promotionId = '',
  promotionType = '',
  eventCursor = null,
  gap = false,
}, routeIdentity = {}) {
  const route = {
    accountId: accountId || routeIdentity.accountId || routeIdentity.account_id,
    childUserId: childUserId || routeIdentity.childUserId || routeIdentity.child_user_id,
    siteId: siteId || routeIdentity.siteId || routeIdentity.site_id,
  };
  const activityState = saveActivityCacheState({
    ...route,
    promotionId,
    promotionType,
    dirty: true,
    continuity: gap ? 'gap' : undefined, eventCursor, lastError: null,
  });
  if (promotionId || promotionType) {
    saveActivityCacheState({
      ...route,
      promotionId: '',
      promotionType: '',
      dirty: true,
      continuity: gap ? 'gap' : undefined,
      eventCursor,
      lastError: null,
    });
  }
  return activityState;
}

export function hasActivityCallbackEvent(eventId) {
  return Boolean(get(
    `SELECT 1 AS found
     FROM activity_callback_events
     WHERE event_id = ? AND processing_state = 'completed'`,
    [String(eventId)],
  ));
}

export function getActivityCallbackEvent(eventId) {
  return get('SELECT * FROM activity_callback_events WHERE event_id = ?', [String(eventId)]);
}

export function claimActivityCallbackEvent(event, {
  leaseMs = 2 * 60 * 1000,
  now = new Date(),
} = {}) {
  const eventId = String(event?.event_id || '').trim();
  if (!eventId) throw new Error('活动通知缺少事件编号，无法原子认领。');
  const nowDate = now instanceof Date ? now : new Date(now);
  const claimedAt = nowDate.toISOString();
  const claimExpiresAt = new Date(nowDate.getTime() + Math.max(1_000, Number(leaseMs) || 0)).toISOString();
  const claimToken = crypto.randomUUID();
  return transaction((database) => {
    const existing = database.prepare(
      'SELECT event_id, processing_state, claim_expires_at, attempt_count FROM activity_callback_events WHERE event_id = ?',
    ).get(eventId);
    if (existing?.processing_state === 'completed') {
      return { status: 'duplicate', event_id: eventId, claim_token: null };
    }
    if (existing?.processing_state === 'processing'
      && Date.parse(String(existing.claim_expires_at || '')) > nowDate.getTime()) {
      return { status: 'in_progress', event_id: eventId, claim_token: null };
    }

    if (!existing) {
      database.prepare(
        `INSERT INTO activity_callback_events
          (event_id, schema_version, account_id, site_id, promotion_id, promotion_type,
           cursor, previous_cursor, gap, received_at, topic, resource, remote_user_id,
           child_user_id, application_id, outcome, resource_status, raw_json,
           processing_state, claim_token, claimed_at, claim_expires_at, completed_at,
           last_error, attempt_count)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?, NULL, NULL, 1)`,
      ).run(
        eventId,
        String(event.schema_version || ''),
        String(event.account_id || ''),
        String(event.site_id || '').toUpperCase(),
        String(event.promotion_id || ''),
        String(event.promotion_type || '').toUpperCase(),
        event.cursor || null,
        event.previous_cursor || null,
        Number(Boolean(event.gap)),
        event.received_at || claimedAt,
        event.topic || null,
        event.resource || null,
        event.remote_user_id || null,
        event.child_user_id || null,
        event.application_id || null,
        event.outcome || null,
        event.resource_status || null,
        event.raw_json || null,
        claimToken,
        claimedAt,
        claimExpiresAt,
      );
    } else {
      database.prepare(
        `UPDATE activity_callback_events
         SET processing_state = 'processing',
             claim_token = ?,
             claimed_at = ?,
             claim_expires_at = ?,
             completed_at = NULL,
             last_error = NULL,
             attempt_count = COALESCE(attempt_count, 0) + 1
         WHERE event_id = ?`,
      ).run(claimToken, claimedAt, claimExpiresAt, eventId);
    }
    return { status: 'claimed', event_id: eventId, claim_token: claimToken };
  });
}

export function finalizeActivityCallbackEvent({
  eventId,
  claimToken,
  status,
  event = {},
  error = '',
  now = new Date(),
} = {}) {
  const normalizedStatus = status === 'failed' ? 'failed' : 'completed';
  const completedAt = (now instanceof Date ? now : new Date(now)).toISOString();
  const lastError = normalizedStatus === 'failed'
    ? String(error || '活动通知处理失败。').replace(/[\r\n]+/g, ' ').slice(0, 500)
    : null;
  return transaction((database) => {
    const result = database.prepare(
      `UPDATE activity_callback_events
       SET schema_version = COALESCE(NULLIF(?, ''), schema_version),
           account_id = COALESCE(NULLIF(?, ''), account_id),
           child_user_id = COALESCE(NULLIF(?, ''), child_user_id),
           site_id = COALESCE(NULLIF(?, ''), site_id),
           promotion_id = COALESCE(NULLIF(?, ''), promotion_id),
           promotion_type = COALESCE(NULLIF(?, ''), promotion_type),
           cursor = COALESCE(?, cursor),
           previous_cursor = COALESCE(?, previous_cursor),
           gap = ?,
           received_at = COALESCE(NULLIF(?, ''), received_at),
           topic = COALESCE(NULLIF(?, ''), topic),
           resource = COALESCE(NULLIF(?, ''), resource),
           remote_user_id = COALESCE(NULLIF(?, ''), remote_user_id),
           application_id = COALESCE(NULLIF(?, ''), application_id),
           outcome = COALESCE(NULLIF(?, ''), outcome),
           resource_status = COALESCE(NULLIF(?, ''), resource_status),
           raw_json = COALESCE(?, raw_json),
           processing_state = ?,
           claim_token = NULL,
           claim_expires_at = NULL,
           completed_at = ?,
           last_error = ?
       WHERE event_id = ? AND processing_state = 'processing' AND claim_token = ?`,
    ).run(
      String(event.schema_version || ''),
      String(event.account_id || ''),
      String(event.child_user_id || ''),
      String(event.site_id || '').toUpperCase(),
      String(event.promotion_id || ''),
      String(event.promotion_type || '').toUpperCase(),
      event.cursor || null,
      event.previous_cursor || null,
      Number(Boolean(event.gap)),
      event.received_at || '',
      event.topic || '',
      event.resource || '',
      event.remote_user_id || '',
      event.application_id || '',
      event.outcome || '',
      event.resource_status || '',
      event.raw_json || null,
      normalizedStatus,
      completedAt,
      lastError,
      String(eventId || ''),
      String(claimToken || ''),
    );
    if (Number(result.changes || 0) !== 1) {
      throw new Error('活动通知认领已失效，未写入处理结果。');
    }
    return database.prepare('SELECT * FROM activity_callback_events WHERE event_id = ?').get(String(eventId || ''));
  });
}

export function saveActivityCallbackEvent(event) {
  return run(
    `INSERT OR IGNORE INTO activity_callback_events
      (event_id, schema_version, account_id, site_id, promotion_id, promotion_type, cursor, previous_cursor, gap, received_at,
       topic, resource, remote_user_id, child_user_id, application_id, outcome, resource_status, raw_json,
       processing_state, completed_at, attempt_count)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, 1)`,
    [event.event_id, event.schema_version, event.account_id, event.site_id, event.promotion_id || '',
      event.promotion_type || '', event.cursor || null, event.previous_cursor || null, Number(Boolean(event.gap)), event.received_at || nowIso(),
      event.topic || null, event.resource || null, event.remote_user_id || null, event.child_user_id || null,
      event.application_id || null, event.outcome || null, event.resource_status || null, event.raw_json || null,
      event.received_at || nowIso()]
  );
}

export function listVerifiedActivityCallbackPromotionMappings({
  accountId,
  childUserId,
  siteId,
  promotionType = 'SELLER_CAMPAIGN',
} = {}) {
  return all(
    `SELECT event_id, account_id, child_user_id, site_id, promotion_id, promotion_type,
            outcome, resource_status, received_at
     FROM activity_callback_events
     WHERE schema_version = '2'
       AND account_id = ? AND child_user_id = ? AND site_id = ?
       AND promotion_id <> '' AND promotion_type = ?
       AND outcome = 'activity_dirty'
       AND processing_state = 'completed'
     ORDER BY received_at DESC`,
    [String(accountId || ''), String(childUserId || ''), String(siteId || '').toUpperCase(), String(promotionType || '').toUpperCase()]
  );
}

export function listSellerCampaignRecoveryCandidates({ accountId, childUserId, siteId, name } = {}) {
  const routeParams = [String(accountId || ''), String(childUserId || ''), String(siteId || '').toUpperCase(), String(name || '')];
  const campaigns = all(
    `SELECT account_id, child_user_id, site_id, promotion_id, promotion_type, name, status, finish_date, updated_at
     FROM promo_campaigns
     WHERE account_id = ? AND child_user_id = ? AND site_id = ?
       AND promotion_type = 'SELLER_CAMPAIGN' AND name = ?
     ORDER BY updated_at DESC`,
    routeParams
  );
  const createResults = all(
    `SELECT account_id, child_user_id, site_id, promotion_id, promotion_type,
            promotion_name AS name, request_status AS status, finish_date, updated_at
     FROM seller_campaign_create_results
     WHERE account_id = ? AND child_user_id = ? AND site_id = ?
       AND promotion_type = 'SELLER_CAMPAIGN' AND promotion_name = ? AND promotion_id <> ''
     ORDER BY updated_at DESC`,
    routeParams
  );
  const unique = new Map();
  for (const row of [...campaigns, ...createResults]) {
    const promotionId = String(row.promotion_id || '');
    if (promotionId && !unique.has(promotionId)) unique.set(promotionId, row);
  }
  return [...unique.values()];
}

export function listHiddenSellerCampaignsForRoute({ accountId, childUserId, siteId } = {}) {
  return all(
    `SELECT account_id, child_user_id, site_id, promotion_name, promotion_id,
            request_status, detection_status, created_at, updated_at
     FROM seller_campaign_create_results
     WHERE account_id = ? AND child_user_id = ? AND site_id = ?
       AND request_status = 'duplicate_name_hidden'
     ORDER BY updated_at DESC`,
    [String(accountId || ''), String(childUserId || ''), String(siteId || '').toUpperCase()]
  );
}

export function listHiddenSellerCampaignsForAccount(accountId) {
  return all(
    `SELECT account_id, child_user_id, site_id, promotion_name, promotion_id,
            request_status, detection_status, created_at, updated_at
     FROM seller_campaign_create_results
     WHERE account_id = ? AND request_status = 'duplicate_name_hidden'
     ORDER BY updated_at DESC`,
    [String(accountId || '')]
  );
}

export function recordActivityCatalogCalibration({ accountId, siteId = '', checkedAt = nowIso(), error = null }) {
  return saveActivityCacheState({
    accountId, siteId, catalogCheckedAt: error ? undefined : checkedAt,
    dirty: Boolean(error), continuity: error ? 'gap' : 'continuous', lastError: error,
  });
}

export function recordActivityItemsCalibration({ accountId, siteId = '', promotionId, promotionType, checkedAt = nowIso(), error = null }) {
  return saveActivityCacheState({
    accountId, siteId, promotionId, promotionType,
    itemsFullCheckedAt: error ? undefined : checkedAt,
    dirty: Boolean(error), continuity: error ? 'gap' : 'continuous', lastError: error,
  });
}

export function listItems(accountId, promotionId, promotionType, status, route = null) {
  const resolvedRoute = resolveRouteForIdentity({ accountId, promotionId, promotionType, route });
  const params = [String(accountId), resolvedRoute.childUserId, resolvedRoute.siteId, promotionId, promotionType];
  let sql = `SELECT * FROM promo_items
             WHERE account_id = ? AND child_user_id = ? AND site_id = ?
               AND promotion_id = ? AND promotion_type = ?`;
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
    map.set(promotionKey(promo), listItems(accountId, promo.promotion_id, promo.promotion_type, status, promo));
  }
  return map;
}

export function listItemFetchStatesForPromotions(accountId, promotions, status) {
  const map = new Map();
  for (const promo of promotions) {
    const state = getItemFetchState(accountId, promo.promotion_id, promo.promotion_type, status, promo);
    if (state) map.set(promotionKey(promo), state);
  }
  return map;
}

export function createTask({ accountId, promotionId, promotionType, action, mode, discountPercent, directPrice, plan, executionGroupId = null, executionJobId = null }) {
  const now = nowIso();
  const result = run(
    `INSERT INTO promo_tasks
      (account_id, promotion_id, promotion_type, action, mode, discount_percent, direct_price, status,
       total_count, success_count, failed_count, skipped_count, empty_count, completed, summary_json,
       execution_group_id, execution_job_id, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)`,
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
      executionGroupId ? String(executionGroupId) : null,
      executionJobId ? String(executionJobId) : null,
      now,
      now
    ]
  );
  return result.lastInsertRowid;
}

export function savePlanResults({ taskId, accountId, promotionId, promotionType, action, mode, plan }) {
  transaction(() => {
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
    publishHistorySummaryForTask(taskId);
  });
}

export function saveExecutionResult({ taskId, accountId, promotionId, promotionType, itemId, action, mode, status, dealPrice, errorCn, errorRaw, response }) {
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

export function finishTask(taskId, counts, status = 'completed', completed = true, options = {}) {
  transaction(() => {
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
    if (options.publishHistory !== false) publishHistorySummaryForTask(taskId);
  });
}

export function deleteTasks(taskIds = []) {
  const ids = [...new Set(taskIds.map((id) => Number(id)).filter((id) => Number.isInteger(id) && id > 0))];
  if (!ids.length) return { deleted: 0 };
  const placeholders = ids.map(() => '?').join(',');
  run(`DELETE FROM promo_action_results WHERE task_id IN (${placeholders})`, ids);
  const result = run(`DELETE FROM promo_tasks WHERE id IN (${placeholders})`, ids);
  const state = get('SELECT schema_version, status FROM history_summary_state WHERE id = 1');
  if (Number(state?.schema_version || 0) === HISTORY_SUMMARY_SCHEMA_VERSION
    && String(state?.status || '') === 'complete') {
    backfillHistoryBatchSummaries({ force: true });
  }
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

export function listLatestWriteRepeatGuards({ accountId, action, sameDayStartIso }) {
  return all(
    `WITH ranked AS (
       SELECT promotion_id, promotion_type, item_id, action, status, deal_price, error_cn, created_at,
              ROW_NUMBER() OVER (
                PARTITION BY promotion_id, promotion_type, action, item_id
                ORDER BY id DESC
              ) AS rn
       FROM promo_action_results
       WHERE account_id = ?
         AND action = ?
         AND TRIM(COALESCE(item_id, '')) <> ''
         AND status IN ('success', 'failed', 'request_success', 'live_verified_removed', 'live_still_started', 'pending_verification')
     )
     SELECT promotion_id, promotion_type, item_id, action, status, deal_price, error_cn, created_at
     FROM ranked
     WHERE rn = 1
       AND (
         status = 'pending_verification'
         OR (status IN ('failed', 'live_still_started') AND created_at >= ?)
       )`,
    [String(accountId || ''), String(action || ''), String(sameDayStartIso || '')],
  );
}

export function listTaskSummaries(limit = 300, options = {}) {
  const requested = Math.max(Number(limit) || 20, 1);
  const includeDetails = options.includeDetails !== false;
  ensureHistoryBatchSummariesReady();
  const summaries = all(
    `SELECT data_json
     FROM history_batch_summaries
     WHERE schema_version = ?
     ORDER BY sort_created_at DESC, summary_id DESC
     LIMIT ?`,
    [HISTORY_SUMMARY_SCHEMA_VERSION, requested]
  ).map((row) => JSON.parse(row.data_json));
  if (!includeDetails) return summaries;
  return summaries.map((summary) => ({
    ...summary,
    details: listTaskDetails(summary.task_ids || [summary.id])
  }));
}

export function buildLegacyHistoryBaseline(limit = 300) {
  const requested = Math.max(Number(limit) || TASK_SUMMARY_CANONICAL_LIMIT, 1);
  const settings = readSettings();
  const rows = fetchTaskSummaryRows(null, settings);
  return buildLegacyTaskSummaries(rows, requested, { includeDetails: false });
}

export function backfillHistoryBatchSummaries({ force = false } = {}) {
  const existing = get('SELECT * FROM history_summary_state WHERE id = 1');
  if (!force
    && Number(existing?.schema_version || 0) === HISTORY_SUMMARY_SCHEMA_VERSION
    && String(existing?.status || '') === 'complete') {
    const count = Number(get(
      'SELECT COUNT(*) AS count FROM history_batch_summaries WHERE schema_version = ?',
      [HISTORY_SUMMARY_SCHEMA_VERSION]
    )?.count || 0);
    return { status: 'complete', summary_count: count, reused: true };
  }

  const startedAt = nowIso();
  run(
    `INSERT INTO history_summary_state (id, schema_version, status, started_at, completed_at, last_error)
     VALUES (1, ?, 'building', ?, NULL, NULL)
     ON CONFLICT(id) DO UPDATE SET
       schema_version = excluded.schema_version,
       status = 'building',
       started_at = excluded.started_at,
       completed_at = NULL,
       last_error = NULL`,
    [HISTORY_SUMMARY_SCHEMA_VERSION, startedAt]
  );
  try {
    const settings = readSettings();
    const taskRows = fetchTaskSummaryRows(null, settings);
    const summaries = buildLegacyTaskSummaries(taskRows, Math.max(taskRows.length, 1), { includeDetails: false });
    const stamp = taskSummaryStamp();
    transaction(() => {
      run('DELETE FROM history_batch_summaries WHERE schema_version = ?', [HISTORY_SUMMARY_SCHEMA_VERSION]);
      for (const summary of summaries) writeMaterializedSummary(summary);
      run(
        `UPDATE history_summary_state SET
           schema_version = ?, status = 'complete', task_count = ?, task_max_id = ?,
           result_count = ?, result_max_id = ?, completed_at = ?, last_error = NULL
         WHERE id = 1`,
        [
          HISTORY_SUMMARY_SCHEMA_VERSION,
          stamp.task_count,
          stamp.task_max_id,
          stamp.result_count,
          stamp.result_max_id,
          nowIso()
        ]
      );
    });
    return { status: 'complete', summary_count: summaries.length, reused: false };
  } catch (error) {
    run(
      `UPDATE history_summary_state SET status = 'failed', last_error = ?, completed_at = ? WHERE id = 1`,
      [String(error?.message || error).slice(0, 1000), nowIso()]
    );
    throw error;
  }
}

function ensureHistoryBatchSummariesReady() {
  const state = get('SELECT schema_version, status FROM history_summary_state WHERE id = 1');
  if (Number(state?.schema_version || 0) === HISTORY_SUMMARY_SCHEMA_VERSION
    && String(state?.status || '') === 'complete') return;
  backfillHistoryBatchSummaries();
}

export function publishHistorySummaryForTask(taskId) {
  const state = get('SELECT schema_version, status FROM history_summary_state WHERE id = 1');
  if (Number(state?.schema_version || 0) !== HISTORY_SUMMARY_SCHEMA_VERSION
    || String(state?.status || '') !== 'complete') {
    return { published: false, reason: 'materialization_not_ready' };
  }
  const task = get('SELECT id, action, mode, created_at FROM promo_tasks WHERE id = ?', [Number(taskId)]);
  if (!task) return { published: false, reason: 'task_not_found' };
  const settings = readSettings();
  const rows = fetchTaskSummaryRows(null, settings, {
    action: task.action,
    day: String(task.created_at || '').slice(0, 10)
  });
  const summaries = buildLegacyTaskSummaries(rows, rows.length || 1, {
    includeDetails: false,
    targetTaskId: Number(taskId)
  });
  const summary = summaries.find((row) => (row.task_ids || [row.id]).map(Number).includes(Number(taskId)));
  if (!summary) return { published: false, reason: 'summary_not_found' };
  const taskIds = materializedTaskIds(summary);
  const overlappingKeys = all(
    'SELECT summary_key, task_ids_json FROM history_batch_summaries WHERE schema_version = ?',
    [HISTORY_SUMMARY_SCHEMA_VERSION]
  ).filter((row) => parseTaskIds(row.task_ids_json).some((id) => taskIds.includes(id)))
    .map((row) => row.summary_key);
  for (const key of overlappingKeys) {
    run('DELETE FROM history_batch_summaries WHERE summary_key = ?', [key]);
  }
  writeMaterializedSummary(summary);
  const stamp = taskSummaryStamp();
  run(
    `UPDATE history_summary_state SET
       task_count = ?, task_max_id = ?, result_count = ?, result_max_id = ?, completed_at = ?
     WHERE id = 1`,
    [stamp.task_count, stamp.task_max_id, stamp.result_count, stamp.result_max_id, nowIso()]
  );
  return { published: true, summary_key: materializedSummaryKey(summary) };
}

export function publishHistorySummaryForExecutionGroup(executionGroupId) {
  const groupId = String(executionGroupId || '');
  if (!groupId) return { published: false, reason: 'execution_group_required' };
  const state = get('SELECT schema_version, status FROM history_summary_state WHERE id = 1');
  if (Number(state?.schema_version || 0) !== HISTORY_SUMMARY_SCHEMA_VERSION
    || String(state?.status || '') !== 'complete') {
    return { published: false, reason: 'materialization_not_ready' };
  }
  const settings = readSettings();
  const rows = fetchTaskSummaryRows(null, settings, { executionGroupId: groupId });
  if (!rows.length) return { published: false, reason: 'group_tasks_not_found' };
  const summary = buildExecutionGroupSummaryRow(groupId, rows, { includeDetails: false, skipActionResults: false });
  transaction(() => {
    run('DELETE FROM history_batch_summaries WHERE summary_key = ?', [`v${HISTORY_SUMMARY_SCHEMA_VERSION}:execution-group:${groupId}`]);
    writeMaterializedSummary(summary);
    const stamp = taskSummaryStamp();
    run(
      `UPDATE history_summary_state SET
         task_count = ?, task_max_id = ?, result_count = ?, result_max_id = ?, completed_at = ?
       WHERE id = 1`,
      [stamp.task_count, stamp.task_max_id, stamp.result_count, stamp.result_max_id, nowIso()]
    );
  });
  return { published: true, summary_key: materializedSummaryKey(summary) };
}

function writeMaterializedSummary(summary) {
  const taskIds = materializedTaskIds(summary);
  run(
    `INSERT OR REPLACE INTO history_batch_summaries
      (summary_key, schema_version, summary_id, action, mode, status, sort_created_at,
       sort_updated_at, task_ids_json, data_json, published_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      materializedSummaryKey(summary),
      HISTORY_SUMMARY_SCHEMA_VERSION,
      Number(summary.id || Math.max(0, ...taskIds)),
      String(summary.action || ''),
      String(summary.mode || ''),
      String(summary.status || ''),
      String(summary.created_at || summary.updated_at || ''),
      String(summary.updated_at || summary.created_at || ''),
      JSON.stringify(taskIds),
      JSON.stringify(summary),
      nowIso()
    ]
  );
}

function materializedTaskIds(summary) {
  return [...new Set((summary.task_ids || [summary.id])
    .map((id) => Number(id))
    .filter((id) => Number.isInteger(id) && id > 0))].sort((a, b) => a - b);
}

function materializedSummaryKey(summary) {
  if (summary.execution_group_id) {
    return `v${HISTORY_SUMMARY_SCHEMA_VERSION}:execution-group:${summary.execution_group_id}`;
  }
  return `v${HISTORY_SUMMARY_SCHEMA_VERSION}:${materializedTaskIds(summary).join(',')}`;
}

function parseTaskIds(value) {
  try {
    const parsed = JSON.parse(value || '[]');
    return Array.isArray(parsed) ? parsed.map(Number).filter(Number.isFinite) : [];
  } catch {
    return [];
  }
}

export function listGlobalDiscountExecutionSummaries(limit = 300) {
  const settings = readSettings();
  const requested = Math.max(Number(limit) || TASK_SUMMARY_CANONICAL_LIMIT, 1);
  const summaries = ['enroll', 'update'].flatMap((action) => {
    const rows = fetchTaskSummaryRows(Math.max(requested * 24, 480), settings, { action, mode: 'real' });
    return buildLegacyTaskSummaries(rows, requested, { includeDetails: false, skipActionResults: true });
  });
  return summaries
    .sort((a, b) => Date.parse(b.updated_at || b.created_at || 0) - Date.parse(a.updated_at || a.created_at || 0))
    .slice(0, requested);
}

function fetchTaskSummaryRows(fetchLimit, settings, filters = {}) {
  const where = [];
  const params = [];
  if (filters.action) {
    where.push('t.action = ?');
    params.push(String(filters.action));
  }
  if (filters.mode) {
    where.push('t.mode = ?');
    params.push(String(filters.mode));
  }
  if (filters.day) {
    where.push("substr(t.created_at, 1, 10) = ?");
    params.push(String(filters.day));
  }
  if (filters.executionGroupId) {
    where.push('t.execution_group_id = ?');
    params.push(String(filters.executionGroupId));
  }
  const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';
  const limitSql = Number.isFinite(Number(fetchLimit)) && Number(fetchLimit) > 0 ? 'LIMIT ?' : '';
  if (limitSql) params.push(Number(fetchLimit));
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
     ${whereSql}
     ORDER BY t.id DESC ${limitSql}`,
    params
  ).map((row) => ({
    ...row,
    store_name: storeNameForAccount({ accountId: row.account_id, rawDisplayName: row.account_display_name, storeAliases: settings.storeAliases }),
    site_name: siteDisplayName(row.site_id)
  }));
}

export function buildLegacyTaskSummaries(rows = [], limit = 300, options = {}) {
  const includeDetails = options.includeDetails !== false;
  const skipActionResults = options.skipActionResults === true;
  const targetTaskId = Number(options.targetTaskId || 0);
  const ordered = [...rows].sort((a, b) => Number(b.id || 0) - Number(a.id || 0));
  const explicitGroups = new Map();
  for (const row of ordered) {
    const groupId = String(row.execution_group_id || '');
    if (!groupId) continue;
    const bucket = explicitGroups.get(groupId) || [];
    bucket.push(row);
    explicitGroups.set(groupId, bucket);
  }
  const explicitSummaries = [...explicitGroups.entries()].map(([groupId, groupRows]) =>
    buildExecutionGroupSummaryRow(groupId, groupRows, { includeDetails, skipActionResults }));
  const legacyOrdered = ordered.filter((row) => !row.execution_group_id);
  if (targetTaskId) {
    const explicit = explicitSummaries.find((row) => (row.task_ids || []).map(Number).includes(targetTaskId));
    if (explicit) return [explicit];
  }
  const batchRows = legacyOrdered.filter(isBatchTaskRow);
  if (!batchRows.length) {
    const standalone = targetTaskId
      ? legacyOrdered.filter((row) => Number(row.id || 0) === targetTaskId)
      : legacyOrdered;
    return [...explicitSummaries, ...standalone.map((row) => decorateTaskSummaryRow(row, { skipActionResults }))]
      .sort((a, b) => dateMs(b.created_at) - dateMs(a.created_at) || Number(b.id || 0) - Number(a.id || 0))
      .slice(0, limit);
  }

  const nonBatchRows = legacyOrdered.filter((row) => !isBatchTaskRow(row));
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

  if (targetTaskId) {
    const targetGroup = groups.find((group) => groupTaskIds(group).includes(targetTaskId));
    if (targetGroup) return [buildBatchSummaryRow(targetGroup, { includeDetails, skipActionResults })];
    const targetRow = ordered.find((row) => Number(row.id || 0) === targetTaskId);
    return targetRow ? [decorateTaskSummaryRow(targetRow, { skipActionResults })] : [];
  }

  const coveredTaskIds = new Set();
  const summaryRows = groups.map((group) => {
    const row = buildBatchSummaryRow(group, { includeDetails, skipActionResults });
    for (const id of row.task_ids || []) coveredTaskIds.add(Number(id));
    return row;
  });
  const uncovered = nonBatchRows.filter((row) => !coveredTaskIds.has(Number(row.id || 0)));
  const outputRows = [...explicitSummaries, ...summaryRows, ...uncovered];
  return outputRows
    .sort((a, b) => dateMs(b.created_at) - dateMs(a.created_at) || Number(b.id || 0) - Number(a.id || 0))
    .slice(0, limit)
    .map((row) => decorateTaskSummaryRow(row, { skipActionResults }));
}

function buildExecutionGroupSummaryRow(groupId, rows, options = {}) {
  const batchRows = rows.filter(isBatchTaskRow);
  const details = rows.filter((row) => !isBatchTaskRow(row));
  if (!batchRows.length) {
    const first = [...details].sort((a, b) => Number(b.id || 0) - Number(a.id || 0))[0] || {};
    return { ...decorateTaskSummaryRow(first, options), execution_group_id: groupId };
  }
  const windows = batchRows.map((batch) => {
    const accountDetails = details.filter((row) => String(row.account_id || '') === String(batch.account_id || ''));
    const times = [batch, ...accountDetails].map((row) => dateMs(row.created_at)).filter(Number.isFinite);
    return {
      batch,
      details: accountDetails,
      startMs: times.length ? Math.min(...times) : dateMs(batch.created_at),
      endMs: times.length ? Math.max(...times) : dateMs(batch.created_at),
    };
  });
  const summary = buildBatchSummaryRow({
    windows,
    startMs: Math.min(...windows.map((row) => row.startMs)),
    endMs: Math.max(...windows.map((row) => row.endMs)),
  }, options);
  return { ...summary, execution_group_id: groupId };
}

function groupTaskIds(group) {
  return [...new Set([
    ...(group.windows || []).flatMap((window) => [window.batch, ...(window.details || [])]),
    ...(group.orphanDetails || []),
    ...(group.coverageOnlyDetails || [])
  ].map((row) => Number(row?.id || 0)).filter(Boolean))];
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
    store_name: storeNameForAccount({ accountId: row.account_id, rawDisplayName: row.account_display_name, storeAliases: settings.storeAliases }),
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
  const skipActionResults = options.skipActionResults === true;
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
  const uniqueWriteSummary = skipActionResults
    ? emptyUniqueActionSummary(false)
    : summarizeUniqueActionResultsForTaskIds(summaryDetails.map((row) => row.id));
  const contractTaskIds = summaryDetails
    .filter((row) => Number(parseSummary(row.summary_json).result_contract_version || 0) >= RESULT_CONTRACT_VERSION)
    .map((row) => row.id);
  const contractSummary = skipActionResults || !contractTaskIds.length
    ? null
    : summarizeResultContractForTaskIds(contractTaskIds);
  const useUniqueWriteSummary = uniqueWriteSummary.hasRows && uniqueWriteSummary.total > 0;
  const failureReasons = useUniqueWriteSummary
    ? uniqueWriteSummary.failure_reasons
    : (skipActionResults ? [] : summarizeFailureReasonsForTaskIds(summaryDetails.map((row) => row.id)));
  const skippedReasons = useUniqueWriteSummary
    ? uniqueWriteSummary.skipped_reasons
    : (skipActionResults ? [] : summarizeSkippedReasonsForTaskIds(summaryDetails.map((row) => row.id)));
  const fallbackSkippedReasons = mergeSkippedReasons(batchRows.flatMap((row) => parseSummary(row.summary_json).skipped_reasons || []));
  const topSkippedReasons = skippedReasons.length ? skippedReasons : fallbackSkippedReasons;
  const fallbackReasons = mergeFailureReasons(batchRows.flatMap((row) => parseSummary(row.summary_json).failure_reasons || []));
  const topReasons = failureReasons.length ? failureReasons : fallbackReasons;
  const primaryOrphanDetails = orphanDetails.filter((row) => String(row.mode || '') === primaryMode);
  const latestUpdatedAt = latestTaskTime([...batchRows, ...summaryDetails]);
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
  if (contractSummary) {
    countedSummary.success = contractSummary.success;
    countedSummary.failed = contractSummary.failed;
    countedSummary.skipped = contractSummary.skipped;
    countedSummary.planned = contractSummary.relation_count;
    countedSummary.total = contractSummary.relation_count;
  }
  const platformPendingTotal = Number(contractSummary?.platform_pending_count
    ?? sumSummary(batchRows, 'platform_pending_count')
    ?? 0);
  const processedTotal = countedSummary.success + countedSummary.failed + countedSummary.skipped + platformPendingTotal;
  const candidatePoolTotal = Math.max(rawTotal, countedSummary.total, processedTotal);
  const isCancelAction = String(first.action || '') === 'cancel';
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
    seller_activity_text: isCancelAction ? '' : discountText(summaryDetails.filter(isSellerCampaignTask).map((row) => row.discount_percent)),
    official_activity_text: isCancelAction ? '' : discountText(summaryDetails.filter((row) => !isSellerCampaignTask(row)).map((row) => row.discount_percent))
  };
  Object.assign(summary, contractSummary || {
    relation_count: null,
    unique_item_count: null,
    activity_failure_count: null,
    request_success_count: null,
    live_verified_removed_count: null,
    pending_verification_count: null,
    platform_pending_count: null,
    retryable_pending_count: null,
  });
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
    updated_at: latestUpdatedAt || first.updated_at || first.created_at,
    status: deriveBatchStatus([...batchRows, ...summaryDetails]),
    total_count: String(first.action || '') === 'enroll' ? summary.enrolled_count : summary.total,
    success_count: String(first.action || '') === 'enroll' ? summary.enrolled_count : summary.success,
    failed_count: summary.failed,
    skipped_count: summary.skipped,
    empty_count: sum(batchRows, 'empty_count'),
    completed: [...batchRows, ...summaryDetails].every((row) => Number(row.completed || 0) === 1) ? 1 : 0,
    summary_json: JSON.stringify(summary),
    short_failure_reason: shortFailureReason(topReasons, summary.skipped, summary.blocked, summary.failed, topSkippedReasons),
    full_failure_reasons: fullFailureReasonRows(topReasons),
    planned_count: summary.planned,
    blocked_count: summary.blocked,
    promotions_total: summary.promotions_total,
    relation_count: summary.relation_count,
    unique_item_count: summary.unique_item_count,
    activity_failure_count: summary.activity_failure_count,
    request_success_count: summary.request_success_count,
    live_verified_removed_count: summary.live_verified_removed_count,
    pending_verification_count: summary.pending_verification_count,
    platform_pending_count: summary.platform_pending_count,
    retryable_pending_count: summary.retryable_pending_count,
    seller_activity_text: summary.seller_activity_text,
    official_activity_text: summary.official_activity_text,
    detail_count: details.length,
    ...(includeDetails ? { details: details.map(taskDetail) } : {})
  };
}

function latestTaskTime(rows) {
  const latest = rows
    .map((row) => row?.updated_at || row?.created_at)
    .filter((value) => Number.isFinite(dateMs(value)))
    .sort((a, b) => dateMs(b) - dateMs(a))[0];
  return latest || null;
}

function decorateTaskSummaryRow(row, options = {}) {
  const skipActionResults = options.skipActionResults === true;
  const summary = parseSummary(row.summary_json);
  const reasons = mergeFailureReasons(summary.failure_reasons || []);
  const skippedReasons = summary.skipped_reasons
    || (skipActionResults ? [] : summarizeSkippedReasonsForTaskIds([row.id]));
  return {
    ...row,
    relation_count: row.relation_count ?? summary.relation_count ?? null,
    unique_item_count: row.unique_item_count ?? summary.unique_item_count ?? null,
    activity_failure_count: row.activity_failure_count ?? summary.activity_failure_count ?? null,
    request_success_count: row.request_success_count ?? summary.request_success_count ?? null,
    live_verified_removed_count: row.live_verified_removed_count ?? summary.live_verified_removed_count ?? null,
    pending_verification_count: row.pending_verification_count ?? summary.pending_verification_count ?? null,
    platform_pending_count: row.platform_pending_count ?? summary.platform_pending_count ?? null,
    retryable_pending_count: row.retryable_pending_count ?? summary.retryable_pending_count ?? null,
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
    relation_count: summary.relation_count ?? null,
    unique_item_count: summary.unique_item_count ?? null,
    activity_failure_count: summary.activity_failure_count ?? null,
    request_success_count: summary.request_success_count ?? null,
    live_verified_removed_count: summary.live_verified_removed_count ?? null,
    pending_verification_count: summary.pending_verification_count ?? null,
    platform_pending_count: summary.platform_pending_count ?? null,
    retryable_pending_count: summary.retryable_pending_count ?? null,
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

function summarizeResultContractForTaskIds(taskIds = []) {
  const ids = [...new Set(taskIds.map((id) => Number(id)).filter((id) => Number.isInteger(id) && id > 0))];
  if (!ids.length) return null;
  const placeholders = ids.map(() => '?').join(',');
  const rows = all(
    `SELECT account_id, promotion_id, promotion_type, item_id, action, status, error_cn, error_raw, response_json
     FROM promo_action_results
     WHERE task_id IN (${placeholders})
       AND status IN ('success', 'failed', 'skipped', 'activity_failed', 'request_success', 'live_verified_removed', 'live_still_started', 'pending_verification')
     ORDER BY id ASC`,
    ids,
  );
  return summarizeResultContractRows(rows);
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
        skippedReasons.push({ ...classifySkippedReason(row.error_raw || row.response_json || row.error_cn, { action: row.action }), count });
      } else if (status === 'failed') {
        summary.failed += count;
        failureReasons.push({ ...classifyFailureReason(row.error_raw || row.response_json || row.error_cn, { action: row.action }), count });
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
      skippedReasons.push({ ...classifySkippedReason(row.error_raw || row.response_json || row.error_cn, { action: row.action }), count: 1 });
    } else {
      summary.failed += 1;
      failureReasons.push({ ...classifyFailureReason(row.error_raw || row.response_json || row.error_cn, { action: row.action }), count: 1 });
    }
  }
  summary.failure_reasons = mergeFailureReasons(failureReasons);
  summary.skipped_reasons = mergeSkippedReasons(skippedReasons);
  return summary;
}

function nonItemResultCount(row = {}) {
  const text = [row.error_cn, row.error_raw, row.response_json].filter(Boolean).join(' ');
  const patterns = [
    /平台还有\s*(\d+)\s*个已报名商品未返回明细/,
    /还有\s*(\d+)\s*个已报名商品/,
    /(\d+)\s*个已报名商品未返回明细/,
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
  if (text === '已报名商品明细不完整') return '已报名明细不完整';
  if (text === '折扣比例不符合要求') return '折扣比例';
  return text.replace(/\s+/g, '').slice(0, 8);
}

function fullFailureReasonName(reason = '') {
  const text = String(reason || '').trim();
  if (text === 'SMART未发送') return 'SMART未参与批量报名';
  if (text === 'LIGHTNING未发送') return 'LIGHTNING未参与批量报名';
  return text;
}

function classifySkippedReason(reason, context = {}) {
  const text = cleanFailureText(reason);
  const raw = text.toLowerCase();
  const action = String(context.action || '').toLowerCase();
  if (/未开始的商品.*留待下次继续|未开始的商品已跳过|执行任务已停止/.test(raw)) {
    return { reason: '未执行待继续' };
  }
  if (/当前活动价已等于目标价|已是目标价格|already matches target/.test(raw)) {
    return { reason: '已是目标价格' };
  }
  if (/高于最高允许价|低于最低允许价|超出平台范围|min_discounted_price|max_discounted_price|price range/.test(raw)) {
    return { reason: '活动价超出平台范围' };
  }
  if (/已报名商品明细不完整|已报名商品未返回明细|started 商品未返回明细/.test(raw)) {
    return { reason: '已报名商品明细不完整' };
  }
  if (/候选明细不完整|候选未返回明细/.test(raw)) {
    return { reason: action === 'cancel' ? '已报名商品明细不完整' : '候选明细不完整' };
  }
  if (/无可处理|未读取到/.test(raw)) {
    return { reason: '无可处理商品' };
  }
  return { reason: '其他跳过' };
}

export function classifyFailureReason(reason, context = {}) {
  const text = cleanFailureText(reason);
  const raw = text.toLowerCase();
  const action = String(context.action || '').toLowerCase();
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
    return { reason: '请求参数不符合平台要求', sent_to_api: true, suggestion: '重新读取该活动商品后再处理；仍失败则检查活动报价参数' };
  }
  if (/credible|discounted price is not credible|折扣价不被平台认可/.test(raw)) {
    return { reason: '折扣价不被平台认可', sent_to_api: true, suggestion: '调整折扣或价格边界后重跑' };
  }
  if (/minimum_discount_percent|discount final price must be more than/.test(raw)) {
    return { reason: '折扣比例不符合要求', sent_to_api: true, suggestion: '提高折扣幅度或按平台要求调整活动价' };
  }
  if (/offer|活动报价/.test(raw)) {
    return { reason: '缺少活动报价ID', sent_to_api: true, suggestion: '重新读取该活动商品和活动报价后再处理' };
  }
  if (/candidate 商品未读取到|无可处理商品/.test(raw)) {
    return { reason: '未读取到可处理候选商品', sent_to_api: false, suggestion: '先刷新活动商品或检查筛选范围' };
  }
  if (/平台还有.*已报名商品未返回明细|已报名商品明细不完整|已报名商品未返回明细|started 商品未返回明细/.test(raw)) {
    return { reason: '已报名商品明细不完整', sent_to_api: false, suggestion: '重新读取该活动已报名商品；若仍无明细，说明平台未返回可取消商品明细' };
  }
  if (/平台还有.*候选未返回明细|候选未返回明细|候选明细不完整/.test(raw)) {
    if (action === 'cancel') {
      return { reason: '已报名商品明细不完整', sent_to_api: false, suggestion: '重新读取该活动已报名商品；若仍无明细，说明平台未返回可取消商品明细' };
    }
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
