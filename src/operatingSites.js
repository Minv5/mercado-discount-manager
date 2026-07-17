export function normalizeOperatingSites(value) {
  const normalized = {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) return normalized;
  for (const [accountId, siteIds] of Object.entries(value)) {
    const key = String(accountId || '').trim();
    if (!key) continue;
    const values = Array.isArray(siteIds) ? siteIds : String(siteIds || '').split(',');
    normalized[key] = [...new Set(values
      .map((siteId) => String(siteId || '').trim().toUpperCase())
      .filter(Boolean))].sort();
  }
  return normalized;
}

export function hasOperatingSiteScope(settings, accountId) {
  const scopes = settings?.operatingSites;
  return Boolean(scopes && typeof scopes === 'object' && !Array.isArray(scopes)
    && Object.prototype.hasOwnProperty.call(scopes, String(accountId || '').trim()));
}

export function operatingSiteIds(settings, accountId) {
  if (!hasOperatingSiteScope(settings, accountId)) return null;
  return new Set(normalizeOperatingSites(settings.operatingSites)[String(accountId || '').trim()] || []);
}

export function filterByOperatingSites(rows = [], settings = {}, accountId = '') {
  const allowed = operatingSiteIds(settings, accountId);
  if (allowed === null) return [...rows];
  return rows.filter((row) => allowed.has(String(row?.site_id || row?.siteId || '').trim().toUpperCase()));
}

export function mergeOperatingSiteEvidence(rows = [], settings = {}) {
  const grouped = new Map();
  for (const row of rows) {
    const accountId = String(row?.account_id || row?.accountId || '').trim();
    const siteId = String(row?.site_id || row?.siteId || '').trim().toUpperCase();
    if (!accountId || !siteId) continue;
    const key = `${accountId}|${siteId}`;
    const current = grouped.get(key) || {
      account_id: accountId,
      store_name: String(row?.store_name || row?.storeName || ''),
      site_id: siteId,
      site_name: String(row?.site_name || row?.siteName || siteId),
      child_count: 0,
      activity_count: 0,
      active_listing_count: 0,
      active_probe_ok_count: 0,
      active_probe_error_count: 0
    };
    current.child_count += 1;
    current.activity_count += Math.max(0, Number(row?.activity_count ?? row?.total ?? 0) || 0);
    if (row?.active_probe_ok === true) {
      current.active_probe_ok_count += 1;
      current.active_listing_count += Math.max(0, Number(row?.active_listing_count ?? 0) || 0);
    } else if (row?.active_probe_ok === false) {
      current.active_probe_error_count += 1;
    }
    grouped.set(key, current);
  }

  return [...grouped.values()].map((entry) => {
    const configured = hasOperatingSiteScope(settings, entry.account_id);
    const allowed = operatingSiteIds(settings, entry.account_id);
    const activeProbeComplete = entry.active_probe_ok_count > 0 && entry.active_probe_error_count === 0;
    const suggested = activeProbeComplete
      ? entry.active_listing_count > 0
      : entry.active_listing_count > 0 || entry.activity_count > 0;
    return {
      ...entry,
      configured,
      operating: configured ? allowed.has(entry.site_id) : suggested,
      suggested_operating: suggested,
      evidence: entry.active_listing_count > 0
        ? 'active_listings'
        : activeProbeComplete
          ? 'no_active_listings'
          : entry.activity_count > 0
            ? 'activity_cache_only'
            : 'insufficient'
    };
  }).sort((a, b) => a.store_name.localeCompare(b.store_name, 'zh-CN') || a.site_id.localeCompare(b.site_id));
}
