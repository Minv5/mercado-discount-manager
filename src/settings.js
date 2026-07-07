import fs from 'node:fs';
import path from 'node:path';
import { DATA_DIR, STANDALONE_AUTH_DIR } from './config.js';
import { normalizeConcurrency, normalizeWriteConcurrency } from './concurrency.js';

export const SETTINGS_PATH = path.join(DATA_DIR, 'settings.json');

export const DEFAULT_SETTINGS = {
  authDir: STANDALONE_AUTH_DIR,
  outputDir: path.join(DATA_DIR, 'exports'),
  sellerDefaultDiscount: 5,
  officialDefaultDiscount: 6,
  cancelMaxRounds: 5,
  maxItemsPerPromotion: 50,
  readConcurrency: 2,
  previewConcurrency: 2,
  writeConcurrency: 2,
  storeAliases: {},
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
  if (!fs.existsSync(SETTINGS_PATH)) return { ...DEFAULT_SETTINGS, defaultFilters: { ...DEFAULT_SETTINGS.defaultFilters }, storeAliases: {} };
  const parsed = safeJson(fs.readFileSync(SETTINGS_PATH, 'utf8'));
  return normalizeSettings({ ...DEFAULT_SETTINGS, ...parsed, defaultFilters: { ...DEFAULT_SETTINGS.defaultFilters, ...(parsed.defaultFilters || {}) } });
}

export function saveSettings(input) {
  const settings = normalizeSettings({ ...readSettings(), ...input, defaultFilters: { ...readSettings().defaultFilters, ...(input.defaultFilters || {}) } });
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(SETTINGS_PATH, JSON.stringify(settings, null, 2), 'utf8');
  return settings;
}

export function normalizeSettings(input) {
  const authDir = String(input.authDir || DEFAULT_SETTINGS.authDir);
  const outputDir = String(input.outputDir || DEFAULT_SETTINGS.outputDir);
  return {
    authDir,
    outputDir,
    sellerDefaultDiscount: boundedNumber(input.sellerDefaultDiscount, 1, 90, 5),
    officialDefaultDiscount: boundedNumber(input.officialDefaultDiscount, 1, 90, 6),
    cancelMaxRounds: Math.max(1, Math.floor(Number(input.cancelMaxRounds || 5))),
    maxItemsPerPromotion: Math.max(1, Math.floor(Number(input.maxItemsPerPromotion || 50))),
    readConcurrency: normalizeConcurrency(input.readConcurrency, DEFAULT_SETTINGS.readConcurrency),
    previewConcurrency: normalizeConcurrency(input.previewConcurrency, DEFAULT_SETTINGS.previewConcurrency),
    writeConcurrency: normalizeWriteConcurrency(input.writeConcurrency, DEFAULT_SETTINGS.writeConcurrency),
    storeAliases: normalizeStoreAliases(input.storeAliases || {}),
    defaultFilters: normalizeDefaultFilters(input.defaultFilters || {})
  };
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

function arrayOfText(value) {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
  return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}
