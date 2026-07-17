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
