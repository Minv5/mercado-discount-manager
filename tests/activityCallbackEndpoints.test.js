import assert from 'node:assert/strict';
import test from 'node:test';

import { activityClaimConfig } from '../src/activityClaimConsumer.js';
import {
  DEFAULT_ACTIVITY_ACK_URL,
  DEFAULT_ACTIVITY_CLAIM_URL,
  DEFAULT_WEBHOOK_CALLBACK_URL,
} from '../src/callbackEndpoints.js';

test('dedicated webhook endpoint contract uses the Tencent callback origin', () => {
  assert.equal(DEFAULT_WEBHOOK_CALLBACK_URL, 'https://webhook.xingtupro1020.com/webhook/mercado-libre/');
  assert.equal(DEFAULT_ACTIVITY_CLAIM_URL, 'https://webhook.xingtupro1020.com/meli-callback/consumer/claim');
  assert.equal(DEFAULT_ACTIVITY_ACK_URL, 'https://webhook.xingtupro1020.com/meli-callback/consumer/ack');
  const config = activityClaimConfig({}, {});
  assert.equal(config.claimUrl, DEFAULT_ACTIVITY_CLAIM_URL);
  assert.equal(config.ackUrl, DEFAULT_ACTIVITY_ACK_URL);
});

test('legacy saved claim and ack endpoints migrate but explicit custom endpoints remain authoritative', () => {
  const migrated = activityClaimConfig({}, {
    activityCallbackClaimUrl: 'https://xingtupro1020.com/meli-callback/consumer/claim',
    activityCallbackAckUrl: 'https://xingtupro1020.com/meli-callback/consumer/ack',
  });
  assert.equal(migrated.claimUrl, DEFAULT_ACTIVITY_CLAIM_URL);
  assert.equal(migrated.ackUrl, DEFAULT_ACTIVITY_ACK_URL);

  const custom = activityClaimConfig({}, {
    activityCallbackClaimUrl: 'https://callback.example.test/claim',
    activityCallbackAckUrl: 'https://callback.example.test/ack',
  });
  assert.equal(custom.claimUrl, 'https://callback.example.test/claim');
  assert.equal(custom.ackUrl, 'https://callback.example.test/ack');
});
