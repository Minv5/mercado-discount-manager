import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { DATA_DIR, KEY_PATH } from './config.js';

function ensureKey() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  if (process.env.MDM_MASTER_KEY) {
    return crypto.createHash('sha256').update(process.env.MDM_MASTER_KEY, 'utf8').digest();
  }
  if (!fs.existsSync(KEY_PATH)) {
    fs.writeFileSync(KEY_PATH, crypto.randomBytes(32).toString('base64'), { flag: 'wx' });
  }
  const raw = fs.readFileSync(KEY_PATH, 'utf8').trim();
  return crypto.createHash('sha256').update(raw, 'utf8').digest();
}

const key = ensureKey();

export function encryptSecret(value) {
  if (value === null || value === undefined || value === '') return null;
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const ciphertext = Buffer.concat([cipher.update(String(value), 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `v1:${iv.toString('base64')}:${tag.toString('base64')}:${ciphertext.toString('base64')}`;
}

export function decryptSecret(payload) {
  if (!payload) return null;
  const [version, ivB64, tagB64, textB64] = String(payload).split(':');
  if (version !== 'v1') throw new Error('不支持的本地加密格式');
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, Buffer.from(ivB64, 'base64'));
  decipher.setAuthTag(Buffer.from(tagB64, 'base64'));
  return Buffer.concat([
    decipher.update(Buffer.from(textB64, 'base64')),
    decipher.final()
  ]).toString('utf8');
}

export function createPkcePair() {
  const verifier = crypto.randomBytes(48).toString('base64url');
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
  return { verifier, challenge };
}

export function createState() {
  return crypto.randomBytes(24).toString('base64url');
}
