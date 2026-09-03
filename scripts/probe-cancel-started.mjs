// 只读诊断：读取平台上某个活动当前的 started 列表数量，确认取消是否生效。
// 不调用任何 Mercado 写接口。
import { DatabaseSync } from 'node:sqlite';

const args = new Map();
for (let index = 2; index < process.argv.length; index += 1) {
  const key = process.argv[index];
  if (!key.startsWith('--')) continue;
  const next = process.argv[index + 1];
  if (next && !next.startsWith('--')) { args.set(key, next); index += 1; }
  else args.set(key, true);
}
const dataDir = args.get('--data-dir');
const accountId = args.get('--account-id');
const promotionId = args.get('--promotion-id');
const promotionType = args.get('--promotion-type') || 'SELLER_CAMPAIGN';
if (!dataDir || !accountId || !promotionId) {
  throw new Error('required: --data-dir --account-id --promotion-id [--promotion-type]');
}

// 必须在 import 项目模块之前设置，否则 config.js 会用默认 DATA_DIR/KEY_PATH。
process.env.MDM_DATA_DIR = dataDir;
process.env.MDM_KEY_PATH = `${dataDir}/local.key`;
const { decryptSecret } = await import('../src/security.js');
const { MercadoLibreClient } = await import('../src/mlClient.js');

const db = new DatabaseSync(`${dataDir}/discount-manager.sqlite`, { readOnly: true });
const tokenRow = db.prepare('SELECT * FROM oauth_tokens WHERE account_id = ?').get(accountId);
const campaignRow = db.prepare(
  `SELECT child_user_id, site_id FROM promo_campaigns WHERE account_id=? AND promotion_id=? AND promotion_type=? LIMIT 1`
).get(accountId, promotionId, promotionType.toUpperCase());
db.close();
if (!tokenRow) throw new Error(`no token for account ${accountId}`);
if (!campaignRow) throw new Error(`no campaign row for ${accountId}/${promotionId}/${promotionType}`);

const accessToken = decryptSecret(tokenRow.access_token_cipher);
const childUserId = String(campaignRow.child_user_id || '');
const siteId = String(campaignRow.site_id || '');
const marketplace = Boolean(childUserId && childUserId !== String(accountId));

const client = new MercadoLibreClient({
  accessToken,
  userId: childUserId || accountId,
  callerId: accountId,
  marketplace,
});
const result = await client.fetchAllPromotionItems({
  promotionId,
  promotionType: promotionType.toUpperCase(),
  status: 'started',
  maxItems: 'all',
});
const items = Array.isArray(result?.results) ? result.results : [];
console.log(JSON.stringify({
  account_id: accountId,
  promotion_id: promotionId,
  promotion_type: promotionType.toUpperCase(),
  child_user_id: childUserId,
  site_id: siteId,
  marketplace,
  platform_started_count: items.length,
  detail_status: result?.detailStatus || result?.detail_status || null,
  is_full_fetch: result?.isFullFetch ?? result?.is_full_fetch ?? null,
  sample_only: result?.sampleOnly ?? result?.sample_only ?? null,
  first_item_ids: items.slice(0, 5).map((item) => item?.item_id || item?.id),
}, null, 2));
