import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import {
  activityCallbackConfig,
  createActivityCallbackAdapter,
  signActivityCallbackEvent,
} from '../src/activityCallbackAdapter.js';
import { PRODUCT_DISPLAY_NAME, PRODUCT_ID } from '../src/productContract.js';

const EVENT = {
  schema_version: '1',
  event_id: 'evt-1',
  account_id: 'A1',
  site_id: 'MLM',
  promotion_id: 'P-1',
  promotion_type: 'DEAL',
  cursor: '11',
  previous_cursor: '10',
};

test('activity callback is disabled by default and cannot dirty cache', async () => {
  const marked = [];
  const adapter = createActivityCallbackAdapter({
    config: activityCallbackConfig({}),
    hasEvent: () => false,
    saveEvent: () => {},
    getCacheState: () => ({ event_cursor: '10' }),
    markDirty: (row) => marked.push(row),
  });
  const result = await adapter.accept(EVENT, 'unused');
  assert.equal(result.status, 'disabled');
  assert.equal(marked.length, 0);
});

test('activity callback verifies signature, is idempotent and marks only the target with continuity evidence', async () => {
  const secret = 'local-forwarder-secret';
  const saved = new Set();
  const marked = [];
  const adapter = createActivityCallbackAdapter({
    config: activityCallbackConfig({ MDM_ACTIVITY_CALLBACK_ENABLED: '1', MDM_ACTIVITY_CALLBACK_SECRET: secret }),
    hasEvent: (eventId) => saved.has(eventId),
    saveEvent: (event) => saved.add(event.event_id),
    getCacheState: () => ({ event_cursor: '9' }),
    markDirty: (row) => marked.push(row),
  });
  await assert.rejects(() => adapter.accept(EVENT, 'bad'), /签名/);
  const signature = signActivityCallbackEvent(EVENT, secret);
  const first = await adapter.accept(EVENT, signature);
  const duplicate = await adapter.accept(EVENT, signature);
  assert.equal(first.status, 'accepted');
  assert.equal(first.gap, true);
  assert.equal(duplicate.status, 'duplicate');
  assert.deepEqual(marked, [{
    accountId: 'A1', siteId: 'MLM', promotionId: 'P-1', promotionType: 'DEAL', eventCursor: '11', gap: true,
  }]);
});

test('visible product name changes while internal compatibility identity remains stable', () => {
  assert.equal(PRODUCT_DISPLAY_NAME, '美客多活动管家');
  assert.equal(PRODUCT_ID, 'mercado-discount-manager');
  const config = fs.readFileSync(new URL('../src/config.js', import.meta.url), 'utf8');
  assert.match(config, /28758/);
  assert.match(config, /MDM_DATA_DIR/);
  const server = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  assert.match(server, /PRODUCT_DISPLAY_NAME/);
  const validation = fs.readFileSync(new URL('../scripts/validate.ps1', import.meta.url), 'utf8');
  assert.match(validation, /0x7F8E,0x5BA2,0x591A,0x6D3B,0x52A8,0x7BA1,0x5BB6/);
  assert.doesNotMatch(validation, /0x7F8E,0x5BA2,0x591A,0x6D3B,0x52A8,0x52A9,0x624B/);
  assert.doesNotMatch(validation, /0x7F8E,0x5BA2,0x591A,0x6298,0x6263,0x7BA1,0x5BB6/);
});
