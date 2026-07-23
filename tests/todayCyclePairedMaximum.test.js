import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import { decideToday } from '../src/today.js';
import { promotionKey } from '../src/planner.js';
import { submissionRequestFingerprint } from '../src/submissionPersistence.js';

const promotions = [
  { account_id: 'A', site_id: 'MLM', promotion_id: 'C-1', promotion_type: 'SELLER_CAMPAIGN' },
  { account_id: 'B', site_id: 'MLM', promotion_id: 'P-1', promotion_type: 'DEAL' },
];
const started = new Map(promotions.map((row) => [promotionKey(row), 3]));

function baseline(baseSeller, baseOfficial, seller, official, sourceTime) {
  return {
    source: 'latest_effective_discount',
    source_time: sourceTime,
    base_seller_discount: baseSeller,
    base_official_discount: baseOfficial,
    seller_discount: seller,
    official_discount: official,
    seller_max_discount: 10,
    official_max_discount: 10,
  };
}

test('paired global update baseline reaches 10/10 by update before a later cancel cycle', () => {
  const today = new Date('2026-07-15T10:00:00+08:00');
  const nineTen = decideToday({
    promotions, startedCountsByPromotion: started, today,
    globalCycle: baseline(9, 10, 10, 10, '2026-07-14T21:00:00+08:00'),
  });
  assert.equal(nineTen.action, 'update');
  assert.deepEqual(nineTen.rows.map((row) => row.discount), [10, 10]);
  assert.deepEqual([...new Set(nineTen.rows.map((row) => row.action))], ['update']);

  const sameDayTen = decideToday({
    promotions, startedCountsByPromotion: started, today,
    globalCycle: baseline(10, 10, 10, 10, '2026-07-15T08:00:00+08:00'),
  });
  assert.notEqual(sameDayTen.action, 'cancel');

  const priorDayTen = decideToday({
    promotions, startedCountsByPromotion: started, today,
    globalCycle: baseline(10, 10, 10, 10, '2026-07-14T21:00:00+08:00'),
  });
  assert.equal(priorDayTen.action, 'cancel');

  const noStarted = decideToday({
    promotions, startedCountsByPromotion: new Map(), today,
    globalCycle: baseline(10, 10, 10, 10, '2026-07-14T21:00:00+08:00'),
  });
  assert.notEqual(noStarted.action, 'cancel');
});

test('one activity at 10 cannot contaminate a multi-account update phase and no history still enrolls', () => {
  const today = new Date('2026-07-15T10:00:00+08:00');
  const states = new Map([
    [promotionKey(promotions[0]), { seller_discount_percent: 9, status: 'completed', updated_at: '2026-07-14T21:00:00+08:00' }],
    [promotionKey(promotions[1]), { official_discount_percent: 10, status: 'completed', updated_at: '2026-07-14T21:00:00+08:00' }],
  ]);
  const decision = decideToday({
    promotions, cycleStatesByPromotion: states, startedCountsByPromotion: started, today,
    globalCycle: baseline(9, 10, 10, 10, '2026-07-14T21:00:00+08:00'),
  });
  assert.equal(decision.action, 'update');
  assert.equal(decideToday({ promotions, startedCountsByPromotion: new Map(), today }).action, 'enroll');
});

test('scope decision is aggregate and old paused fingerprints are versioned out', () => {
  const server = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  const desktop = fs.readFileSync(new URL('../desktop-pyside/main_window.py', import.meta.url), 'utf8');
  assert.match(server, /buildTodayDecisionForScope/);
  assert.match(desktop, /"accountIds": account_ids/);
  assert.doesNotMatch(desktop, /for account_id in account_ids:[\s\S]{0,220}\/api\/today\/decision/);
  const current = submissionRequestFingerprint({ action: 'auto', accountIds: ['A', 'B'] });
  const old = submissionRequestFingerprint({ action: 'auto', accountIds: ['A', 'B'], decision_contract_version: 1 });
  assert.notEqual(current, old);
});
