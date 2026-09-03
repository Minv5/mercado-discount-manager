import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeItemSnapshot, revalidatePlannedRow } from '../src/itemSnapshot.js';
import { calculateDealPrice, validateDealPrice } from '../src/planner.js';
import { createWriteTimeItemRevalidator } from '../src/writeTimeItemSnapshot.js';

function fixture({ action = 'enroll', shared = new Map(), readItem = null } = {}) {
  const snapshots = new Map();
  let reads = 0;
  let writes = 0;
  const identityKey = ({ accountId, childUserId, siteId, itemId }) => [accountId, childUserId, siteId, itemId].join('|');
  const build = (promotionId) => createWriteTimeItemRevalidator({
    accountId: 'A',
    campaign: { child_user_id: 'C', site_id: 'MLB', promotion_id: promotionId, promotion_type: 'DEAL' },
    action,
    plan: { priceMode: 'discount', discountPercent: 20 },
    getSnapshot: (identity) => snapshots.get(identityKey(identity)) || null,
    readItem: async (itemId) => {
      reads += 1;
      return readItem ? readItem(itemId) : { id: itemId, price: 80, original_price: 100, currency_id: 'USD', status: 'active' };
    },
    persistSnapshot: async (identity) => {
      writes += 1;
      snapshots.set(identityKey(identity), normalizeItemSnapshot(identity.resource, { confirmed: true }));
    },
    revalidate: revalidatePlannedRow,
    calculateDealPrice,
    validateDealPrice,
    readCache: shared,
  });
  return { snapshots, build, counts: () => ({ reads, writes }) };
}

test('missing snapshot performs one exact GET across activities and recomputes from platform original price', async () => {
  const shared = new Map();
  const value = fixture({ shared });
  const row = { status: 'planned', item: { item_id: 'MLB1', price: 64, original_price: 80 }, deal_price: 51.2 };
  const [left, right] = await Promise.all([value.build('P1')(row), value.build('P2')(row)]);
  assert.deepEqual(value.counts(), { reads: 1, writes: 1 });
  assert.equal(left.row.item.original_price, 100);
  assert.equal(left.row.deal_price, 80);
  assert.equal(right.row.deal_price, 80);
});

test('failed missing-snapshot GET blocks only the item before any Mercado write', async () => {
  const value = fixture({ readItem: async () => { throw Object.assign(new Error('fetch failed'), { code: 'ECONNRESET' }); } });
  await assert.rejects(
    () => value.build('P1')({ status: 'planned', item: { item_id: 'MLB1', original_price: 80 }, deal_price: 64 }),
    (error) => error.code === 'ITEM_SNAPSHOT_REFRESH_FAILED' && error.policyBlocked === true,
  );
  assert.deepEqual(value.counts(), { reads: 1, writes: 0 });
});

test('confirmed snapshot and cancel path do not add unnecessary item GETs', async () => {
  const value = fixture();
  const identity = { accountId: 'A', childUserId: 'C', siteId: 'MLB', itemId: 'MLB1' };
  value.snapshots.set('A|C|MLB|MLB1', normalizeItemSnapshot({ id: 'MLB1', price: 100, original_price: 100, status: 'active' }));
  const enrolled = await value.build('P1')({ status: 'planned', item: { item_id: 'MLB1', original_price: 80 }, deal_price: 64 });
  const cancelValue = fixture({ action: 'cancel' });
  const cancelled = await cancelValue.build('P1')({ status: 'planned', item: { item_id: 'MLB2' } });
  assert.equal(enrolled.row.deal_price, 80);
  assert.equal(cancelled.changed, false);
  assert.deepEqual(value.counts(), { reads: 0, writes: 0 });
  assert.deepEqual(cancelValue.counts(), { reads: 0, writes: 0 });
});

test('mismatched item identity is rejected before snapshot persistence', async () => {
  const value = fixture({ readItem: async () => ({ id: 'MLB-OTHER', price: 100, original_price: 100 }) });
  await assert.rejects(
    () => value.build('P1')({ status: 'planned', item: { item_id: 'MLB1', original_price: 80 }, deal_price: 64 }),
    (error) => error.code === 'ITEM_SNAPSHOT_IDENTITY_MISMATCH',
  );
  assert.deepEqual(value.counts(), { reads: 1, writes: 0 });
});
