import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

import { MercadoLibreClient } from '../src/mlClient.js';
import { createBalancedReadScheduler } from '../src/balancedReadScheduler.js';

export const ROUTE_CATALOG_PAGE_LIMIT = 50;
export const ROUTE_CATALOG_DEFAULT_RETRY_RESERVE = 20;

const LOCAL_RENAME_RETRY_CODES = new Set(['EPERM', 'EACCES', 'EBUSY']);

export async function writeTextAtomicWindows(target, text, {
  rename = fs.rename,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  retries = 4,
} = {}) {
  const targetPath = path.resolve(String(target));
  await fs.mkdir(path.dirname(targetPath), { recursive: true });
  const token = crypto.randomUUID().replaceAll('-', '');
  const temporary = `${targetPath}.${process.pid}.${token}.tmp`;
  const journal = `${targetPath}.journal`;
  const backup = `${targetPath}.bak`;
  const data = String(text);
  const bytes = Buffer.byteLength(data, 'utf8');
  const journalValue = {
    target: targetPath,
    temporary,
    payload_sha256: crypto.createHash('sha256').update(data, 'utf8').digest('hex').toUpperCase(),
    payload_bytes: bytes,
    written_at: new Date().toISOString(),
  };
  const handle = await fs.open(temporary, 'w');
  try {
    await handle.writeFile(data, 'utf8');
    await handle.sync();
  } finally {
    await handle.close();
  }
  const journalHandle = await fs.open(journal, 'w');
  try {
    await journalHandle.writeFile(JSON.stringify(journalValue, null, 2), 'utf8');
    await journalHandle.sync();
  } finally {
    await journalHandle.close();
  }
  if (await exists(targetPath)) {
    await fs.copyFile(targetPath, backup);
  }
  let replaced = false;
  try {
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      try {
        await rename(temporary, targetPath);
        replaced = true;
        break;
      } catch (error) {
        if (!LOCAL_RENAME_RETRY_CODES.has(String(error?.code || '').toUpperCase()) || attempt >= retries) throw error;
        await sleep(50 * (2 ** attempt));
      }
    }
  } finally {
    if (replaced) await fs.rm(journal, { force: true });
  }
  return { target: targetPath, backup: (await exists(backup)) ? backup : null, journal: replaced ? null : journal, replaced };
}

async function exists(file) {
  try { await fs.access(file); return true; } catch { return false; }
}

export async function writeJsonAtomicWindows(target, value, options = {}) {
  return writeTextAtomicWindows(target, JSON.stringify(value, null, 2), options);
}

function statusOf(error) {
  return Number(error?.status || error?.statusCode || error?.response?.status || 0);
}

function transient(error) {
  const status = statusOf(error);
  const code = String(error?.code || error?.cause?.code || '').toUpperCase();
  const message = String(error?.message || '').toLowerCase();
  return status === 429 || (status >= 500 && status <= 599)
    || /ETIMEDOUT|UND_ERR|ECONNRESET|ECONNREFUSED|EAI_AGAIN|SOCKET/.test(code)
    || /timeout|fetch failed|network|socket|eof/.test(message);
}

function retryAfterMs(error) {
  const direct = Number(error?.retryAfterMs ?? error?.retry_after_ms);
  if (Number.isFinite(direct) && direct >= 0) return direct;
  const seconds = Number(error?.retryAfter ?? error?.retry_after);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds * 1_000 : 0;
}

export function createLowConcurrencyMarketplaceClient({
  accessToken,
  userId,
  callerId = userId,
  apiBaseUrl,
  scheduler = null,
  readAccountId = null,
} = {}) {
  const balanced = scheduler || createBalancedReadScheduler({
    initialLimit: 1,
    maxLimit: 2,
    perAccountLimit: 1,
    activityLimit: 1,
    activityPerAccountLimit: 1,
    successesPerIncrease: 10_000,
  });
  // The existing mlClient and scheduler remain authoritative; this adapter
  // disables their internal retry layer so the runner owns one global cap.
  const noRetryScheduler = {
    schedule: (options, task) => balanced.schedule({ ...options, retry: false }, task),
  };
  return new MercadoLibreClient({
    accessToken,
    userId,
    callerId,
    apiBaseUrl,
    marketplace: true,
    readScheduler: noRetryScheduler,
    readAccountId: readAccountId || callerId || userId,
  });
}

export function pageIdentityRecords(page, route, observedAt = new Date().toISOString()) {
  const results = Array.isArray(page?.results) ? page.results : null;
  if (!results) throw Object.assign(new Error('catalog page results must be an array'), { code: 'CATALOG_RESULTS_INVALID' });
  const allowed = new Set([
    'id', 'item_id', 'global_item_id', 'marketplace_item_id', 'parent_item_id', 'cbt_item_id',
    'catalog_id', 'catalog_item_id', 'global_id', 'status', 'seller_id', 'site_id',
  ]);
  return results.map((item) => {
    const identityFields = item && typeof item === 'object'
      ? Object.fromEntries(Object.entries(item).filter(([key, value]) => allowed.has(key)
        && ['string', 'number', 'boolean'].includes(typeof value)))
      : { id: String(item || '') };
    const localItemId = String(identityFields.id || identityFields.item_id || identityFields.marketplace_item_id || '').trim();
    if (!localItemId) throw Object.assign(new Error('catalog page item identity missing'), { code: 'CATALOG_IDENTITY_INVALID' });
    return {
      route_hash: String(route.route_hash || ''),
      account_id: String(route.account_id || ''),
      child_identity_hash: String(route.child_identity_hash || ''),
      site_id: String(route.site_id || '').toUpperCase(),
      local_item_id: localItemId,
      identity_fields: identityFields,
      observed_at: observedAt,
    };
  });
}

async function fetchWithRetry({ fetchPage, retryState, retryReserve, maxAttempts, sleep }) {
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    if (retryState.attempts >= retryState.attemptCeiling) {
      throw Object.assign(new Error('catalog runner attempt ceiling exhausted'), { code: 'CATALOG_ATTEMPT_CEILING' });
    }
    retryState.attempts += 1;
    try {
      return await fetchPage();
    } catch (error) {
      if (!transient(error) || attempt >= maxAttempts || retryState.retries >= retryReserve) throw error;
      retryState.retries += 1;
      const delay = retryAfterMs(error) || 250 * (2 ** (attempt - 1));
      await sleep(delay);
    }
  }
  throw new Error('catalog runner bounded retry exhausted');
}

async function writeCheckpointWithRetry(writeCheckpoint, value, sleep) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      return await writeCheckpoint(value);
    } catch (error) {
      const code = String(error?.code || '').toUpperCase();
      if (!['EPERM', 'EACCES', 'EBUSY'].includes(code) || attempt >= 3) throw error;
      await sleep(50 * (2 ** attempt));
    }
  }
  throw new Error('checkpoint write retry exhausted');
}

function extractNextCursor(page) {
  for (const key of ['scroll_id', 'scrollId', 'cursor', 'next_cursor', 'nextCursor']) {
    if (page?.[key] !== undefined && page?.[key] !== null && String(page[key])) return String(page[key]);
  }
  const paging = page?.paging && typeof page.paging === 'object' ? page.paging : {};
  for (const key of ['scroll_id', 'scrollId', 'cursor', 'next_cursor', 'nextCursor']) {
    if (paging[key] !== undefined && paging[key] !== null && String(paging[key])) return String(paging[key]);
  }
  return '';
}

export async function runRouteCatalogResume({
  routeStates,
  clientFactory,
  writePage,
  writeSidecar,
  writeCheckpoint,
  identitySets = new Map(),
  retryReserve = ROUTE_CATALOG_DEFAULT_RETRY_RESERVE,
  attemptCeiling = Number.MAX_SAFE_INTEGER,
  successPageCeiling = Number.MAX_SAFE_INTEGER,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  onProgress = null,
  comparePage = null,
  shouldStop = () => false,
  initialSuccessPages = 0,
} = {}) {
  if (!Array.isArray(routeStates) || typeof clientFactory !== 'function'
    || typeof writePage !== 'function' || typeof writeSidecar !== 'function' || typeof writeCheckpoint !== 'function') {
    throw new TypeError('route catalog runner requires states, client and checkpoint writers');
  }
  const retryState = { retries: 0, attempts: 0, attemptCeiling };
  let successPages = initialSuccessPages;
  const ensureSet = (routeHash) => {
    if (!identitySets.has(routeHash)) identitySets.set(routeHash, new Set());
    return identitySets.get(routeHash);
  };
  for (const state of routeStates) {
    const remaining = Math.max(0, Number(state.expected_remaining_pages || 0) - Number(state.pages_completed || 0));
    let cursor = String(state.next_cursor || '');
    if (remaining && !cursor) throw Object.assign(new Error('route cursor missing before resume'), { code: 'CATALOG_CURSOR_MISSING' });
    const client = remaining ? await clientFactory(state) : null;
    for (let offset = 0; offset < remaining; offset += 1) {
      if (shouldStop()) return { status: 'interrupted', successPages, retryState };
      if (successPages >= successPageCeiling) throw Object.assign(new Error('success page ceiling exhausted'), { code: 'CATALOG_SUCCESS_CEILING' });
      const pageNumber = Number(state.pages_completed || 0) + 1;
      const page = await fetchWithRetry({
        fetchPage: () => client.searchMarketplaceUserItems({
          userId: state.child_user_id,
          status: 'all',
          limit: ROUTE_CATALOG_PAGE_LIMIT,
          scrollId: cursor,
          searchType: 'scan',
        }),
        retryState,
        retryReserve,
        maxAttempts: 3,
        sleep,
      });
      const total = Number(page?.paging?.total ?? page?.total);
      const limit = Number(page?.paging?.limit ?? page?.limit ?? ROUTE_CATALOG_PAGE_LIMIT);
      if (!Number.isFinite(total) || total !== Number(state.total) || limit !== ROUTE_CATALOG_PAGE_LIMIT) {
        throw Object.assign(new Error('catalog page total/limit drift'), { code: 'CATALOG_PAGING_DRIFT' });
      }
      const records = pageIdentityRecords(page, state);
      if (typeof comparePage === 'function') await comparePage({ route: state, pageNumber, records, page });
      const nextCursor = extractNextCursor(page);
      if (!nextCursor) throw Object.assign(new Error('catalog next cursor missing'), { code: 'CATALOG_CURSOR_MISSING' });
      const set = ensureSet(String(state.route_hash));
      for (const record of records) set.add(record.local_item_id);
      const pageWriteResult = await writePage({ route: state, pageNumber, records });
      await writeSidecar({
        route: state,
        pageNumber,
        recordCount: records.length,
        nextCursor,
        pageHash: pageWriteResult?.sha256 || null,
      });
      state.pages_completed = pageNumber;
      state.materialized_unique_count = set.size;
      state.next_cursor = nextCursor;
      state.reconciled_page = pageNumber;
      state.complete = set.size >= Number(state.total);
      successPages += 1;
      await writeCheckpointWithRetry(writeCheckpoint, { routeStates, successPages, retryState }, sleep);
      if (successPages % 300 === 0 && typeof onProgress === 'function') {
        await onProgress({ successPages, retryAttempts: retryState.retries, attempts: retryState.attempts });
      }
      cursor = nextCursor;
    }
    if (Number(state.materialized_unique_count || 0) !== Number(state.total || 0)) {
      throw Object.assign(new Error('route unique materialization mismatch'), { code: 'CATALOG_UNIQUE_MISMATCH' });
    }
  }
  return { status: 'complete', successPages, retryState };
}
