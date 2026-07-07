export function queryFiltersFromSearchParams(searchParams) {
  return {
    siteId: searchParams.get('siteId') || '',
    siteIds: multiValues(searchParams, ['siteIds', 'siteId']),
    promotionType: searchParams.get('promotionType') || '',
    promotionTypes: multiValues(searchParams, ['promotionTypes', 'promotionType']).map((value) => value.toUpperCase()),
    status: searchParams.get('status') || '',
    name: searchParams.get('name') || '',
    keywords: multiValues(searchParams, ['keywords', 'name']),
    sellerActivityNames: multiValues(searchParams, ['sellerActivityNames', 'sellerActivityName']),
    officialActivityNames: multiValues(searchParams, ['officialActivityNames', 'officialActivityName']),
    excludeSeller: booleanValue(searchParams.get('excludeSeller')),
    excludeOfficial: booleanValue(searchParams.get('excludeOfficial'))
  };
}

export function multiValues(searchParams, keys) {
  const values = [];
  for (const key of keys) {
    for (const value of searchParams.getAll(key)) {
      values.push(...String(value || '').split(','));
    }
  }
  return values.map((value) => value.trim()).filter(Boolean);
}

export function booleanValue(value) {
  const text = String(value || '').trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(text)) return true;
  if (['0', 'false', 'no', 'off', ''].includes(text)) return false;
  return false;
}
