import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  reconcileActivityCatalog,
  requireAuthoritativeActivityCatalogRead,
  selectMarketplaceUsersForCatalogRoutes,
  sellerCampaignWriteThroughPromotion,
  summarizeActivityCatalogRouteReads,
} from '../src/activityCatalogReconcile.js';
import { bindActivitiesToAccountRoute } from '../src/accountRouteIdentity.js';
import { createBalancedReadScheduler } from '../src/balancedReadScheduler.js';
import { createConfirmedExecutionScope } from '../src/submissionScopeFreeze.js';
import { resolveStoreIdentity } from '../src/storeNameDomain.js';

const evidencePath = new URL(
  '../data/validation-evidence/20260716-new-hunan-seller-campaign-source-preflight-10-10/prepare-hard-acceptance-analysis.json',
  import.meta.url,
);
const HUNAN_ROUTE = { account_id: '3408885754', child_user_id: '3407227823', site_id: 'MLM' };
const GUANGZHOU_ROUTE = { account_id: '3332096437', child_user_id: '3333531550', site_id: 'MLM' };

function runIsolated(source, dataDir) {
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: process.cwd(),
    env: { ...process.env, MDM_DATA_DIR: dataDir },
    encoding: 'utf8',
    timeout: 30_000,
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return JSON.parse(String(result.stdout || '').trim());
}

function liveFixture() {
  const evidence = JSON.parse(fs.readFileSync(evidencePath, 'utf8'));
  return bindActivitiesToAccountRoute(evidence.new_campaign.live_api.promotions, HUNAN_ROUTE);
}

test('real total 2 to 3 fixture admits the new Hunan seller campaign into targets and frozen scope only', () => {
  const live = liveFixture();
  const cached = live.filter((row) => row.promotion_id !== 'C-MLM1273939');
  const reconciled = reconcileActivityCatalog({ route: HUNAN_ROUTE, cachedPromotions: cached, livePromotions: live });

  assert.equal(reconciled.cached_total, 2);
  assert.equal(reconciled.live_total, 3);
  assert.deepEqual(reconciled.added.map((row) => row.promotion_id), ['C-MLM1273939']);
  assert.equal(reconciled.changed.length, 0);
  assert.equal(reconciled.removed.length, 0);
  assert.equal(reconciled.unchanged.length, 2);

  const targets = reconciled.live_promotions;
  const liveRead = targets.map((promotion) => ({
    ...promotion, item_status: 'candidate', item_ids: [], detail_status: 'empty', blocked: false,
  }));
  const observed = createConfirmedExecutionScope({ action: 'enroll', activities: liveRead });
  const confirmed = createConfirmedExecutionScope({ action: 'enroll', activities: liveRead });
  for (const collection of [targets, liveRead, observed.activities, confirmed.activities]) {
    assert.equal(collection.filter((row) => row.promotion_id === 'C-MLM1273939').length, 1);
    assert.equal(collection.filter((row) => row.promotion_id === 'C-MLM1246620').length, 0);
  }
  assert.equal(targets.filter((row) => row.promotion_type === 'SELLER_CAMPAIGN').length, 1);
});

test('catalog reconciliation keeps clean item cache for unchanged metadata and dirties only changed identities', () => {
  const base = bindActivitiesToAccountRoute([{
    promotion_id: 'P-ONE', promotion_type: 'DEAL', name: 'Deal', status: 'started',
    start_date: '2026-07-01T00:00:00Z', finish_date: '2026-08-01T00:00:00Z', revision: 'R1',
  }], HUNAN_ROUTE);
  const unchanged = reconcileActivityCatalog({ route: HUNAN_ROUTE, cachedPromotions: base, livePromotions: [{ ...base[0] }] });
  assert.equal(unchanged.unchanged.length, 1);
  assert.deepEqual(unchanged.dirty_identities, []);

  const changed = reconcileActivityCatalog({
    route: HUNAN_ROUTE,
    cachedPromotions: base,
    livePromotions: [{ ...base[0], status: 'finished', finish_date: '2026-07-15T00:00:00Z', revision: 'R2' }],
  });
  assert.equal(changed.changed.length, 1);
  assert.deepEqual(changed.dirty_identities.map((row) => row.reason), ['metadata_changed']);

  const removed = reconcileActivityCatalog({ route: HUNAN_ROUTE, cachedPromotions: base, livePromotions: [] });
  assert.equal(removed.removed.length, 1);
  assert.deepEqual(removed.dirty_identities.map((row) => row.reason), ['removed']);
});

test('seller campaign creation write-through is bound to the exact account child and site route', () => {
  const row = sellerCampaignWriteThroughPromotion({
    route: HUNAN_ROUTE,
    promotionId: 'C-MLM1273939',
    name: '95',
    startDate: '2026-07-16',
    finishDate: '2026-07-31',
    response: { id: 'C-MLM1273939', status: 'started', type: 'SELLER_CAMPAIGN' },
  });
  assert.deepEqual({
    account_id: row.account_id,
    child_user_id: row.child_user_id,
    site_id: row.site_id,
    promotion_id: row.promotion_id,
    promotion_type: row.promotion_type,
    name: row.name,
  }, { ...HUNAN_ROUTE, promotion_id: 'C-MLM1273939', promotion_type: 'SELLER_CAMPAIGN', name: '95' });
  assert.notEqual(`${row.account_id}|${row.child_user_id}`, `${GUANGZHOU_ROUTE.account_id}|${GUANGZHOU_ROUTE.child_user_id}`);
});

test('seller campaign write-through dirties only the exact account child and site in isolated storage', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-catalog-write-through-'));
  try {
    const result = runIsolated(`
      const repo = await import('./src/repository.js');
      const { sellerCampaignWriteThroughPromotion } = await import('./src/activityCatalogReconcile.js');
      const hunan = ${JSON.stringify(HUNAN_ROUTE)};
      const guangzhou = ${JSON.stringify(GUANGZHOU_ROUTE)};
      repo.saveMarketplaceSites(hunan.account_id, [{ user_id: hunan.child_user_id, site_id: hunan.site_id, logistic_type: 'remote' }]);
      repo.saveMarketplaceSites(guangzhou.account_id, [{ user_id: guangzhou.child_user_id, site_id: guangzhou.site_id, logistic_type: 'remote' }]);
      repo.updateMarketplaceSitePromotionStatus({ accountId: hunan.account_id, childUserId: hunan.child_user_id, count: 2, status: 'ok' });
      repo.updateMarketplaceSitePromotionStatus({ accountId: guangzhou.account_id, childUserId: guangzhou.child_user_id, count: 7, status: 'ok' });
      const promotion = sellerCampaignWriteThroughPromotion({
        route: hunan, promotionId: 'C-MLM1273939', name: '95',
        startDate: '2026-07-16', finishDate: '2026-07-31', response: { status: 'started' },
      });
      repo.saveCampaigns(hunan.account_id, [promotion], { childUserId: hunan.child_user_id, siteId: hunan.site_id });
      repo.invalidateMarketplaceSiteCatalog({ accountId: hunan.account_id, childUserId: hunan.child_user_id, siteId: hunan.site_id });
      repo.markActivityCacheDirty({
        accountId: hunan.account_id, siteId: hunan.site_id,
        promotionId: promotion.promotion_id, promotionType: promotion.promotion_type,
      });
      console.log(JSON.stringify({
        hunanCampaigns: repo.listCampaigns(hunan.account_id),
        guangzhouCampaigns: repo.listCampaigns(guangzhou.account_id),
        hunanSites: repo.listMarketplaceSites(hunan.account_id),
        guangzhouSites: repo.listMarketplaceSites(guangzhou.account_id),
        cache: repo.getActivityCacheState(hunan.account_id, hunan.site_id, promotion.promotion_id, promotion.promotion_type),
      }));
    `, dataDir);
    assert.equal(result.hunanCampaigns.length, 1);
    assert.deepEqual({
      account_id: result.hunanCampaigns[0].account_id,
      child_user_id: result.hunanCampaigns[0].child_user_id,
      site_id: result.hunanCampaigns[0].site_id,
      promotion_id: result.hunanCampaigns[0].promotion_id,
    }, { ...HUNAN_ROUTE, promotion_id: 'C-MLM1273939' });
    assert.equal(result.guangzhouCampaigns.length, 0);
    assert.equal(result.hunanSites[0].last_promotion_status, 'dirty');
    assert.equal(result.guangzhouSites[0].last_promotion_status, 'ok');
    assert.equal(result.cache.dirty, 1);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('directory format and cross-account ownership errors block instead of silently treating the catalog as empty', () => {
  assert.throws(
    () => reconcileActivityCatalog({ route: HUNAN_ROUTE, cachedPromotions: [], livePromotions: { results: [] } }),
    (error) => error?.code === 'ACTIVITY_DIRECTORY_FORMAT_INVALID' && error?.status === 422,
  );
  assert.throws(
    () => reconcileActivityCatalog({
      route: HUNAN_ROUTE,
      cachedPromotions: [],
      livePromotions: [{ ...GUANGZHOU_ROUTE, promotion_id: 'C-MLM1246620', promotion_type: 'SELLER_CAMPAIGN' }],
    }),
    (error) => error?.code === 'ACTIVITY_ACCOUNT_ROUTE_MISMATCH' && error?.status === 422,
  );
});

test('catalog route results isolate one unreadable child while preserving other readable routes', () => {
  const secondHunanRoute = { account_id: HUNAN_ROUTE.account_id, child_user_id: '3407227999', site_id: 'MLA' };
  const summary = summarizeActivityCatalogRouteReads({
    expectedRoutes: [HUNAN_ROUTE, secondHunanRoute, GUANGZHOU_ROUTE],
    results: [
      { ...HUNAN_ROUTE, status: 'ok' },
      { ...secondHunanRoute, status: 'error', error: '该站点活动目录暂时无法确认。' },
      { ...GUANGZHOU_ROUTE, status: 'ok' },
    ],
  });
  assert.deepEqual([...summary.refreshed_route_keys].sort(), [
    '3332096437|3333531550|MLM',
    '3408885754|3407227823|MLM',
  ]);
  assert.deepEqual([...summary.blocked_route_keys], ['3408885754|3407227999|MLA']);
  assert.equal(summary.errors_by_route.get('3408885754|3407227999|MLA'), '该站点活动目录暂时无法确认。');

  const missing = summarizeActivityCatalogRouteReads({ expectedRoutes: [HUNAN_ROUTE], results: [] });
  assert.deepEqual([...missing.blocked_route_keys], ['3408885754|3407227823|MLM']);
});

test('same-site child users are selected by the full account route rather than site alone', () => {
  const sameSiteOtherChild = { user_id: '3407227999', site_id: 'MLM', logistic_type: 'remote' };
  const selected = selectMarketplaceUsersForCatalogRoutes({
    accountId: HUNAN_ROUTE.account_id,
    marketplaceUsers: [
      { user_id: HUNAN_ROUTE.child_user_id, site_id: HUNAN_ROUTE.site_id, logistic_type: 'remote' },
      sameSiteOtherChild,
      { user_id: '3407227000', site_id: 'MLA', logistic_type: 'remote' },
    ],
    routes: [HUNAN_ROUTE],
    siteIds: ['MLM'],
  });
  assert.deepEqual(selected.map((row) => row.user_id), [HUNAN_ROUTE.child_user_id]);
});

test('a non-authoritative fallback cannot certify a live activity directory when official reads failed', () => {
  assert.equal(requireAuthoritativeActivityCatalogRead([
    { source: 'parent', authoritative: true, ok: false },
    { source: 'child', authoritative: true, ok: true },
    { source: 'fallback', authoritative: false, ok: true },
  ]), true);
  assert.throws(
    () => requireAuthoritativeActivityCatalogRead([
      { source: 'parent', authoritative: true, ok: false },
      { source: 'child', authoritative: true, ok: false },
      { source: 'fallback', authoritative: false, ok: true },
    ]),
    (error) => error?.code === 'ACTIVITY_DIRECTORY_UNREADABLE' && error?.status === 422,
  );
});

test('catalog reads share the bounded scheduler and never exceed its API request ceiling', async () => {
  const scheduler = createBalancedReadScheduler({ initialLimit: 6, maxLimit: 10, successesPerIncrease: 2 });
  let active = 0;
  let peak = 0;
  const accountActive = new Map();
  const accountPeak = new Map();
  const requests = Array.from({ length: 36 }, (_, index) => scheduler.schedule({
    accountId: `ACCOUNT-${index % 3}`,
    key: `catalog-route-${index}`,
  }, async () => {
    const accountId = `ACCOUNT-${index % 3}`;
    active += 1;
    accountActive.set(accountId, Number(accountActive.get(accountId) || 0) + 1);
    peak = Math.max(peak, active);
    accountPeak.set(accountId, Math.max(Number(accountPeak.get(accountId) || 0), accountActive.get(accountId)));
    await new Promise((resolve) => setTimeout(resolve, 2));
    active -= 1;
    accountActive.set(accountId, accountActive.get(accountId) - 1);
    return index;
  }));
  await Promise.all(requests);
  assert.ok(peak <= 10, `peak=${peak}`);
  assert.ok(scheduler.snapshot().peakInflight <= 10);
  assert.ok([...accountPeak.values()].every((value) => value <= 4), JSON.stringify(Object.fromEntries(accountPeak)));
});

test('verified raw identities correct only stale regional aliases without hard-coding account ids into routing', () => {
  const cases = [
    ['CNHUBEISHENGRUIHESHANGM', '湖北店', '湖北'],
    ['CNGUANGZHOULINGTANGMINB', '湖南店', '广州'],
    ['CNLIUYANGSHIZHEPINGDIAN', '广东店', '湖南'],
  ];
  for (const [rawDisplayName, staleAlias, expected] of cases) {
    const identity = resolveStoreIdentity({ accountId: 'UNROUTED', rawDisplayName, storeAliases: { UNROUTED: staleAlias } });
    assert.equal(identity.store_name, expected);
    assert.equal(identity.store_name_source, 'verified_profile_alias_correction');
  }
  assert.equal(resolveStoreIdentity({
    accountId: 'UNROUTED', rawDisplayName: 'CNLIUYANGSHIZHEPINGDIAN', storeAliases: { UNROUTED: '湖南旗舰店' },
  }).store_name, '湖南旗舰店');
});

test('prepare refreshes only dirty or stale catalog routes while item cache reuse remains metadata-diff driven', () => {
  const server = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  const start = server.indexOf('async function refreshActivityCatalogForPrepare');
  const end = server.indexOf('\nfunction activityCatalogSellerStatus', start);
  const section = server.slice(start, end);
  assert.ok(start >= 0 && end > start);
  assert.match(section, /planActivityCatalogRoutes\(/);
  assert.match(section, /cached_route_keys/);
  assert.match(section, /refreshRoutes/);
  assert.match(section, /liveCatalogSiteIds/);
  assert.match(server, /reconcileActivityCatalog/);
  assert.match(server, /sellerCampaignWriteThroughPromotion/);
  assert.match(server, /summarizeActivityCatalogRouteReads/);
  assert.match(server, /selectMarketplaceUsersForCatalogRoutes/);
  assert.match(server, /requireAuthoritativeActivityCatalogRead/);
  assert.match(server, /blocked_route_keys/);
  assert.match(server, /catalog_identity_changes/);
  assert.match(server, /reconciliation\.dirty_identities\.map/);
  assert.match(server, /invalidateMarketplaceSiteCatalog\(\{ accountId, childUserId, siteId \}\)/);
  assert.match(server, /saveCampaigns\(accountId, \[writeThrough\]/);
});
