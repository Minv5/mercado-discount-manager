import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { createActivityClaimConsumer } from '../src/activityClaimConsumer.js';


async function consumerFixture(topic) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'activity-claim-consumer-'));
  const secretFile = path.join(root, 'consumer.secret');
  await fs.writeFile(secretFile, 'fixture-secret-with-more-than-32-characters', 'utf8');
  const acknowledgements = [];
  const consumed = [];
  const consumer = createActivityClaimConsumer({
    claimUrl: 'https://callback.example.test/claim',
    ackUrl: 'https://callback.example.test/ack',
    secretFile,
    applicationId: 'app-1',
    pollMs: 60_000,
    consumeEvent: async (event) => {
      consumed.push(event);
      return { accepted: true, status: 'skipped', outcome: 'topic_unsupported_skipped' };
    },
    fetchImpl: async (url, options) => {
      if (url.endsWith('/claim')) {
        return {
          status: 200,
          payload: {
            events: [{
              event_id: `event-${topic}`,
              lease_id: `lease-${topic}`,
              topic,
              resource: '/marketplace/benchmarks/items/ITEM/details',
              remote_user_id: 'parent-account',
              application_id: 'app-1',
              received_at: '2026-08-26T09:39:40.000Z',
            }],
          },
        };
      }
      acknowledgements.push(options.body);
      return { status: 200, payload: { ok: true } };
    },
  });
  consumer.start();
  await consumer.pollOnce();
  consumer.stop();
  return { acknowledgements, consumed };
}

test('marketplace price suggestions never enter the desktop business consumer', async () => {
  const result = await consumerFixture('marketplace_price_suggestion');
  assert.equal(result.consumed.length, 0);
  assert.equal(result.acknowledgements.length, 1);
  assert.equal(result.acknowledgements[0].ok, false);
  assert.match(result.acknowledgements[0].error, /暂不支持/);
});

test('documented price suggestions also stay outside the desktop business scope', async () => {
  const result = await consumerFixture('price_suggestion');
  assert.equal(result.consumed.length, 0);
  assert.equal(result.acknowledgements[0].ok, false);
});

test('unknown topics remain rejected and are not passed to the business consumer', async () => {
  const result = await consumerFixture('unknown_business_topic');
  assert.equal(result.consumed.length, 0);
  assert.equal(result.acknowledgements.length, 1);
  assert.equal(result.acknowledgements[0].ok, false);
  assert.match(result.acknowledgements[0].error, /暂不支持/);
});
