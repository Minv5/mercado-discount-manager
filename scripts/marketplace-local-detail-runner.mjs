import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  createLowConcurrencyMarketplaceClient,
  writeJsonAtomicWindows,
} from './route-catalog-node-runner.mjs';
import { createBalancedReadScheduler } from '../src/balancedReadScheduler.js';

const ROOT = process.cwd();
const EVIDENCE_DIR = path.join(ROOT, 'data/validation-evidence/marketplace-local-detail-full-20260825-v1');
const MANIFEST_PATH = path.join(EVIDENCE_DIR, 'manifest.json');
const PLAN_PATH = path.join(EVIDENCE_DIR, 'plan.jsonl');
const CHECKPOINT_PATH = path.join(EVIDENCE_DIR, 'checkpoint.json');
const RESULTS_PATH = path.join(EVIDENCE_DIR, 'results.jsonl');
const FAILURE_PATH = path.join(EVIDENCE_DIR, 'failure.json');
const RETRY_RESERVE = 20;
const MAX_ATTEMPTS_PER_ITEM = 3;
const LINKAGE_FIELDS = [
  'parent_id', 'parent_user_id', 'parent_user_product_id', 'global_item_id',
  'global_item', 'cbt_item_id', 'global_listing_id', 'global_user_id',
];
const ROOT_FIELDS = ['id', 'seller_id', 'site_id', 'status', 'price'];
const TOKEN_PATHS = {
  '2651442567': 'C:/Users/dztf6/Documents/美客多授权/mercado_oauth_token.json',
  '3332096437': 'C:/Users/dztf6/Documents/美客多授权/accounts/3332096437/mercado_oauth_token.json',
  '3408885754': 'C:/Users/dztf6/Documents/美客多授权/accounts/3408885754/mercado_oauth_token.json',
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sha256 = (value) => crypto.createHash('sha256').update(value).digest('hex').toUpperCase();
const readJson = async (file) => JSON.parse((await fs.readFile(file, 'utf8')).replace(/^\uFEFF/, ''));
const exists = async (file) => fs.access(file).then(() => true, () => false);

function statusOf(error) {
  return Number(error?.status || error?.statusCode || error?.response?.status || 0);
}

function transient(error) {
  const status = statusOf(error);
  const code = String(error?.code || error?.cause?.code || '').toUpperCase();
  const message = String(error?.message || '').toLowerCase();
  return status === 429 || (status >= 500 && status <= 599)
    || /ETIMEDOUT|UND_ERR|ECONNRESET|ECONNREFUSED|EAI_AGAIN|SOCKET|EOF/.test(code)
    || /timeout|fetch failed|network|socket|eof/.test(message);
}

function retryAfterMs(error) {
  const direct = Number(error?.retryAfterMs ?? error?.retry_after_ms);
  if (Number.isFinite(direct) && direct >= 0) return Math.min(direct, 120_000);
  const seconds = Number(error?.retryAfter ?? error?.retry_after);
  return Number.isFinite(seconds) && seconds >= 0 ? Math.min(seconds * 1_000, 120_000) : 0;
}

function safePresence(value, field) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value)
    && Object.hasOwn(value, field) && value[field] !== null);
}

function summarizeDetail(body, item) {
  const root = body && typeof body === 'object' && !Array.isArray(body) ? body : null;
  const rootFieldPresence = Object.fromEntries(ROOT_FIELDS.map((field) => [field, safePresence(root, field)]));
  const linkageFieldPresence = Object.fromEntries(LINKAGE_FIELDS.map((field) => [field, safePresence(root, field)]));
  const itemIdMatch = Boolean(rootFieldPresence.id && String(root.id) === String(item.item_id));
  const sellerMatch = Boolean(rootFieldPresence.seller_id && String(root.seller_id) === String(item.child_user_id));
  const siteMatch = Boolean(rootFieldPresence.site_id && String(root.site_id).trim().toUpperCase() === String(item.site_id).toUpperCase());
  return {
    root_field_presence: rootFieldPresence,
    linkage_field_presence: linkageFieldPresence,
    linkage_field_count: Object.values(linkageFieldPresence).filter(Boolean).length,
    item_id_match: itemIdMatch,
    seller_site_match: sellerMatch && siteMatch,
    identity_match: itemIdMatch && sellerMatch && siteMatch,
  };
}

async function readToken(accountId) {
  const token = await readJson(TOKEN_PATHS[String(accountId)]);
  const expiresAt = Date.parse(String(token?.expires_at || ''));
  if (String(token?.user_id || '') !== String(accountId)
    || !token?.access_token || !token?.refresh_token
    || !Number.isFinite(expiresAt) || expiresAt - Date.now() <= 60 * 60 * 1_000) {
    throw Object.assign(new Error('token preflight failed'), { code: 'TOKEN_PREFLIGHT_FAILED' });
  }
  return String(token.access_token);
}

async function readPlan() {
  const manifest = await readJson(MANIFEST_PATH);
  const planText = await fs.readFile(PLAN_PATH, 'utf8');
  if (sha256(planText) !== String(manifest.plan_sha256 || '').toUpperCase()) {
    throw Object.assign(new Error('detail plan hash mismatch'), { code: 'DETAIL_PLAN_HASH_MISMATCH' });
  }
  const plan = planText.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  if (plan.length !== Number(manifest.planned_single_gets)) {
    throw Object.assign(new Error('detail plan count mismatch'), { code: 'DETAIL_PLAN_COUNT_MISMATCH' });
  }
  for (let index = 0; index < plan.length; index += 1) {
    if (Number(plan[index].index) !== index) throw Object.assign(new Error('detail plan index mismatch'), { code: 'DETAIL_PLAN_INDEX_MISMATCH' });
  }
  return { manifest, plan };
}

async function fetchDetailWithRetry({ client, item, state }) {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS_PER_ITEM; attempt += 1) {
    if (state.attempts >= state.attemptCeiling) throw Object.assign(new Error('detail attempt ceiling exhausted'), { code: 'DETAIL_ATTEMPT_CEILING' });
    state.attempts += 1;
    try {
      const body = await client.getMarketplaceItem(item.item_id);
      return { body, httpStatus: 200 };
    } catch (error) {
      if (!transient(error) || attempt >= MAX_ATTEMPTS_PER_ITEM || state.retries >= RETRY_RESERVE) throw error;
      state.retries += 1;
      await sleep(retryAfterMs(error) || 250 * (2 ** (attempt - 1)));
    }
  }
  throw Object.assign(new Error('detail retry exhausted'), { code: 'DETAIL_RETRY_EXHAUSTED' });
}

async function checkpointWrite(value) {
  await writeJsonAtomicWindows(CHECKPOINT_PATH, value);
}

async function main() {
  const { manifest, plan } = await readPlan();
  const checkpoint = await readJson(CHECKPOINT_PATH);
  if (String(checkpoint.plan_sha256 || '').toUpperCase() !== String(manifest.plan_sha256).toUpperCase()) {
    throw Object.assign(new Error('checkpoint plan hash mismatch'), { code: 'DETAIL_CHECKPOINT_HASH_MISMATCH' });
  }
  const startIndex = Number(checkpoint.next_index || 0);
  const state = {
    attempts: Number(checkpoint.attempts_total || 0),
    retries: Number(checkpoint.retry_attempts_used || 0),
    attemptCeiling: Number(manifest.attempt_ceiling),
  };
  if (startIndex < 0 || startIndex > plan.length) throw Object.assign(new Error('checkpoint index invalid'), { code: 'DETAIL_CHECKPOINT_INVALID' });
  const resultHandle = await fs.open(RESULTS_PATH, 'a+');
  const clients = new Map();
  const tokens = new Map();
  const scheduler = createBalancedReadScheduler({
    initialLimit: 1,
    maxLimit: 2,
    perAccountLimit: 1,
    activityLimit: 1,
    activityPerAccountLimit: 1,
    successesPerIncrease: 10_000,
  });
  try {
    for (const accountId of Object.keys(TOKEN_PATHS)) tokens.set(accountId, await readToken(accountId));
    for (let index = startIndex; index < plan.length; index += 1) {
      const item = plan[index];
      if (!clients.has(item.account_id)) {
        clients.set(item.account_id, createLowConcurrencyMarketplaceClient({
          accessToken: tokens.get(item.account_id),
          userId: item.child_user_id,
          callerId: item.child_user_id,
          readAccountId: item.account_id,
          scheduler,
        }));
      }
      let fetched;
      try {
        fetched = await fetchDetailWithRetry({ client: clients.get(item.account_id), item, state });
      } catch (error) {
        const failure = {
          schema_version: 1,
          status: 'stopped_on_failure',
          failed_index: index,
          route_label: item.route_label,
          http_status: statusOf(error) || null,
          error_kind: transient(error) ? 'transient_exhausted' : 'non_transient',
          retry_attempts_used: state.retries,
          attempts_total: state.attempts,
          nonGET: 0,
          oauth: 0,
        };
        await writeJsonAtomicWindows(FAILURE_PATH, failure);
        throw Object.assign(new Error('detail full batch stopped on GET failure'), { code: 'DETAIL_FORMAL_FAILURE' });
      }
      const summary = summarizeDetail(fetched.body, item);
      if (!summary.identity_match) {
        const failure = {
          schema_version: 1,
          status: 'stopped_on_identity_mismatch',
          failed_index: index,
          route_label: item.route_label,
          http_status: fetched.httpStatus,
          ...summary,
          retry_attempts_used: state.retries,
          attempts_total: state.attempts,
          nonGET: 0,
          oauth: 0,
        };
        await writeJsonAtomicWindows(FAILURE_PATH, failure);
        throw Object.assign(new Error('detail full batch stopped on identity mismatch'), { code: 'DETAIL_IDENTITY_MISMATCH' });
      }
      const record = {
        schema_version: 1,
        index,
        route_label: item.route_label,
        route_hash: item.route_hash,
        account_id: item.account_id,
        child_user_id: item.child_user_id,
        site_id: item.site_id,
        item_id: item.item_id,
        http_status: fetched.httpStatus,
        ...summary,
      };
      await resultHandle.write(`${JSON.stringify(record)}\n`, 'utf8');
      await resultHandle.sync();
      await checkpointWrite({
        schema_version: 1,
        formal_batch_id: manifest.formal_batch_id,
        plan_sha256: manifest.plan_sha256,
        next_index: index + 1,
        success_count: index + 1,
        attempts_total: state.attempts,
        retry_attempts_used: state.retries,
        status: index + 1 === plan.length ? 'complete' : 'running',
      });
      if ((index + 1) % 100 === 0 || index + 1 === plan.length) {
        process.stdout.write(`${JSON.stringify({ processed: index + 1, total: plan.length, succeeded: index + 1, retry_attempts_used: state.retries, attempts_total: state.attempts })}\n`);
      }
    }
    await checkpointWrite({
      schema_version: 1,
      formal_batch_id: manifest.formal_batch_id,
      plan_sha256: manifest.plan_sha256,
      next_index: plan.length,
      success_count: plan.length,
      attempts_total: state.attempts,
      retry_attempts_used: state.retries,
      status: 'complete',
      nonGET: 0,
      oauth: 0,
    });
    process.stdout.write(`${JSON.stringify({ status: 'complete', processed: plan.length, succeeded: plan.length, retry_attempts_used: state.retries, attempts_total: state.attempts, nonGET: 0, oauth: 0 })}\n`);
  } finally {
    await resultHandle.close();
  }
}

if (path.resolve(fileURLToPath(import.meta.url)) === path.resolve(process.argv[1] || '')) {
  main().catch((error) => {
    if (error?.code === 'DETAIL_FORMAL_FAILURE' || error?.code === 'DETAIL_IDENTITY_MISMATCH') process.exitCode = 1;
    else process.exitCode = 1;
  });
}
