import assert from 'node:assert/strict';
import test from 'node:test';

import { MercadoLibreClient } from '../src/mlClient.js';

test('marketplace promotion item writes always include the remote user id', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: new URL(String(url)), options });
    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
  };

  try {
    const client = new MercadoLibreClient({
      accessToken: 'test-token',
      userId: '3407224975',
      callerId: '3408885754',
      marketplace: true,
    });
    await client.enrollItem({ itemId: 'MCO1', promotionId: 'P-MCO1', promotionType: 'DEAL', dealPrice: 10 });
    await client.updateItem({ itemId: 'MCO2', promotionId: 'P-MCO1', promotionType: 'DEAL', dealPrice: 9 });
    await client.cancelItem({ itemId: 'MCO3', promotionId: 'P-MCO1', promotionType: 'DEAL' });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(calls.map((call) => call.options.method), ['POST', 'PUT', 'DELETE']);
  for (const call of calls) {
    assert.equal(call.url.searchParams.get('user_id'), '3407224975');
    assert.equal(call.url.searchParams.get('app_version'), 'v2');
    assert.equal(call.options.headers['X-Caller-Id'], '3408885754');
    assert.equal(call.options.headers['X-Client-Id'], '3408885754');
  }
  assert.equal(calls[2].url.searchParams.get('promotion_id'), 'P-MCO1');
  assert.equal(calls[2].url.searchParams.get('promotion_type'), 'DEAL');
});
