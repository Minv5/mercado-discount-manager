import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

test('item index resolves every exact promotion relation inside account and site scope', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-targeted-cancel-index-'));
  process.env.MDM_DATA_DIR = dataDir;
  process.env.MDM_DB_PATH = path.join(dataDir, 'targeted.sqlite');
  const repo = await import('../src/repository.js');

  const routes = [
    { accountId: 'A1', childUserId: 'C1', siteId: 'MLB', promotionId: 'P-1', promotionType: 'DEAL' },
    { accountId: 'A1', childUserId: 'C1', siteId: 'MLB', promotionId: 'P-2', promotionType: 'SMART' },
    { accountId: 'A2', childUserId: 'C2', siteId: 'MLM', promotionId: 'P-3', promotionType: 'SELLER_CAMPAIGN' },
  ];
  for (const route of routes) {
    repo.saveCampaigns(route.accountId, [{
      promotion_id: route.promotionId,
      promotion_type: route.promotionType,
      name: route.promotionId,
      status: 'started',
    }], route);
    repo.saveItems(route.accountId, route.promotionId, route.promotionType, [
      { item_id: route.siteId === 'MLM' ? 'MLM200' : 'MLB100', status: 'started' },
    ], { ...route, itemStatus: 'started' });
  }
  repo.saveCampaigns('A1', [{
    promotion_id: 'P-CANDIDATE', promotion_type: 'DEAL', name: '候选活动', status: 'started',
  }], { childUserId: 'C1', siteId: 'MLB' });
  repo.saveItems('A1', 'P-CANDIDATE', 'DEAL', [
    { item_id: 'MLB300', status: 'candidate', original_price: 100, price: 100 },
  ], { childUserId: 'C1', siteId: 'MLB', itemStatus: 'candidate' });

  const allMlb = repo.listPromotionRelationsByItemIds({
    accountIds: ['A1', 'A2'], itemIds: ['mlb100'], statuses: ['started', 'pending'],
  });
  assert.equal(allMlb.length, 2);
  assert.deepEqual(allMlb.map((row) => [row.account_id, row.child_user_id, row.site_id, row.promotion_id]), [
    ['A1', 'C1', 'MLB', 'P-1'],
    ['A1', 'C1', 'MLB', 'P-2'],
  ]);

  const wrongSite = repo.listPromotionRelationsByItemIds({
    accountIds: ['A1'], itemIds: ['MLB100'], siteIds: ['MLM'], statuses: ['started'],
  });
  assert.deepEqual(wrongSite, []);

  const secondAccount = repo.listPromotionRelationsByItemIds({
    accountIds: ['A2'], itemIds: ['MLM200'], siteIds: ['MLM'], statuses: ['started'],
  });
  assert.equal(secondAccount.length, 1);
  assert.equal(secondAccount[0].promotion_id, 'P-3');

  const candidate = repo.listPromotionRelationsByItemIds({
    accountIds: ['A1'], itemIds: ['MLB300'], siteIds: ['MLB'], statuses: ['candidate'],
  });
  assert.equal(candidate.length, 1);
  assert.equal(candidate[0].promotion_id, 'P-CANDIDATE');

  const pendingRoute = {
    accountId: 'A1', childUserId: 'C1', siteId: 'MLB', promotionId: 'P-PENDING', promotionType: 'DEAL',
  };
  repo.saveCampaigns('A1', [{ promotion_id: 'P-PENDING', promotion_type: 'DEAL', name: '待确认活动', status: 'started' }], pendingRoute);
  repo.saveItems('A1', 'P-PENDING', 'DEAL', [
    { item_id: 'MLB400', status: 'pending', original_price: 100, price: 90 },
  ], { ...pendingRoute, itemStatus: 'pending' });
  for (const status of ['candidate', 'pending', 'started']) {
    repo.saveItemFetchState({
      ...pendingRoute,
      itemStatus: status,
      platformTotal: status === 'pending' ? 1 : 0,
      savedCount: status === 'pending' ? 1 : 0,
      detailStatus: status === 'pending' ? 'full' : 'empty',
    });
  }
  repo.applySuccessfulPromotionItemWrites({ ...pendingRoute, action: 'cancel', items: [{ itemId: 'MLB400' }] });
  repo.reconcilePromotionItemFetchCounts(pendingRoute);
  assert.equal(repo.getItemFetchState('A1', 'P-PENDING', 'DEAL', 'pending', pendingRoute).saved_count, 0);
  assert.equal(repo.getItemFetchState('A1', 'P-PENDING', 'DEAL', 'candidate', pendingRoute).saved_count, 1);

  repo.removeGhostPromotionItem({
    accountId: 'A1',
    childUserId: 'C1',
    siteId: 'MLB',
    promotionId: 'P-1',
    promotionType: 'DEAL',
    itemId: 'MLB100',
  });
  const remainingStarted = repo.listPromotionRelationsByItemIds({
    accountIds: ['A1'],
    itemIds: ['MLB100'],
    siteIds: ['MLB'],
    statuses: ['candidate', 'pending', 'started'],
  });
  assert.deepEqual(remainingStarted.map((row) => [row.promotion_id, row.promotion_type]), [['P-2', 'SMART']]);

  const cleanupRoute = { accountId: 'A1', childUserId: 'C1', siteId: 'MLB' };
  for (const fixture of [
    { promotionId: 'P-EXPIRED', status: 'started', finishDate: '2026-08-25T23:59:59Z', itemId: 'MLB501', removed: true },
    { promotionId: 'P-CLOSED', status: 'closed', finishDate: '2026-09-05T23:59:59Z', itemId: 'MLB502', removed: true },
    { promotionId: 'P-ACTIVE', status: 'started', finishDate: '2026-09-05T23:59:59Z', itemId: 'MLB503', removed: false },
  ]) {
    const identity = { ...cleanupRoute, promotionId: fixture.promotionId, promotionType: 'DEAL' };
    repo.saveCampaigns('A1', [{
      promotion_id: fixture.promotionId,
      promotion_type: 'DEAL',
      name: fixture.promotionId,
      status: fixture.status,
      finish_date: fixture.finishDate,
    }], cleanupRoute);
    repo.saveItems('A1', fixture.promotionId, 'DEAL', [{
      item_id: fixture.itemId,
      status: 'started',
    }], { ...cleanupRoute, itemStatus: 'started' });
    repo.saveItemFetchState({
      ...identity,
      itemStatus: 'started',
      platformTotal: 1,
      savedCount: 1,
      detailStatus: 'full',
    });
    repo.saveActivityCacheState({
      ...cleanupRoute,
      promotionId: fixture.promotionId,
      promotionType: 'DEAL',
      dirty: false,
      continuity: 'continuous',
      itemsFullCheckedAt: '2026-08-25T00:00:00.000Z',
    });
  }
  const cleanup = repo.cleanupRemovedCampaignItemData({ businessDate: '2026-08-26' });
  assert.equal(cleanup.cleaned_inactive_campaigns, 2);
  for (const fixture of [
    { promotionId: 'P-EXPIRED', itemId: 'MLB501', removed: true },
    { promotionId: 'P-CLOSED', itemId: 'MLB502', removed: true },
    { promotionId: 'P-ACTIVE', itemId: 'MLB503', removed: false },
  ]) {
    const identity = { ...cleanupRoute, promotionId: fixture.promotionId, promotionType: 'DEAL' };
    const relations = repo.listPromotionRelationsByItemIds({
      accountIds: ['A1'],
      itemIds: [fixture.itemId],
      siteIds: ['MLB'],
      statuses: ['candidate', 'pending', 'started'],
    });
    assert.equal(relations.length, fixture.removed ? 0 : 1);
    assert.equal(!repo.getItemFetchState('A1', fixture.promotionId, 'DEAL', 'started', cleanupRoute), fixture.removed);
    assert.equal(!repo.getActivityCacheState(identity), fixture.removed);
  }
});
