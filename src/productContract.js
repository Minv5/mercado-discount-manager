import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const PRODUCT_ID = 'mercado-discount-manager';
export const PRODUCT_DISPLAY_NAME = '美客多活动助手';
export const PROTOCOL_VERSION = '3';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, '..');

function sourceFingerprint(root) {
  const hash = crypto.createHash('sha256');
  for (const relative of ['package.json', 'src/productContract.js', 'src/server.js']) {
    const file = path.join(root, relative);
    hash.update(relative);
    hash.update(fs.readFileSync(file));
  }
  return hash.digest('hex').toUpperCase();
}

export function readProductBuildInfo(root = APP_ROOT) {
  const file = path.join(root, 'build-info.json');
  if (fs.existsSync(file)) {
    try {
      const value = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
      if (
        value?.product === PRODUCT_ID
        && String(value?.protocol_version || '') === PROTOCOL_VERSION
        && /^[A-F0-9]{64}$/i.test(String(value?.build_fingerprint || ''))
      ) {
        return {
          product: PRODUCT_ID,
          display_name: PRODUCT_DISPLAY_NAME,
          protocol_version: PROTOCOL_VERSION,
          build_fingerprint: String(value.build_fingerprint).toUpperCase(),
        };
      }
    } catch {
      // A malformed generated file must not weaken the product/protocol identity.
    }
  }
  return {
    product: PRODUCT_ID,
    display_name: PRODUCT_DISPLAY_NAME,
    protocol_version: PROTOCOL_VERSION,
    build_fingerprint: sourceFingerprint(root),
  };
}

export const PRODUCT_BUILD_INFO = readProductBuildInfo();
