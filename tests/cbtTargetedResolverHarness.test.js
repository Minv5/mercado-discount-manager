import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const ROOT = path.resolve(import.meta.dirname, '..');
const HARNESS = path.resolve(ROOT, 'scripts', 'cbt-targeted-resolver-harness.mjs');
const ROUTES = {
  '2651442567': [{ account_id: '2651442567', child_user_id: '2659555001', site_id: 'MLB' }],
  '3332096437': [{ account_id: '3332096437', child_user_id: '3333555001', site_id: 'MLB' }],
  '3408885754': [{ account_id: '3408885754', child_user_id: '3407555001', site_id: 'MLB' }],
};

function run(input) {
  const child = execFileSync(process.execPath, [HARNESS], {
    cwd: ROOT,
    input: JSON.stringify(input),
    encoding: 'utf8',
    timeout: 30_000,
  });
  assert.equal(child.trim().split('\n').length, 1);
  return JSON.parse(child);
}

function base(accountId, resource, resourceData) {
  return {
    event: { topic: 'items', resource, remote_user_id: accountId },
    resource: resourceData,
    routes: ROUTES[accountId],
    accounts: [{ account_id: accountId }],
  };
}

test('harness dry-runs three account marketplace_items fixtures with a whitelist-only result', () => {
  for (const accountId of Object.keys(ROUTES)) {
    const result = run(base(accountId, `/items/CBT-SYNTH-${accountId}`, {
      marketplace_items: [{ id: `MLB-SYNTH-${accountId}`, seller_id: ROUTES[accountId][0].child_user_id, site_id: 'MLB', status: 'active' }],
    }));
    assert.equal(result.ok, true);
    assert.equal(result.marketplace_items_length, 1);
    assert.equal(result.exact_owned_route, true);
    assert.equal(result.targeted_fallback_classification, 'exact_owned');
    assert.equal(Object.hasOwn(result, 'raw_body'), false);
    assert.equal(JSON.stringify(result).includes('SYNTH'), false);
  }
});

test('harness covers empty, missing-field, deleted and malformed fixtures without child-process failure', () => {
  const empty = run(base('2651442567', '/items/CBT-EMPTY', { marketplace_items: [] }));
  assert.equal(empty.ok, true);
  assert.equal(empty.http_status, 200);
  assert.equal(empty.targeted_fallback_classification, 'terminal_no_marketplace_children');
  assert.equal(empty.child_mapping_field_presence.marketplace_items, true);
  assert.equal(empty.child_mapping_present_empty, true);

  const missing = run(base('2651442567', '/items/CBT-MISSING', {}));
  assert.equal(missing.ok, true);
  assert.equal(missing.targeted_fallback_classification, 'quarantined_unknown');
  assert.equal(missing.child_mapping_field_presence.marketplace_items, false);
  assert.equal(missing.child_mapping_present_empty, false);

  const partial = { ...base('2651442567', '/items/CBT-PARTIAL', { marketplace_items: [] }), http_status: 206 };
  const partialResult = run(partial);
  assert.equal(partialResult.ok, true);
  assert.equal(partialResult.http_status, 206);
  assert.equal(partialResult.child_mapping_present_empty, true);
  assert.equal(partialResult.targeted_fallback_classification, 'quarantined_unknown');

  const deleted = run(base('3332096437', '/items/CBT-DELETED', {
    marketplace_items: [{ id: 'CHILD', seller_id: '3333555001', site_id: 'MLB', status: 'deleted' }],
  }));
  assert.equal(deleted.ok, true);
  assert.equal(deleted.targeted_fallback_classification, 'terminal_irrelevant');

  const malformed = run({
    event: { topic: 'items', resource: '/items/CBT-MALFORMED', remote_user_id: '3408885754' },
    resource: { marketplace_items: 'not-an-array' },
    routes: ROUTES['3408885754'],
    accounts: [{ account_id: '3408885754' }],
  });
  assert.equal(malformed.ok, true);
  assert.equal(malformed.marketplace_items_length, 0);
  assert.equal(malformed.child_mapping_malformed_present_field, true);
  assert.equal(malformed.child_mapping_present_empty, false);
  assert.equal(malformed.targeted_fallback_classification, 'quarantined_unknown');

  const invalid = run({ event: { resource: '/items/not-cbt', remote_user_id: '2651442567' }, resource: {}, routes: [], accounts: [] });
  assert.equal(invalid.ok, false);
  assert.equal(invalid.error.code, 'CBT_RESOLVER_ERROR');
  assert.equal(JSON.stringify(invalid).includes('not-cbt'), false);
});

test('harness syntax and output whitelist contain no token/seller/site values', () => {
  assert.equal(fs.existsSync(HARNESS), true);
  const source = fs.readFileSync(HARNESS, 'utf8');
  assert.match(source, /readFileSync\(0/);
  assert.match(source, /resolveCbtItemRoutes/);
  assert.doesNotMatch(source, /access_token|refresh_token|Authorization|fetch\(|https?:\/\//i);
});
