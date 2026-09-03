import crypto from 'node:crypto';

const SNAPSHOT_FIELDS = [
  'price',
  'original_price',
  'currency_id',
  'available_quantity',
  'dimensions',
  'weight',
  'status',
];

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function textOrNull(value) {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  return text || null;
}

function resourceRevision(value = {}) {
  return textOrNull(value.last_updated)
    ?? textOrNull(value.date_modified)
    ?? textOrNull(value.updated_at)
    ?? textOrNull(value.lastUpdated);
}

function resourceRevisionMs(value = {}) {
  const parsed = Date.parse(String(resourceRevision(value) || ''));
  return Number.isFinite(parsed) ? parsed : null;
}

function newerResource(current, candidate) {
  if (!current) return candidate;
  if (!candidate) return current;
  const currentRevision = resourceRevisionMs(current);
  const candidateRevision = resourceRevisionMs(candidate);
  if (currentRevision !== null && candidateRevision !== null) {
    return candidateRevision >= currentRevision ? candidate : current;
  }
  if (candidateRevision !== null) return candidate;
  if (currentRevision !== null) return current;
  // Without a comparable platform revision, prefer the last successful GET.
  return candidate;
}

export function itemResourceRevisionIsBehindEvent(resource = {}, eventReceivedAt = '') {
  const revisionMs = Date.parse(String(resourceRevision(resource) || ''));
  const eventMs = Date.parse(String(eventReceivedAt || ''));
  return Number.isFinite(revisionMs) && Number.isFinite(eventMs) && revisionMs < eventMs;
}

export async function readItemResourceAfterEvent({
  read,
  eventReceivedAt = '',
  retryDelaysMs = [2000, 5000, 10000],
  sleepFn = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  if (typeof read !== 'function') throw new TypeError('item resource reader is required');
  let resource = await read();
  let attempts = 1;
  let retryError = null;
  for (const rawDelay of retryDelaysMs || []) {
    if (!itemResourceRevisionIsBehindEvent(resource, eventReceivedAt)) break;
    const delayMs = Math.max(0, Number(rawDelay) || 0);
    if (delayMs > 0) await sleepFn(delayMs);
    try {
      const candidate = await read();
      attempts += 1;
      resource = newerResource(resource, candidate);
    } catch (error) {
      retryError = error;
      break;
    }
  }
  return {
    resource,
    attempts,
    // A notification timestamp is not a resource revision. Mercado documents
    // the notification as a signal to GET and compare the resource, so a
    // successful bounded read is usable even when last_updated precedes the
    // notification delivery time. The comparison remains a retry hint only.
    fresh: true,
    timestamp_hint_behind: itemResourceRevisionIsBehindEvent(resource, eventReceivedAt),
    retry_error: retryError ? String(retryError?.code || retryError?.message || 'resource_retry_failed') : null,
    source_revision: resourceRevision(resource),
  };
}

function attributeMap(resource = {}) {
  const result = new Map();
  for (const attribute of Array.isArray(resource.attributes) ? resource.attributes : []) {
    const id = String(attribute?.id || attribute?.name || '').trim().toUpperCase();
    if (!id) continue;
    const value = attribute?.value_struct
      ? { number: numberOrNull(attribute.value_struct.number), unit: textOrNull(attribute.value_struct.unit) }
      : textOrNull(attribute?.value_name ?? attribute?.value_id ?? attribute?.value);
    if (value !== null) result.set(id, value);
  }
  return result;
}

function firstAttribute(attributes, names) {
  for (const name of names) {
    if (attributes.has(name)) return attributes.get(name);
  }
  return null;
}

function normalizeDimensions(resource = {}, attributes = attributeMap(resource)) {
  const shipping = resource.shipping && typeof resource.shipping === 'object' ? resource.shipping : {};
  const dimensions = resource.dimensions && typeof resource.dimensions === 'object' ? resource.dimensions : {};
  const normalized = {
    height: dimensions.height ?? shipping.height ?? firstAttribute(attributes, ['PACKAGE_HEIGHT', 'HEIGHT']),
    width: dimensions.width ?? shipping.width ?? firstAttribute(attributes, ['PACKAGE_WIDTH', 'WIDTH']),
    length: dimensions.length ?? shipping.length ?? firstAttribute(attributes, ['PACKAGE_LENGTH', 'LENGTH', 'DEPTH']),
  };
  const compact = Object.fromEntries(Object.entries(normalized).filter(([, value]) => value !== null && value !== undefined && value !== ''));
  if (Object.keys(compact).length) return compact;
  return textOrNull(shipping.dimensions ?? resource.package_dimensions ?? resource.shipping_dimensions);
}

function normalizeWeight(resource = {}, attributes = attributeMap(resource)) {
  const shipping = resource.shipping && typeof resource.shipping === 'object' ? resource.shipping : {};
  return shipping.weight
    ?? resource.weight
    ?? resource.package_weight
    ?? firstAttribute(attributes, ['PACKAGE_WEIGHT', 'WEIGHT']);
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableValue(value[key])]));
}

function stableJson(value) {
  return JSON.stringify(stableValue(value));
}

export function normalizeItemSnapshot(resource = {}, options = {}) {
  const attributes = attributeMap(resource);
  const status = resource?.status && typeof resource.status === 'object'
    ? resource.status.id ?? resource.status.status
    : resource?.status;
  const observedAt = textOrNull(options.observedAt) || new Date().toISOString();
  const sourceRevision = textOrNull(options.sourceRevision)
    ?? resourceRevision(resource);
  const snapshot = {
    item_id: textOrNull(resource.id ?? resource.item_id),
    price: numberOrNull(resource.price),
    original_price: numberOrNull(resource.original_price),
    currency_id: textOrNull(resource.currency_id ?? resource.currency?.id),
    available_quantity: numberOrNull(resource.available_quantity ?? resource.stock ?? resource.inventory),
    dimensions: normalizeDimensions(resource, attributes),
    weight: normalizeWeight(resource, attributes),
    status: textOrNull(status)?.toLowerCase() || null,
    source_revision: sourceRevision,
    observed_at: observedAt,
    confirmed: options.confirmed !== false,
  };
  snapshot.snapshot_hash = crypto.createHash('sha256')
    .update(stableJson(Object.fromEntries(SNAPSHOT_FIELDS.map((field) => [field, snapshot[field]]))), 'utf8')
    .digest('hex');
  return snapshot;
}

export function snapshotFromCacheRow(row = {}) {
  let dimensions = null;
  let weight = null;
  try { dimensions = row.dimensions_json ? JSON.parse(row.dimensions_json) : null; } catch { dimensions = null; }
  try { weight = row.weight_json ? JSON.parse(row.weight_json) : row.weight ?? null; } catch { weight = row.weight ?? null; }
  return normalizeItemSnapshot({
    id: row.item_id,
    price: row.price,
    original_price: row.original_price,
    currency_id: row.currency_id,
    available_quantity: row.available_quantity,
    dimensions,
    weight,
    status: row.status,
    last_updated: row.source_revision,
  }, {
    observedAt: row.observed_at || row.updated_at,
    sourceRevision: row.source_revision,
    confirmed: Number(row.confirmed ?? 1) === 1,
  });
}

export function compareItemSnapshots(previous, next) {
  if (!previous) return { changed: true, changed_fields: [...SNAPSHOT_FIELDS] };
  const changedFields = SNAPSHOT_FIELDS.filter((field) => stableJson(previous[field] ?? null) !== stableJson(next[field] ?? null));
  return { changed: changedFields.length > 0, changed_fields: changedFields };
}

export function snapshotIsOlder(previous, next) {
  if (!previous || !next) return false;
  const previousRevision = Date.parse(String(previous.source_revision || ''));
  const nextRevision = Date.parse(String(next.source_revision || ''));
  if (Number.isFinite(previousRevision) && Number.isFinite(nextRevision)) return nextRevision < previousRevision;
  // A platform revision is stronger than local event arrival order. Historical
  // webhook replay may fetch a current resource for an old event, so do not
  // reject a revisioned resource merely because received_at is older.
  if (!Number.isFinite(previousRevision) && Number.isFinite(nextRevision)) return false;
  const previousObserved = Date.parse(String(previous.observed_at || ''));
  const nextObserved = Date.parse(String(next.observed_at || ''));
  return Number.isFinite(previousObserved) && Number.isFinite(nextObserved) && nextObserved < previousObserved;
}

export function revalidatePlannedRow({ row, latestSnapshot, action, priceMode, discountPercent, directPrice, promotionType, calculateDealPrice, validateDealPrice }) {
  if (!row || !latestSnapshot || !['enroll', 'update'].includes(String(action || '').toLowerCase())) {
    return { row, changed: false, changed_fields: [] };
  }
  if (latestSnapshot.confirmed === false) {
    const error = new Error('商品最新数据尚未确认，已阻断该商品提交。');
    error.code = 'ITEM_SNAPSHOT_UNCONFIRMED';
    error.policyBlocked = true;
    throw error;
  }
  const currentHash = String(row.item?.snapshot_hash || '');
  const latestHash = String(latestSnapshot.snapshot_hash || '');
  const snapshotChanged = !currentHash || !latestHash || currentHash !== latestHash;
  if (!snapshotChanged) return { row, changed: false, changed_fields: [] };
  const item = {
    ...(row.item || {}),
    price: latestSnapshot.price ?? row.item?.price ?? null,
    original_price: latestSnapshot.original_price ?? latestSnapshot.price ?? row.item?.original_price ?? row.item?.price ?? null,
    currency_id: latestSnapshot.currency_id ?? row.item?.currency_id ?? null,
    status: row.item?.status,
    snapshot_hash: latestHash || currentHash,
    snapshot_observed_at: latestSnapshot.observed_at || null,
  };
  const dealPrice = calculateDealPrice(item, { priceMode, discountPercent, directPrice });
  const priceError = validateDealPrice(item, dealPrice, promotionType);
  if (priceError) {
    const error = new Error(`商品数据已变化，重新计算后不可提交：${priceError}`);
    error.code = 'ITEM_SNAPSHOT_PRICE_INVALID';
    error.policyBlocked = true;
    throw error;
  }
  return {
    changed: true,
    changed_fields: latestSnapshot.change_flags || [],
    row: { ...row, item, deal_price: dealPrice, reason: '写入前已按最新商品快照重算' },
  };
}
