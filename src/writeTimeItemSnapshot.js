function itemSnapshotError(message, code, cause = null) {
  const error = new Error(message);
  error.code = code;
  error.policyBlocked = true;
  if (cause) error.cause = cause;
  return error;
}

function itemIdentityKey({ accountId, childUserId, siteId, itemId }) {
  return [accountId, childUserId, String(siteId || '').toUpperCase(), itemId].map(String).join('|');
}

export function createWriteTimeItemRevalidator({
  accountId,
  campaign = {},
  action,
  plan = {},
  getSnapshot,
  readItem,
  persistSnapshot,
  revalidate,
  calculateDealPrice,
  validateDealPrice,
  readCache = new Map(),
} = {}) {
  return async (row) => {
    if (!['enroll', 'update'].includes(String(action || '').toLowerCase())) {
      return { row, changed: false, changed_fields: [] };
    }
    const itemId = String(row?.item?.item_id || '').trim();
    if (!itemId) return { row, changed: false, changed_fields: [] };
    const identity = {
      accountId: String(accountId || ''),
      childUserId: String(campaign?.child_user_id || ''),
      siteId: String(campaign?.site_id || '').toUpperCase(),
      itemId,
    };
    let latestSnapshot = getSnapshot?.(identity) || null;
    if (!latestSnapshot || latestSnapshot.confirmed === false) {
      if (typeof readItem !== 'function' || typeof persistSnapshot !== 'function') {
        throw itemSnapshotError('商品最新数据缺失，无法在提交前核对价格，已跳过该商品。', 'ITEM_SNAPSHOT_REQUIRED');
      }
      const key = itemIdentityKey(identity);
      if (!readCache.has(key)) {
        readCache.set(key, (async () => {
          const resource = await readItem(itemId, identity);
          const returnedId = String(resource?.id || resource?.item_id || '').trim();
          if (returnedId && returnedId !== itemId) {
            throw itemSnapshotError('平台返回的商品身份与目标不一致，已跳过该商品。', 'ITEM_SNAPSHOT_IDENTITY_MISMATCH');
          }
          await persistSnapshot({ ...identity, resource, confirmed: true });
          const persisted = getSnapshot?.(identity) || null;
          if (!persisted || persisted.confirmed === false) {
            throw itemSnapshotError('商品最新数据未能形成可确认快照，已跳过该商品。', 'ITEM_SNAPSHOT_NOT_CONFIRMED');
          }
          return persisted;
        })().catch((cause) => {
          if (cause?.policyBlocked) throw cause;
          throw itemSnapshotError('商品最新数据读取失败，未发送活动写入，已跳过该商品。', 'ITEM_SNAPSHOT_REFRESH_FAILED', cause);
        }));
      }
      latestSnapshot = await readCache.get(key);
    }
    return revalidate({
      row,
      latestSnapshot,
      action,
      priceMode: plan?.priceMode || 'discount',
      discountPercent: plan?.discountPercent,
      directPrice: plan?.directPrice,
      promotionType: campaign?.promotion_type,
      calculateDealPrice,
      validateDealPrice,
    });
  };
}
