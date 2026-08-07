import fs from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { DATA_DIR, DB_PATH } from './config.js';

let db;

export function getDb() {
  if (db) return db;
  fs.mkdirSync(DATA_DIR, { recursive: true });
  db = new DatabaseSync(DB_PATH);
  db.exec('PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;');
  migrate(db);
  return db;
}

export function closeDb() {
  if (db) {
    db.close();
    db = null;
  }
}

function migrate(database) {
  database.exec(`
    CREATE TABLE IF NOT EXISTS oauth_states (
      state TEXT PRIMARY KEY,
      client_id TEXT NOT NULL,
      client_secret_cipher TEXT NOT NULL,
      redirect_uri TEXT NOT NULL,
      auth_domain TEXT NOT NULL,
      code_verifier TEXT NOT NULL,
      code_challenge TEXT NOT NULL,
      created_at TEXT NOT NULL,
      processing_state TEXT NOT NULL DEFAULT 'pending',
      claim_token TEXT,
      claimed_at TEXT,
      claim_expires_at TEXT,
      consumed_at TEXT,
      last_error_code TEXT,
      attempt_count INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS oauth_tokens (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      provider TEXT NOT NULL DEFAULT 'mercadolibre',
      account_id TEXT NOT NULL UNIQUE,
      display_name TEXT,
      site_id TEXT,
      scopes TEXT,
      access_token_cipher TEXT NOT NULL,
      refresh_token_cipher TEXT,
      token_type TEXT,
      expires_at TEXT,
      raw_json TEXT,
      client_id TEXT,
      client_secret_cipher TEXT,
      redirect_uri TEXT,
      auth_domain TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS account_profiles (
      account_id TEXT PRIMARY KEY,
      provider TEXT NOT NULL,
      display_name TEXT NOT NULL,
      site_id TEXT,
      fetched_at TEXT NOT NULL,
      source TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS promo_campaigns (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id TEXT NOT NULL,
      promotion_id TEXT NOT NULL,
      promotion_type TEXT NOT NULL,
      merchant_id TEXT,
      child_user_id TEXT NOT NULL DEFAULT '',
      site_id TEXT NOT NULL DEFAULT '',
      logistic_type TEXT,
      name TEXT,
      status TEXT,
      start_date TEXT,
      finish_date TEXT,
      raw_json TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(account_id, child_user_id, site_id, promotion_id, promotion_type)
    );

    CREATE TABLE IF NOT EXISTS marketplace_sites (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id TEXT NOT NULL,
      child_user_id TEXT NOT NULL,
      site_id TEXT,
      logistic_type TEXT,
      last_promotion_status TEXT,
      last_promotion_count INTEGER NOT NULL DEFAULT 0,
      last_error TEXT,
      raw_json TEXT,
      updated_at TEXT NOT NULL,
      UNIQUE(account_id, child_user_id)
    );

    CREATE TABLE IF NOT EXISTS promo_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id TEXT NOT NULL,
      promotion_id TEXT NOT NULL,
      promotion_type TEXT NOT NULL,
      child_user_id TEXT NOT NULL DEFAULT '',
      site_id TEXT NOT NULL DEFAULT '',
      logistic_type TEXT,
      item_id TEXT NOT NULL,
      status TEXT,
      currency_id TEXT,
      original_price REAL,
      price REAL,
      suggested_discounted_price REAL,
      min_discounted_price REAL,
      max_discounted_price REAL,
      source TEXT,
      raw_json TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(account_id, child_user_id, site_id, promotion_id, promotion_type, item_id)
    );

    CREATE TABLE IF NOT EXISTS promo_tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id TEXT NOT NULL,
      promotion_id TEXT NOT NULL,
      promotion_type TEXT NOT NULL,
      action TEXT NOT NULL,
      mode TEXT NOT NULL,
      discount_percent REAL,
      direct_price REAL,
      status TEXT NOT NULL,
      total_count INTEGER NOT NULL DEFAULT 0,
      success_count INTEGER NOT NULL DEFAULT 0,
      failed_count INTEGER NOT NULL DEFAULT 0,
      skipped_count INTEGER NOT NULL DEFAULT 0,
      empty_count INTEGER NOT NULL DEFAULT 0,
      completed INTEGER NOT NULL DEFAULT 0,
      summary_json TEXT,
      execution_group_id TEXT,
      execution_job_id TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS promo_action_results (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id INTEGER NOT NULL,
      account_id TEXT NOT NULL,
      promotion_id TEXT NOT NULL,
      promotion_type TEXT NOT NULL,
      item_id TEXT NOT NULL,
      action TEXT NOT NULL,
      mode TEXT NOT NULL,
      status TEXT NOT NULL,
      deal_price REAL,
      top_deal_price REAL,
      error_cn TEXT,
      error_raw TEXT,
      response_json TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY(task_id) REFERENCES promo_tasks(id)
    );

    CREATE TABLE IF NOT EXISTS history_batch_summaries (
      summary_key TEXT PRIMARY KEY,
      schema_version INTEGER NOT NULL,
      summary_id INTEGER NOT NULL,
      action TEXT NOT NULL,
      mode TEXT NOT NULL,
      status TEXT NOT NULL,
      sort_created_at TEXT NOT NULL,
      sort_updated_at TEXT NOT NULL,
      task_ids_json TEXT NOT NULL,
      data_json TEXT NOT NULL,
      published_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS history_summary_state (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      schema_version INTEGER NOT NULL,
      status TEXT NOT NULL,
      task_count INTEGER NOT NULL DEFAULT 0,
      task_max_id INTEGER NOT NULL DEFAULT 0,
      result_count INTEGER NOT NULL DEFAULT 0,
      result_max_id INTEGER NOT NULL DEFAULT 0,
      started_at TEXT,
      completed_at TEXT,
      last_error TEXT
    );

    CREATE TABLE IF NOT EXISTS seller_campaign_create_results (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      account_id TEXT NOT NULL,
      store_name TEXT,
      site_id TEXT,
      site_name TEXT,
      promotion_type TEXT NOT NULL DEFAULT 'SELLER_CAMPAIGN',
      promotion_name TEXT,
      start_date TEXT,
      finish_date TEXT,
      selected INTEGER NOT NULL DEFAULT 1,
      request_status TEXT NOT NULL,
      promotion_id TEXT,
      http_status INTEGER,
      error_cn TEXT,
      error_raw TEXT,
      rechecked INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_promo_tasks_id_desc
      ON promo_tasks(id DESC);
    CREATE INDEX IF NOT EXISTS idx_promo_tasks_created_action_mode
      ON promo_tasks(created_at DESC, action, mode);
    CREATE INDEX IF NOT EXISTS idx_promo_action_results_task_id_id
      ON promo_action_results(task_id, id);
    CREATE INDEX IF NOT EXISTS idx_promo_action_results_task_status
      ON promo_action_results(task_id, status);
    CREATE INDEX IF NOT EXISTS idx_promo_action_results_task_item
      ON promo_action_results(task_id, account_id, promotion_id, promotion_type, action, item_id, id);
    CREATE INDEX IF NOT EXISTS idx_promo_action_results_task_status_item
      ON promo_action_results(task_id, status, item_id, account_id, promotion_id, promotion_type, action, id);
    CREATE INDEX IF NOT EXISTS idx_history_batch_summaries_sort
      ON history_batch_summaries(schema_version, sort_created_at DESC, summary_id DESC);
    CREATE INDEX IF NOT EXISTS idx_history_batch_summaries_action_time
      ON history_batch_summaries(schema_version, action, mode, sort_created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_seller_campaign_create_results_run_id
      ON seller_campaign_create_results(run_id, id);
    CREATE INDEX IF NOT EXISTS idx_seller_campaign_create_results_account_site
      ON seller_campaign_create_results(account_id, site_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_account_profiles_fetched_at
      ON account_profiles(fetched_at DESC);

    CREATE TABLE IF NOT EXISTS promo_item_fetch_states (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id TEXT NOT NULL,
      child_user_id TEXT NOT NULL DEFAULT '',
      site_id TEXT NOT NULL DEFAULT '',
      promotion_id TEXT NOT NULL,
      promotion_type TEXT NOT NULL,
      item_status TEXT NOT NULL,
      platform_total INTEGER,
      saved_count INTEGER NOT NULL DEFAULT 0,
      detail_status TEXT NOT NULL,
      warning TEXT,
      raw_json TEXT,
      updated_at TEXT NOT NULL,
      UNIQUE(account_id, child_user_id, site_id, promotion_id, promotion_type, item_status)
    );

    CREATE TABLE IF NOT EXISTS activity_cache_states (
      account_id TEXT NOT NULL,
      child_user_id TEXT NOT NULL DEFAULT '',
      site_id TEXT NOT NULL DEFAULT '',
      promotion_id TEXT NOT NULL DEFAULT '',
      promotion_type TEXT NOT NULL DEFAULT '',
      catalog_checked_at TEXT,
      items_full_checked_at TEXT,
      dirty INTEGER NOT NULL DEFAULT 0,
      expired INTEGER NOT NULL DEFAULT 0,
      continuity TEXT NOT NULL DEFAULT 'continuous',
      event_cursor TEXT,
      last_error TEXT,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (account_id, child_user_id, site_id, promotion_id, promotion_type)
    );

    CREATE INDEX IF NOT EXISTS idx_activity_cache_due
      ON activity_cache_states(account_id, site_id, dirty, continuity, catalog_checked_at, items_full_checked_at);

    CREATE TABLE IF NOT EXISTS activity_callback_events (
      event_id TEXT PRIMARY KEY,
      schema_version TEXT NOT NULL,
      account_id TEXT NOT NULL,
      site_id TEXT NOT NULL,
      promotion_id TEXT NOT NULL DEFAULT '',
      promotion_type TEXT NOT NULL DEFAULT '',
      cursor TEXT,
      previous_cursor TEXT,
      gap INTEGER NOT NULL DEFAULT 0,
      received_at TEXT NOT NULL,
      processing_state TEXT NOT NULL DEFAULT 'completed',
      claim_token TEXT,
      claimed_at TEXT,
      claim_expires_at TEXT,
      completed_at TEXT,
      last_error TEXT,
      attempt_count INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_activity_callback_scope
      ON activity_callback_events(account_id, site_id, promotion_id, promotion_type, received_at);

    CREATE TABLE IF NOT EXISTS cycle_states (
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
  `);
  migrateOAuthStateClaimsAndDailySnapshots(database);
  migrateActivityCacheIdentity(database);
  database.exec(`
    DROP INDEX IF EXISTS idx_activity_cache_due;
    CREATE INDEX idx_activity_cache_due
      ON activity_cache_states(
        account_id, child_user_id, site_id, dirty, continuity,
        catalog_checked_at, items_full_checked_at
      );
  `);
  addColumnIfMissing(database, 'promo_campaigns', 'merchant_id', 'TEXT');
  addColumnIfMissing(database, 'promo_campaigns', 'child_user_id', 'TEXT');
  addColumnIfMissing(database, 'promo_campaigns', 'site_id', 'TEXT');
  addColumnIfMissing(database, 'promo_campaigns', 'logistic_type', 'TEXT');
  addColumnIfMissing(database, 'marketplace_sites', 'last_promotion_status', 'TEXT');
  addColumnIfMissing(database, 'marketplace_sites', 'last_promotion_count', 'INTEGER NOT NULL DEFAULT 0');
  addColumnIfMissing(database, 'marketplace_sites', 'last_error', 'TEXT');
  addColumnIfMissing(database, 'promo_items', 'child_user_id', 'TEXT');
  addColumnIfMissing(database, 'promo_items', 'site_id', 'TEXT');
  addColumnIfMissing(database, 'promo_items', 'logistic_type', 'TEXT');
  addColumnIfMissing(database, 'promo_items', 'source', 'TEXT');
  addColumnIfMissing(database, 'promo_item_fetch_states', 'child_user_id', "TEXT NOT NULL DEFAULT ''");
  addColumnIfMissing(database, 'promo_item_fetch_states', 'site_id', "TEXT NOT NULL DEFAULT ''");
  addColumnIfMissing(database, 'promo_tasks', 'execution_group_id', 'TEXT');
  addColumnIfMissing(database, 'promo_tasks', 'execution_job_id', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'topic', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'resource', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'remote_user_id', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'child_user_id', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'application_id', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'outcome', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'resource_status', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'raw_json', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'processing_state', "TEXT NOT NULL DEFAULT 'completed'");
  addColumnIfMissing(database, 'activity_callback_events', 'claim_token', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'claimed_at', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'claim_expires_at', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'completed_at', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'last_error', 'TEXT');
  addColumnIfMissing(database, 'activity_callback_events', 'attempt_count', 'INTEGER NOT NULL DEFAULT 0');
  addColumnIfMissing(database, 'seller_campaign_create_results', 'child_user_id', 'TEXT');
  addColumnIfMissing(database, 'seller_campaign_create_results', 'detection_status', 'TEXT');
  migrateRouteIdentityTables(database);
  database.exec(`
    CREATE INDEX IF NOT EXISTS idx_promo_tasks_execution_group
      ON promo_tasks(execution_group_id, id);
    CREATE INDEX IF NOT EXISTS idx_promo_tasks_execution_job
      ON promo_tasks(execution_job_id, id);
    CREATE INDEX IF NOT EXISTS idx_activity_callback_remote
      ON activity_callback_events(remote_user_id, child_user_id, site_id, received_at);
    CREATE INDEX IF NOT EXISTS idx_activity_callback_processing
      ON activity_callback_events(processing_state, claim_expires_at, received_at);
    CREATE INDEX IF NOT EXISTS idx_seller_campaign_create_results_route_name
      ON seller_campaign_create_results(account_id, child_user_id, site_id, promotion_name, created_at);
  `);
}

function migrateRouteIdentityTables(database) {
  migrateRouteIdentityTable(database, {
    table: 'promo_campaigns',
    uniqueColumns: ['account_id', 'child_user_id', 'site_id', 'promotion_id', 'promotion_type'],
    columns: [
      'account_id', 'promotion_id', 'promotion_type', 'merchant_id', 'child_user_id', 'site_id',
      'logistic_type', 'name', 'status', 'start_date', 'finish_date', 'raw_json', 'updated_at'
    ],
    createSql: `
      CREATE TABLE promo_campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        promotion_id TEXT NOT NULL,
        promotion_type TEXT NOT NULL,
        merchant_id TEXT,
        child_user_id TEXT NOT NULL DEFAULT '',
        site_id TEXT NOT NULL DEFAULT '',
        logistic_type TEXT,
        name TEXT,
        status TEXT,
        start_date TEXT,
        finish_date TEXT,
        raw_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(account_id, child_user_id, site_id, promotion_id, promotion_type)
      )`
  });
  migrateRouteIdentityTable(database, {
    table: 'promo_items',
    uniqueColumns: ['account_id', 'child_user_id', 'site_id', 'promotion_id', 'promotion_type', 'item_id'],
    columns: [
      'account_id', 'promotion_id', 'promotion_type', 'child_user_id', 'site_id', 'logistic_type',
      'item_id', 'status', 'currency_id', 'original_price', 'price', 'suggested_discounted_price',
      'min_discounted_price', 'max_discounted_price', 'source', 'raw_json', 'updated_at'
    ],
    createSql: `
      CREATE TABLE promo_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        promotion_id TEXT NOT NULL,
        promotion_type TEXT NOT NULL,
        child_user_id TEXT NOT NULL DEFAULT '',
        site_id TEXT NOT NULL DEFAULT '',
        logistic_type TEXT,
        item_id TEXT NOT NULL,
        status TEXT,
        currency_id TEXT,
        original_price REAL,
        price REAL,
        suggested_discounted_price REAL,
        min_discounted_price REAL,
        max_discounted_price REAL,
        source TEXT,
        raw_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(account_id, child_user_id, site_id, promotion_id, promotion_type, item_id)
      )`
  });
  migrateRouteIdentityTable(database, {
    table: 'promo_item_fetch_states',
    uniqueColumns: ['account_id', 'child_user_id', 'site_id', 'promotion_id', 'promotion_type', 'item_status'],
    columns: [
      'account_id', 'child_user_id', 'site_id', 'promotion_id', 'promotion_type', 'item_status',
      'platform_total', 'saved_count', 'detail_status', 'warning', 'raw_json', 'updated_at'
    ],
    createSql: `
      CREATE TABLE promo_item_fetch_states (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        child_user_id TEXT NOT NULL DEFAULT '',
        site_id TEXT NOT NULL DEFAULT '',
        promotion_id TEXT NOT NULL,
        promotion_type TEXT NOT NULL,
        item_status TEXT NOT NULL,
        platform_total INTEGER,
        saved_count INTEGER NOT NULL DEFAULT 0,
        detail_status TEXT NOT NULL,
        warning TEXT,
        raw_json TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(account_id, child_user_id, site_id, promotion_id, promotion_type, item_status)
      )`
  });
}

function migrateRouteIdentityTable(database, { table, uniqueColumns, columns, createSql }) {
  if (hasUniqueIndex(database, table, uniqueColumns)) return;
  const sourceTable = `${table}_legacy_source`;
  const unresolvedTable = `${table}_legacy_unresolved`;
  database.exec('BEGIN IMMEDIATE');
  try {
    database.exec(`ALTER TABLE ${table} RENAME TO ${sourceTable}`);
    database.exec(`
      CREATE TABLE IF NOT EXISTS ${unresolvedTable} AS
      SELECT *, CAST(NULL AS TEXT) AS blocked_reason, CAST(NULL AS TEXT) AS blocked_at
      FROM ${sourceTable} WHERE 0
    `);
    database.exec(createSql);
    const sourceRows = database.prepare(`SELECT * FROM ${sourceTable} ORDER BY id`).all();
    const insertable = database.prepare(
      `INSERT INTO ${table} (${columns.join(', ')}) VALUES (${columns.map(() => '?').join(', ')})`
    );
    const unresolvedColumns = [...columns, 'blocked_reason', 'blocked_at'];
    const unresolvedInsert = database.prepare(
      `INSERT INTO ${unresolvedTable} (${unresolvedColumns.join(', ')}) VALUES (${unresolvedColumns.map(() => '?').join(', ')})`
    );
    const seen = new Set();
    for (const row of sourceRows) {
      const route = legacyRouteForRow(database, table, row);
      const values = route
        ? routeValuesForRow(table, row, route)
        : null;
      const key = values ? identityKeyForTable(table, values) : null;
      if (!values || seen.has(key)) {
        const reason = !values ? 'blocked_legacy_route' : 'duplicate_route_identity';
        unresolvedInsert.run(...columns.map((column) => row[column] ?? null), reason, new Date().toISOString());
        continue;
      }
      insertable.run(...columns.map((column) => values[column] ?? null));
      seen.add(key);
    }
    database.exec(`DROP TABLE ${sourceTable}`);
    database.exec('COMMIT');
  } catch (error) {
    try {
      database.exec('ROLLBACK');
    } catch {
      // Preserve the original migration error.
    }
    throw error;
  }
}

function hasUniqueIndex(database, table, expectedColumns) {
  const indexes = database.prepare(`PRAGMA index_list(${table})`).all();
  return indexes.some((index) => {
    if (Number(index.unique) !== 1) return false;
    const columns = database.prepare(`PRAGMA index_info(${index.name})`).all()
      .sort((left, right) => Number(left.seqno) - Number(right.seqno))
      .map((row) => String(row.name));
    return columns.length === expectedColumns.length
      && columns.every((name, indexPosition) => name === expectedColumns[indexPosition]);
  });
}

function legacyRouteForRow(database, table, row) {
  const directChild = String(row.child_user_id || '').trim();
  const directSite = String(row.site_id || '').trim().toUpperCase();
  if (directChild && directSite) return { child_user_id: directChild, site_id: directSite };
  if (table === 'promo_campaigns') return null;
  const routes = database.prepare(
    `SELECT DISTINCT child_user_id, site_id
     FROM promo_campaigns
     WHERE account_id = ? AND promotion_id = ? AND promotion_type = ?
       AND child_user_id <> '' AND site_id <> ''`
  ).all(String(row.account_id || ''), String(row.promotion_id || ''), String(row.promotion_type || ''))
    .map((candidate) => ({
      child_user_id: String(candidate.child_user_id || '').trim(),
      site_id: String(candidate.site_id || '').trim().toUpperCase(),
    }))
    .filter((candidate) => candidate.child_user_id && candidate.site_id);
  return routes.length === 1 ? routes[0] : null;
}

function routeValuesForRow(table, row, route) {
  return {
    ...row,
    child_user_id: route.child_user_id,
    site_id: route.site_id,
    promotion_type: String(row.promotion_type || '').toUpperCase(),
    ...(table === 'promo_items' ? { source: row.source || 'legacy_route_migration' } : {})
  };
}

function identityKeyForTable(table, row) {
  const base = [row.account_id, row.child_user_id, row.site_id, row.promotion_id, row.promotion_type];
  if (table === 'promo_items') base.push(row.item_id);
  if (table === 'promo_item_fetch_states') base.push(row.item_status);
  return base.map((value) => String(value || '').toUpperCase()).join('|');
}

function migrateOAuthStateClaimsAndDailySnapshots(database) {
  database.exec('BEGIN IMMEDIATE');
  try {
    addColumnIfMissing(database, 'oauth_states', 'processing_state', "TEXT NOT NULL DEFAULT 'pending'");
    addColumnIfMissing(database, 'oauth_states', 'claim_token', 'TEXT');
    addColumnIfMissing(database, 'oauth_states', 'claimed_at', 'TEXT');
    addColumnIfMissing(database, 'oauth_states', 'claim_expires_at', 'TEXT');
    addColumnIfMissing(database, 'oauth_states', 'consumed_at', 'TEXT');
    addColumnIfMissing(database, 'oauth_states', 'last_error_code', 'TEXT');
    addColumnIfMissing(database, 'oauth_states', 'attempt_count', 'INTEGER NOT NULL DEFAULT 0');
    database.exec(`
      UPDATE oauth_states
      SET processing_state = 'pending'
      WHERE processing_state IS NULL OR processing_state = '';

      CREATE INDEX IF NOT EXISTS idx_oauth_states_processing
        ON oauth_states(processing_state, claim_expires_at, created_at);

      CREATE TABLE IF NOT EXISTS daily_item_identity_snapshots (
        business_date TEXT NOT NULL,
        account_id TEXT NOT NULL,
        child_user_id TEXT NOT NULL,
        site_id TEXT NOT NULL,
        complete INTEGER NOT NULL DEFAULT 0,
        item_count INTEGER NOT NULL DEFAULT 0,
        item_ids_hash TEXT NOT NULL,
        item_ids_json TEXT NOT NULL,
        source TEXT,
        captured_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (business_date, account_id, child_user_id, site_id)
      );

      CREATE INDEX IF NOT EXISTS idx_daily_item_identity_route
        ON daily_item_identity_snapshots(
          account_id, child_user_id, site_id, business_date, complete
        );
    `);
    database.exec('COMMIT');
  } catch (error) {
    try {
      database.exec('ROLLBACK');
    } catch {
      // Preserve the migration error.
    }
    throw error;
  }
}

function migrateActivityCacheIdentity(database) {
  const columns = database.prepare('PRAGMA table_info(activity_cache_states)').all();
  const primaryKey = columns
    .filter((row) => Number(row.pk) > 0)
    .sort((left, right) => Number(left.pk) - Number(right.pk))
    .map((row) => String(row.name));
  const expectedPrimaryKey = ['account_id', 'child_user_id', 'site_id', 'promotion_id', 'promotion_type'];
  if (primaryKey.length === expectedPrimaryKey.length
    && primaryKey.every((name, index) => name === expectedPrimaryKey[index])) {
    return;
  }

  const childExpression = columns.some((row) => row.name === 'child_user_id')
    ? "COALESCE(child_user_id, '')"
    : "''";
  database.exec('BEGIN IMMEDIATE');
  try {
    database.exec('ALTER TABLE activity_cache_states RENAME TO activity_cache_states_legacy_identity');
    database.exec(`
      CREATE TABLE activity_cache_states (
        account_id TEXT NOT NULL,
        child_user_id TEXT NOT NULL DEFAULT '',
        site_id TEXT NOT NULL DEFAULT '',
        promotion_id TEXT NOT NULL DEFAULT '',
        promotion_type TEXT NOT NULL DEFAULT '',
        catalog_checked_at TEXT,
        items_full_checked_at TEXT,
        dirty INTEGER NOT NULL DEFAULT 0,
        expired INTEGER NOT NULL DEFAULT 0,
        continuity TEXT NOT NULL DEFAULT 'continuous',
        event_cursor TEXT,
        last_error TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (account_id, child_user_id, site_id, promotion_id, promotion_type)
      );
      INSERT INTO activity_cache_states
        (account_id, child_user_id, site_id, promotion_id, promotion_type,
         catalog_checked_at, items_full_checked_at, dirty, expired, continuity,
         event_cursor, last_error, updated_at)
      SELECT account_id, ${childExpression}, site_id, promotion_id, promotion_type,
             catalog_checked_at, items_full_checked_at, dirty, expired, continuity,
             event_cursor, last_error, updated_at
      FROM activity_cache_states_legacy_identity;
      DROP TABLE activity_cache_states_legacy_identity;
      CREATE INDEX idx_activity_cache_due
        ON activity_cache_states(
          account_id, child_user_id, site_id, dirty, continuity,
          catalog_checked_at, items_full_checked_at
        );
    `);
    database.exec('COMMIT');
  } catch (error) {
    try {
      database.exec('ROLLBACK');
    } catch {
      // Preserve the migration error.
    }
    throw error;
  }
}

function addColumnIfMissing(database, table, column, type) {
  const columns = database.prepare(`PRAGMA table_info(${table})`).all();
  if (!columns.some((row) => row.name === column)) {
    database.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${type}`);
  }
}

export function nowIso() {
  return new Date().toISOString();
}

export function all(sql, params = []) {
  return getDb().prepare(sql).all(...params);
}

export function get(sql, params = []) {
  return getDb().prepare(sql).get(...params);
}

export function run(sql, params = []) {
  return getDb().prepare(sql).run(...params);
}

export function transaction(callback) {
  const database = getDb();
  database.exec('BEGIN IMMEDIATE');
  try {
    const result = callback(database);
    database.exec('COMMIT');
    return result;
  } catch (error) {
    try {
      database.exec('ROLLBACK');
    } catch {
      // Preserve the original transaction error.
    }
    throw error;
  }
}
