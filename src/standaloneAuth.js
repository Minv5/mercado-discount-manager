import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { STANDALONE_AUTH_DIR } from './config.js';

const TOKEN_PATH = path.join(STANDALONE_AUTH_DIR, 'mercado_oauth_token.json');
const CONFIG_PATH = path.join(STANDALONE_AUTH_DIR, 'mercado_oauth_config.json');
const REFRESH_SCRIPT = path.join(STANDALONE_AUTH_DIR, 'refresh_now.ps1');

export function hasStandaloneAuth() {
  return fs.existsSync(TOKEN_PATH) && fs.existsSync(CONFIG_PATH);
}

export function readStandaloneToken() {
  if (!fs.existsSync(TOKEN_PATH)) return null;
  const token = JSON.parse(fs.readFileSync(TOKEN_PATH, 'utf8'));
  return token;
}

export function readStandaloneConfig() {
  if (!fs.existsSync(CONFIG_PATH)) return null;
  return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
}

export function standaloneAccountSummary(profile = null) {
  const token = readStandaloneToken();
  if (!token?.user_id) return null;
  return {
    id: `standalone-${token.user_id}`,
    provider: 'mercadolibre-standalone',
    account_id: String(token.user_id),
    display_name: profile?.nickname || `账号 ${token.user_id}`,
    site_id: profile?.site_id || token.site_id || null,
    scopes: token.scope || null,
    token_type: token.token_type || null,
    expires_at: token.expires_at || null,
    created_at: token.created_at || null,
    updated_at: token.refreshed_at || token.created_at || null,
    auth_source: 'standalone'
  };
}

export function getStandaloneSecrets() {
  const token = readStandaloneToken();
  const config = readStandaloneConfig();
  if (!token?.access_token || !token?.user_id) return null;
  return {
    ...standaloneAccountSummary(),
    accessToken: token.access_token,
    refreshToken: token.refresh_token || null,
    client_id: config?.client_id || token.client_id || null,
    clientSecret: config?.client_secret || null,
    redirect_uri: token.redirect_uri || config?.redirect_uri || null,
    authSource: 'standalone'
  };
}

export function refreshStandaloneToken({ force = false } = {}) {
  if (!fs.existsSync(REFRESH_SCRIPT)) {
    throw new Error('缺少 standalone Mercado refresh 脚本');
  }
  const args = [
    '-NoProfile',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    REFRESH_SCRIPT
  ];
  if (force) args.push('-Force');
  const output = execFileSync('powershell.exe', args, {
    cwd: STANDALONE_AUTH_DIR,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    timeout: 60000
  });
  return safeRefreshOutput(output);
}

function safeRefreshOutput(output) {
  try {
    const parsed = JSON.parse(output);
    return {
      ok: parsed.ok,
      action: parsed.action,
      expires_at: parsed.expires_at,
      token_path: parsed.token_path ? '[standalone-token-file]' : undefined
    };
  } catch {
    return { ok: true, action: 'refresh_script_completed' };
  }
}
