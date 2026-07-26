import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { DATA_DIR, KEY_PATH } from './config.js';

export const DEFAULT_JSON_BODY_MAX_BYTES = 1024 * 1024;

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

let cachedKey = null;

function encryptionKey() {
  if (!cachedKey) cachedKey = ensureKey();
  return cachedKey;
}

export function encryptSecret(value) {
  if (value === null || value === undefined || value === '') return null;
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', encryptionKey(), iv);
  const ciphertext = Buffer.concat([cipher.update(String(value), 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `v1:${iv.toString('base64')}:${tag.toString('base64')}:${ciphertext.toString('base64')}`;
}

export function decryptSecret(payload) {
  if (!payload) return null;
  const [version, ivB64, tagB64, textB64] = String(payload).split(':');
  if (version !== 'v1') throw new Error('不支持的本地加密格式');
  const decipher = crypto.createDecipheriv('aes-256-gcm', encryptionKey(), Buffer.from(ivB64, 'base64'));
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

export async function readJsonBody(request, {
  maxBytes = DEFAULT_JSON_BODY_MAX_BYTES,
} = {}) {
  const limit = normalizeBodyLimit(maxBytes);
  const chunks = [];
  let received = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    received += buffer.length;
    if (received > limit) {
      const error = new Error('请求内容超过允许大小');
      error.name = 'RequestBodyTooLargeError';
      error.code = 'REQUEST_BODY_TOO_LARGE';
      error.statusCode = 413;
      error.kind = 'request_body_too_large';
      error.operation = 'read_json_body';
      error.max_bytes = limit;
      throw error;
    }
    chunks.push(buffer);
  }

  const text = Buffer.concat(chunks, received).toString('utf8');
  if (!text.trim()) return {};
  try {
    return JSON.parse(text);
  } catch (cause) {
    const error = new Error('请求内容不是有效 JSON', { cause });
    error.name = 'InvalidJsonBodyError';
    error.code = 'INVALID_JSON_BODY';
    error.statusCode = 400;
    error.kind = 'invalid_json';
    error.operation = 'parse_json_body';
    throw error;
  }
}

export function safeErrorDetails(error, context = {}) {
  const source = error && typeof error === 'object' ? error : {};
  const safeContext = context && typeof context === 'object' ? context : {};
  const details = {};
  assignSafeText(details, 'kind', safeContext.kind ?? source.kind);
  assignSafeStatus(details, safeContext.status ?? source.status ?? source.statusCode ?? source.http_status);
  assignSafeCode(details, 'code', safeContext.code ?? source.code);
  assignSafeCode(details, 'cause_code', safeContext.cause_code ?? source.cause_code ?? source.cause?.code);
  assignSafeText(details, 'operation', safeContext.operation ?? source.operation);
  assignSafeText(details, 'stage', safeContext.stage ?? source.stage);
  return details;
}

function normalizeBodyLimit(value) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 1) {
    throw new TypeError('maxBytes must be a positive safe integer');
  }
  return parsed;
}

function assignSafeStatus(target, value) {
  const status = Number(value);
  if (Number.isInteger(status) && status >= 100 && status <= 599) {
    target.status = status;
  }
}

function assignSafeCode(target, key, value) {
  const text = String(value || '').trim().toUpperCase();
  if (/^[A-Z0-9_]{1,80}$/.test(text)) target[key] = text;
}

function assignSafeText(target, key, value) {
  const text = String(value || '').trim();
  if (/^[A-Za-z0-9_.:-]{1,120}$/.test(text)) target[key] = text;
}
