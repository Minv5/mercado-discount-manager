import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { STANDALONE_AUTH_DIR } from './config.js';

const TOKEN_PATH = path.join(STANDALONE_AUTH_DIR, 'mercado_oauth_token.json');
const CONFIG_PATH = path.join(STANDALONE_AUTH_DIR, 'mercado_oauth_config.json');
const REFRESH_SCRIPT = path.join(STANDALONE_AUTH_DIR, 'refresh_now.ps1');
const POWERSHELL_EXE = 'C:\\Program Files\\PowerShell\\7\\pwsh.exe';

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

export function buildStandaloneTokenAccountImport({
  token = readStandaloneToken(),
  config = readStandaloneConfig(),
  profile = null,
  authDomain = 'https://global-selling.mercadolibre.com',
} = {}) {
  if (!token?.user_id || !token?.access_token || !token?.refresh_token) {
    throw new Error('standalone token 缺少 user_id、access_token 或 refresh_token，未导入本地加密授权。');
  }
  if (!config?.client_id || !config?.client_secret) {
    throw new Error('standalone OAuth 配置缺少 client_id 或 client_secret，未导入本地加密授权。');
  }
  const accountId = String(token.user_id);
  return {
    token: {
      user_id: accountId,
      access_token: String(token.access_token),
      refresh_token: String(token.refresh_token),
      token_type: token.token_type || 'Bearer',
      expires_at: token.expires_at || null,
      scope: token.scope || null,
    },
    profile: {
      id: accountId,
      nickname: profile?.display_name || profile?.nickname || `账号 ${accountId}`,
      site_id: profile?.site_id || token.site_id || null,
    },
    clientId: String(config.client_id),
    clientSecret: String(config.client_secret),
    redirectUri: String(token.redirect_uri || config.redirect_uri || ''),
    authDomain: String(config.auth_domain || authDomain),
  };
}

export function refreshStandaloneToken({ force = false } = {}) {
  if (!fs.existsSync(REFRESH_SCRIPT)) {
    throw new Error('缺少 standalone Mercado refresh 脚本');
  }
  if (!fs.existsSync(POWERSHELL_EXE)) {
    throw new Error('缺少 PowerShell 7.6 Core 运行时');
  }
  const args = [
    '-NoLogo',
    '-NoProfile',
    '-NonInteractive',
    '-File',
    REFRESH_SCRIPT
  ];
  if (force) args.push('-Force');
  const output = execFileSync(POWERSHELL_EXE, args, {
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
