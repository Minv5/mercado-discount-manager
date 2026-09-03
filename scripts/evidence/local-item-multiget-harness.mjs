import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_API_BASE_URL = 'https://api.mercadolibre.com';
const EXPECTED_SAMPLE_COUNT = 3;
const MULTIGET_BATCH_LIMIT = 20;

function safeNumber(value) {
  return Number.isFinite(Number(value)) ? Number(value) : null;
}

function safeFieldPresence(body) {
  const source = body && typeof body === 'object' && !Array.isArray(body) ? body : null;
  return Object.fromEntries(['id', 'seller_id', 'site_id', 'status', 'price']
    .map((field) => [field, Boolean(source && Object.hasOwn(source, field) && source[field] !== null)]));
}

function normalizeExpected(expected = {}) {
  return {
    child_user_id: String(expected.child_user_id || expected.childUserId || '').trim(),
    site_id: String(expected.site_id || expected.siteId || '').trim().toUpperCase(),
  };
}

function validateSample(sample) {
  if (!sample || typeof sample !== 'object' || Array.isArray(sample)) throw new TypeError('sample must be an object');
  if (!String(sample.account_mask || '').trim()) throw new TypeError('sample account_mask is required');
  if (!String(sample.item_id || '').trim()) throw new TypeError('sample item_id is required');
  const expected = normalizeExpected(sample.expected);
  if (!expected.child_user_id || !expected.site_id) throw new TypeError('sample expected route is required');
  return { expected };
}

export function buildItemMultigetUrl(baseUrl, itemId) {
  const url = new URL('/items', String(baseUrl || DEFAULT_API_BASE_URL));
  url.searchParams.set('ids', String(itemId || '').trim());
  return url.toString();
}

export function buildDryRunPlan(samples = []) {
  if (!Array.isArray(samples) || samples.length !== EXPECTED_SAMPLE_COUNT) {
    return {
      ok: false,
      status: 'invalid_plan',
      reason_code: 'EXPECTED_THREE_SAMPLES',
      planned_gets: 0,
      nonGET: 0,
      oauth: 0,
    };
  }
  try {
    samples.forEach(validateSample);
    return {
      ok: true,
      status: 'dry_run',
      endpoint_family: 'global_items_multiget',
      method: 'GET',
      planned_gets: EXPECTED_SAMPLE_COUNT,
      batch_limit: MULTIGET_BATCH_LIMIT,
      nonGET: 0,
      oauth: 0,
    };
  } catch (error) {
    return {
      ok: false,
      status: 'invalid_plan',
      reason_code: 'INVALID_SAMPLE',
      planned_gets: 0,
      nonGET: 0,
      oauth: 0,
    };
  }
}

function summarizePayload({ httpStatus, payload, expected, itemId }) {
  const rows = Array.isArray(payload) ? payload : [];
  const row = rows.length === 1 && rows[0] && typeof rows[0] === 'object' ? rows[0] : null;
  const body = row?.body && typeof row.body === 'object' && !Array.isArray(row.body) ? row.body : null;
  const fieldPresence = safeFieldPresence(body);
  const itemIdMatch = Boolean(fieldPresence.id && String(body.id) === String(itemId));
  const sellerMatch = Boolean(fieldPresence.seller_id && String(body.seller_id) === expected.child_user_id);
  const siteMatch = Boolean(fieldPresence.site_id && String(body.site_id).trim().toUpperCase() === expected.site_id);
  const outerCode = safeNumber(row?.code);
  const ok = httpStatus >= 200 && httpStatus < 300 && outerCode === 200 && itemIdMatch && sellerMatch && siteMatch;
  return {
    ok,
    http_status: Number.isInteger(httpStatus) ? httpStatus : null,
    outer_code: outerCode,
    response_array_length: Array.isArray(payload) ? payload.length : null,
    body_field_presence: fieldPresence,
    item_id_match: itemIdMatch,
    seller_site_match: sellerMatch && siteMatch,
    identity_match: itemIdMatch && sellerMatch && siteMatch,
    reason_code: ok ? null : 'OUTER_CODE_OR_IDENTITY_MISMATCH',
  };
}

async function readRuntimeToken(tokenPath, expectedUserId) {
  try {
    const text = await fs.readFile(String(tokenPath), 'utf8');
    const token = JSON.parse(text);
    const expiresAt = token?.expires_at ? new Date(token.expires_at).getTime() : NaN;
    const valid = String(token?.user_id || '') === String(expectedUserId || '')
      && Boolean(token?.access_token)
      && Boolean(token?.refresh_token)
      && Number.isFinite(expiresAt)
      && expiresAt - Date.now() > 60 * 60 * 1000;
    if (!valid) return { ok: false, reason_code: 'TOKEN_PRECHECK_FAILED' };
    return { ok: true, accessToken: String(token.access_token) };
  } catch {
    return { ok: false, reason_code: 'TOKEN_READ_FAILED' };
  }
}

async function requestOne({ baseUrl, token, itemId, expected, fetchImpl = globalThis.fetch, timeoutMs = 45_000 }) {
  if (typeof fetchImpl !== 'function') return { ok: false, http_status: null, outer_code: null, reason_code: 'FETCH_UNAVAILABLE' };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(buildItemMultigetUrl(baseUrl, itemId), {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
      redirect: 'error',
      signal: controller.signal,
    });
    const text = await response.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      return { ok: false, http_status: response.status, outer_code: null, reason_code: 'JSON_PARSE_ERROR' };
    }
    return summarizePayload({ httpStatus: response.status, payload, expected, itemId });
  } catch (error) {
    return {
      ok: false,
      http_status: null,
      outer_code: null,
      reason_code: error?.name === 'AbortError' ? 'TIMEOUT' : 'TRANSPORT_OR_REDIRECT_BLOCKED',
    };
  } finally {
    clearTimeout(timer);
  }
}

export async function runSamples({ samples = [], baseUrl = DEFAULT_API_BASE_URL, fetchImpl = globalThis.fetch, tokenLoader = readRuntimeToken } = {}) {
  const plan = buildDryRunPlan(samples);
  if (!plan.ok) return { ...plan, get_count: 0, nonGET: 0, oauth: 0, results: [] };
  const results = [];
  let getCount = 0;
  for (const sample of samples) {
    const { expected } = validateSample(sample);
    const tokenResult = await tokenLoader(sample.token_path, sample.expected_user_id);
    if (!tokenResult?.ok) {
      results.push({ ok: false, account_mask: String(sample.account_mask), route_label: String(sample.route_label || ''), reason_code: tokenResult?.reason_code || 'TOKEN_PRECHECK_FAILED' });
      break;
    }
    getCount += 1;
    const result = await requestOne({ baseUrl, token: tokenResult.accessToken, itemId: sample.item_id, expected, fetchImpl });
    results.push({ ok: result.ok, account_mask: String(sample.account_mask), route_label: String(sample.route_label || ''), ...result });
    if (!result.ok) break;
  }
  return {
    ok: results.length === EXPECTED_SAMPLE_COUNT && results.every((result) => result.ok),
    status: results.every((result) => result.ok) && results.length === EXPECTED_SAMPLE_COUNT ? 'completed' : 'stopped_on_failure',
    endpoint_family: 'global_items_multiget',
    get_count: getCount,
    nonGET: 0,
    oauth: 0,
    results,
  };
}

export function isCliEntry(moduleUrl = import.meta.url, argv1 = process.argv[1]) {
  if (!argv1) return false;
  try {
    return path.resolve(fileURLToPath(moduleUrl)) === path.resolve(argv1);
  } catch {
    return false;
  }
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
  const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  const args = new Set(process.argv.slice(2));
  if (args.has('--dry-run')) {
    process.stdout.write(`${JSON.stringify(buildDryRunPlan(input.samples || []))}\n`);
    return;
  }
  if (!args.has('--execute')) {
    process.stdout.write(`${JSON.stringify({ ok: false, status: 'invalid_mode', reason_code: 'USE_DRY_RUN_OR_EXECUTE' })}\n`);
    return;
  }
  const result = await runSamples({ samples: input.samples || [], baseUrl: input.base_url || DEFAULT_API_BASE_URL });
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (isCliEntry()) main().catch(() => process.stdout.write(`${JSON.stringify({ ok: false, status: 'harness_error', reason_code: 'SAFE_HARNESS_ERROR' })}\n`));
