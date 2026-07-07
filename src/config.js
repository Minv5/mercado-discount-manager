import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const ROOT_DIR = path.resolve(__dirname, '..');
export const DATA_DIR = process.env.MDM_DATA_DIR || path.join(ROOT_DIR, 'data');
export const DB_PATH = process.env.MDM_DB_PATH || path.join(DATA_DIR, 'discount-manager.sqlite');
export const KEY_PATH = process.env.MDM_KEY_PATH || path.join(DATA_DIR, 'local.key');
export const HOST = process.env.MDM_HOST || '127.0.0.1';
export const PORT = Number(process.env.MDM_PORT || 28758);
export const API_BASE_URL = process.env.ML_API_BASE_URL || 'https://api.mercadolibre.com';
export const DEFAULT_AUTH_DOMAIN = process.env.ML_AUTH_DOMAIN || 'https://global-selling.mercadolibre.com';
export const APP_VERSION = process.env.ML_PROMO_APP_VERSION || 'v2';
export const STANDALONE_AUTH_DIR = process.env.ML_STANDALONE_AUTH_DIR || 'C:\\Users\\dztf6\\Documents\\美客多授权';
