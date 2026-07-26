import fs from 'node:fs';
import path from 'node:path';
import { DATA_DIR, STANDALONE_AUTH_DIR } from './config.js';
import { DEFAULT_ACTIVITY_CONCURRENCY, DEFAULT_READ_CONCURRENCY, DEFAULT_WRITE_CONCURRENCY, normalizeActivityConcurrency, normalizeConcurrency, normalizeWriteConcurrency } from './concurrency.js';
import { normalizeOperatingSites } from './operatingSites.js';
import { decryptSecret, encryptSecret } from './security.js';
import {
  JsonFilePersistenceError,
  readJsonFileSync,
  writeJsonFileAtomicallySync,
} from './jsonFilePersistence.js';

export const SETTINGS_PATH = path.join(DATA_DIR, 'settings.json');
const CONCURRENCY_POLICY_VERSION = 2;

export const DEFAULT_SETTINGS = {
  authDir: STANDALONE_AUTH_DIR,
  outputDir: path.join(DATA_DIR, 'exports'),
  sellerDefaultDiscount: 5,
  officialDefaultDiscount: 6,
  sellerMaxDiscount: null,
  officialMaxDiscount: null,
  cancelMaxRounds: 5,
  maxItemsPerPromotion: 50,
  readConcurrency: DEFAULT_READ_CONCURRENCY,
  previewConcurrency: DEFAULT_ACTIVITY_CONCURRENCY,
  writeConcurrency: DEFAULT_WRITE_CONCURRENCY,
  concurrencyPolicyVersion: CONCURRENCY_POLICY_VERSION,
  oauthClientId: '',
  oauthRedirectUri: '',
  webhookCallbackUrl: '',
  storeAliases: {},
  operatingSites: {},
  defaultFilters: {
    siteIds: [],
    promotionTypes: [],
    status: '',
    keywords: [],
    sellerActivityNames: [],
    officialActivityNames: [],
    excludeSeller: false,
    excludeOfficial: false
  }
};

export function readSettings() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const parsed = migrateConcurrencyPolicy(readStoredSettings());
  return normalizeSettings({ ...DEFAULT_SETTINGS, ...parsed, defaultFilters: { ...DEFAULT_SETTINGS.defaultFilters, ...(parsed.defaultFilters || {}) } });
}

export function saveSettings(input) {
  const previous = readStoredSettings();
  const current = readSettings();
  const suppliedSecret = String(input?.oauthClientSecret ?? '').trim();
  const clearSecret = input?.clearOAuthClientSecret === true;
  const secretCipher = clearSecret
    ? ''
    : suppliedSecret ? encryptSecret(suppliedSecret) : String(previous.oauthClientSecretCipher || '');
  const settings = normalizeSettings({
    ...current,
    ...input,
    oauthClientSecretCipher: secretCipher,
    defaultFilters: { ...current.defaultFilters, ...(input.defaultFilters || {}) },
  });
  fs.mkdirSync(DATA_DIR, { recursive: true });
  writeJsonFileAtomicallySync({
    target: SETTINGS_PATH,
    value: { ...settings, oauthClientSecretCipher: secretCipher },
  });
  return settings;
}

export function readConfiguredOAuth() {
  const raw = readStoredSettings();
  const clientSecretCipher = String(raw.oauthClientSecretCipher || '');
  let clientSecret = '';
  if (clientSecretCipher) {
    try {
      clientSecret = decryptSecret(clientSecretCipher);
    } catch {
      clientSecret = '';
    }
  }
  return {
    client_id: text(raw.oauthClientId),
    client_secret: clientSecret,
    redirect_uri: normalizeUrl(raw.oauthRedirectUri),
  };
}

export function resolveOAuthConfig(standaloneConfig = null) {
  const saved = readConfiguredOAuth();
  const fallback = standaloneConfig && typeof standaloneConfig === 'object' ? standaloneConfig : {};
  const usesSettings = Boolean(saved.client_id || saved.client_secret || saved.redirect_uri);
  return {
    ...fallback,
    client_id: saved.client_id || text(fallback.client_id || fallback.clientId),
    client_secret: saved.client_secret || text(fallback.client_secret || fallback.clientSecret),
    redirect_uri: saved.redirect_uri || normalizeUrl(fallback.redirect_uri || fallback.redirectUri),
    config_source: usesSettings ? 'settings' : 'standalone-auth-dir',
  };
}

export function normalizeSettings(input) {
  const authDir = String(input.authDir || DEFAULT_SETTINGS.authDir);
  const outputDir = String(input.outputDir || DEFAULT_SETTINGS.outputDir);
  return {
    authDir,
    outputDir,
    sellerDefaultDiscount: boundedNumber(input.sellerDefaultDiscount, 1, 90, 5),
    officialDefaultDiscount: boundedNumber(input.officialDefaultDiscount, 1, 90, 6),
    sellerMaxDiscount: optionalBoundedNumber(input.sellerMaxDiscount, 1, 90),
    officialMaxDiscount: optionalBoundedNumber(input.officialMaxDiscount, 1, 90),
    cancelMaxRounds: Math.max(1, Math.floor(Number(input.cancelMaxRounds || 5))),
    maxItemsPerPromotion: Math.max(1, Math.floor(Number(input.maxItemsPerPromotion || 50))),
    readConcurrency: normalizeConcurrency(input.readConcurrency, DEFAULT_SETTINGS.readConcurrency),
    previewConcurrency: normalizeActivityConcurrency(input.previewConcurrency, DEFAULT_SETTINGS.previewConcurrency),
    writeConcurrency: normalizeWriteConcurrency(input.writeConcurrency, DEFAULT_SETTINGS.writeConcurrency),
    concurrencyPolicyVersion: CONCURRENCY_POLICY_VERSION,
    oauthClientId: text(input.oauthClientId).slice(0, 160),
    oauthRedirectUri: normalizeUrl(input.oauthRedirectUri),
    webhookCallbackUrl: normalizeUrl(input.webhookCallbackUrl),
    oauthClientSecretConfigured: Boolean(String(input.oauthClientSecretCipher || '')),
    storeAliases: normalizeStoreAliases(input.storeAliases || {}),
    operatingSites: normalizeOperatingSites(input.operatingSites || {}),
    defaultFilters: normalizeDefaultFilters(input.defaultFilters || {})
  };
}

export function hasConfiguredCycleMaximums(settings = {}) {
  return optionalBoundedNumber(settings.sellerMaxDiscount, 1, 90) !== null
    && optionalBoundedNumber(settings.officialMaxDiscount, 1, 90) !== null;
}

function migrateConcurrencyPolicy(stored = {}) {
  const migrated = { ...stored };
  const version = Math.max(0, Math.floor(Number(stored.concurrencyPolicyVersion || 0)));
  if (version < CONCURRENCY_POLICY_VERSION) {
    if (Number(stored.previewConcurrency) === 42) migrated.previewConcurrency = DEFAULT_ACTIVITY_CONCURRENCY;
    if (Number(stored.writeConcurrency) === 24) migrated.writeConcurrency = DEFAULT_WRITE_CONCURRENCY;
    migrated.concurrencyPolicyVersion = CONCURRENCY_POLICY_VERSION;
  }
  return migrated;
}

export function normalizeStoreAliases(value) {
  const aliases = {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) return aliases;
  for (const [accountId, alias] of Object.entries(value)) {
    const key = String(accountId || '').trim();
    const text = String(alias || '').trim();
    if (key && text) aliases[key] = text.slice(0, 30);
  }
  return aliases;
}

export function normalizeDefaultFilters(filters) {
  return {
    siteIds: arrayOfText(filters.siteIds),
    promotionTypes: arrayOfText(filters.promotionTypes).map((value) => value.toUpperCase()),
    status: String(filters.status || ''),
    keywords: arrayOfText(filters.keywords),
    sellerActivityNames: arrayOfText(filters.sellerActivityNames),
    officialActivityNames: arrayOfText(filters.officialActivityNames),
    excludeSeller: Boolean(filters.excludeSeller),
    excludeOfficial: Boolean(filters.excludeOfficial)
  };
}

function boundedNumber(value, min, max, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

function optionalBoundedNumber(value, min, max) {
  if (value === null || value === undefined || String(value).trim() === '') return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.min(max, Math.max(min, n));
}

function arrayOfText(value) {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
  return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
}

function readStoredSettings() {
  try {
    const result = readJsonFileSync({
      target: SETTINGS_PATH,
      backupPath: `${SETTINGS_PATH}.bak`,
      allowMissing: true,
    });
    if (!result.exists) return {};
    if (!result.value || typeof result.value !== 'object' || Array.isArray(result.value)) {
      throw new JsonFilePersistenceError('JSON 文件内容不是配置对象', {
        code: 'JSON_FILE_CORRUPT',
        cause_code: 'INVALID_SETTINGS_SHAPE',
        operation: 'parse_json_file',
        storage_target: path.basename(SETTINGS_PATH),
        backup_target: path.basename(`${SETTINGS_PATH}.bak`),
        backup_available: result.backup_available,
      });
    }
    return result.value;
  } catch (error) {
    if (!(error instanceof JsonFilePersistenceError)) throw error;
    const corrupt = error.code === 'JSON_FILE_CORRUPT';
    const settingsError = new Error(
      corrupt
        ? '设置文件损坏，已停止加载；不会使用默认值覆盖，请从备份恢复。'
        : '设置文件暂时无法读取，已停止加载；不会使用默认值覆盖。',
      { cause: error },
    );
    settingsError.name = 'SettingsPersistenceError';
    settingsError.code = corrupt ? 'SETTINGS_JSON_CORRUPT' : 'SETTINGS_FILE_UNREADABLE';
    settingsError.cause_code = error.cause_code || error.code;
    settingsError.operation = 'read_settings';
    settingsError.storage_target = path.basename(SETTINGS_PATH);
    settingsError.backup_target = path.basename(`${SETTINGS_PATH}.bak`);
    settingsError.backup_available = Boolean(error.backup_available);
    throw settingsError;
  }
}

function text(value) {
  return String(value || '').trim();
}

function normalizeUrl(value) {
  const candidate = text(value);
  if (!candidate) return '';
  try {
    const parsed = new URL(candidate);
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed.toString() : '';
  } catch {
    return '';
  }
}
