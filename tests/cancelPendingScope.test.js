import fs from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

const server = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
const repository = fs.readFileSync(new URL('../src/repository.js', import.meta.url), 'utf8');
const db = fs.readFileSync(new URL('../src/db.js', import.meta.url), 'utf8');
const planner = fs.readFileSync(new URL('../src/planner.js', import.meta.url), 'utf8');

test('batch cancellation includes pending activity items in the frozen scope', () => {
  assert.match(server, /const CANCEL_ITEM_STATUSES = Object\.freeze\(\['started', 'pending'\]\)/);
  assert.match(server, /function itemStatusesForAction\(action, itemStatus\)/);
  assert.match(server, /listItemsForAction\(accountId, promotions, 'cancel', itemStatus\)/);
  assert.match(server, /action === 'cancel' && itemStatus === 'started' && request\.cancelIncludePending !== false/);
  assert.doesNotMatch(server, /cancelLiveRead = action === 'cancel' && CANCEL_ITEM_STATUSES\.includes\(itemStatus\)/);
  assert.match(server, /A clean cache is reusable for cancellation/);
  assert.match(server, /listItemsForAction\(\s*promotion\.account_id,\s*\[promotion\],\s*action,\s*itemStatus,\s*\)/);
  assert.match(server, /Promise\.all\(CANCEL_ITEM_STATUSES\.map\(\(status\) => \(\s*client\.fetchAllPromotionItems/);
  assert.match(server, /const pendingResult = await client\.fetchAllPromotionItems\(\{[\s\S]*?status: 'pending'/);
  assert.match(server, /cancellationReadbackApplied\(\{[\s\S]*?pendingComplete,[\s\S]*?inPending: pendingByItem\.has\(itemId\)/);
  assert.match(planner, /action === 'cancel'\) return status === 'started' \|\| status === 'pending'/);
});

test('repository exposes route-scoped multi-status reads for cancellation', () => {
  assert.match(repository, /export function listItemsForPromotionStatuses\(/);
  assert.match(repository, /export function listItemsForPromotionsByStatuses\(/);
  assert.match(repository, /export function listItemFetchStatesForPromotionsByStatuses\(/);
  assert.match(repository, /for \(const status of \['candidate', 'pending', 'started'\]\)/);
});

test('item status query has normalized item lookup indexes', () => {
  assert.match(db, /idx_promo_action_results_item_norm/);
  assert.match(db, /idx_promo_items_item_norm/);
  assert.match(db, /idx_item_price_cache_item_norm/);
});
