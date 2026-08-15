import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';

const ROOT = path.resolve(import.meta.dirname, '..');

function runWithDb(dbPath, source) {
  return JSON.parse(execFileSync(
    process.execPath,
    ['--input-type=module', '--eval', source],
    {
      cwd: ROOT,
      env: { ...process.env, MDM_DATA_DIR: path.dirname(dbPath), MDM_DB_PATH: dbPath },
      encoding: 'utf8',
      timeout: 30_000,
    },
  ));
}

test('legacy cycle_states schema migrates to route-scoped columns without data loss', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-cycle-legacy-'));
  const dbPath = path.join(dataDir, 'legacy.sqlite');
  try {
    const legacy = new DatabaseSync(dbPath);
    legacy.exec(`
      CREATE TABLE cycle_states (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        promotion_id TEXT NOT NULL,
        promotion_type TEXT NOT NULL,
        seller_discount_percent REAL,
        official_discount_percent REAL,
        status TEXT NOT NULL,
        raw_json TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(account_id, promotion_id, promotion_type)
      );
      INSERT INTO cycle_states
        (account_id, promotion_id, promotion_type, seller_discount_percent, official_discount_percent, status, updated_at)
      VALUES
        ('acc-1', 'promo-a', 'SELLER_CAMPAIGN', 5, NULL, 'completed', '2026-08-01T00:00:00.000Z'),
        ('acc-1', 'promo-b', 'OFFICIAL_DEAL', NULL, 6, 'completed', '2026-08-02T00:00:00.000Z');
    `);
    legacy.close();

    const payload = runWithDb(dbPath, `
      const db = await import('./src/db.js');
      const { getCycleState, upsertCycleState } = await import('./src/cycle.js');
      const database = db.getDb();
      const columns = database.prepare('PRAGMA table_info(cycle_states)').all().map((row) => row.name);
      const stateA = getCycleState('acc-1', 'promo-a', 'SELLER_CAMPAIGN');
      upsertCycleState({
        accountId: 'acc-1',
        childUserId: 'child-9',
        siteId: 'MLB',
        activityRevision: 'rev-1',
        promotionId: 'promo-a',
        promotionType: 'SELLER_CAMPAIGN',
        sellerDiscountPercent: 6,
        officialDiscountPercent: null,
        status: 'completed',
      });
      const stateA2 = getCycleState('acc-1', 'promo-a', 'SELLER_CAMPAIGN', { childUserId: 'child-9', siteId: 'MLB', activityRevision: 'rev-1' });
      const stateB = getCycleState('acc-1', 'promo-b', 'OFFICIAL_DEAL');
      const all = database.prepare('SELECT account_id, promotion_id, child_user_id, site_id, activity_revision, identity_state FROM cycle_states ORDER BY id').all();
      console.log(JSON.stringify({ columns, stateA, stateA2, stateB, all }));
      db.closeDb();
    `);

    for (const column of ['child_user_id', 'site_id', 'activity_revision', 'identity_state']) {
      assert.ok(payload.columns.includes(column), `column ${column} must exist after migration`);
    }
    // Legacy rows keep their data and get empty route scoping.
    assert.equal(payload.stateA.promotion_id, 'promo-a');
    assert.equal(payload.stateA.seller_discount_percent, 5);
    assert.equal(payload.stateA.identity_state, 'route_scoped');
    assert.equal(payload.stateB.promotion_id, 'promo-b');
    assert.equal(payload.stateB.official_discount_percent, 6);
    // Route-scoped upsert and read-back work.
    assert.equal(payload.stateA2.child_user_id, 'child-9');
    assert.equal(payload.stateA2.site_id, 'MLB');
    assert.equal(payload.stateA2.activity_revision, 'rev-1');
    assert.equal(payload.stateA2.seller_discount_percent, 6);
    // No rows were dropped or duplicated.
    assert.equal(payload.all.length, 3);
    const legacyRouteRows = payload.all.filter((row) => row.child_user_id === '' && row.site_id === '');
    assert.equal(legacyRouteRows.length, 2);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('fresh database creates route-scoped cycle_states directly', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-cycle-fresh-'));
  const dbPath = path.join(dataDir, 'fresh.sqlite');
  try {
    const payload = runWithDb(dbPath, `
      const db = await import('./src/db.js');
      const database = db.getDb();
      const columns = database.prepare('PRAGMA table_info(cycle_states)').all().map((row) => row.name);
      const indexes = database.prepare('PRAGMA index_list(cycle_states)').all()
        .filter((index) => Number(index.unique) === 1)
        .map((index) => database.prepare('PRAGMA index_info(' + index.name + ')').all()
          .sort((a, b) => Number(a.seqno) - Number(b.seqno))
          .map((row) => row.name));
      console.log(JSON.stringify({ columns, indexes }));
      db.closeDb();
    `);
    assert.ok(payload.columns.includes('child_user_id'));
    assert.ok(payload.columns.includes('activity_revision'));
    assert.deepEqual(payload.indexes, [
      ['account_id', 'child_user_id', 'site_id', 'promotion_id', 'promotion_type', 'activity_revision'],
    ]);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
