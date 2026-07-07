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
      created_at TEXT NOT NULL
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

    CREATE TABLE IF NOT EXISTS promo_campaigns (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id TEXT NOT NULL,
      promotion_id TEXT NOT NULL,
      promotion_type TEXT NOT NULL,
      merchant_id TEXT,
      child_user_id TEXT,
      site_id TEXT,
      logistic_type TEXT,
      name TEXT,
      status TEXT,
      start_date TEXT,
      finish_date TEXT,
      raw_json TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(account_id, promotion_id, promotion_type)
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
      child_user_id TEXT,
      site_id TEXT,
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
      UNIQUE(account_id, promotion_id, promotion_type, item_id)
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

    CREATE TABLE IF NOT EXISTS history_task_summary_cache (
      cache_key TEXT PRIMARY KEY,
      task_count INTEGER NOT NULL DEFAULT 0,
      task_max_id INTEGER NOT NULL DEFAULT 0,
      task_updated_at TEXT,
      result_count INTEGER NOT NULL DEFAULT 0,
      result_max_id INTEGER NOT NULL DEFAULT 0,
      data_json TEXT NOT NULL,
      created_at TEXT NOT NULL
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

    CREATE TABLE IF NOT EXISTS promo_item_fetch_states (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id TEXT NOT NULL,
      promotion_id TEXT NOT NULL,
      promotion_type TEXT NOT NULL,
      item_status TEXT NOT NULL,
      platform_total INTEGER,
      saved_count INTEGER NOT NULL DEFAULT 0,
      detail_status TEXT NOT NULL,
      warning TEXT,
      raw_json TEXT,
      updated_at TEXT NOT NULL,
      UNIQUE(account_id, promotion_id, promotion_type, item_status)
    );

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
