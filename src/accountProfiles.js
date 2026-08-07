export function isSyntheticAccountName(value, accountId = '') {
  const text = String(value || '').trim();
  const id = String(accountId || '').trim();
  if (!text) return true;
  if (/^standalone(?:\s|$)/i.test(text)) return true;
  if (/^账号\s*\d+$/i.test(text)) return true;
  return Boolean(id) && text === id;
}

export function accountProfileDisplayName({
  accountId,
  cachedDisplayName,
  storedDisplayName,
  standaloneNickname
} = {}) {
  const id = String(accountId || '').trim();
  for (const candidate of [cachedDisplayName, storedDisplayName, standaloneNickname]) {
    const text = String(candidate || '').trim();
    if (text && !isSyntheticAccountName(text, id)) return text;
  }
  return `本地授权账号 ${id.slice(-4) || '未知'}`;
}

export function accountProfileRecord({ accountId, provider, profile, source = 'users_me', fetchedAt } = {}) {
  const id = String(accountId || profile?.id || '').trim();
  const displayName = String(profile?.nickname || profile?.display_name || profile?.first_name || '').trim();
  if (!id || !displayName) return null;
  return {
    account_id: id,
    provider: String(provider || 'mercadolibre'),
    display_name: displayName,
    site_id: String(profile?.site_id || '').trim() || null,
    fetched_at: String(fetchedAt || new Date().toISOString()),
    source: String(source || 'users_me')
  };
}

const ACCOUNT_IDENTITY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;

function normalizeOAuthIdentity(value) {
  const normalized = String(value ?? '').trim();
  return ACCOUNT_IDENTITY_PATTERN.test(normalized) ? normalized : '';
}

function accountIdentityMismatchError() {
  const error = new Error('OAuth 账号身份校验失败，授权未保存。');
  error.code = 'ACCOUNT_IDENTITY_MISMATCH';
  error.status = 422;
  return error;
}

export function requireOAuthTargetAccountId(value) {
  const target = normalizeOAuthIdentity(value);
  if (!target) throw accountIdentityMismatchError();
  return target;
}

export function assertOAuthIdentityMatch({ targetAccountId, profileId, tokenUserId } = {}) {
  const target = normalizeOAuthIdentity(targetAccountId);
  const profile = normalizeOAuthIdentity(profileId);
  const token = normalizeOAuthIdentity(tokenUserId);
  if (!target || !profile || !token || target !== profile || target !== token) {
    throw accountIdentityMismatchError();
  }
  return target;
}
