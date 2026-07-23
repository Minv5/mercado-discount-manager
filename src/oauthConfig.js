import { DEFAULT_AUTH_DOMAIN, HOST, PORT } from './config.js';
import { buildAuthorizationUrl } from './mlClient.js';

export const STANDALONE_DEFAULT_REDIRECT_URI = 'https://xingtupro1020.com/callback/';

export function defaultOAuthRedirectUri({ host = HOST, port = PORT } = {}) {
  if (host && port && process.env.ML_ALLOW_LOCAL_OAUTH_REDIRECT === '1') {
    return `http://${host}:${port}/oauth/callback`;
  }
  return STANDALONE_DEFAULT_REDIRECT_URI;
}

export function prepareOAuthStartFromConfig(config, { pkce, state, redirectUri, tokenRedirectUri } = {}) {
  if (!config || typeof config !== 'object') {
    throw Object.assign(new Error('缺少 Mercado OAuth 配置文件'), { status: 400 });
  }
  const clientId = text(config.client_id || config.clientId);
  const clientSecret = text(config.client_secret || config.clientSecret);
  if (!clientId) throw Object.assign(new Error('OAuth 配置缺少 client_id'), { status: 400 });
  if (!clientSecret) throw Object.assign(new Error('OAuth 配置缺少 client_secret'), { status: 400 });

  const configuredRedirectUri = text(config.redirect_uri || config.redirectUri);
  const effectiveRedirectUri = text(redirectUri || tokenRedirectUri || configuredRedirectUri) || defaultOAuthRedirectUri();
  const authDomain = text(config.auth_domain || config.authDomain) || DEFAULT_AUTH_DOMAIN;
  const code = pkce || {};
  const verifier = code.verifier;
  const challenge = code.challenge;
  if (!verifier || !challenge) throw Object.assign(new Error('OAuth PKCE 参数生成失败'), { status: 500 });
  if (!state) throw Object.assign(new Error('OAuth state 生成失败'), { status: 500 });

  return {
    stateRecord: {
      state,
      clientId,
      clientSecret,
      redirectUri: effectiveRedirectUri,
      authDomain,
      codeVerifier: verifier,
      codeChallenge: challenge
    },
    response: {
      ok: true,
      authorizationUrl: buildAuthorizationUrl({
        authDomain,
        clientId,
        redirectUri: effectiveRedirectUri,
        state,
        codeChallenge: challenge
      }),
      redirectUri: effectiveRedirectUri,
      authDomain,
      configSource: text(config.config_source) || 'standalone-auth-dir',
      warning: configuredRedirectUri || tokenRedirectUri
        ? null
        : `当前配置未提供 redirect_uri，已使用已验证的授权回调 ${effectiveRedirectUri}。如果授权页面仍提示 redirect_uri 不匹配，请在 Mercado App 后台确认该回调地址已登记。`
    }
  };
}

function text(value) {
  return String(value || '').trim();
}
