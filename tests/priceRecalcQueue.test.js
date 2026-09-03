import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';


function runIsolated(source) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-price-recalc-'));
  try {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: process.cwd(),
      env: { ...process.env, MDM_DATA_DIR: dataDir },
      encoding: 'utf8',
      timeout: 30_000,
    });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    return JSON.parse(String(result.stdout || '').trim());
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

test('item price webhook queues active promotion repricing and successful update resolves it', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    const promotion = {
      account_id: 'A', child_user_id: 'CHILD', site_id: 'MLB',
      promotion_id: 'P-1', promotion_type: 'DEAL'
    };
    repo.saveItems('A', 'P-1', 'DEAL', [{
      id: 'MLB1', status: 'started', original_price: 100, price: 82, currency_id: 'USD'
    }], { childUserId: 'CHILD', siteId: 'MLB', itemStatus: 'started' });
    const unchanged = repo.updateItemPriceByWebhook({
      accountId: 'A', childUserId: 'CHILD', siteId: 'MLB', itemId: 'MLB1',
      price: 82, originalPrice: 100, status: 'active'
    });
    const changed = repo.updateItemPriceByWebhook({
      accountId: 'A', childUserId: 'CHILD', siteId: 'MLB', itemId: 'MLB1',
      price: 82, originalPrice: 120, status: 'active'
    });
    const changedAgain = repo.updateItemPriceByWebhook({
      accountId: 'A', childUserId: 'CHILD', siteId: 'MLB', itemId: 'MLB1',
      price: 82, originalPrice: 130, status: 'active'
    });
    const before = repo.listItems('A', 'P-1', 'DEAL', 'started', promotion)[0];
    const countsBefore = Object.fromEntries(repo.listPendingActivityPriceRecalcCountsForPromotions('A', [promotion]));
    const taskId = Number(repo.createTask({
      accountId: 'A', promotionId: 'P-1', promotionType: 'DEAL', action: 'update', mode: 'real',
      discountPercent: 18, plan: { total: 1, planned: 1, skipped: 0, priceMode: 'discount', rows: [] }
    }));
    repo.saveExecutionResult({
      taskId, accountId: 'A', promotionId: 'P-1', promotionType: 'DEAL', itemId: 'MLB1',
      action: 'update', mode: 'real', status: 'success', dealPrice: 106.6
    });
    const after = repo.listItems('A', 'P-1', 'DEAL', 'started', promotion)[0];
    const countsAfter = Object.fromEntries(repo.listPendingActivityPriceRecalcCountsForPromotions('A', [promotion]));
    console.log(JSON.stringify({ unchanged, changed, changedAgain, before, after, countsBefore, countsAfter }));
  `);

  assert.equal(result.unchanged.reprice_queued, 0);
  assert.equal(result.changed.reprice_queued, 1);
  assert.equal(result.changedAgain.reprice_queued, 1);
  assert.equal(result.before.price_recalc_required, 1);
  assert.equal(result.before.price_recalc_previous_base, 100);
  assert.equal(result.before.price_recalc_new_base, 130);
  assert.equal(Object.values(result.countsBefore)[0], 1);
  assert.equal(result.after.price_recalc_required, 0);
  assert.equal(Object.values(result.countsAfter)[0], 0);
});

test('candidate-only relations are not queued for active-price recalculation', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    const promotion = {
      account_id: 'A', child_user_id: 'CHILD', site_id: 'MLB',
      promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN'
    };
    repo.saveItems('A', 'C-1', 'SELLER_CAMPAIGN', [{
      id: 'MLB2', status: 'candidate', original_price: 100, price: 100
    }], { childUserId: 'CHILD', siteId: 'MLB', itemStatus: 'candidate' });
    const update = repo.updateItemPriceByWebhook({
      accountId: 'A', childUserId: 'CHILD', siteId: 'MLB', itemId: 'MLB2',
      price: 120, originalPrice: 120, status: 'active'
    });
    const counts = Object.fromEntries(repo.listPendingActivityPriceRecalcCountsForPromotions('A', [promotion]));
    console.log(JSON.stringify({ update, counts }));
  `);
  assert.equal(result.update.reprice_queued, 0);
  assert.equal(Object.values(result.counts)[0], 0);
});

test('verified cancellation restores candidate price from the route item cache even when cancellation is newer', () => {
  const result = runIsolated(`
    const repo = await import('./src/repository.js');
    const { buildPlan } = await import('./src/planner.js');
    const promotion = {
      account_id: 'A', child_user_id: 'CHILD', site_id: 'MLB',
      promotion_id: 'P-1', promotion_type: 'DEAL'
    };
    repo.saveItems('A', 'P-1', 'DEAL', [{
      id: 'MLB1', status: 'started', original_price: 100, price: 82, currency_id: 'USD'
    }], { childUserId: 'CHILD', siteId: 'MLB', itemStatus: 'started' });
    repo.upsertItemPriceCache({
      accountId: 'A', childUserId: 'CHILD', siteId: 'MLB', itemId: 'MLB1',
      price: 120, originalPrice: 120, currencyId: 'USD', status: 'active'
    });
    repo.applySuccessfulPromotionItemWrites({
      accountId: 'A', childUserId: 'CHILD', siteId: 'MLB', promotionId: 'P-1', promotionType: 'DEAL',
      action: 'cancel', items: [{ itemId: 'MLB1' }]
    });
    const candidate = repo.listItems('A', 'P-1', 'DEAL', 'candidate', promotion)[0];
    const plan = buildPlan({
      action: 'enroll', promotion, items: [candidate], priceMode: 'discount', discountPercent: 18
    });
    console.log(JSON.stringify({ candidate, dealPrice: plan.rows[0].deal_price }));
  `);
  assert.equal(result.candidate.original_price, 120);
  assert.equal(result.candidate.price, 120);
  assert.equal(result.candidate.source, 'cancel_verified_local_transition');
  assert.equal(result.dealPrice, 98.4);
});
