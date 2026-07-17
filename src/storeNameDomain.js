import { accountProfileDisplayName } from './accountProfiles.js';

function clean(value) {
  return String(value || '').trim();
}

const VERIFIED_ALIAS_BY_RAW_DISPLAY_NAME = new Map([
  ['CNHUBEISHENGRUIHESHANGM', '湖北'],
  ['CNGUANGZHOULINGTANGMINB', '广州'],
  ['CNLIUYANGSHIZHEPINGDIAN', '湖南'],
]);
const STALE_REGIONAL_ALIASES = new Set(['湖北店', '广州店', '湖南店', '广东店']);

export function verifiedStoreAliasCorrection({ rawDisplayName, alias } = {}) {
  const current = clean(alias);
  const verified = VERIFIED_ALIAS_BY_RAW_DISPLAY_NAME.get(clean(rawDisplayName).toUpperCase()) || '';
  if (!verified || !STALE_REGIONAL_ALIASES.has(current)) return null;
  return { from: current, to: verified, reason: 'verified_raw_profile' };
}

export function resolveStoreIdentity({
  accountId,
  account = {},
  profile = {},
  storeAliases = {},
  rawDisplayName,
  standaloneNickname,
} = {}) {
  const safeAccount = account && typeof account === 'object' ? account : {};
  const safeProfile = profile && typeof profile === 'object' ? profile : {};
  const id = clean(accountId || safeAccount.account_id || safeProfile.account_id || safeProfile.id);
  const raw = clean(rawDisplayName) || accountProfileDisplayName({
    accountId: id,
    cachedDisplayName: safeProfile.display_name || safeProfile.nickname,
    storedDisplayName: safeAccount.raw_display_name || safeAccount.display_name || safeAccount.nickname,
    standaloneNickname: standaloneNickname || safeAccount.profile?.nickname,
  });
  const alias = clean(storeAliases?.[id]);
  if (alias) {
    const correction = verifiedStoreAliasCorrection({ rawDisplayName: raw, alias });
    if (correction) {
      return { raw_display_name: raw, store_name: correction.to, store_name_source: 'verified_profile_alias_correction' };
    }
    return { raw_display_name: raw, store_name: alias, store_name_source: 'explicit_alias' };
  }
  return { raw_display_name: raw, store_name: '店铺待命名', store_name_source: 'fallback' };
}

export function storeNameForAccount(input = {}) {
  return resolveStoreIdentity(input).store_name;
}
