import assert from 'node:assert/strict';
import { mkdtemp, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  buildNotificationResourcePath,
  classifyActivityWebhookResource,
  createActivityWebhookConsumer,
  normalizeActivityWebhookEvent,
  resolveActivityWebhookRoute,
} from '../src/activityWebhookConsumer.js';
import {
  activityCallbackConfig,
  createActivityCallbackAdapter,
  signActivityCallbackEvent,
} from '../src/activityCallbackAdapter.js';

const ROUTES = [
  { account_id: '2651442567', child_user_id: '2659555001', site_id: 'MLM' },
  { account_id: '3332096437', child_user_id: '3333531550', site_id: 'MLM' },
  { account_id: '3408885754', child_user_id: '3407227823', site_id: 'MLM' },
];
const EVENT = {
  schema_version: '2',
  event_id: 'evt-offer-1',
  topic: 'public_offers',
  resource: '/seller-promotions/promotions/offer/OFFER-MLM1-1/3333531550',
  remote_user_id: '3333531550',
  application_id: '123',
  received_at: '2026-07-16T10:00:00.000Z',
};

test('formal callback configuration reads the shared secret from a repository-external file', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'activity-callback-secret-'));
  const secretFile = path.join(root, 'secret.txt');
  await writeFile(secretFile, 'fixture-private-secret');
  const config = activityCallbackConfig({
    MDM_ACTIVITY_CALLBACK_ENABLED: '1',
    MDM_ACTIVITY_CALLBACK_SECRET_FILE: secretFile,
    MDM_ACTIVITY_CALLBACK_SECRET: 'must-not-win',
    MDM_ACTIVITY_CALLBACK_APPLICATION_ID: '123',
  });
  assert.equal(config.secret, 'fixture-private-secret');
  assert.equal(config.secretFile, secretFile);
});

test('remote child identity maps to the authoritative account child and site without display aliases', () => {
  const route = resolveActivityWebhookRoute({ event: EVENT, marketplaceSites: ROUTES, accounts: [] });
  assert.deepEqual(route, ROUTES[1]);
  const other = resolveActivityWebhookRoute({
    event: { ...EVENT, remote_user_id: '3407227823', resource: '/seller-promotions/promotions/offer/OFFER-MLM1-1/3407227823' },
    marketplaceSites: ROUTES,
    accounts: [],
  });
  assert.deepEqual(other, ROUTES[2]);
  assert.throws(
    () => resolveActivityWebhookRoute({ event: { ...EVENT, resource: '/items/MLA123' }, marketplaceSites: ROUTES, accounts: [] }),
    (error) => error?.code === 'ACTIVITY_CALLBACK_ROUTE_MISMATCH',
  );
});

test('cross-account child identity cannot be overridden by display data or a mismatched resource owner', () => {
  assert.throws(
    () => resolveActivityWebhookRoute({
      event: { ...EVENT, remote_user_id: '3407227823' },
      marketplaceSites: ROUTES.map((route) => ({ ...route, store_name: route.account_id === '3332096437' ? '湖南' : '广州' })),
      accounts: [],
    }),
    (error) => error?.code === 'ACTIVITY_CALLBACK_ROUTE_MISMATCH',
  );
  assert.throws(
    () => resolveActivityWebhookRoute({ event: { ...EVENT, remote_user_id: '999' }, marketplaceSites: ROUTES, accounts: [] }),
    (error) => error?.code === 'ACTIVITY_CALLBACK_ROUTE_MISMATCH',
  );
});

test('parent account notifications resolve only when resource site selects one authoritative child', () => {
  const route = resolveActivityWebhookRoute({
    event: { ...EVENT, topic: 'items', resource: '/items/MLM123', remote_user_id: '3332096437' },
    marketplaceSites: ROUTES,
    accounts: [{ account_id: '3332096437' }],
  });
  assert.deepEqual(route, ROUTES[1]);
  assert.throws(
    () => resolveActivityWebhookRoute({
      event: { ...EVENT, topic: 'items', resource: '/items/MLA123', remote_user_id: '3332096437' },
      marketplaceSites: ROUTES,
      accounts: [{ account_id: '3332096437' }],
    }),
    (error) => error?.code === 'ACTIVITY_CALLBACK_ROUTE_UNRESOLVED',
  );
});

test('public offer and candidate resources require a GET result with a real promotion identity', () => {
  assert.equal(buildNotificationResourcePath(EVENT), '/marketplace/seller-promotions/promotions/offer/OFFER-MLM1-1/3333531550');
  const result = classifyActivityWebhookResource({
    event: EVENT,
    route: ROUTES[1],
    resourceData: { id: 'OFFER-MLM1-1', item_id: 'MLM123', promotion_id: 'P-MLM9', type: 'DEAL', status: { id: 'started' } },
  });
  assert.deepEqual(result.dirty_activities, [{ ...ROUTES[1], promotion_id: 'P-MLM9', promotion_type: 'DEAL' }]);
  assert.equal(result.catalog_dirty, false);
  assert.throws(
    () => classifyActivityWebhookResource({ event: EVENT, route: ROUTES[1], resourceData: { item_id: 'MLM123' } }),
    (error) => error?.code === 'ACTIVITY_CALLBACK_RESOURCE_UNCLASSIFIED',
  );
});

test('public candidate uses the same exact promotion relation contract', () => {
  const event = { ...EVENT, event_id: 'evt-candidate-1', topic: 'public_candidates', resource: '/seller-promotions/promotions/candidate/CANDIDATE-MLM1/3333531550' };
  assert.equal(buildNotificationResourcePath(event), '/marketplace/seller-promotions/promotions/candidate/CANDIDATE-MLM1/3333531550');
  const result = classifyActivityWebhookResource({
    event,
    route: ROUTES[1],
    resourceData: { item_id: 'MLM321', promotion_id: 'C-MLM10', promotion_type: 'SELLER_CAMPAIGN' },
  });
  assert.equal(result.catalog_dirty, false);
  assert.equal(result.dirty_activities[0].promotion_id, 'C-MLM10');
});

test('items only mark an exact activity when the GET proves the relation, otherwise they dirty the route catalog', () => {
  const itemEvent = normalizeActivityWebhookEvent({
    ...EVENT, event_id: 'evt-item-1', topic: 'items', resource: '/items/MLM123', remote_user_id: '3333531550',
  });
  const exact = classifyActivityWebhookResource({
    event: itemEvent, route: ROUTES[1],
    resourceData: { id: 'MLM123', promotions: [{ id: 'C-MLM8', type: 'SELLER_CAMPAIGN' }] },
  });
  assert.equal(exact.catalog_dirty, false);
  assert.equal(exact.dirty_activities[0].promotion_id, 'C-MLM8');

  const fallback = classifyActivityWebhookResource({ event: itemEvent, route: ROUTES[1], resourceData: { id: 'MLM123', status: 'active' } });
  assert.equal(fallback.catalog_dirty, true);
  assert.deepEqual(fallback.dirty_activities, []);
});

test('consumer performs one read-only resource GET and marks only the resolved child route', async () => {
  const marked = [];
  const invalidated = [];
  const reads = [];
  const consumer = createActivityWebhookConsumer({
    listMarketplaceSites: () => ROUTES,
    listAccounts: () => [],
    createResourceClient: async (route) => ({
      getNotificationResource: async (resourcePath) => {
        reads.push({ route, resourcePath });
        return { item_id: 'MLM123', promotion_id: 'P-MLM9', type: 'DEAL' };
      },
      enrollItem: () => assert.fail('write method must never be used'),
    }),
    markDirty: (value) => marked.push(value),
    invalidateCatalog: (value) => invalidated.push(value),
  });
  const result = await consumer(EVENT);
  assert.equal(result.account_id, '3332096437');
  assert.equal(result.child_user_id, '3333531550');
  assert.equal(reads.length, 1);
  assert.equal(marked.length, 1);
  assert.equal(marked[0].promotionId, 'P-MLM9');
  assert.equal(invalidated.length, 0);
});

test('item notification without a proved activity relation dirties only the resolved site catalog', async () => {
  const marked = [];
  const invalidated = [];
  const consumer = createActivityWebhookConsumer({
    listMarketplaceSites: () => ROUTES,
    listAccounts: () => [],
    createResourceClient: async () => ({ getNotificationResource: async () => ({ id: 'MLM123', status: 'active' }) }),
    markDirty: (value) => marked.push(value),
    invalidateCatalog: (value) => invalidated.push(value),
  });
  const result = await consumer({ ...EVENT, event_id: 'evt-item-catalog', topic: 'items', resource: '/items/MLM123' });
  assert.equal(result.outcome, 'catalog_dirty');
  assert.deepEqual(marked, [{ accountId: '3332096437', siteId: 'MLM', eventCursor: null, gap: false }]);
  assert.deepEqual(invalidated, [{ accountId: '3332096437', childUserId: '3333531550', siteId: 'MLM' }]);
});

test('resource GET failures remain retryable and do not dirty any cache state', async () => {
  const marked = [];
  const consumer = createActivityWebhookConsumer({
    listMarketplaceSites: () => ROUTES,
    listAccounts: () => [],
    createResourceClient: async () => ({ getNotificationResource: async () => { throw new Error('GET unavailable'); } }),
    markDirty: (value) => marked.push(value),
    invalidateCatalog: () => assert.fail('catalog must remain untouched'),
  });
  await assert.rejects(() => consumer(EVENT), /GET unavailable/);
  assert.deepEqual(marked, []);
});

test('v2 adapter validates app id and signature, retries resource failures and persists only completed consumption', async () => {
  const secret = 'fixture-private-secret';
  const saved = new Set();
  let consumeCalls = 0;
  const adapter = createActivityCallbackAdapter({
    config: activityCallbackConfig({
      MDM_ACTIVITY_CALLBACK_ENABLED: '1', MDM_ACTIVITY_CALLBACK_SECRET: secret, MDM_ACTIVITY_CALLBACK_APPLICATION_ID: '123',
    }),
    hasEvent: (eventId) => saved.has(eventId),
    saveEvent: (event) => saved.add(event.event_id),
    consumeEvent: async () => { consumeCalls += 1; throw new Error('resource GET failed'); },
  });
  const signature = signActivityCallbackEvent(EVENT, secret);
  await assert.rejects(() => adapter.accept(EVENT, signature), /resource GET failed/);
  assert.equal(saved.size, 0);
  assert.equal(consumeCalls, 1);
  await assert.rejects(
    () => adapter.accept({ ...EVENT, application_id: '999' }, signActivityCallbackEvent({ ...EVENT, application_id: '999' }, secret)),
    /应用标识/,
  );
  const missingAppConfig = createActivityCallbackAdapter({
    config: activityCallbackConfig({ MDM_ACTIVITY_CALLBACK_ENABLED: '1', MDM_ACTIVITY_CALLBACK_SECRET: secret }),
    hasEvent: () => false,
    saveEvent: () => assert.fail('event must not be saved'),
    consumeEvent: () => assert.fail('event must not be consumed'),
  });
  await assert.rejects(() => missingAppConfig.accept(EVENT, signature), /尚未配置应用标识/);
});

test('v2 adapter acknowledges duplicate event ids without a second resource read', async () => {
  const secret = 'fixture-private-secret';
  const saved = new Set();
  let consumeCalls = 0;
  const adapter = createActivityCallbackAdapter({
    config: activityCallbackConfig({
      MDM_ACTIVITY_CALLBACK_ENABLED: '1', MDM_ACTIVITY_CALLBACK_SECRET: secret, MDM_ACTIVITY_CALLBACK_APPLICATION_ID: '123',
    }),
    hasEvent: (eventId) => saved.has(eventId),
    saveEvent: (event) => saved.add(event.event_id),
    consumeEvent: async () => {
      consumeCalls += 1;
      return { account_id: '3332096437', child_user_id: '3333531550', site_id: 'MLM', outcome: 'activity_dirty' };
    },
  });
  const signature = signActivityCallbackEvent(EVENT, secret);
  assert.equal((await adapter.accept(EVENT, signature)).status, 'accepted');
  assert.equal((await adapter.accept(EVENT, signature)).status, 'duplicate');
  assert.equal(consumeCalls, 1);
});

test('v2 adapter rejects non-scalar remote ids and body/signature mismatches before dirtying state', async () => {
  const secret = 'fixture-private-secret';
  let dirtyCalls = 0;
  const adapter = createActivityCallbackAdapter({
    config: activityCallbackConfig({
      MDM_ACTIVITY_CALLBACK_ENABLED: '1', MDM_ACTIVITY_CALLBACK_SECRET: secret, MDM_ACTIVITY_CALLBACK_APPLICATION_ID: '123',
    }),
    hasEvent: () => false,
    saveEvent: () => assert.fail('invalid event must not be saved'),
    consumeEvent: () => { dirtyCalls += 1; },
  });
  for (const remoteUserId of [[], ['3333531550'], null, {}, { value: '3333531550' }]) {
    await assert.rejects(
      () => adapter.accept({ ...EVENT, remote_user_id: remoteUserId }, '00'),
      /缺少必要业务字段|签名无效/,
    );
  }
  const signature = signActivityCallbackEvent(EVENT, secret);
  await assert.rejects(
    () => adapter.accept({ ...EVENT, remote_user_id: '3407227823' }, signature),
    /签名无效/,
  );
  assert.equal(dirtyCalls, 0);
});
