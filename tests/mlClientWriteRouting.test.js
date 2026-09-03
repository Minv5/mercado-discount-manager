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

test('marketplace item promotion lookup uses one item-scoped GET', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: new URL(String(url)), options });
    return new Response('[{"id":"P-1","type":"DEAL","status":"started"}]', {
      status: 200, headers: { 'content-type': 'application/json' },
    });
  };
  try {
    const client = new MercadoLibreClient({
      accessToken: 'test-token', userId: '3407224975', callerId: '3408885754', marketplace: true,
    });
    const rows = await client.getItemPromotions({ itemId: 'MLB100' });
    assert.equal(rows.length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, 'GET');
  assert.equal(calls[0].url.pathname, '/marketplace/seller-promotions/items/MLB100');
  assert.equal(calls[0].url.searchParams.get('user_id'), '3407224975');
  assert.equal(calls[0].url.searchParams.get('app_version'), 'v2');
});

test('write methods leave retry ownership to the outer executor', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response('{"message":"service unavailable"}', { status: 503, headers: { 'content-type': 'application/json' } });
  };
  try {
    const client = new MercadoLibreClient({ accessToken: 'test-token', userId: 'A1' });
    await assert.rejects(() => client.cancelItem({ itemId: 'MLB1', promotionId: 'P1', promotionType: 'DEAL' }));
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls, 1);
});
