import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import {
  activityItemsDecision,
  buildItemIdentitySummary,
  candidatePreparationReadDecision,
  planActivityCatalogRoutes,
} from '../src/activityChangeCache.js';
import {
  buildFinalRevalidationPlan,
  buildPreparedRouteSnapshot,
  comparePreparedRouteSnapshot,
} from '../src/finalRevalidationPlan.js';
import { MercadoLibreClient } from '../src/mlClient.js';
import {
  commitSubmission,
  createSubmissionPersistence,
} from '../src/submissionPersistence.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const NOW = new Date('2026-07-26T04:00:00.000Z');
const CHECKED_AT = '2026-07-26T01:00:00.000Z';

function activity(child, site, promotion) {
  return {
    account_id: 'A-1',
    child_user_id: child,
    site_id: site,
    promotion_id: promotion,
    promotion_type: 'DEAL',
    status: 'started',
  };
}

function routeOf(row) {
  return {
    account_id: row.account_id,
    child_user_id: row.child_user_id,
    site_id: row.site_id,
  };
}

function routeKey(row) {
  return `${row.account_id}|${row.child_user_id}|${row.site_id}`;
}

function cleanRouteState(overrides = {}) {
  return {
    catalog_checked_at: CHECKED_AT,
    updated_at: CHECKED_AT,
    dirty: 0,
    continuity: 'continuous',
    event_cursor: 'evt-10',
    last_error: null,
    ...overrides,
  };
}

function cleanActivityState(overrides = {}) {
  return {
    ...cleanRouteState(),
    items_full_checked_at: CHECKED_AT,
    ...overrides,
  };
}

function fullFetchState(overrides = {}) {
  return {
    updated_at: CHECKED_AT,
    detail_status: 'ok',
    platform_total: 2,
    saved_count: 2,
    raw_json: JSON.stringify({
      is_full_fetch: true,
      identity_summary: buildItemIdentitySummary(['I-1', 'I-2'], { complete: true }),
    }),
    ...overrides,
  };
}

function sha256(value) {
  return crypto.createHash('sha256').update(JSON.stringify(value), 'utf8').digest('hex').toUpperCase();
}

function resignRouteRow(row = {}) {
  const facts = {
    route_key: row.route_key,
    account_id: row.account_id,
    child_user_id: row.child_user_id,
    site_id: row.site_id,
    catalog_checked_at: row.catalog_checked_at,
    updated_at: row.updated_at,
    dirty: row.dirty,
    continuity: row.continuity,
    event_cursor: row.event_cursor,
    last_error: row.last_error,
  };
  return { ...row, digest: sha256(facts) };
}

function resignPreparedSnapshot(snapshot = {}) {
  const payload = {
    version: snapshot.version,
    source: snapshot.source,
    captured_at: snapshot.captured_at,
    business_date: snapshot.business_date,
    routes: snapshot.routes,
  };
  return { ...snapshot, digest: sha256(payload) };
}

test('same-day second enrollment and immediate final commit perform zero catalog and item reads', () => {
  const activities = [
    activity('CH-1', 'MLM', 'P-1'),
    activity('CH-2', 'MLB', 'P-2'),
  ];
  const routes = activities.map(routeOf);
  const routeStates = new Map(routes.map((route) => [routeKey(route), cleanRouteState()]));
  const getRouteState = (route) => routeStates.get(routeKey(route));

  const firstPreparationCatalog = planActivityCatalogRoutes(routes, getRouteState, NOW);
  assert.equal(firstPreparationCatalog.refresh.length, 0);
  assert.equal(firstPreparationCatalog.cached.length, 2);
  for (const row of activities) {
    const cacheDecision = activityItemsDecision({
      promotion: row,
      cacheState: cleanActivityState(),
      fetchState: fullFetchState(),
      itemStatus: 'candidate',
      now: NOW,
    });
    assert.equal(cacheDecision.refresh, false);
    assert.equal(candidatePreparationReadDecision({
      action: 'enroll',
      itemStatus: 'candidate',
      cacheDecision,
      fetchState: fullFetchState(),
      now: NOW,
    }).probe, false);
  }

  const preparedRouteSnapshot = buildPreparedRouteSnapshot({
    routes,
    getRouteState,
    capturedAt: NOW,
  });
  const evidence = comparePreparedRouteSnapshot({
    preparedSnapshot: preparedRouteSnapshot,
    routes,
    getRouteState,
    now: NOW,
  });
  const finalPlan = buildFinalRevalidationPlan({
    confirmedScope: { action: 'enroll', activities },
    currentPromotions: activities,
    action: 'enroll',
    preparedAt: NOW.toISOString(),
    now: NOW,
    verificationEvidence: evidence,
    getCacheState: () => cleanActivityState(),
    getFetchState: () => fullFetchState(),
  });
  assert.equal(finalPlan.platform_read_required, false);
  assert.equal(finalPlan.required_live_route_keys.size, 0);
  assert.equal(finalPlan.item_read_identity_keys.size, 0);
});

test('one webhook-dirty route refreshes only that route and its changed activity', () => {
  const changed = activity('CH-1', 'MLM', 'P-1');
  const unchanged = activity('CH-2', 'MLB', 'P-2');
  const routes = [changed, unchanged].map(routeOf);
  const preparedStates = new Map(routes.map((route) => [routeKey(route), cleanRouteState()]));
  const prepared = buildPreparedRouteSnapshot({
    routes,
    getRouteState: (route) => preparedStates.get(routeKey(route)),
    capturedAt: NOW,
  });

  const dirtyStates = new Map(preparedStates);
  dirtyStates.set(routeKey(changed), cleanRouteState({
    dirty: 1,
    updated_at: '2026-07-26T03:00:00.000Z',
    event_cursor: 'evt-11',
  }));
  const routePlan = planActivityCatalogRoutes(routes, (route) => dirtyStates.get(routeKey(route)), NOW);
  assert.deepEqual(routePlan.refresh.map(routeKey), [routeKey(changed)]);

  dirtyStates.set(routeKey(changed), cleanRouteState({
    catalog_checked_at: '2026-07-26T03:10:00.000Z',
    updated_at: '2026-07-26T03:10:00.000Z',
    event_cursor: 'evt-11',
  }));
  const evidence = comparePreparedRouteSnapshot({
    preparedSnapshot: prepared,
    routes,
    getRouteState: (route) => dirtyStates.get(routeKey(route)),
    liveRouteKeys: [routeKey(changed)],
    attemptedRouteKeys: [routeKey(changed)],
    now: NOW,
  });
  const newActivity = activity('CH-1', 'MLM', 'P-NEW');
  const finalPlan = buildFinalRevalidationPlan({
    confirmedScope: { action: 'enroll', activities: [changed, unchanged] },
    currentPromotions: [changed, unchanged, newActivity],
    catalogRefreshes: [{
      blocked_route_keys: [],
      catalog_identity_changes: [{ key: changed, reason: 'metadata_changed' }],
    }],
    action: 'enroll',
    preparedAt: NOW.toISOString(),
    now: NOW,
    verificationEvidence: evidence,
    getCacheState: () => cleanActivityState(),
    getFetchState: () => fullFetchState(),
  });
  assert.equal(finalPlan.platform_read_required, false);
  assert.deepEqual([...finalPlan.item_read_identity_keys], ['A-1|CH-1|MLM|P-1|DEAL']);
  assert.equal(finalPlan.excluded_new_activity_count, 1);
});

test('cross-day route evidence requires recalibration while same-day single-page probe never upgrades', () => {
  const row = activity('CH-1', 'MLM', 'P-1');
  const route = routeOf(row);
  const prepared = buildPreparedRouteSnapshot({
    routes: [route],
    getRouteState: () => cleanRouteState(),
    capturedAt: NOW,
  });
  const nextDay = new Date('2026-07-27T04:00:00.000Z');
  const evidence = comparePreparedRouteSnapshot({
    preparedSnapshot: prepared,
    routes: [route],
    getRouteState: () => cleanRouteState(),
    now: nextDay,
  });
  const plan = buildFinalRevalidationPlan({
    confirmedScope: { action: 'enroll', activities: [row] },
    currentPromotions: [row],
    action: 'enroll',
    preparedAt: NOW.toISOString(),
    now: nextDay,
    verificationEvidence: evidence,
    getCacheState: () => cleanActivityState(),
    getFetchState: () => fullFetchState(),
  });
  assert.deepEqual([...plan.required_live_route_keys], ['A-1|CH-1|MLM']);

  const cacheDecision = activityItemsDecision({
    promotion: row,
    cacheState: cleanActivityState(),
    fetchState: fullFetchState(),
    itemStatus: 'candidate',
    now: NOW,
  });
  assert.deepEqual(candidatePreparationReadDecision({
    action: 'enroll',
    itemStatus: 'candidate',
    cacheDecision,
    fetchState: fullFetchState(),
    now: NOW,
  }), {
    probe: false,
    reason: 'same_day_candidate_cache',
  });
});

test('prepared route evidence is invalidated by every local continuity change without widening routes', () => {
  const first = routeOf(activity('CH-1', 'MLM', 'P-1'));
  const second = routeOf(activity('CH-2', 'MLB', 'P-2'));
  const baseline = new Map([
    [routeKey(first), cleanRouteState()],
    [routeKey(second), cleanRouteState()],
  ]);
  const prepared = buildPreparedRouteSnapshot({
    routes: [first, second],
    getRouteState: (route) => baseline.get(routeKey(route)),
    capturedAt: NOW,
  });
  const mutations = [
    ['dirty', cleanRouteState({ dirty: 1 })],
    ['gap', cleanRouteState({ continuity: 'gap' })],
    ['last_error', cleanRouteState({ last_error: 'catalog_unreadable' })],
    ['event_cursor', cleanRouteState({ event_cursor: 'evt-11' })],
    ['updated_at', cleanRouteState({ updated_at: '2026-07-26T03:00:00.000Z' })],
    ['missing', null],
  ];
  for (const [label, mutated] of mutations) {
    const current = new Map(baseline);
    if (mutated) current.set(routeKey(first), mutated);
    else current.delete(routeKey(first));
    const evidence = comparePreparedRouteSnapshot({
      preparedSnapshot: prepared,
      routes: [first, second],
      getRouteState: (route) => current.get(routeKey(route)) || null,
      now: NOW,
    });
    assert.deepEqual(evidence.invalid_route_keys, [routeKey(first)], label);
    const plan = buildFinalRevalidationPlan({
      confirmedScope: {
        action: 'enroll',
        activities: [
          activity('CH-1', 'MLM', 'P-1'),
          activity('CH-2', 'MLB', 'P-2'),
        ],
      },
      currentPromotions: [
        activity('CH-1', 'MLM', 'P-1'),
        activity('CH-2', 'MLB', 'P-2'),
      ],
      action: 'enroll',
      preparedAt: NOW.toISOString(),
      now: NOW,
      verificationEvidence: evidence,
      getCacheState: () => cleanActivityState(),
      getFetchState: () => fullFetchState(),
    });
    assert.deepEqual([...plan.required_live_route_keys], [routeKey(first)], label);
  }
});

test('prepared route snapshot rejects malformed or mismatched aggregate digests', () => {
  const first = routeOf(activity('CH-1', 'MLM', 'P-1'));
  const second = routeOf(activity('CH-2', 'MLB', 'P-2'));
  const states = new Map([
    [routeKey(first), cleanRouteState()],
    [routeKey(second), cleanRouteState()],
  ]);
  const prepared = buildPreparedRouteSnapshot({
    routes: [first, second],
    getRouteState: (route) => states.get(routeKey(route)),
    capturedAt: NOW,
  });
  for (const [label, snapshot] of [
    ['malformed', { ...prepared, digest: 'not-a-sha256' }],
    ['mismatched', {
      ...prepared,
      routes: prepared.routes.map((row, index) => (
        index === 0 ? { ...row, event_cursor: 'evt-tampered' } : row
      )),
    }],
  ]) {
    const evidence = comparePreparedRouteSnapshot({
      preparedSnapshot: snapshot,
      routes: [first, second],
      getRouteState: (route) => states.get(routeKey(route)),
      now: NOW,
    });
    assert.deepEqual(evidence.local_verified_route_keys, [], label);
    assert.deepEqual(evidence.invalid_route_keys, [routeKey(first), routeKey(second)], label);
  }
});

test('prepared route snapshot rejects schema defects even when every digest is recomputed', () => {
  const route = routeOf(activity('CH-1', 'MLM', 'P-1'));
  const prepared = buildPreparedRouteSnapshot({
    routes: [route],
    getRouteState: () => cleanRouteState(),
    capturedAt: NOW,
  });
  const routeMismatch = resignRouteRow({
    ...prepared.routes[0],
    route_key: 'A-1|CH-OTHER|MLM',
  });
  const missingField = { ...prepared.routes[0] };
  delete missingField.last_error;
  const cases = [
    ['version', resignPreparedSnapshot({ ...prepared, version: 2 })],
    ['source', resignPreparedSnapshot({ ...prepared, source: 'other_source' })],
    ['captured_at', resignPreparedSnapshot({ ...prepared, captured_at: 'not-an-iso-date' })],
    ['business_date', resignPreparedSnapshot({ ...prepared, business_date: '2026-07-25' })],
    ['duplicate_route', resignPreparedSnapshot({
      ...prepared,
      routes: [prepared.routes[0], { ...prepared.routes[0] }],
    })],
    ['route_key_mismatch', resignPreparedSnapshot({ ...prepared, routes: [routeMismatch] })],
    ['missing_required_field', resignPreparedSnapshot({
      ...prepared,
      routes: [resignRouteRow(missingField)],
    })],
  ];
  for (const [label, snapshot] of cases) {
    const evidence = comparePreparedRouteSnapshot({
      preparedSnapshot: snapshot,
      routes: [route],
      getRouteState: () => cleanRouteState(),
      now: NOW,
    });
    assert.equal(evidence.prepared_snapshot_valid, false, label);
    assert.deepEqual(evidence.local_verified_route_keys, [], label);
    assert.deepEqual(evidence.invalid_route_keys, [routeKey(route)], label);
  }
});

test('attempted but unreadable seller route remains required and prevents creation', async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-seller-attempted-unreadable-'));
  try {
    const activityRoute = routeOf(activity('CH-1', 'MLM', 'P-1'));
    const sellerRoute = {
      account_id: 'A-1',
      child_user_id: 'CH-2',
      site_id: 'MLB',
    };
    const preparedStates = new Map([
      [routeKey(activityRoute), cleanRouteState()],
      [routeKey(sellerRoute), cleanRouteState()],
    ]);
    const prepared = buildPreparedRouteSnapshot({
      routes: [activityRoute, sellerRoute],
      getRouteState: (route) => preparedStates.get(routeKey(route)),
      capturedAt: NOW,
    });
    const currentStates = new Map(preparedStates);
    currentStates.set(routeKey(sellerRoute), cleanRouteState({
      dirty: 1,
      continuity: 'gap',
      last_error: 'catalog_unreadable',
      updated_at: '2026-07-26T03:00:00.000Z',
    }));
    const evidence = comparePreparedRouteSnapshot({
      preparedSnapshot: prepared,
      routes: [activityRoute, sellerRoute],
      getRouteState: (route) => currentStates.get(routeKey(route)),
      attemptedRouteKeys: [routeKey(sellerRoute)],
      liveRouteKeys: [],
      now: NOW,
    });
    const plan = buildFinalRevalidationPlan({
      confirmedScope: {
        action: 'enroll',
        activities: [activity('CH-1', 'MLM', 'P-1')],
        seller_create_target_keys: [routeKey(sellerRoute)],
      },
      currentPromotions: [activity('CH-1', 'MLM', 'P-1')],
      catalogRefreshes: [{
        blocked_route_keys: [routeKey(sellerRoute)],
        catalog_identity_changes: [],
      }],
      action: 'enroll',
      preparedAt: NOW.toISOString(),
      now: NOW,
      verificationEvidence: evidence,
      getCacheState: () => cleanActivityState(),
      getFetchState: () => fullFetchState(),
    });
    assert.deepEqual(evidence.attempted_route_keys, [routeKey(sellerRoute)]);
    assert.deepEqual(evidence.live_route_keys, []);
    assert.deepEqual(evidence.invalid_route_keys, [routeKey(sellerRoute)]);
    assert.equal(plan.platform_read_required, true);
    assert.deepEqual([...plan.required_live_route_keys], [routeKey(sellerRoute)]);

    const store = createSubmissionPersistence({
      stateDir,
      now: () => NOW.toISOString(),
      progressCoalesceMs: 0,
    });
    store.create({
      id: 'seller-unreadable',
      client_submission_id: 'seller-unreadable-client',
      state: 'prepared',
      scope_hash: 'scope',
      expires_at: '2026-07-26T15:59:59.999Z',
      execution_confirmation_token: 'confirm',
      confirmed_execution_scope: {
        action: 'enroll',
        activities: [activity('CH-1', 'MLM', 'P-1')],
        seller_create_target_keys: [routeKey(sellerRoute)],
      },
      seller_input: {
        name: '95',
        selected_targets: [{ ...sellerRoute, detection_status: 'confirmed_absent' }],
      },
      group_id: null,
      progress: { stage: 'prepared', percent: 100, completed: 1, total: 1 },
    });
    let createCalls = 0;
    await assert.rejects(() => commitSubmission({
      store,
      prepareId: 'seller-unreadable',
      confirmText: 'REAL_SUBMIT',
      createConfirmText: 'CREATE_SELLER_CAMPAIGN',
      confirmationToken: 'confirm',
      now: () => '2026-07-26T04:10:00.000Z',
      revalidate: async () => {
        if (plan.platform_read_required) {
          const error = new Error('seller route live verification incomplete');
          error.code = 'FINAL_LIVE_CATALOG_EVIDENCE_INCOMPLETE';
          throw error;
        }
        return { scope_hash: 'scope', execution_relation_count: 1 };
      },
      createSellerCampaigns: async () => {
        createCalls += 1;
        return { ok: true, failed_count: 0, recheck_missing_count: 0 };
      },
      startGroup: async () => ({ id: 'must-not-start' }),
    }), /seller route live verification incomplete/);
    assert.equal(createCalls, 0);
    assert.equal(store.load('seller-unreadable').group_id, null);
  } finally {
    fs.rmSync(stateDir, { recursive: true, force: true });
  }
});

test('seller create route without an existing activity stays inside final route verification', () => {
  const activityRoute = routeOf(activity('CH-1', 'MLM', 'P-1'));
  const sellerRoute = {
    account_id: 'A-1',
    child_user_id: 'CH-2',
    site_id: 'MLB',
  };
  const states = new Map([
    [routeKey(activityRoute), cleanRouteState()],
    [routeKey(sellerRoute), cleanRouteState()],
  ]);
  const prepared = buildPreparedRouteSnapshot({
    routes: [activityRoute, sellerRoute],
    getRouteState: (route) => states.get(routeKey(route)),
    capturedAt: NOW,
  });
  states.set(routeKey(sellerRoute), cleanRouteState({
    dirty: 1,
    updated_at: '2026-07-26T03:00:00.000Z',
    event_cursor: 'evt-seller-changed',
  }));
  const evidence = comparePreparedRouteSnapshot({
    preparedSnapshot: prepared,
    routes: [activityRoute, sellerRoute],
    getRouteState: (route) => states.get(routeKey(route)),
    now: NOW,
  });
  const plan = buildFinalRevalidationPlan({
    confirmedScope: {
      action: 'enroll',
      activities: [activity('CH-1', 'MLM', 'P-1')],
      seller_create_target_keys: [routeKey(sellerRoute)],
    },
    currentPromotions: [activity('CH-1', 'MLM', 'P-1')],
    action: 'enroll',
    preparedAt: NOW.toISOString(),
    now: NOW,
    verificationEvidence: evidence,
    getCacheState: () => cleanActivityState(),
    getFetchState: () => fullFetchState(),
  });
  assert.deepEqual([...plan.required_live_route_keys], [routeKey(sellerRoute)]);
  assert.equal(plan.item_read_identity_keys.size, 0);
  assert.equal(plan.excluded_new_activity_count, 0);
});

test('prepared patch appends a selected seller route without rewriting activity scope', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-prepared-seller-route-'));
  try {
    const output = execFileSync(process.execPath, ['--input-type=module', '--eval', `
      const repo = await import('./src/repository.js');
      const { buildPreparedRouteSnapshot } = await import('./src/finalRevalidationPlan.js');
      const { executionSubmissionPreparedPatch } = await import('./src/server.js');
      const checkedAt = new Date().toISOString();
      const activityRoute = { account_id: 'A-1', child_user_id: 'CH-1', site_id: 'MLM' };
      const sellerRoute = {
        account_id: 'A-1',
        child_user_id: 'CH-2',
        site_id: 'MLB',
        detection_status: 'confirmed_absent',
      };
      for (const route of [activityRoute, sellerRoute]) {
        repo.saveActivityCacheState({
          accountId: route.account_id,
          childUserId: route.child_user_id,
          siteId: route.site_id,
          promotionId: '',
          promotionType: '',
          catalogCheckedAt: checkedAt,
          dirty: false,
          continuity: 'continuous',
          eventCursor: 'evt-clean',
          lastError: null,
        });
      }
      const routeState = (route) => repo.getActivityCacheState({
        accountId: route.account_id,
        childUserId: route.child_user_id,
        siteId: route.site_id,
        promotionId: '',
        promotionType: '',
      });
      const preparedRouteSnapshot = buildPreparedRouteSnapshot({
        routes: [activityRoute],
        getRouteState: routeState,
      });
      const activity = {
        ...activityRoute,
        promotion_id: 'P-1',
        promotion_type: 'DEAL',
        status: 'started',
        item_ids: ['I-1'],
      };
      const scope = {
        action: 'enroll',
        activities: [activity],
        seller_create_target_keys: [],
        seller_target_states: {},
      };
      const patch = executionSubmissionPreparedPatch({
        resolved_action: 'enroll',
        discounts: { seller: 10, official: 10 },
        targets: [activity],
        activity_buckets: { seller: 0, official: 1 },
        excluded_buckets: { smart: 0, lightning: 0, other: 0 },
        seller_detection: {
          existing: [],
          existing_without_visible_id: [],
          confirmed_absent: [sellerRoute],
          needs_manual_review: [],
          unreadable: [],
        },
        live_read: { rows: [], readable_count: 1, blocked_count: 0, all_blocked: false },
        observed_execution_scope: scope,
        confirmed_execution_scope: scope,
        prepared_route_snapshot: preparedRouteSnapshot,
        confirmation_summary: 'prepared',
        group_request: { action: 'enroll' },
      }, {
        seller_input: {
          name: '95',
          start_date: '2026-07-26',
          finish_date: '2026-07-31',
          selected_targets: [sellerRoute],
          validation_errors: [],
        },
      });
      console.log(JSON.stringify({
        route_keys: patch.prepared_route_snapshot.routes.map((row) => row.route_key),
        seller_keys: patch.confirmed_execution_scope.seller_create_target_keys,
        activity_ids: patch.confirmed_execution_scope.activities.map((row) => row.promotion_id),
      }));
    `], {
      cwd: ROOT,
      env: {
        ...process.env,
        MDM_SERVER_LIBRARY_MODE: '1',
        MDM_DATA_DIR: dataDir,
        MDM_DB_PATH: path.join(dataDir, 'test.sqlite'),
      },
      encoding: 'utf8',
      timeout: 30_000,
    });
    const result = JSON.parse(String(output).trim());
    assert.deepEqual(result.route_keys, ['A-1|CH-1|MLM', 'A-1|CH-2|MLB']);
    assert.deepEqual(result.seller_keys, ['A-1|CH-2|MLB']);
    assert.deepEqual(result.activity_ids, ['P-1']);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('prepared route snapshot survives persistence restart and final commit performs zero reads', () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-prepare-route-restart-'));
  try {
    const output = execFileSync(process.execPath, ['--input-type=module', '--eval', `
      const {
        buildFinalRevalidationPlan,
        buildPreparedRouteSnapshot,
        comparePreparedRouteSnapshot,
      } = await import('./src/finalRevalidationPlan.js');
      const {
        commitSubmission,
        createSubmissionPersistence,
        runSubmissionPreparation,
      } = await import('./src/submissionPersistence.js');
      const { executionSubmissionPreparedPatch } = await import('./src/server.js');
      const now = new Date('2026-07-26T04:00:00.000Z');
      const checkedAt = '2026-07-26T01:00:00.000Z';
      const row = {
        account_id: 'A-1',
        child_user_id: 'CH-1',
        site_id: 'MLM',
        promotion_id: 'P-1',
        promotion_type: 'DEAL',
        status: 'started',
      };
      const route = { account_id: 'A-1', child_user_id: 'CH-1', site_id: 'MLM' };
      const routeState = {
        catalog_checked_at: checkedAt,
        updated_at: checkedAt,
        dirty: 0,
        continuity: 'continuous',
        event_cursor: 'evt-10',
        last_error: null,
      };
      const preparedRouteSnapshot = buildPreparedRouteSnapshot({
        routes: [route],
        getRouteState: () => routeState,
        capturedAt: now,
      });
      const firstStore = createSubmissionPersistence({
        stateDir: process.env.MDM_DATA_DIR,
        now: () => now.toISOString(),
        progressCoalesceMs: 0,
      });
      firstStore.create({
        id: 'prepare-restart',
        client_submission_id: 'client-restart',
        state: 'preparing',
        request: {},
        request_fingerprint: 'request-fingerprint',
        seller_input: {
          name: '',
          start_date: null,
          finish_date: null,
          selected_targets: [],
          validation_errors: [],
        },
        execution_confirmation_token: 'confirm-token',
        group_id: null,
        progress: { stage: 'queued', percent: 0, completed: 0, total: 0 },
      });
      const scope = {
        action: 'enroll',
        activities: [{
          ...row,
          item_ids: ['I-1'],
          detail_status: 'ok',
          platform_total: 1,
          saved_count: 1,
          blocked: false,
        }],
        seller_create_target_keys: [],
        seller_target_states: {},
      };
      const prepared = await runSubmissionPreparation({
        store: firstStore,
        prepareId: 'prepare-restart',
        buildSnapshot: async () => ({
          resolved_action: 'enroll',
          discounts: { seller: 10, official: 10 },
          targets: [row],
          activity_buckets: { seller: 0, official: 1 },
          excluded_buckets: { smart: 0, lightning: 0, other: 0 },
          seller_detection: {
            existing: [],
            existing_without_visible_id: [],
            confirmed_absent: [],
            needs_manual_review: [],
            unreadable: [],
          },
          live_read: { rows: [], readable_count: 1, blocked_count: 0, all_blocked: false },
          observed_execution_scope: scope,
          confirmed_execution_scope: scope,
          prepared_route_snapshot: preparedRouteSnapshot,
          confirmation_summary: 'prepared',
          group_request: { action: 'enroll' },
        }),
        preparedPatch: executionSubmissionPreparedPatch,
      });
      const restartedStore = createSubmissionPersistence({
        stateDir: process.env.MDM_DATA_DIR,
        now: () => now.toISOString(),
        progressCoalesceMs: 0,
      });
      const recovered = restartedStore.load('prepare-restart');
      let catalogGets = 0;
      let itemGets = 0;
      const committed = await commitSubmission({
        store: restartedStore,
        prepareId: 'prepare-restart',
        confirmText: 'REAL_SUBMIT',
        confirmationToken: 'confirm-token',
        now: () => '2026-07-26T04:10:00.000Z',
        revalidate: async (current) => {
          const evidence = comparePreparedRouteSnapshot({
            preparedSnapshot: current.prepared_route_snapshot,
            routes: [route],
            getRouteState: () => routeState,
            now,
          });
          const cleanState = {
            ...routeState,
            items_full_checked_at: checkedAt,
          };
          const fetchState = {
            updated_at: checkedAt,
            detail_status: 'ok',
            platform_total: 1,
            saved_count: 1,
            raw_json: JSON.stringify({
              is_full_fetch: true,
              identity_summary: {
                version: 1,
                algorithm: 'sha256_sorted_unique_item_ids',
                count: 1,
                digest: 'TEST',
                complete: true,
              },
            }),
          };
          const plan = buildFinalRevalidationPlan({
            confirmedScope: current.confirmed_execution_scope,
            currentPromotions: [row],
            action: 'enroll',
            preparedAt: current.updated_at,
            now,
            verificationEvidence: evidence,
            getCacheState: () => cleanState,
            getFetchState: () => fetchState,
          });
          catalogGets += plan.required_live_route_keys.size;
          itemGets += plan.item_read_identity_keys.size;
          return {
            scope_hash: current.scope_hash,
            reconfirm_required: false,
            execution_relation_count: 1,
          };
        },
        startGroup: async () => ({ id: 'group-zero-read' }),
      });
      console.log(JSON.stringify({
        prepared_state: prepared.state,
        prepared_digest: prepared.prepared_route_snapshot?.digest || null,
        recovered_digest: recovered.prepared_route_snapshot?.digest || null,
        group_id: committed.group?.id || null,
        catalog_gets: catalogGets,
        item_gets: itemGets,
      }));
    `], {
      cwd: ROOT,
      env: {
        ...process.env,
        MDM_SERVER_LIBRARY_MODE: '1',
        MDM_DATA_DIR: stateDir,
        MDM_DB_PATH: path.join(stateDir, 'test.sqlite'),
      },
      encoding: 'utf8',
      timeout: 30_000,
    });
    const result = JSON.parse(String(output).trim());
    assert.equal(result.prepared_state, 'prepared');
    assert.match(result.prepared_digest, /^[A-F0-9]{64}$/);
    assert.equal(result.recovered_digest, result.prepared_digest);
    assert.equal(result.group_id, 'group-zero-read');
    assert.equal(result.catalog_gets, 0);
    assert.equal(result.item_gets, 0);
  } finally {
    fs.rmSync(stateDir, { recursive: true, force: true });
  }
});

test('full reads use the canonical item identity summary and partial DEAL keeps its own fetch state', async () => {
  const expected = buildItemIdentitySummary(['I-2', 'I-1', 'I-2'], { complete: true });
  const client = new MercadoLibreClient({ accessToken: 'fake' });
  client.getPromotionItems = async () => ({
    paging: { total: 2, limit: 50, offset: 0 },
    results: [{ id: 'I-2' }, { id: 'I-1' }],
  });
  const full = await client.fetchAllPromotionItems({
    promotionId: 'P-1',
    promotionType: 'DEAL',
    status: 'candidate',
    maxItems: 'all',
  });
  assert.deepEqual(full.rawSummary.identity_summary, expected);

  const previousMode = process.env.MDM_SERVER_LIBRARY_MODE;
  process.env.MDM_SERVER_LIBRARY_MODE = '1';
  try {
    const { effectivePreparationFetchState } = await import(`../src/server.js?effective-state=${Date.now()}`);
    const fetchState = { saved_count: 37, platform_total: 40 };
    const fallbackState = { saved_count: 0, platform_total: 0 };
    assert.equal(effectivePreparationFetchState(
      { effective_state: 'partial_api_sparse_marketplace_candidate' },
      fetchState,
      fallbackState,
    ), fetchState);
    assert.equal(effectivePreparationFetchState(
      { effective_state: 'candidate_plus_inventory_fallback' },
      fetchState,
      fallbackState,
    ), fallbackState);
  } finally {
    if (previousMode === undefined) delete process.env.MDM_SERVER_LIBRARY_MODE;
    else process.env.MDM_SERVER_LIBRARY_MODE = previousMode;
  }
});

test('server same-day preparation performs zero GET and cross-day probe reuses its first page', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-server-candidate-read-'));
  try {
    const output = execFileSync(process.execPath, ['--input-type=module', '--eval', `
      const repo = await import('./src/repository.js');
      const db = await import('./src/db.js');
      const { MercadoLibreClient } = await import('./src/mlClient.js');
      const { buildItemIdentitySummary } = await import('./src/activityChangeCache.js');
      const { prepareItemsForExecution } = await import('./src/server.js');
      const campaign = {
        account_id: 'A-1',
        child_user_id: 'CH-1',
        site_id: 'MLM',
        promotion_id: 'P-1',
        promotion_type: 'DEAL',
        status: 'started',
        finish_date: '2026-08-31T23:59:59.000Z',
      };
      const account = {
        account_id: 'A-1',
        accessToken: 'fake',
        site_id: 'CBT',
      };
      const baseline = Array.from({ length: 50 }, (_, index) => ({ item_id: 'I-' + (index + 1) }));
      repo.saveActivityCacheState({
        accountId: 'A-1',
        childUserId: 'CH-1',
        siteId: 'MLM',
        promotionId: 'P-1',
        promotionType: 'DEAL',
        dirty: false,
        continuity: 'continuous',
        itemsFullCheckedAt: new Date().toISOString(),
      });
      repo.saveItems('A-1', 'P-1', 'DEAL', baseline, {
        childUserId: 'CH-1',
        siteId: 'MLM',
        replaceStatus: 'candidate',
        itemStatus: 'candidate',
      });
      repo.saveItemFetchState({
        accountId: 'A-1',
        promotionId: 'P-1',
        promotionType: 'DEAL',
        itemStatus: 'candidate',
        platformTotal: 50,
        savedCount: 50,
        detailStatus: 'ok',
        raw: {
          is_full_fetch: true,
          identity_summary: buildItemIdentitySummary(baseline, { complete: true }),
        },
      });
      let getCalls = 0;
      MercadoLibreClient.prototype.getPromotionItems = async ({ offset = 0 }) => {
        getCalls += 1;
        if (offset === 0) {
          return {
            paging: { total: 60, limit: 50, offset: 0 },
            results: Array.from({ length: 50 }, (_, index) => ({ item_id: 'I-' + (index + 1) })),
          };
        }
        return {
          paging: { total: 60, limit: 50, offset },
          results: Array.from({ length: 10 }, (_, index) => ({ item_id: 'I-' + (offset + index + 1) })),
        };
      };
      const run = () => prepareItemsForExecution({
        account,
        promotions: [campaign],
        action: 'enroll',
        itemStatus: 'candidate',
        settings: { readConcurrency: 1, maxItemsPerPromotion: 50 },
        request: { fetchMode: 'full', maxItems: 'all', allowInventoryFallback: false },
        operationReadCache: new Map(),
        probeCandidateTotals: true,
        finalRevalidation: false,
      });
      const sameDay = await run();
      const sameDayCalls = getCalls;
      const old = new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString();
      db.run(
        'UPDATE promo_item_fetch_states SET updated_at = ? WHERE account_id = ? AND promotion_id = ? AND promotion_type = ? AND item_status = ?',
        [old, 'A-1', 'P-1', 'DEAL', 'candidate'],
      );
      db.run(
        'UPDATE activity_cache_states SET items_full_checked_at = ?, updated_at = ? WHERE account_id = ? AND child_user_id = ? AND site_id = ? AND promotion_id = ? AND promotion_type = ?',
        [old, old, 'A-1', 'CH-1', 'MLM', 'P-1', 'DEAL'],
      );
      const crossDay = await run();
      console.log(JSON.stringify({
        same_day_get_calls: sameDayCalls,
        same_day_saved: sameDay.summary.rows[0].saved_count,
        cross_day_total_get_calls: getCalls,
        cross_day_saved: crossDay.summary.rows[0].saved_count,
      }));
    `], {
      cwd: ROOT,
      env: {
        ...process.env,
        MDM_SERVER_LIBRARY_MODE: '1',
        MDM_DATA_DIR: dataDir,
        MDM_DB_PATH: path.join(dataDir, 'test.sqlite'),
      },
      encoding: 'utf8',
      timeout: 30_000,
    });
    const result = JSON.parse(String(output).trim());
    assert.equal(result.same_day_get_calls, 0);
    assert.equal(result.same_day_saved, 50);
    assert.equal(result.cross_day_total_get_calls, 2);
    assert.equal(result.cross_day_saved, 60);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('sites endpoint is local by default and explicit refresh enters account discovery path', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-sites-local-'));
  const port = 35000 + Math.floor(Math.random() * 1000);
  execFileSync(process.execPath, ['--input-type=module', '--eval', `
    const repo = await import('./src/repository.js');
    const db = await import('./src/db.js');
    repo.saveMarketplaceSites('A-LOCAL', [{
      child_user_id: 'CH-LOCAL',
      site_id: 'MLM',
      logistic_type: 'remote',
    }]);
    db.closeDb();
  `], {
    cwd: ROOT,
    env: {
      ...process.env,
      MDM_DATA_DIR: dataDir,
      MDM_DB_PATH: path.join(dataDir, 'test.sqlite'),
    },
  });
  const child = spawn(process.execPath, ['src/server.js'], {
    cwd: ROOT,
    env: {
      ...process.env,
      MDM_DATA_DIR: dataDir,
      MDM_DB_PATH: path.join(dataDir, 'test.sqlite'),
      MDM_KEY_PATH: path.join(dataDir, 'local.key'),
      MDM_PORT: String(port),
      MDM_ACTIVITY_CALLBACK_ENABLED: '0',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  try {
    const base = `http://127.0.0.1:${port}`;
    const deadline = Date.now() + 10_000;
    while (Date.now() < deadline) {
      try {
        if ((await fetch(`${base}/api/health`)).ok) break;
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    const localResponse = await fetch(`${base}/api/accounts/A-LOCAL/sites`);
    assert.equal(localResponse.status, 200);
    const local = await localResponse.json();
    assert.deepEqual(local.sites.map((row) => row.child_user_id), ['CH-LOCAL']);

    const refreshResponse = await fetch(`${base}/api/accounts/A-LOCAL/sites?refresh=1`);
    assert.equal(refreshResponse.status, 500);
    const refresh = await refreshResponse.json();
    assert.match(refresh.error, /未找到授权账号/);
  } finally {
    child.kill();
    await Promise.race([
      new Promise((resolve) => child.once('exit', resolve)),
      new Promise((resolve) => setTimeout(resolve, 5_000)),
    ]);
    if (child.exitCode === null) child.kill('SIGKILL');
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
