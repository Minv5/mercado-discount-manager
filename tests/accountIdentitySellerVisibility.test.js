import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import {
  accountRouteKey,
  bindActivitiesToAccountRoute,
  bindActivityToAccountRoute,
  normalizeAccountRoute,
} from '../src/accountRouteIdentity.js';
import { buildSellerCampaignBatchCreatePrecheck } from '../src/promotionCreation.js';
import { promotionKey } from '../src/planner.js';
import { filterPromotionsByConfirmedScope } from '../src/executionItemFilter.js';
import { resolveStoreIdentity } from '../src/storeNameDomain.js';

const evidencePath = new URL(
  '../data/validation-evidence/20260716-seller-campaign-visibility-root-cause/seller-campaign-visibility-root-cause-20260716.json',
  import.meta.url,
);

test('store aliases are display-only and raw names never become routing or inferred regional names', () => {
  const aliases = {
    '2651442567': '湖北',
    '3332096437': '广州',
    '3408885754': '湖南',
  };
  assert.deepEqual(resolveStoreIdentity({
    accountId: '3332096437',
    profile: { display_name: 'CNGUANGZHOULINGTANGMINB' },
    storeAliases: aliases,
  }), {
    raw_display_name: 'CNGUANGZHOULINGTANGMINB',
    store_name: '广州',
    store_name_source: 'explicit_alias',
  });
  assert.equal(resolveStoreIdentity({
    accountId: '3332096437',
    profile: { display_name: '湖南' },
  }).store_name, '店铺待命名');
});

test('account route identity is account plus child plus site and ignores swapped aliases', () => {
  const guangzhou = normalizeAccountRoute({
    account_id: '3332096437', child_user_id: '3333531550', site_id: 'mlm', store_name: '湖南',
  });
  const hunan = normalizeAccountRoute({
    account_id: '3408885754', child_user_id: '3407227823', site_id: 'MLM', store_name: '广州',
  });
  assert.equal(accountRouteKey(guangzhou), '3332096437|3333531550|MLM');
  assert.equal(accountRouteKey(hunan), '3408885754|3407227823|MLM');
});

test('explicit activity ownership mismatch is a Chinese 422 safe block', () => {
  const expected = { account_id: '3408885754', child_user_id: '3407227823', site_id: 'MLM' };
  assert.throws(
    () => bindActivityToAccountRoute({
      id: 'C-MLM1246620',
      type: 'SELLER_CAMPAIGN',
      account_id: '3332096437',
      child_user_id: '3333531550',
      site_id: 'MLM',
    }, expected),
    (error) => error?.status === 422
      && error?.code === 'ACTIVITY_ACCOUNT_ROUTE_MISMATCH'
      && /活动归属.*不一致.*停止准备/.test(String(error?.message || '')),
  );
});

test('promotion maps and confirmed execution scope keep identical promotion ids separate by child route', () => {
  const guangzhou = {
    account_id: '3332096437', child_user_id: '3333531550', site_id: 'MLM',
    promotion_id: 'C-SAME', promotion_type: 'SELLER_CAMPAIGN',
  };
  const wrongChild = { ...guangzhou, child_user_id: '3333531536' };
  assert.notEqual(promotionKey(guangzhou), promotionKey(wrongChild));
  const result = filterPromotionsByConfirmedScope({
    accountId: '3332096437',
    promotions: [guangzhou, wrongChild],
    request: { confirmedExecutionScope: { activities: [{ ...guangzhou, item_ids: ['MLM-1'] }] } },
  });
  assert.deepEqual(result.promotions, [guangzhou]);
  assert.deepEqual(result.missingActivityKeys, []);
});

test('authoritative 333 and 340 fixtures cannot borrow SELLER campaigns across account routes', () => {
  const evidence = JSON.parse(fs.readFileSync(evidencePath, 'utf8'));
  const guangzhouRoute = { account_id: '3332096437', child_user_id: '3333531550', site_id: 'MLM' };
  const hunanRoute = { account_id: '3408885754', child_user_id: '3407227823', site_id: 'MLM' };
  const guangzhou = bindActivitiesToAccountRoute(
    evidence.live_accounts['3332096437'].checks.marketplace_directory_full.promotions,
    guangzhouRoute,
  );
  const hunan = bindActivitiesToAccountRoute(
    evidence.live_accounts['3408885754'].checks.marketplace_directory_full.promotions,
    hunanRoute,
  );
  assert.deepEqual(
    guangzhou.filter((row) => row.promotion_type === 'SELLER_CAMPAIGN').map((row) => row.promotion_id),
    ['C-MLM1246620'],
  );
  assert.deepEqual(hunan.filter((row) => row.promotion_type === 'SELLER_CAMPAIGN'), []);
  assert.throws(
    () => bindActivitiesToAccountRoute(guangzhou, hunanRoute),
    (error) => error?.status === 422 && error?.code === 'ACTIVITY_ACCOUNT_ROUTE_MISMATCH',
  );
});

test('visibility unknown is review-only while only confirmed absent can enter create preview', () => {
  const result = buildSellerCampaignBatchCreatePrecheck({
    name: '95',
    startDate: '2026-07-16T00:00:00',
    finishDate: '2026-08-01T00:00:00',
    targets: [
      { account_id: '3332096437', child_user_id: '3333531550', site_id: 'MLM', detection_status: 'existing' },
      { account_id: '3408885754', child_user_id: '3407227823', site_id: 'MLM', detection_status: 'visibility_unknown' },
      { account_id: 'A', child_user_id: 'A-CHILD', site_id: 'MLB', detection_status: 'confirmed_absent' },
      { account_id: 'B', child_user_id: 'B-CHILD', site_id: 'MLC', detection_status: 'unreadable' },
    ],
  });
  assert.equal(result.existing_count, 1);
  assert.equal(result.confirmed_absent_count, 1);
  assert.equal(result.needs_manual_review_count, 1);
  assert.equal(result.unreadable_count, 1);
  assert.equal(result.preview_ready_count, 1);
  assert.deepEqual(result.prechecks.map((row) => row.account_id), ['A']);
  assert.deepEqual(result.needs_manual_review.map((row) => row.account_id), ['3408885754']);
  assert.match(result.user_message, /可验证.*不存在|确认不存在/);
});

test('server binds directory, prepare, create targets, and execution scope to the route identity', () => {
  const source = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  assert.match(source, /bindActivitiesToAccountRoute/);
  assert.match(source, /bindActivityToAccountRoute/);
  assert.match(source, /accountRouteKey/);
  assert.match(source, /child_user_id:\s*String\(target\.child_user_id/);
  assert.doesNotMatch(source, /seller_detection\?\.unknown_not_returned[^\n]*map\(\(target\) => \[sellerCampaignTargetKey/);
});
