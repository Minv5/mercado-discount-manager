import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

import { createBalancedReadScheduler } from '../src/balancedReadScheduler.js';
import {
  createLowConcurrencyMarketplaceClient,
  runRouteCatalogResume,
  writeJsonAtomicWindows,
  writeTextAtomicWindows,
} from './route-catalog-node-runner.mjs';

const root = process.cwd();
const manifestPath = path.join(root, 'data/validation-evidence/route-catalog-first-page-cursor-20260825-v2/manifest.json');
const base = path.join(root, 'data/validation-evidence/route-catalog-remaining-pages-20260825-v2');
const checkpointPath = path.join(base, 'checkpoint-recovered.json');
const pageDir = path.join(base, 'pages');
const outDir = path.join(root, 'data/validation-evidence/route-catalog-resume-20260825-v6');
const tokenPaths = {
  '2651442567': 'C:/Users/dztf6/Documents/美客多授权/mercado_oauth_token.json',
  '3332096437': 'C:/Users/dztf6/Documents/美客多授权/accounts/3332096437/mercado_oauth_token.json',
  '3408885754': 'C:/Users/dztf6/Documents/美客多授权/accounts/3408885754/mercado_oauth_token.json',
};
const manifestHash = '718FE3BC2CD00B342565B434737EE2F932086D6DFC892F37CE9D0122D79B7DDD';
const inputCheckpointHash = '2AE4EB208436D93763AAF9F982ECDCFAF412F44987C2A321E0CB585C84561ECD';
const accounts = Object.keys(tokenPaths);
const sha256 = (value) => crypto.createHash('sha256').update(value).digest('hex').toUpperCase();
const exists = async (file) => fs.access(file).then(() => true, () => false);
const readJson = async (file) => JSON.parse((await fs.readFile(file, 'utf8')).replace(/^\uFEFF/, ''));

const manifest = await readJson(manifestPath);
const checkpoint = await readJson(checkpointPath);
if (sha256(await fs.readFile(manifestPath)) !== manifestHash) throw new Error('manifest hash mismatch');
if (sha256(await fs.readFile(checkpointPath)) !== inputCheckpointHash) throw new Error('checkpoint hash mismatch');
if (Number(checkpoint.processed_gets) !== 226) throw new Error('safe checkpoint drift');
if (await exists(outDir)) throw new Error('v6 output exists; refusing overwrite');

const routeStates = checkpoint.route_states;
const routeByHash = new Map(manifest.routes.map((route) => [String(route.route_hash), route]));
const identitySets = new Map(routeStates.map((state) => [String(state.route_hash), new Set()]));
for (const state of routeStates) {
  const frozen = routeByHash.get(String(state.route_hash));
  for (const item of frozen?.first_page_identity_records || []) {
    const id = String(item.id || item.item_id || item.marketplace_item_id || '').trim();
    if (id) identitySets.get(String(state.route_hash)).add(id);
  }
}
for (const file of await fs.readdir(pageDir)) {
  if (!file.endsWith('.jsonl')) continue;
  const lines = (await fs.readFile(path.join(pageDir, file), 'utf8')).split(/\r?\n/).filter(Boolean);
  for (const line of lines) {
    const record = JSON.parse(line);
    if (record.route_hash && record.local_item_id) identitySets.get(String(record.route_hash))?.add(String(record.local_item_id));
  }
}
for (const state of routeStates) {
  if (identitySets.get(String(state.route_hash)).size !== Number(state.materialized_unique_count)) throw new Error('identity map/checkpoint mismatch');
}
for (const accountId of accounts) {
  const token = await readJson(tokenPaths[accountId]);
  if (String(token.user_id) !== accountId || !token.access_token || Date.parse(token.expires_at) <= Date.now()) throw new Error('token preflight failed');
}

await fs.mkdir(path.join(outDir, 'pages'), { recursive: true });
const scheduler = createBalancedReadScheduler({ initialLimit: 1, maxLimit: 2, perAccountLimit: 1, activityLimit: 1, successesPerIncrease: 10000 });
const clients = new Map();
const clientFactory = async (state) => {
  const accountId = String(state.account_id);
  if (!clients.has(accountId)) {
    const token = await readJson(tokenPaths[accountId]);
    clients.set(accountId, createLowConcurrencyMarketplaceClient({ accessToken: token.access_token, userId: state.child_user_id, callerId: state.child_user_id, readAccountId: accountId, scheduler }));
  }
  return clients.get(accountId);
};
const routeIndex = new Map(routeStates.map((state, index) => [state, index + 1]));
const writePage = async ({ route, pageNumber, records }) => {
  const index = String(routeIndex.get(route)).padStart(2, '0');
  const file = path.join(pageDir, `${index}-${String(pageNumber).padStart(3, '0')}.jsonl`);
  const text = records.map((record) => JSON.stringify(record)).join('\n') + '\n';
  await writeTextAtomicWindows(file, text);
  return { sha256: sha256(text) };
};
const writeSidecar = async ({ route, pageNumber, recordCount, nextCursor, pageHash }) => {
  const index = String(routeIndex.get(route)).padStart(2, '0');
  await writeJsonAtomicWindows(path.join(pageDir, `${index}-${String(pageNumber).padStart(3, '0')}.meta.json`), {
    schema_version: 1, route_hash: route.route_hash, page: pageNumber, record_count: recordCount, page_sha256: pageHash, next_cursor: nextCursor, observed_at: new Date().toISOString(),
  });
};
const writeCheckpoint = async ({ routeStates: states, successPages, retryState }) => writeJsonAtomicWindows(checkpointPath, {
  schema_version: 6, formal_batch_id: 'route-catalog-node-resume-20260825-v6', manifest_hash: manifestHash, source_checkpoint_hash: inputCheckpointHash,
  prior_safe_processed_gets: 226, resume_success_pages: successPages, processed_gets: 226 + successPages, retry_attempts_used: retryState.retries, attempts_total: retryState.attempts,
  success_ceiling: 1479, attempt_ceiling: 1499, route_states: states,
});

const result = await runRouteCatalogResume({
  routeStates,
  clientFactory,
  writePage,
  writeSidecar,
  writeCheckpoint,
  identitySets,
  retryReserve: 20,
  attemptCeiling: 1499,
  successPageCeiling: 1479,
  initialSuccessPages: 0,
  onProgress: async (progress) => process.stdout.write(`${JSON.stringify({ success_pages: progress.successPages, retry_attempts_used: progress.retryAttempts, attempts: progress.attempts })}\n`),
});
if (result.status !== 'complete') throw new Error('runner interrupted');

const snapshotPath = path.join(outDir, 'identity-snapshot.jsonl');
const records = new Map(routeStates.map((state) => [String(state.route_hash), new Map()]));
for (const state of routeStates) {
  const frozen = routeByHash.get(String(state.route_hash));
  for (const item of frozen?.first_page_identity_records || []) {
    const fields = Object.fromEntries(Object.entries(item).filter(([key]) => ['id', 'item_id', 'global_item_id', 'marketplace_item_id', 'parent_item_id', 'cbt_item_id', 'catalog_id', 'catalog_item_id', 'global_id', 'status', 'seller_id', 'site_id'].includes(key)));
    const id = String(fields.id || fields.item_id || fields.marketplace_item_id || '').trim();
    if (id) records.get(String(state.route_hash)).set(id, { route_hash: state.route_hash, account_id: state.account_id, child_identity_hash: state.child_identity_hash, site_id: state.site_id, local_item_id: id, identity_fields: fields, observed_at: item.observed_at || null, revision: item.revision || null });
  }
}
for (const file of await fs.readdir(pageDir)) {
  if (!file.endsWith('.jsonl')) continue;
  for (const line of (await fs.readFile(path.join(pageDir, file), 'utf8')).split(/\r?\n/).filter(Boolean)) {
    const record = JSON.parse(line);
    if (record.route_hash && record.local_item_id) records.get(String(record.route_hash))?.set(String(record.local_item_id), record);
  }
}
let snapshotText = '';
for (const values of records.values()) for (const record of values.values()) snapshotText += `${JSON.stringify(record)}\n`;
await fs.writeFile(snapshotPath, snapshotText, 'utf8');
const summary = { schema_version: 6, formal_batch_id: 'route-catalog-node-resume-20260825-v6', manifest_hash: manifestHash, input_checkpoint_hash: inputCheckpointHash, success_pages: result.successPages, get_success_count: 226 + result.successPages, retry_attempts_used: result.retryState.retries, attempts_total: result.retryState.attempts, success_ceiling: 1479, attempt_ceiling: 1499, hard_ceiling: 1705, nonGET: 0, route_count: 18, total_expected: 85616, total_materialized_unique: [...records.values()].reduce((sum, map) => sum + map.size, 0), snapshot_path: snapshotPath, raw_body_saved: false, raw_body_output: false };
for (const state of routeStates) {
  const account = summary.per_account ||= {};
  const value = account[state.account_id] ||= { routes: 0, total: 0, materialized_unique: 0, pages: 0 };
  value.routes += 1; value.total += Number(state.total); value.materialized_unique += Number(state.materialized_unique_count); value.pages += Number(state.pages_completed);
}
await writeJsonAtomicWindows(path.join(outDir, 'summary.json'), summary);
  process.stdout.write(`${JSON.stringify({ success_pages: result.successPages, retry_attempts_used: result.retryState.retries, attempts_total: result.retryState.attempts, get_success_count: summary.get_success_count, total_materialized_unique: summary.total_materialized_unique, summary_path: path.join(outDir, 'summary.json'), snapshot_path: snapshotPath })}\n`);
