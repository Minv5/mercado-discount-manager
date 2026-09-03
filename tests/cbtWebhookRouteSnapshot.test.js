import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  createActivityWebhookConsumer,
  createCbtItemRoutesResolver,
  createCbtItemRouteResolver,
  resolveCbtItemRoutes,
  resolveCbtItemRoute,
  resolveActivityWebhookRoute,
} from '../src/activityWebhookConsumer.js';
import {
  buildCbtReplayErrorDiagnostic,
  buildCbtWebhookReplayPlan,
  createCbtWebhookReplay,
} from '../src/activityWebhookReplay.js';
import { createPhysicalGetBudget, readMarketplaceItemWithBudget } from '../src/physicalGetBudget.js';

const ROUTES = [
  { account_id: '2651442567', child_user_id: '2659555001', site_id: 'MLB' },
  { account_id: '2651442567', child_user_id: '2659555002', site_id: 'MLM' },
  { account_id: '3332096437', child_user_id: '3333531550', site_id: 'MLB' },
];

function runIsolated(source) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-cbt-replay-'));
  try {
    return JSON.parse(execFileSync(process.execPath, ['--input-type=module', '--eval', source], {
      cwd: path.resolve(import.meta.dirname, '..'),
      env: { ...process.env, MDM_DATA_DIR: dataDir, MDM_DB_PATH: path.join(dataDir, 'cbt.sqlite') },
      encoding: 'utf8',
      timeout: 30_000,
    }));
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

function cbtEvent(overrides = {}) {
  return {
    schema_version: '2',
    event_id: 'evt-cbt-1',
    topic: 'items',
    resource: '/items/CBT6745693466',
    remote_user_id: '2651442567',
    application_id: 'APP-1',
    received_at: '2026-08-24T02:00:00.000Z',
    ...overrides,
  };
}

test('CBT parent notification uses official seller and site signals to select one route', () => {
  assert.throws(
    () => resolveActivityWebhookRoute({
      event: cbtEvent({ event_id: 'evt-cbt-requires-official-resource' }),
      marketplaceSites: [ROUTES[0]],
      accounts: [{ account_id: '2651442567' }],
    }),
    (error) => error?.code === 'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED',
  );
  const route = resolveCbtItemRoute({
    event: cbtEvent(),
    resourceData: { id: 'CBT6745693466', seller_id: '2659555001', site_id: 'MLB' },
    marketplaceSites: ROUTES,
    accounts: [{ account_id: '2651442567' }],
  });
  assert.deepEqual(route, ROUTES[0]);

  assert.throws(
    () => resolveCbtItemRoute({
      event: cbtEvent({ event_id: 'evt-cbt-missing-site' }),
      resourceData: { id: 'CBT6745693466', seller_id: '2659555001' },
      marketplaceSites: ROUTES,
      accounts: [{ account_id: '2651442567' }],
    }),
    (error) => error?.code === 'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED',
  );
  assert.throws(
    () => resolveCbtItemRoute({
      event: cbtEvent({ event_id: 'evt-cbt-cross-account' }),
      resourceData: { id: 'CBT6745693466', seller_id: '3333531550', site_id: 'MLB' },
      marketplaceSites: ROUTES,
      accounts: [{ account_id: '2651442567' }],
    }),
    (error) => error?.code === 'ACTIVITY_CALLBACK_ROUTE_MISMATCH',
  );
});

test('CBT documented site_items fields select the child route without trusting the parent seller/site', () => {
  const route = resolveCbtItemRoute({
    event: cbtEvent({ event_id: 'evt-cbt-site-items' }),
    resourceData: {
      id: 'CBT6745693466',
      seller_id: '2651442567',
      site_id: 'CBT',
      site_items: [{ item_id: 'MLB6745693466', seller_id: '2659555001', site_id: 'MLB', status: 'active' }],
    },
    marketplaceSites: ROUTES,
    accounts: [{ account_id: '2651442567' }],
  });
  assert.deepEqual(route, ROUTES[0]);
});

test('CBT exact-owned resolver expands every marketplace child route and isolates foreign/missing/deleted entries', () => {
  const routes = [
    ...ROUTES,
    { account_id: '2651442567', child_user_id: '2659555003', site_id: 'MLB' },
  ];
  const expansion = resolveCbtItemRoutes({
    event: cbtEvent({ event_id: 'evt-cbt-multiroute' }),
    resourceData: {
      id: 'CBT-MULTI', seller_id: '2651442567', site_id: 'CBT', status: 'active',
      marketplace_items: [
        { item_id: 'MLB-CHILD-1', seller_id: '2659555001', site_id: 'MLB', status: 'active' },
        { item_id: 'MLM-CHILD-2', seller_id: '2659555002', site_id: 'MLM', status: 'paused' },
        { item_id: 'MLB-FOREIGN', seller_id: '3333531550', site_id: 'MLB', status: 'active' },
        { item_id: 'MLB-MISSING', site_id: 'MLB', status: 'active' },
        { item_id: 'MLB-DELETED', seller_id: '2659555003', site_id: 'MLB', status: 'deleted' },
      ],
    },
    marketplaceSites: routes,
    accounts: [{ account_id: '2651442567' }],
  });
  assert.deepEqual(expansion.targets.map((target) => target.marketplace_item_id), ['MLB-CHILD-1', 'MLM-CHILD-2']);
  assert.equal(expansion.diagnostics.foreign_skipped, 1);
  assert.equal(expansion.diagnostics.missing_skipped, 1);
  assert.equal(expansion.diagnostics.unusable_skipped, 1);
  assert.equal(expansion.diagnostics.target_count, 2);
  assert.ok(expansion.diagnostics.codes.some((row) => row.code === 'ROUTE_NOT_OWNED'));
  assert.ok(expansion.diagnostics.codes.some((row) => row.code === 'OFFICIAL_SELLER_MISSING'));
  assert.ok(expansion.diagnostics.codes.some((row) => row.code === 'RESOURCE_STATUS_UNUSABLE'));
});

test('CBT resolver preserves documented 2/5/5 child fanout shapes per parent account', () => {
  const accountShapes = [
    { account: '2651442567', count: 2 },
    { account: '3332096437', count: 5 },
    { account: '3408885754', count: 5 },
  ];
  for (const shape of accountShapes) {
    const routes = Array.from({ length: shape.count }, (_, index) => ({ account_id: shape.account, child_user_id: `${shape.account}-child-${index}`, site_id: ['MLB', 'MLM', 'MLC', 'MCO', 'MLA'][index] }));
    const children = routes.map((route, index) => ({ item_id: `${route.site_id}-SYNTH-${index}`, seller_id: route.child_user_id, site_id: route.site_id, status: 'active' }));
    const expansion = resolveCbtItemRoutes({
      event: cbtEvent({ event_id: `evt-shape-${shape.account}`, remote_user_id: shape.account }),
      resourceData: { id: `CBT-SHAPE-${shape.account}`, seller_id: shape.account, site_id: 'CBT', marketplace_items: children },
      marketplaceSites: routes,
      accounts: [{ account_id: shape.account }],
    });
    assert.equal(expansion.targets.length, shape.count);
    assert.equal(expansion.diagnostics.target_count, shape.count);
  }
});

test('CBT resolver emits stable diagnostic codes and signal presence without exposing ids', () => {
  assert.throws(
    () => resolveCbtItemRoute({
      event: cbtEvent({ event_id: 'evt-cbt-seller-missing' }),
      resourceData: { id: 'CBT6745693466', seller_id: '2651442567', site_id: 'CBT', site_items: [{ item_id: 'MLB6745693466', site_id: 'MLB' }] },
      marketplaceSites: ROUTES,
      accounts: [{ account_id: '2651442567' }],
    }),
    (error) => error?.diagnostic_code === 'OFFICIAL_SELLER_MISSING'
      && error?.resolver_diagnostic?.signal_presence?.seller_signal_count === 0
      && error?.resolver_diagnostic?.signal_presence?.site_signal_count === 1,
  );
  assert.throws(
    () => resolveCbtItemRoute({
      event: cbtEvent({ event_id: 'evt-cbt-status-deleted' }),
      resourceData: { id: 'CBT6745693466', seller_id: '2651442567', site_id: 'CBT', status: 'deleted', site_items: [{ item_id: 'MLB6745693466', seller_id: '2659555001', site_id: 'MLB', status: 'deleted' }] },
      marketplaceSites: ROUTES,
      accounts: [{ account_id: '2651442567' }],
    }),
    (error) => error?.diagnostic_code === 'RESOURCE_STATUS_UNUSABLE'
      && error?.resolver_diagnostic?.signal_presence?.status_present === true,
  );
  assert.throws(
    () => resolveCbtItemRoute({
      event: cbtEvent({ event_id: 'evt-cbt-route-ambiguous' }),
      resourceData: { id: 'CBT6745693466', seller_id: '2651442567', site_id: 'CBT', site_items: [
        { item_id: 'MLB6745693466', seller_id: '2659555001', site_id: 'MLB', status: 'active' },
        { item_id: 'MLB6745693467', seller_id: '2659555002', site_id: 'MLB', status: 'active' },
      ] },
      marketplaceSites: ROUTES,
      accounts: [{ account_id: '2651442567' }],
    }),
    (error) => error?.diagnostic_code === 'OFFICIAL_SELLER_AMBIGUOUS',
  );
});

test('CBT item route resolver uses an explicitly injected client and performs one official GET', async () => {
  let clientFactoryCalls = 0;
  let officialGets = 0;
  const resourceData = { id: 'CBT-INJECTED', seller_id: '2659555001', site_id: 'MLB' };
  const resolver = createCbtItemRouteResolver({
    createResourceClient: async (route) => {
      clientFactoryCalls += 1;
      assert.deepEqual(route, { account_id: '2651442567', child_user_id: '', site_id: '' });
      return { getMarketplaceItem: async (itemId) => { officialGets += 1; assert.equal(itemId, 'CBT-INJECTED'); return resourceData; } };
    },
  });
  const result = await resolver({ event: cbtEvent({ resource: '/items/CBT-INJECTED' }), itemId: 'CBT-INJECTED', marketplaceSites: ROUTES, accounts: [{ account_id: '2651442567' }] });
  assert.deepEqual(result.route, ROUTES[0]);
  assert.equal(clientFactoryCalls, 1);
  assert.equal(officialGets, 1);
});

test('quarantined CBT rows enter a bounded targeted fallback plan without a full refresh or extra method', async () => {
  const event = {
    event_id: 'evt-targeted-fallback', topic: 'items', resource: '/items/CBT-TARGETED',
    remote_user_id: '2651442567', child_user_id: '', outcome: 'route_unresolved_skipped',
    received_at: '2026-08-24T03:00:00.000Z',
    last_error: JSON.stringify({ code: 'OFFICIAL_SITE_MISSING', classification: 'quarantined_unknown' }),
  };
  const replay = createCbtWebhookReplay({
    listEvents: async () => [event],
    countEvents: async () => ({
      event_count: 1, item_count: 1, eligible_event_count: 0, eligible_item_count: 0,
      classification_counts: { quarantined_unknown: 1 },
      classification_item_counts: { quarantined_unknown: 1 },
    }),
    claimEvent: async () => ({ status: 'claimed', claim_token: 'targeted-claim' }),
    consumeEvent: async () => { throw new Error('must not consume quarantined row'); },
    finalizeEvent: async () => {},
  });
  const result = await replay({ pageSize: 1, totalBudget: 1, shouldRetryEvent: () => false });
  assert.equal(result.targeted_fallback_planned, 1);
  assert.equal(result.physical_get_used, 0);
  assert.equal(result.physical_budget_exhausted, false);
});

test('CBT replay diagnostics are structured and omit raw body/token values', () => {
  const diagnostic = buildCbtReplayErrorDiagnostic(Object.assign(new Error('timeout Bearer SECRET-TOKEN'), {
    code: 'ETIMEDOUT',
    status: 504,
    body: { token: 'SECRET-TOKEN', raw: 'do-not-store' },
  }), { attemptCount: 2 });
  assert.deepEqual(diagnostic, {
    operation: 'cbt_webhook_replay',
    endpoint_family: 'marketplace_item_resource',
    error_kind: 'timeout',
    http_status: 504,
    code: 'ETIMEDOUT',
    cause_code: null,
    attempt_count: 2,
    reason_cn: 'timeout Bearer [REDACTED]',
  });
  assert.doesNotMatch(JSON.stringify(diagnostic), /SECRET-TOKEN|do-not-store/);
  assert.equal(buildCbtReplayErrorDiagnostic(new ReferenceError('createResourceClient is not defined')).error_kind, 'local_contract');
});

test('CBT item consumer writes a new item price cache route without inventing promo_items relations', async () => {
  const prices = [];
  const relations = [];
  let notificationReads = 0;
  const officialResource = {
    id: 'CBT6745693466',
    seller_id: '2659555001',
    site_id: 'MLB',
    price: 99,
    original_price: 120,
    currency_id: 'BRL',
    status: 'active',
  };
  const consumer = createActivityWebhookConsumer({
    listMarketplaceSites: () => ROUTES,
    listAccounts: () => [{ account_id: '2651442567' }],
    resolveItemRoute: async ({ event, marketplaceSites, accounts }) => ({
      route: resolveCbtItemRoute({ event, resourceData: officialResource, marketplaceSites, accounts }),
      resourceData: officialResource,
    }),
    createResourceClient: async () => ({
      getNotificationResource: async () => {
        notificationReads += 1;
        throw new Error('preloaded CBT resource should avoid a second read');
      },
    }),
    markDirty: () => assert.fail('new CBT item must not dirty activity catalog'),
    updateItemPrice: (value) => prices.push(value),
    updateItemRelations: (value) => relations.push(value),
  });

  const result = await consumer(cbtEvent());
  assert.equal(result.outcome, 'item_updated');
  assert.equal(notificationReads, 0);
  assert.deepEqual(prices, [{
    accountId: '2651442567',
    childUserId: '2659555001',
    siteId: 'MLB',
    itemId: 'CBT6745693466',
    price: 99,
    originalPrice: 120,
    status: 'active',
    raw: officialResource,
    observedAt: '2026-08-24T02:00:00.000Z',
    sourceRevision: '',
    confirmed: true,
  }]);
  assert.deepEqual(relations, []);
});

test('CBT consumer fans out exact child GETs and writes only child prices without promo relations', async () => {
  const parentEvent = cbtEvent({ event_id: 'evt-fanout-2', resource: '/items/CBT-FANOUT-2' });
  const parentResource = {
    id: 'CBT-FANOUT-2', seller_id: '2651442567', site_id: 'CBT', status: 'active',
    marketplace_items: [
      { item_id: 'MLB-CHILD-A', seller_id: '2659555001', site_id: 'MLB', status: 'active' },
      { item_id: 'MLM-CHILD-B', seller_id: '2659555002', site_id: 'MLM', status: 'active' },
    ],
  };
  const childGets = [];
  const prices = [];
  const relations = [];
  const consumer = createActivityWebhookConsumer({
    listMarketplaceSites: () => ROUTES,
    listAccounts: () => [{ account_id: '2651442567' }],
    resolveItemRoutes: async () => ({
      ...resolveCbtItemRoutes({ event: parentEvent, resourceData: parentResource, marketplaceSites: ROUTES, accounts: [{ account_id: '2651442567' }] }),
      resourceData: parentResource,
      parent_get_count: 1,
    }),
    createResourceClient: async (route) => ({
      getMarketplaceItem: async (itemId) => {
        childGets.push({ route: { account_id: route.account_id, child_user_id: route.child_user_id, site_id: route.site_id }, itemId });
        return itemId === 'MLB-CHILD-A'
          ? { id: itemId, seller_id: '2659555001', site_id: 'MLB', status: 'active', price: 101, original_price: 120 }
          : { id: itemId, seller_id: '2659555002', site_id: 'MLM', status: 'paused', price: 202, original_price: 240 };
      },
    }),
    updateItemPrice: (value) => prices.push(value),
    updateItemRelations: (value) => relations.push(value),
  });
  const result = await consumer(parentEvent);
  assert.equal(result.outcome, 'item_updated');
  assert.equal(result.route_target_count, 2);
  assert.equal(result.child_get_count, 2);
  assert.equal(result.cache_updated_count, 2);
  assert.deepEqual(childGets.map((row) => row.itemId), ['MLB-CHILD-A', 'MLM-CHILD-B']);
  assert.deepEqual(prices.map((row) => [row.itemId, row.siteId, row.price]), [['MLB-CHILD-A', 'MLB', 101], ['MLM-CHILD-B', 'MLM', 202]]);
  assert.deepEqual(relations, []);
});

test('CBT consumer reserves the parent and child GETs before fanout and leaves budget targets for resume', async () => {
  const parentEvent = cbtEvent({ event_id: 'evt-fanout-budget', resource: '/items/CBT-FANOUT-BUDGET' });
  const parentResource = {
    id: 'CBT-FANOUT-BUDGET', seller_id: '2651442567', site_id: 'CBT', status: 'active',
    marketplace_items: [
      { item_id: 'MLB-BUDGET-A', seller_id: '2659555001', site_id: 'MLB', status: 'active' },
      { item_id: 'MLM-BUDGET-B', seller_id: '2659555002', site_id: 'MLM', status: 'active' },
    ],
  };
  const reads = [];
  const prices = [];
  const createClient = async () => ({
    getMarketplaceItem: async (itemId) => {
      reads.push(itemId);
      if (itemId === 'CBT-FANOUT-BUDGET') return parentResource;
      return {
        id: itemId,
        seller_id: itemId === 'MLB-BUDGET-A' ? '2659555001' : '2659555002',
        site_id: itemId.startsWith('MLB') ? 'MLB' : 'MLM',
        status: 'active', price: 10,
      };
    },
  });
  const consumer = createActivityWebhookConsumer({
    listMarketplaceSites: () => ROUTES,
    listAccounts: () => [{ account_id: '2651442567' }],
    resolveItemRoutes: createCbtItemRoutesResolver({
      createResourceClient: createClient,
    }),
    createResourceClient: createClient,
    updateItemPrice: (value) => prices.push(value),
  });
  const budget = createPhysicalGetBudget(2);
  const result = await consumer(parentEvent, { physicalGetBudget: budget });
  const stats = budget.stats();
  assert.equal(result.outcome, 'partial');
  assert.deepEqual(reads, ['CBT-FANOUT-BUDGET', 'MLB-BUDGET-A']);
  assert.equal(stats.completed, 2);
  assert.equal(stats.completed <= stats.physical_budget, true);
  assert.equal(result.budget_remaining, true);
  assert.equal(result.fanout_success_count, 1);
  assert.equal(result.fanout_failed_count, 1);
  assert.equal(prices.length, 1);
});

test('CBT partial fanout retries only failed child targets and preserves prior success', async () => {
  const parentEvent = cbtEvent({ event_id: 'evt-fanout-partial', resource: '/items/CBT-FANOUT-PARTIAL' });
  const parentResource = {
    id: 'CBT-FANOUT-PARTIAL', seller_id: '2651442567', site_id: 'CBT', status: 'active',
    marketplace_items: [
      { item_id: 'MLB-CHILD-A', seller_id: '2659555001', site_id: 'MLB', status: 'active' },
      { item_id: 'MLM-CHILD-B', seller_id: '2659555002', site_id: 'MLM', status: 'active' },
    ],
  };
  const calls = [];
  const prices = [];
  let failB = true;
  const consumer = createActivityWebhookConsumer({
    listMarketplaceSites: () => ROUTES,
    listAccounts: () => [{ account_id: '2651442567' }],
    resolveItemRoutes: async () => ({
      ...resolveCbtItemRoutes({ event: parentEvent, resourceData: parentResource, marketplaceSites: ROUTES, accounts: [{ account_id: '2651442567' }] }),
      resourceData: parentResource,
      parent_get_count: 1,
    }),
    createResourceClient: async () => ({
      getMarketplaceItem: async (itemId) => {
        calls.push(itemId);
        if (itemId === 'MLM-CHILD-B' && failB) throw Object.assign(new Error('child timeout'), { code: 'ETIMEDOUT' });
        return { id: itemId, seller_id: itemId === 'MLB-CHILD-A' ? '2659555001' : '2659555002', site_id: itemId.startsWith('MLB') ? 'MLB' : 'MLM', status: 'active', price: itemId.startsWith('MLB') ? 101 : 202 };
      },
    }),
    updateItemPrice: (value) => prices.push(value),
  });
  const first = await consumer(parentEvent);
  assert.equal(first.outcome, 'partial');
  assert.equal(first.fanout_success_count, 1);
  assert.equal(first.fanout_failed_count, 1);
  assert.equal(prices.length, 1);
  failB = false;
  const second = await consumer({ ...parentEvent, fanout_success_keys: first.fanout_success_keys });
  assert.equal(second.outcome, 'item_updated');
  assert.deepEqual(calls, ['MLB-CHILD-A', 'MLM-CHILD-B', 'MLM-CHILD-B']);
  assert.equal(prices.length, 2);
});

test('bounded CBT compensation plan dedupes event ids and filters non-CBT history', () => {
  const events = Array.from({ length: 94 }, (_, index) => ({
    event_id: `evt-${index}`,
    topic: 'items',
    resource: `/items/CBT${index}`,
    remote_user_id: index % 3 === 0 ? '2651442567' : index % 3 === 1 ? '3332096437' : '3408885754',
    child_user_id: '',
    outcome: 'route_unresolved_skipped',
    received_at: `2026-08-24T00:${String(index).padStart(2, '0')}:00.000Z`,
  }));
  const plan = buildCbtWebhookReplayPlan([...events, events[0], {
    event_id: 'ignore-public',
    topic: 'public_offers',
    resource: '/items/CBT-not-an-item',
    outcome: 'route_unresolved_skipped',
  }]);
  assert.equal(plan.length, 94);
  assert.equal(new Set(plan.map((event) => event.event_id)).size, 94);
  assert.equal(plan[0].event_id, 'evt-0');
});

test('CBT compensation walks 257 events across three stable cursor pages and keeps duplicate resources idempotent', async () => {
  const events = Array.from({ length: 257 }, (_, index) => ({
    event_id: `evt-page-${String(index).padStart(3, '0')}`,
    topic: 'items',
    resource: `/items/CBT${index % 237}`,
    remote_user_id: '2651442567',
    child_user_id: '',
    outcome: 'route_unresolved_skipped',
    received_at: `2026-08-24T00:${String(Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}.000Z`,
  }));
  const cursors = [];
  const sideEffects = new Set();
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit, afterReceivedAt = null, afterEventId = null }) => {
      if (afterReceivedAt) cursors.push({ afterReceivedAt, afterEventId });
      const eligible = events.filter((event) => !afterReceivedAt
        || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId));
      return eligible.slice(0, limit);
    },
    countEvents: async ({ afterReceivedAt = null, afterEventId = null }) => {
      const remaining = events.filter((event) => !afterReceivedAt
        || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId));
      return { event_count: remaining.length, item_count: new Set(remaining.map((event) => event.resource)).size };
    },
    claimEvent: async () => ({ status: 'claimed', claim_token: 'page-claim' }),
    consumeEvent: async (event) => {
      sideEffects.add(event.resource);
      return { outcome: 'item_updated', account_id: '2651442567', child_user_id: '2659555001', site_id: 'MLB' };
    },
    finalizeEvent: async () => {},
  });

  const result = await replay({ pageSize: 100, totalBudget: 300, since: '2026-08-24T00:00:00.000Z' });
  assert.equal(result.pages, 3);
  assert.equal(result.considered, 257);
  assert.equal(result.replayed, 257);
  assert.equal(result.remaining_events, 0);
  assert.equal(result.remaining_unique_resources, 0);
  assert.equal(result.budget_exhausted, false);
  assert.equal(sideEffects.size, 237);
  assert.equal(cursors.length, 2);
  assert.ok(cursors[0].afterEventId < cursors[1].afterEventId);
});

test('CBT replay advances past duplicate event rows without truncating the next cursor page', async () => {
  const events = [
    { event_id: 'evt-dup', topic: 'items', resource: '/items/CBT-DUP', outcome: 'route_unresolved_skipped', received_at: '2026-08-24T00:00:00.000Z' },
    { event_id: 'evt-dup', topic: 'items', resource: '/items/CBT-DUP', outcome: 'route_unresolved_skipped', received_at: '2026-08-24T00:00:00.000Z' },
    { event_id: 'evt-next-1', topic: 'items', resource: '/items/CBT-DUP', outcome: 'route_unresolved_skipped', received_at: '2026-08-24T00:00:01.000Z' },
    { event_id: 'evt-next-2', topic: 'items', resource: '/items/CBT-NEXT', outcome: 'route_unresolved_skipped', received_at: '2026-08-24T00:00:02.000Z' },
  ];
  const processed = [];
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit, afterReceivedAt = null, afterEventId = null }) => events
      .filter((event) => !afterReceivedAt
        || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId))
      .slice(0, limit),
    countEvents: async ({ afterReceivedAt = null, afterEventId = null }) => ({
      event_count: events.filter((event) => !afterReceivedAt
        || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId)).length,
      item_count: 0,
    }),
    claimEvent: async () => ({ status: 'claimed', claim_token: 'dup-claim' }),
    consumeEvent: async (event) => { processed.push(event.event_id); return { outcome: 'item_updated' }; },
    finalizeEvent: async () => {},
  });
  const result = await replay({ pageSize: 2, totalBudget: 10 });
  assert.equal(result.pages, 2);
  assert.equal(result.considered, 3);
  assert.deepEqual(processed, ['evt-dup', 'evt-next-2']);
});

test('startup-sized CBT backlog of 365 events and 326 resources drains across four bounded pages', async () => {
  const events = Array.from({ length: 365 }, (_, index) => ({
    event_id: `evt-startup-${String(index).padStart(3, '0')}`,
    topic: 'items',
    resource: `/items/CBT-STARTUP-${index % 326}`,
    remote_user_id: index % 3 === 0 ? '2651442567' : index % 3 === 1 ? '3332096437' : '3408885754',
    child_user_id: '',
    outcome: 'route_unresolved_skipped',
    received_at: new Date(Date.UTC(2026, 7, 24, 0, index, 0)).toISOString(),
  }));
  const seenResources = new Set();
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit, afterReceivedAt = null, afterEventId = null }) => events
      .filter((event) => !afterReceivedAt
        || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId))
      .slice(0, limit),
    countEvents: async ({ afterReceivedAt = null, afterEventId = null }) => {
      const remaining = events.filter((event) => !afterReceivedAt
        || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId));
      return { event_count: remaining.length, item_count: new Set(remaining.map((event) => event.resource)).size };
    },
    claimEvent: async () => ({ status: 'claimed', claim_token: 'startup-claim' }),
    consumeEvent: async (event) => { seenResources.add(event.resource); return { outcome: 'item_updated' }; },
    finalizeEvent: async () => {},
  });
  const result = await replay({ pageSize: 100, totalBudget: 1_000, since: '2026-08-23T00:00:00.000Z' });
  assert.equal(result.pages, 4);
  assert.equal(result.considered, 365);
  assert.equal(result.remaining_events, 0);
  assert.equal(result.remaining_unique_resources, 0);
  assert.equal(seenResources.size, 326);
});

test('351 first unresolved resources plus 41 duplicate events remain unresolved without synthetic retryable failures', async () => {
  const events = Array.from({ length: 351 }, (_, index) => ({
    event_id: `evt-351-${String(index).padStart(3, '0')}`,
    topic: 'items',
    resource: `/items/CBT-351-${index}`,
    remote_user_id: index % 3 === 0 ? '2651442567' : index % 3 === 1 ? '3332096437' : '3408885754',
    child_user_id: '',
    outcome: 'route_unresolved_skipped',
    received_at: new Date(Date.UTC(2026, 7, 24, 1, index, 0)).toISOString(),
  }));
  for (let index = 0; index < 40; index += 1) {
    events.push({ ...events[index], event_id: `evt-duplicate-${String(index).padStart(3, '0')}`, received_at: new Date(Date.UTC(2026, 7, 24, 2, index, 0)).toISOString() });
  }
  events.push({ ...events[0], event_id: 'evt-duplicate-extra', received_at: '2026-08-24T02:40:00.000Z' });
  const finalized = [];
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit, afterReceivedAt = null, afterEventId = null }) => events
      .filter((event) => ['route_unresolved_skipped', 'compensation_failed'].includes(event.outcome)
        && (!afterReceivedAt || event.received_at > afterReceivedAt
          || (event.received_at === afterReceivedAt && event.event_id > afterEventId)))
      .sort((left, right) => left.received_at.localeCompare(right.received_at) || left.event_id.localeCompare(right.event_id))
      .slice(0, limit),
    countEvents: async ({ afterReceivedAt = null, afterEventId = null }) => {
      const remaining = events.filter((event) => ['route_unresolved_skipped', 'compensation_failed'].includes(event.outcome)
        && (!afterReceivedAt || event.received_at > afterReceivedAt
          || (event.received_at === afterReceivedAt && event.event_id > afterEventId)));
      return { event_count: remaining.length, item_count: new Set(remaining.map((event) => event.resource)).size, retryable_failed_count: remaining.filter((event) => event.outcome === 'compensation_failed').length };
    },
    claimEvent: async (_eventId) => ({ status: 'claimed', claim_token: 'claim-351', attempt_count: 2 }),
    consumeEvent: async () => { throw Object.assign(new Error('official seller/site signals are not unique'), { code: 'ACTIVITY_CALLBACK_CBT_ROUTE_UNRESOLVED', diagnostic_code: 'OFFICIAL_SITE_MISSING' }); },
    finalizeEvent: async (value) => { finalized.push(value); events.find((event) => event.event_id === value.eventId).outcome = value.event.outcome; },
  });
  const result = await replay({ pageSize: 100, totalBudget: 1_000 });
  assert.equal(result.pages, 4);
  assert.equal(result.attempted, 392);
  assert.equal(result.unique_resource_attempted, 351);
  assert.equal(result.unresolved, 392);
  assert.equal(result.unique_resource_unresolved, 351);
  assert.equal(result.retryable_failed, 0);
  assert.equal(result.deduplicated, 41);
  assert.equal(result.deduplicated_unresolved, 41);
  assert.equal(result.unique_resource_retained_failure, 351);
  assert.equal(finalized.filter((entry) => entry.event.outcome === 'compensation_failed').length, 0);
});

test('CBT replay publishes page progress with event and unique-resource counters', async () => {
  const events = Array.from({ length: 205 }, (_, index) => ({
    event_id: `evt-progress-${index}`,
    topic: 'items',
    resource: `/items/CBT-P-${index % 200}`,
    remote_user_id: '2651442567',
    child_user_id: '',
    outcome: 'route_unresolved_skipped',
    received_at: new Date(Date.UTC(2026, 7, 24, 3, index, 0)).toISOString(),
  }));
  const progress = [];
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit, afterReceivedAt = null, afterEventId = null }) => events.filter((event) => !afterReceivedAt
      || event.received_at > afterReceivedAt
      || (event.received_at === afterReceivedAt && event.event_id > afterEventId)).slice(0, limit),
    countEvents: async ({ afterReceivedAt = null, afterEventId = null }) => {
      const remaining = events.filter((event) => !afterReceivedAt
        || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId));
      return { event_count: remaining.length, item_count: new Set(remaining.map((event) => event.resource)).size, retryable_failed_count: 0 };
    },
    claimEvent: async () => ({ status: 'claimed', claim_token: 'progress-claim', attempt_count: 2 }),
    consumeEvent: async () => ({ outcome: 'item_updated' }),
    finalizeEvent: async () => {},
  });
  const result = await replay({ pageSize: 100, totalBudget: 300, onProgress: (value) => progress.push(value) });
  assert.equal(progress.length, 3);
  assert.deepEqual(progress.map((value) => value.pages), [1, 2, 3]);
  assert.equal(progress.at(-1).attempted, 205);
  assert.equal(progress.at(-1).unique_resource_attempted, 200);
  assert.equal(progress.at(-1).remaining_events, 0);
  assert.equal(result.succeeded, 205);
});

test('392 parent events fan out 351 unique CBT resources to exact child targets without duplicate child GETs', async () => {
  const events = Array.from({ length: 351 }, (_, index) => ({
    event_id: `evt-fanout-392-${index}`,
    topic: 'items', resource: `/items/CBT-FANOUT-${index}`,
    remote_user_id: '2651442567', child_user_id: '', outcome: 'route_unresolved_skipped',
    received_at: new Date(Date.UTC(2026, 7, 24, 5, index, 0)).toISOString(),
  }));
  for (let index = 0; index < 40; index += 1) events.push({ ...events[index], event_id: `evt-fanout-dup-${index}`, received_at: new Date(Date.UTC(2026, 7, 24, 6, index, 0)).toISOString() });
  events.push({ ...events[0], event_id: 'evt-fanout-dup-extra', received_at: '2026-08-24T06:40:00.000Z' });
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit, afterReceivedAt = null, afterEventId = null }) => events
      .filter((event) => ['route_unresolved_skipped', 'compensation_failed'].includes(event.outcome)
        && (!afterReceivedAt || event.received_at > afterReceivedAt || (event.received_at === afterReceivedAt && event.event_id > afterEventId)))
      .sort((left, right) => left.received_at.localeCompare(right.received_at) || left.event_id.localeCompare(right.event_id))
      .slice(0, limit),
    countEvents: async ({ afterReceivedAt = null, afterEventId = null }) => {
      const remaining = events.filter((event) => ['route_unresolved_skipped', 'compensation_failed'].includes(event.outcome)
        && (!afterReceivedAt || event.received_at > afterReceivedAt || (event.received_at === afterReceivedAt && event.event_id > afterEventId)));
      return { event_count: remaining.length, item_count: new Set(remaining.map((event) => event.resource)).size, retryable_failed_count: 0 };
    },
    claimEvent: async () => ({ status: 'claimed', claim_token: 'fanout-392-claim', attempt_count: 2 }),
    consumeEvent: async () => ({ outcome: 'item_updated', route_target_count: 2, child_get_count: 2, cache_updated_count: 2, physical_get_count: 3 }),
    finalizeEvent: async (value) => { events.find((event) => event.event_id === value.eventId).outcome = value.event.outcome; },
  });
  const result = await replay({ pageSize: 100, totalBudget: 1000, physicalBudget: 2000 });
  assert.equal(result.attempted, 375);
  assert.equal(result.physical_get_used, 1000);
  assert.ok(result.physical_get_used <= result.physical_budget);
  assert.equal(result.physical_budget_exhausted, true);
  assert.equal(result.succeeded, 375);
  assert.equal(result.deduplicated, 41);
  assert.equal(result.remaining_events, 17);
});

test('CBT replay aggregates fanout targets and stops on physical GET budget with remaining events', async () => {
  const events = Array.from({ length: 12 }, (_, index) => ({
    event_id: `evt-physical-${index}`,
    topic: 'items', resource: `/items/CBT-PHYSICAL-${index}`,
    remote_user_id: '2651442567', child_user_id: '', outcome: 'route_unresolved_skipped',
    received_at: new Date(Date.UTC(2026, 7, 24, 4, index, 0)).toISOString(),
  }));
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit, afterReceivedAt = null, afterEventId = null }) => events.filter((event) => !afterReceivedAt
      || event.received_at > afterReceivedAt
      || (event.received_at === afterReceivedAt && event.event_id > afterEventId)).slice(0, limit),
    countEvents: async ({ afterReceivedAt = null, afterEventId = null }) => {
      const remaining = events.filter((event) => !afterReceivedAt
        || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId));
      return { event_count: remaining.length, item_count: remaining.length, retryable_failed_count: 0 };
    },
    claimEvent: async () => ({ status: 'claimed', claim_token: 'physical-claim', attempt_count: 2 }),
    consumeEvent: async () => ({ outcome: 'item_updated', route_target_count: 2, child_get_count: 2, cache_updated_count: 2, physical_get_count: 3 }),
    finalizeEvent: async () => {},
  });
  const result = await replay({ pageSize: 10, totalBudget: 100, physicalBudget: 12 });
  assert.equal(result.physical_get_used, 12);
  assert.equal(result.route_target_attempted, 8);
  assert.equal(result.child_get_count, 8);
  assert.equal(result.cache_updated_count, 8);
  assert.equal(result.physical_budget_exhausted, true);
  assert.ok(result.remaining_events > 0);
});

test('physical GET permits are atomic, concurrency-safe, and never exceed the hard limit', async () => {
  const budget = createPhysicalGetBudget(3);
  const client = {
    async getMarketplaceItem(itemId) {
      await new Promise((resolve) => setTimeout(resolve, 2));
      return { id: itemId, price: 1 };
    },
  };
  const results = await Promise.all(Array.from({ length: 8 }, (_, index) => (
    readMarketplaceItemWithBudget({ client, itemId: `MLB-BUDGET-${index}`, physicalGetBudget: budget })
      .then(() => 'ok')
      .catch((error) => error.code)
  )));
  const stats = budget.stats();
  assert.equal(results.filter((value) => value === 'ok').length, 3);
  assert.equal(results.filter((value) => value === 'PHYSICAL_GET_BUDGET_EXHAUSTED').length, 5);
  assert.equal(stats.reserved, 3);
  assert.equal(stats.issued, 3);
  assert.equal(stats.completed, 3);
  assert.equal(stats.available, 0);
  assert.ok(stats.completed <= stats.physical_budget);
});

test('physical budget zero blocks the parent GET without claiming or consuming it', async () => {
  const events = [{
    event_id: 'evt-budget-zero', topic: 'items', resource: '/items/CBT-BUDGET-ZERO',
    remote_user_id: '2651442567', child_user_id: '', outcome: 'route_unresolved_skipped',
    received_at: '2026-08-24T04:00:00.000Z',
  }];
  let consumeCalls = 0;
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit }) => events.slice(0, limit),
    countEvents: async () => ({ event_count: events.length, item_count: 1, retryable_failed_count: 0 }),
    claimEvent: async () => ({ status: 'claimed', claim_token: 'budget-zero' }),
    consumeEvent: async () => { consumeCalls += 1; return { outcome: 'item_updated' }; },
    finalizeEvent: async () => {},
  });
  const result = await replay({ pageSize: 1, totalBudget: 1, physicalBudget: 0 });
  assert.equal(consumeCalls, 0);
  assert.equal(result.physical_get_used, 0);
  assert.equal(result.physical_budget_exhausted, true);
  assert.equal(result.remaining_events, 1);
  assert.ok(result.physical_get_used <= result.physical_budget);
});

test('parent plus child fanout reserves only the remaining physical permits and resumes targets', async () => {
  const events = [{
    event_id: 'evt-budget-one', topic: 'items', resource: '/items/CBT-BUDGET-ONE',
    remote_user_id: '2651442567', child_user_id: '', outcome: 'route_unresolved_skipped',
    received_at: '2026-08-24T04:01:00.000Z',
  }];
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit }) => events.slice(0, limit),
    countEvents: async () => ({ event_count: events.length, item_count: 1, retryable_failed_count: 0 }),
    claimEvent: async () => ({ status: 'claimed', claim_token: 'budget-one' }),
    consumeEvent: async (_event, { physicalGetBudget }) => {
      let attempted = 0;
      let remaining = 0;
      for (let index = 0; index < 6; index += 1) {
        const permit = physicalGetBudget.reserve(index === 0 ? 'cbt_parent' : 'cbt_child');
        if (!permit) { remaining += 1; continue; }
        physicalGetBudget.issue(permit);
        physicalGetBudget.complete(permit);
        attempted += 1;
      }
      return {
        outcome: remaining ? 'partial' : 'item_updated',
        budget_remaining: remaining > 0,
        route_target_count: Math.max(0, attempted - 1),
        route_target_attempted_count: 5,
        child_get_count: Math.max(0, attempted - 1),
        cache_updated_count: Math.max(0, attempted - 1),
        physical_get_stats: physicalGetBudget.stats(),
        fanout_failed_count: remaining,
      };
    },
    finalizeEvent: async () => {},
  });
  const result = await replay({ pageSize: 1, totalBudget: 1, physicalBudget: 1 });
  assert.equal(result.physical_get_used, 1);
  assert.equal(result.physical_get_issued, 1);
  assert.equal(result.physical_get_completed, 1);
  assert.equal(result.physical_budget_exhausted, true);
  assert.equal(result.budget_remaining_route_targets, 5);
  assert.ok(result.physical_get_used <= result.physical_budget);
});

test('999 physical permits cap parent plus five-child fanout at exactly 999', async () => {
  const events = Array.from({ length: 200 }, (_, index) => ({
    event_id: `evt-budget-999-${index}`, topic: 'items', resource: `/items/CBT-BUDGET-999-${index}`,
    remote_user_id: '2651442567', child_user_id: '', outcome: 'route_unresolved_skipped',
    received_at: new Date(Date.UTC(2026, 7, 24, 4, Math.floor(index / 60), index % 60)).toISOString(),
  }));
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit, afterReceivedAt = null, afterEventId = null }) => events
      .filter((event) => !afterReceivedAt || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId))
      .slice(0, limit),
    countEvents: async ({ afterReceivedAt = null, afterEventId = null }) => {
      const remaining = events.filter((event) => !afterReceivedAt || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId));
      return { event_count: remaining.length, item_count: remaining.length, retryable_failed_count: 0 };
    },
    claimEvent: async () => ({ status: 'claimed', claim_token: 'budget-999' }),
    consumeEvent: async (_event, { physicalGetBudget }) => {
      let attempted = 0;
      let remaining = 0;
      for (let index = 0; index < 6; index += 1) {
        const permit = physicalGetBudget.reserve(index === 0 ? 'cbt_parent' : 'cbt_child');
        if (!permit) { remaining += 1; continue; }
        physicalGetBudget.issue(permit);
        physicalGetBudget.complete(permit);
        attempted += 1;
      }
      return {
        outcome: remaining ? 'partial' : 'item_updated', budget_remaining: remaining > 0,
        route_target_count: Math.max(0, attempted - 1), route_target_attempted_count: 5,
        child_get_count: Math.max(0, attempted - 1), cache_updated_count: Math.max(0, attempted - 1),
        physical_get_stats: physicalGetBudget.stats(), fanout_failed_count: remaining,
      };
    },
    finalizeEvent: async () => {},
  });
  const result = await replay({ pageSize: 100, totalBudget: 200, physicalBudget: 999 });
  assert.equal(result.physical_get_used, 999);
  assert.equal(result.physical_get_completed, 999);
  assert.equal(result.physical_budget_exhausted, true);
  assert.ok(result.remaining_events > 0);
  assert.ok(result.physical_get_used <= 999);
});

test('CBT replay includes events arriving after the first page and reports remaining when budget is exhausted', async () => {
  const events = Array.from({ length: 257 }, (_, index) => ({
    event_id: `evt-budget-${String(index).padStart(3, '0')}`,
    topic: 'items',
    resource: `/items/CBT-B-${index}`,
    remote_user_id: '3332096437',
    child_user_id: '',
    outcome: 'route_unresolved_skipped',
    received_at: `2026-08-24T01:${String(Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}.000Z`,
  }));
  const old = { ...events[0], event_id: 'evt-old', received_at: '2026-08-22T00:00:00.000Z', resource: '/items/CBT-OLD' };
  let calls = 0;
  const seen = [];
  const replay = createCbtWebhookReplay({
    listEvents: async ({ limit, afterReceivedAt = null, afterEventId = null, since }) => {
      calls += 1;
      if (calls === 1) events.push({ ...events.at(-1), event_id: 'evt-arrived-after-page', received_at: '2026-08-24T02:00:00.000Z', resource: '/items/CBT-ARRIVED' });
      const eligible = [old, ...events].filter((event) => event.received_at >= since && (!afterReceivedAt
        || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId)));
      return eligible.slice(0, limit);
    },
    countEvents: async ({ since, afterReceivedAt = null, afterEventId = null }) => {
      const remaining = [old, ...events].filter((event) => event.received_at >= since && (!afterReceivedAt
        || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId)));
      return { event_count: remaining.length, item_count: new Set(remaining.map((event) => event.resource)).size };
    },
    claimEvent: async () => ({ status: 'claimed', claim_token: 'budget-claim' }),
    consumeEvent: async (event) => { seen.push(event.event_id); return { outcome: 'item_updated' }; },
    finalizeEvent: async () => {},
  });
  const result = await replay({ pageSize: 100, totalBudget: 200, since: '2026-08-23T16:00:00.000Z' });
  assert.equal(result.pages, 2);
  assert.equal(result.considered, 200);
  assert.equal(result.budget_exhausted, true);
  assert.equal(result.remaining_events, 58);
  assert.equal(seen.includes('evt-old'), false);
  assert.ok(calls >= 2);
});

test('CBT compensation replay is bounded and idempotent after the first successful claim', async () => {
  const events = [{
    event_id: 'evt-replay-1',
    schema_version: '2',
    topic: 'items',
    resource: '/items/CBT1',
    remote_user_id: '2651442567',
    child_user_id: '',
    outcome: 'route_unresolved_skipped',
    received_at: '2026-08-24T00:00:00.000Z',
    raw_json: JSON.stringify({ event: cbtEvent({ event_id: 'evt-replay-1', resource: '/items/CBT1' }) }),
  }];
  let claimed = true;
  let consumeCalls = 0;
  const finalizeCalls = [];
  const replay = createCbtWebhookReplay({
    listEvents: async () => events,
    claimEvent: async () => {
      if (!claimed) return { status: 'not_eligible' };
      claimed = false;
      return { status: 'claimed', claim_token: 'claim-1' };
    },
    consumeEvent: async (event) => {
      consumeCalls += 1;
      assert.equal(event.resource, '/items/CBT1');
      return { account_id: '2651442567', child_user_id: '2659555001', site_id: 'MLB', outcome: 'item_updated' };
    },
    finalizeEvent: async (value) => finalizeCalls.push(value),
  });

  const first = await replay({ limit: 100 });
  const second = await replay({ limit: 100 });
  assert.equal(first.replayed, 1);
  assert.equal(second.already_processed, 1);
  assert.equal(consumeCalls, 1);
  assert.equal(finalizeCalls[0].event.outcome, 'item_updated');
});

test('compensation_failed stays retryable, dedupes a repeated resource, and succeeds on a later batch', async () => {
  const events = [
    { event_id: 'evt-r1', topic: 'items', resource: '/items/CBT-R1', remote_user_id: '2651442567', child_user_id: '', outcome: 'route_unresolved_skipped', received_at: '2026-08-24T00:00:00.000Z' },
    { event_id: 'evt-r1-dup', topic: 'items', resource: '/items/CBT-R1', remote_user_id: '2651442567', child_user_id: '', outcome: 'route_unresolved_skipped', received_at: '2026-08-24T00:00:01.000Z' },
    { event_id: 'evt-r2', topic: 'items', resource: '/items/CBT-R2', remote_user_id: '2651442567', child_user_id: '', outcome: 'route_unresolved_skipped', received_at: '2026-08-24T00:00:02.000Z' },
  ];
  const claims = new Map();
  let failR2 = true;
  let externalGets = 0;
  const finalized = [];
  const listEvents = async ({ limit, afterReceivedAt = null, afterEventId = null }) => events
    .filter((event) => ['route_unresolved_skipped', 'compensation_failed'].includes(event.outcome)
      && (!afterReceivedAt || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId)))
    .slice(0, limit);
  const countEvents = async ({ afterReceivedAt = null, afterEventId = null }) => {
    const remaining = events.filter((event) => ['route_unresolved_skipped', 'compensation_failed'].includes(event.outcome)
      && (!afterReceivedAt || event.received_at > afterReceivedAt
        || (event.received_at === afterReceivedAt && event.event_id > afterEventId)));
    return { event_count: remaining.length, item_count: new Set(remaining.map((event) => event.resource)).size, retryable_failed_count: remaining.filter((event) => event.outcome === 'compensation_failed').length };
  };
  const replay = createCbtWebhookReplay({
    listEvents,
    countEvents,
    claimEvent: async (eventId) => {
      if (claims.get(eventId) === 'processing') return { status: 'in_progress' };
      claims.set(eventId, 'processing');
      return { status: 'claimed', claim_token: `claim-${eventId}`, attempt_count: 2 };
    },
    consumeEvent: async (event) => {
      externalGets += 1;
      if (event.resource === '/items/CBT-R2' && failR2) throw Object.assign(new Error('temporary read failure'), { code: 'ETIMEDOUT' });
      return { outcome: 'item_updated', account_id: '2651442567', child_user_id: '2659555001', site_id: 'MLB' };
    },
    finalizeEvent: async (value) => {
      finalized.push(value);
      const target = events.find((event) => event.event_id === value.eventId);
      if (target) {
        target.outcome = value.event.outcome || 'item_updated';
        if (value.status === 'completed') claims.set(target.event_id, 'completed');
        else claims.set(target.event_id, 'failed');
      }
    },
  });
  const first = await replay({ pageSize: 100, totalBudget: 100 });
  assert.equal(first.replayed, 2);
  assert.equal(first.deduplicated, 1);
  assert.equal(first.failed, 1);
  assert.equal(first.retryable_failed, 1);
  assert.equal(externalGets, 2);
  assert.equal(first.remaining_events, 0);
  failR2 = false;
  const second = await replay({ pageSize: 100, totalBudget: 100 });
  assert.equal(second.replayed, 1);
  assert.equal(second.failed, 0);
  assert.equal(externalGets, 3);
  assert.equal(events.some((event) => event.outcome === 'compensation_failed'), false);
  assert.ok(finalized.some((entry) => entry.error?.error_kind === 'timeout'));
});

test('repository compensation queue selects unresolved CBT events and removes a completed replay', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    for (let index = 0; index < 94; index += 1) {
      repo.saveActivityCallbackEvent({
        schema_version: '2',
        event_id: \`evt-db-\${index}\`,
        topic: 'items',
        resource: \`/items/CBT\${index}\`,
        remote_user_id: index % 2 ? '3332096437' : '2651442567',
        application_id: 'APP-1',
        received_at: \`2026-08-24T00:\${String(index).padStart(2, '0')}:00.000Z\`,
        outcome: 'route_unresolved_skipped',
        raw_json: JSON.stringify({ event_id: \`evt-db-\${index}\` }),
      });
    }
    const before = repo.listUnresolvedCbtActivityCallbackEvents({ limit: 100 });
    const claim = repo.claimActivityCallbackReplayEvent('evt-db-0', { now: new Date('2026-08-24T03:00:00.000Z') });
    const inProgress = repo.claimActivityCallbackReplayEvent('evt-db-0', { now: new Date('2026-08-24T03:01:00.000Z') });
    repo.finalizeActivityCallbackEvent({
      eventId: 'evt-db-0',
      claimToken: claim.claim_token,
      status: 'completed',
      event: { schema_version: '2', event_id: 'evt-db-0', topic: 'items', resource: '/items/CBT0', remote_user_id: '2651442567', outcome: 'item_updated', account_id: '2651442567', child_user_id: '2659555001', site_id: 'MLB' },
    });
    const after = repo.listUnresolvedCbtActivityCallbackEvents({ limit: 100 });
    console.log(JSON.stringify({ before: before.length, claim: claim.status, inProgress: inProgress.status, after: after.length }));
  `);
  assert.deepEqual(result, { before: 94, claim: 'claimed', inProgress: 'in_progress', after: 93 });
});

test('repository compensation_failed can be claimed again and then terminalized successfully', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    repo.saveActivityCallbackEvent({
      schema_version: '2', event_id: 'evt-retry-failed', topic: 'items', resource: '/items/CBT-RETRY',
      remote_user_id: '2651442567', received_at: '2026-08-24T00:00:00.000Z', outcome: 'route_unresolved_skipped',
      raw_json: JSON.stringify({ event_id: 'evt-retry-failed' }),
    });
    const first = repo.claimActivityCallbackReplayEvent('evt-retry-failed', { now: new Date('2026-08-24T01:00:00.000Z') });
    repo.finalizeActivityCallbackEvent({
      eventId: 'evt-retry-failed', claimToken: first.claim_token, status: 'failed',
      event: { event_id: 'evt-retry-failed', topic: 'items', resource: '/items/CBT-RETRY', outcome: 'compensation_failed' },
      error: { operation: 'cbt_webhook_replay', endpoint_family: 'marketplace_item_resource', error_kind: 'timeout', http_status: null, code: 'ETIMEDOUT', cause_code: null, attempt_count: first.attempt_count, reason_cn: '读取超时' },
    });
    const visible = repo.listUnresolvedCbtActivityCallbackEvents({ limit: 100 });
    const summary = repo.summarizeUnresolvedCbtActivityCallbackEvents();
    const stored = repo.getActivityCallbackEvent('evt-retry-failed');
    const retry = repo.claimActivityCallbackReplayEvent('evt-retry-failed', { now: new Date('2026-08-24T02:00:00.000Z') });
    repo.finalizeActivityCallbackEvent({
      eventId: 'evt-retry-failed', claimToken: retry.claim_token, status: 'completed',
      event: { event_id: 'evt-retry-failed', topic: 'items', resource: '/items/CBT-RETRY', outcome: 'item_updated', account_id: '2651442567', child_user_id: '2659555001', site_id: 'MLB' },
    });
    const after = repo.listUnresolvedCbtActivityCallbackEvents({ limit: 100 });
    console.log(JSON.stringify({ visible: visible.length, retryable_failed: summary.retryable_failed_count, stored: JSON.parse(stored.last_error), retry: retry.status, attempt: retry.attempt_count, after: after.length }));
  `);
  assert.deepEqual(result, {
    visible: 1,
    retryable_failed: 1,
    stored: {
      operation: 'cbt_webhook_replay',
      endpoint_family: 'marketplace_item_resource',
      error_kind: 'timeout',
      reason_cn: '读取超时',
      http_status: null,
      code: 'ETIMEDOUT',
      cause_code: null,
      attempt_count: 2,
    },
    retry: 'claimed',
    attempt: 3,
    after: 0,
  });
});

test('repository keeps resolver diagnostics on completed route_unresolved events without raw fields', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    repo.saveActivityCallbackEvent({
      schema_version: '2', event_id: 'evt-diagnostic-retained', topic: 'items', resource: '/items/CBT-DIAGNOSTIC',
      remote_user_id: '2651442567', received_at: '2026-08-24T00:00:00.000Z', outcome: 'route_unresolved_skipped',
      raw_json: JSON.stringify({ event_id: 'evt-diagnostic-retained' }),
    });
    const claim = repo.claimActivityCallbackReplayEvent('evt-diagnostic-retained', { now: new Date('2026-08-24T01:00:00.000Z') });
    repo.finalizeActivityCallbackEvent({
      eventId: 'evt-diagnostic-retained', claimToken: claim.claim_token, status: 'completed',
      event: { event_id: 'evt-diagnostic-retained', topic: 'items', resource: '/items/CBT-DIAGNOSTIC', outcome: 'route_unresolved_skipped' },
      error: { operation: 'cbt_route_resolution', endpoint_family: 'items_cbt', error_kind: 'business', http_status: 422, code: 'OFFICIAL_SITE_MISSING', cause_code: null, attempt_count: claim.attempt_count, reason_cn: '缺少官方站点身份', signal_presence: { seller_signal_count: 1, site_signal_count: 0, status_present: true, route_candidate_count: 0 } },
    });
    const row = repo.getActivityCallbackEvent('evt-diagnostic-retained');
    console.log(JSON.stringify({ state: row.processing_state, outcome: row.outcome, diagnostic: JSON.parse(row.last_error) }));
  `);
  assert.deepEqual(result, {
    state: 'completed',
    outcome: 'route_unresolved_skipped',
    diagnostic: {
      operation: 'cbt_route_resolution',
      endpoint_family: 'items_cbt',
      error_kind: 'business',
      reason_cn: '缺少官方站点身份',
      http_status: 422,
      code: 'OFFICIAL_SITE_MISSING',
      cause_code: null,
      attempt_count: 2,
      signal_presence: { seller_signal_count: 1, site_signal_count: 0, status_present: true, route_candidate_count: 0 },
    },
  });
});

test('repository summary deduplicates classification by unique CBT resource', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    const save = (eventId, resource, diagnostic, receivedAt) => repo.saveActivityCallbackEvent({
      schema_version: '2', event_id: eventId, topic: 'items', resource,
      remote_user_id: '2651442567', received_at: receivedAt,
      outcome: 'route_unresolved_skipped', raw_json: JSON.stringify({ event_id: eventId }),
      last_error: diagnostic,
    });
    save('evt-q-1', '/items/CBT-Q1', { code: 'OFFICIAL_SITE_MISSING', classification: 'quarantined_unknown' }, '2026-08-24T01:00:00.000Z');
    save('evt-q-1-dup', '/items/CBT-Q1', { code: 'OFFICIAL_SITE_MISSING', classification: 'quarantined_unknown' }, '2026-08-24T01:00:01.000Z');
    save('evt-terminal-1', '/items/CBT-T1', { code: 'RESOURCE_STATUS_UNUSABLE', classification: 'terminal_irrelevant' }, '2026-08-24T01:00:02.000Z');
    save('evt-partial-1', '/items/CBT-P1', { code: 'CBT_FANOUT_PARTIAL', classification: 'eligible_partial' }, '2026-08-24T01:00:03.000Z');
    const summary = repo.summarizeUnresolvedCbtActivityCallbackEvents();
    console.log(JSON.stringify({
      event_counts: summary.classification_counts,
      item_counts: summary.classification_item_counts,
      eligible_events: summary.eligible_event_count,
      eligible_items: summary.eligible_item_count,
      quarantine: summary.quarantined_unknown_count,
      terminal: summary.terminal_irrelevant_count,
      partial: summary.eligible_partial_count,
    }));
  `);
  assert.deepEqual(result, {
    event_counts: { quarantined_unknown: 2, terminal_irrelevant: 1, eligible_partial: 1 },
    item_counts: { quarantined_unknown: 1, terminal_irrelevant: 1, eligible_partial: 1 },
    eligible_events: 1,
    eligible_items: 1,
    quarantine: 1,
    terminal: 1,
    partial: 1,
  });
});

test('repository reclassifies 36 persisted quarantines as non-actionable parents when local routes are ready', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    repo.saveMarketplaceSites('2651442567', [{ child_user_id: '2659555001', site_id: 'MLB', logistic_type: 'remote' }]);
    for (let index = 0; index < 36; index += 1) {
      repo.saveActivityCallbackEvent({
        schema_version: '2', event_id: 'evt-parent-' + index, topic: 'items', resource: '/items/CBT-PARENT-' + index,
        remote_user_id: '2651442567', received_at: '2026-08-24T02:00:' + String(index).padStart(2, '0') + '.000Z',
        gap: 0, outcome: 'route_unresolved_skipped', raw_json: JSON.stringify({ event_id: 'evt-parent-' + index }),
        last_error: {
          operation: 'cbt_route_resolution', endpoint_family: 'items_cbt', code: 'OFFICIAL_SITE_MISSING',
          classification: 'quarantined_unknown', signal_presence: {
            seller_signal_count: 0, site_signal_count: 0, route_candidate_count: 0, child_item_count: 0,
          },
        },
      });
    }
    for (let index = 0; index < 7; index += 1) {
      repo.saveActivityCallbackEvent({
        schema_version: '2', event_id: 'evt-terminal-' + index, topic: 'items', resource: '/items/CBT-TERMINAL-' + index,
        remote_user_id: '2651442567', received_at: '2026-08-24T03:00:' + String(index).padStart(2, '0') + '.000Z',
        gap: 0, outcome: 'route_unresolved_skipped', raw_json: JSON.stringify({ event_id: 'evt-terminal-' + index }),
        last_error: { code: 'RESOURCE_STATUS_UNUSABLE', classification: 'terminal_irrelevant' },
      });
    }
    const summary = repo.summarizeUnresolvedCbtActivityCallbackEvents();
    console.log(JSON.stringify({
      terminal_parent: summary.terminal_no_actionable_global_parent_count,
      terminal_irrelevant: summary.terminal_irrelevant_count,
      quarantined: summary.quarantined_unknown_count,
      eligible_events: summary.eligible_event_count,
      eligible_items: summary.eligible_item_count,
    }));
  `);
  assert.deepEqual(result, {
    terminal_parent: 36,
    terminal_irrelevant: 7,
    quarantined: 0,
    eligible_events: 0,
    eligible_items: 0,
  });
});
