import fs from 'node:fs';

import { resolveCbtItemRoutes } from '../src/activityWebhookConsumer.js';

const SAFE_CODES = new Set([
  'OFFICIAL_SELLER_MISSING',
  'OFFICIAL_SELLER_AMBIGUOUS',
  'OFFICIAL_SITE_MISSING',
  'OFFICIAL_SITE_AMBIGUOUS',
  'MARKETPLACE_ITEM_MISSING',
  'ROUTE_AMBIGUOUS',
  'ROUTE_NOT_OWNED',
  'ROUTE_CATALOG_GAP',
  'RESOURCE_STATUS_UNUSABLE',
  'CBT_ROUTE_UNRESOLVED',
]);

function safeCode(value) {
  const code = String(value || '').trim().toUpperCase();
  return SAFE_CODES.has(code) ? code : 'CBT_RESOLVER_ERROR';
}

function safeError(error) {
  const code = safeCode(error?.diagnostic_code || error?.code || error?.cause_code);
  return {
    kind: error?.name === 'TypeError' ? 'type' : 'resolver',
    code,
    reason_cn: code === 'CBT_RESOLVER_ERROR' ? 'CBT 资源纯函数解析失败，未写入或继续读取。' : 'CBT 资源未能确认经营路由，已安全隔离。',
  };
}

function present(value) {
  return value !== undefined && value !== null && value !== '';
}

function countSignals(children, field) {
  return (Array.isArray(children) ? children : [])
    .filter((row) => row && typeof row === 'object' && present(row[field])).length;
}

function uniqueSignals(children, field) {
  return new Set((Array.isArray(children) ? children : [])
    .filter((row) => row && typeof row === 'object' && present(row[field]))
    .map((row) => String(row[field]))).size;
}

function fallbackCategory(result, diagnostics = {}) {
  if (Number(result?.targets?.length || 0) > 0) return 'exact_owned';
  if (Number(diagnostics.unusable_skipped || 0) > 0) return 'terminal_irrelevant';
  if (Number(diagnostics.foreign_skipped || 0) > 0
    && !Number(diagnostics.unmatched_skipped || 0)
    && !Number(diagnostics.ambiguous_skipped || 0)) return 'terminal_foreign';
  const codes = Array.isArray(diagnostics.codes) ? diagnostics.codes : [];
  if (codes.some((row) => String(row?.code || '').toUpperCase() === 'ROUTE_CATALOG_GAP')) return 'route_catalog_gap';
  return 'quarantined_unknown';
}

function childMappingSemantics(resource) {
  const fields = ['site_items', 'marketplace_items'];
  const presence = Object.fromEntries(fields.map((field) => [field, Object.hasOwn(resource, field)]));
  const presentArrays = fields.filter((field) => presence[field] && Array.isArray(resource[field]));
  const malformed = fields.some((field) => presence[field] && !Array.isArray(resource[field]));
  return {
    field_presence: presence,
    present_array_count: presentArrays.length,
    malformed_present_field: malformed,
    present_empty: presentArrays.length > 0 && !malformed && presentArrays.every((field) => resource[field].length === 0),
  };
}

function responseHttpStatus(input) {
  const value = Number(input?.http_status);
  return Number.isInteger(value) ? value : 200;
}

function validateInput(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new TypeError('input must be an object');
  if (!input.event || typeof input.event !== 'object') throw new TypeError('event is required');
  if (!/^\/items\/CBT[A-Za-z0-9._:-]+(?:[?#].*)?$/.test(String(input.event.resource || ''))) throw new TypeError('CBT resource is required');
  if (!input.resource || typeof input.resource !== 'object' || Array.isArray(input.resource)) throw new TypeError('resource must be an object');
  if (!Array.isArray(input.routes) || !Array.isArray(input.accounts)) throw new TypeError('routes and accounts must be arrays');
}

function run(input) {
  try {
    validateInput(input);
    const resource = input.resource;
    const mapping = childMappingSemantics(resource);
    const httpStatus = responseHttpStatus(input);
    const siteItems = Array.isArray(resource.site_items) ? resource.site_items : [];
    const marketplaceItems = Array.isArray(resource.marketplace_items) ? resource.marketplace_items : [];
    const children = [...siteItems, ...marketplaceItems];
    const result = resolveCbtItemRoutes({
      event: {
        schema_version: '2',
        event_id: 'offline-targeted-resolver-harness',
        application_id: 'offline-harness',
        ...input.event,
      },
      resourceData: resource,
      marketplaceSites: input.routes,
      accounts: input.accounts,
    });
    const diagnostics = result?.diagnostics || {};
    const code = safeCode(diagnostics.codes?.[0]?.code);
    const category = httpStatus === 200
      && Number(result?.targets?.length || 0) === 0
      && mapping.present_empty
      ? 'terminal_no_marketplace_children'
      : fallbackCategory(result, diagnostics);
    return {
      ok: true,
      http_status: httpStatus,
      root_fields_present: Object.fromEntries(['seller_id', 'site_id', 'status', 'site_items', 'marketplace_items'].map((key) => [key, Object.hasOwn(resource, key)])),
      site_items_length: siteItems.length,
      marketplace_items_length: marketplaceItems.length,
      child_seller_present_count: countSignals(children, 'seller_id'),
      child_site_present_count: countSignals(children, 'site_id'),
      child_status_present_count: countSignals(children, 'status'),
      child_seller_unique_count: uniqueSignals(children, 'seller_id'),
      child_site_unique_count: uniqueSignals(children, 'site_id'),
      child_status_unique_count: uniqueSignals(children, 'status'),
      child_mapping_field_presence: mapping.field_presence,
      child_mapping_present_empty: mapping.present_empty,
      child_mapping_malformed_present_field: mapping.malformed_present_field,
      resolver_code: code === 'CBT_RESOLVER_ERROR' ? null : code,
      route_candidate_count: Number(diagnostics.target_count || 0),
      target_count: Number(result?.targets?.length || 0),
      exact_owned_route: Number(result?.targets?.length || 0) > 0,
      targeted_fallback_classification: category,
      foreign_skipped: Number(diagnostics.foreign_skipped || 0),
      unmatched_skipped: Number(diagnostics.unmatched_skipped || 0),
      unusable_skipped: Number(diagnostics.unusable_skipped || 0),
      ambiguous_skipped: Number(diagnostics.ambiguous_skipped || 0),
    };
  } catch (error) {
    return { ok: false, error: safeError(error) };
  }
}

let input = '';
try {
  input = fs.readFileSync(0, 'utf8');
  const parsed = JSON.parse(input);
  process.stdout.write(`${JSON.stringify(run(parsed))}\n`);
} catch (error) {
  process.stdout.write(`${JSON.stringify({ ok: false, error: safeError(error) })}\n`);
}
