# 官方接口核对记录

核对时间：2026-07-02

本项目第一版按 Mercado Libre 官方开发者文档实现，避免沿用圆佑接口或旧 DLL 假设。

## OAuth

官方文档：<https://developers.mercadolibre.com.ar/es_ar/autenticacion-y-autorizacion>

- 授权 URL：`https://auth.mercadolibre.com.{site}/authorization`
- 换 token：`POST https://api.mercadolibre.com/oauth/token`
- token 参数通过 body 传递，不放 query。
- 支持 PKCE 时，授权链接携带 `code_challenge`，换 token 携带 `code_verifier`。
- `/users/me` 用于验证授权账号。

## 传统活动 DEAL

官方文档：<https://developers.mercadolibre.com.ar/productos-recibe-notificaciones/deals>

当前官方页面展示的 seller promotions 路径为：

- 活动详情：`GET /seller-promotions/promotions/{promotion_id}?promotion_type=DEAL&app_version=v2`
- 活动商品：`GET /seller-promotions/promotions/{promotion_id}/items`
- 报名商品：`POST /seller-promotions/items/{item_id}?app_version=v2`
- 更新商品：`PUT /seller-promotions/items/{item_id}?app_version=v2`
- 取消商品：`DELETE /seller-promotions/items/{item_id}?promotion_type=...&promotion_id=...&app_version=v2`
- DEAL / SELLER_CAMPAIGN 报名和更新 payload 使用 `promotion_id`、`promotion_type`、`deal_price`；如官方 candidate 明细提供 `top_deal_price`，预览 payload 可带该可选字段，缺失时不强行传。

规划文件里出现过 `/marketplace/seller-promotions/...` 和 `version: v2` 的旧口径；第一版程序默认使用官方当前文档的 `/seller-promotions/...` 与 `app_version=v2`。

## CBT marketplace 验证

已通过 standalone token 做过只读验证：

- `/users/me`：CBT 父账号可读取。
- `/marketplace/users/{merchant_id}`：可读取 CBT 子 marketplace users。
- `/marketplace/seller-promotions/users/{child_user_id}`：可读取 MLB/MLM 活动。
- `/marketplace/seller-promotions/promotions/{promotion_id}/items`：可读取子账号活动商品。

## 未验证项

- 真实报名、更新、取消
- 不同站点、不同活动类型的字段差异
- SMART 专属正式提交文档仍未确认；LIGHTNING 报名 body 已确认但真实写入未执行

## SMART / LIGHTNING 专项核对（更新至 2026-07-03）

本阶段只做官方资料核对、真实只读样本和本地预检包，不执行 Mercado Libre 写接口。

### 官方资料访问状态

- `https://developers.mercadolibre.com.ar/productos-recibe-notificaciones/deals`：本机 Node `fetch` 返回 HTTP 403；搜索索引片段可见传统活动文档含 `/seller-promotions/promotions/.../items` 与 `deal_price` 示例，但当前无法完整回读页面。
- `https://developers.mercadolibre.com.ar/es_ar/guia-para-producto/pix`：本机 Node `fetch` 返回 HTTP 403；搜索索引片段可见共同出资/PIX 页面含 `offer_id`、`meli_percentage`、`seller_percentage` 和 `/seller-promotions/items/$ITEM_ID?app_version=v2`，但当前无法完整回读请求体、更新、取消规则。
- `https://developers.mercadolibre.com.bo/es_ar/category/pix`：本机 Node `fetch` 返回 HTTP 403；同上，仅能作为官方页面存在和字段线索，不能作为放行依据。
- `https://global-selling.mercadolibre.com/learning-center/news/get-to-know-your-promotion-central-and-offer-discounts`：HTTP 200，可确认业务层存在 Lightning deal、Shared deal 等活动类型，但该页面不是 API 写入参数文档。

主管线程 2026-07-03 重新读取官方文档后确认：

- LIGHTNING 官方写入 body 已确认：`deal_id`、`deal_price`、`original_price`、`promotion_type=LIGHTNING`、`stock`。
- LIGHTNING 取消路径已确认：`DELETE /marketplace/seller-promotions/items/{ITEM_ID}?user_id={USER_ID}&promotion_type=LIGHTNING&promotion_id={DEAL_ID}`，headers 包含 `version:v2`、`X-Caller-Id`、`X-Client-Id`。
- PIX/BANK 官方 offer_id body 已确认：`promotion_id`、`promotion_type=BANK`、`offer_id`。该证据支持 SMART offer_id 实验 adapter，但不是 SMART 专属正式文档。

结论：LIGHTNING 可以生成官方 body 的 preview payload，但真实报名/取消仍属于外部写入，本轮不得执行。SMART 仍仅允许单商品 limited real test 级别的 offer_id 实验 preview，不能批量放行。

### 真实只读字段样本

SMART `P-MLB17755282` candidate 小样本：

- 平台返回：`total=6`，`detail_status=ok`。
- 典型 raw 字段：`id`、`status`、`currency_id`、`offer_id`、`seller_percentage`、`meli_percentage`、`price`、`original_price`、`net_proceeds`、`start_date`、`end_date`。
- 示例字段形态：`offer_id=CANDIDATE-...`，`seller_percentage=10.8~11.5`，`meli_percentage=1.2~1.9`，含活动开始/结束时间。

LIGHTNING `LGH-MLM1000` candidate 小样本：

- 平台返回：`total=11`，`detail_status=ok`。
- 典型 raw 字段：`id`、`status`、`currency_id`、`stock`、`min_discounted_price`、`price`、`original_price`、`net_proceeds`。
- 示例字段形态：`stock={min,max}`，含 `min_discounted_price`、`price`、`original_price`，未见 `offer_id`、`seller_percentage`、`meli_percentage`。

### 当前程序处理

- `SELLER_CAMPAIGN` / `DEAL`：继续只生成 preview payload，真实写入仍由主管确认门拦截。
- `SMART`：识别并展示 `offer_id`、`seller_percentage`、`meli_percentage`、`start_date`、`end_date`、`price`、`original_price`；当 planned row 有 `offer_id` 时生成实验 preview payload `{ promotion_id, promotion_type: 'SMART', offer_id }`，标记 `preview_only=true`、`requires_limited_real_test=true`、`can_submit=false`。缺 `offer_id` 时状态为 `adapter_fields_incomplete`。
- `LIGHTNING`：识别并展示 `stock`、`min_discounted_price`、`price`、`original_price`；当 planned row 字段齐全时生成 preview payload `{ deal_id, deal_price, original_price, promotion_type: 'LIGHTNING', stock }`，其中 `stock` 默认取 candidate `stock.min`。标记 `preview_only=true`、`requires_limited_real_test=true`、`can_submit=false`，不得批量真实放行。缺 `stock.min`、`deal_price`、`price/min_discounted_price` 或 `original_price` 时阻断。

## Marketplace 自建活动 candidate 稀疏分页（2026-07-03）

真实测试线程确认：

- `SELLER_CAMPAIGN / FLEXIBLE_PERCENTAGE` 的 CBT marketplace child candidate 分页必须优先使用 `searchAfter`；`offset` 会重复第一页。
- `limit` 最大有效值为 50；100/200 会返回 400。
- 部分 `searchAfter` 页会返回 HTTP 200 且 `results=null` 或空数组，但仍给出新的 `searchAfter`；继续传下一页 token 后可能拿到后续候选。
- `C-MLM1209743` 可穿越空页读取到约 412+ 个唯一 candidate，但仍小于平台 total 1566。
- `C-MLB4605191` 首页 `results=null`，继续 `searchAfter` 后可读取到约 38 个唯一 candidate，但仍小于平台 total。
- `status=candidates`、`eligible` 等近似参数会返回 started 商品，不能作为 candidate fallback。

当前程序处理：

- candidate marketplace 分页遇到空页但有新 `searchAfter` 时继续读取，记录 `pages_read`、`empty_page_count`、`consecutive_empty_pages`、`unique_count`、`duplicate_count`、`last_search_after` 和 `stop_reason`。
- 如果最终 `saved_count < platform_total`，且已读取到一部分真实 candidate，状态标记为 `partial_api_sparse_marketplace_candidate`。
- `partial_api_sparse_marketplace_candidate` 可以做“可读候选子集”的 dry-run/样本预览，但不能冒充全量。
- 用户要求“全部报活动”时，全量报名预检必须阻断，并提示平台剩余候选未返回明细；只能等待官方修复、联系 Mercado 支持，或后续通过安全的人工导入/只读补明细方案单独确认可读范围。
- 如果完全读不到明细，仍标记为 `api_incomplete_marketplace_candidate`，并继续禁止近似 status fallback。

## SMART 单商品真实验证准备（2026-07-02）

本阶段只准备一次性放行机制和最终确认包，不执行 Mercado Libre 写接口。

目标候选：

- account_id：`2651442567`
- nickname：`CNHUBEISHENGRUIHESHANGM`
- site_id：`MLB`
- child_user_id：`2668031897`
- promotion_id：`P-MLB17755282`
- promotion_type：`SMART`
- action/status：`enroll` / `candidate`
- item_id：`MLB6729392606`
- offer_id：`CANDIDATE-MLB6729392606-76453189919`
- price/original_price：`19.62 USD` / `21.76 USD`
- seller_percentage / meli_percentage：`8.9` / `1`
- writeConcurrency：`1`

准备好的 payload preview：

```json
{
  "promotion_id": "P-MLB17755282",
  "promotion_type": "SMART",
  "offer_id": "CANDIDATE-MLB6729392606-76453189919"
}
```

保护机制：

- 需要 `confirmText=REAL_SUBMIT`。
- 需要后续主管单独下派 `supervisorReleaseCode`。
- request 中 account/site/child/promotion/item/offer/action/status/price/percentage/writeConcurrency 必须全部匹配上述目标。
- 本轮 `release_policy.enabled=false`，即使字段匹配、甚至传入 release code，也只返回最终确认包，不执行真实报名。
- 不支持 SMART update/cancel 真实执行，不支持批量 SMART 真实执行。

回查计划：

- 真实执行后只读 GET 同活动 candidate/pending/started。
- 确认 `MLB6729392606` 是否从 candidate 变化到 pending 或 started，或是否仍 candidate 且返回错误。
- 记录 Mercado 原始响应和回查结果，不输出 token。
- 失败只记录错误，不自动重试；成功后如需撤销，必须另行生成 cancel 确认包，不自动取消。

## Seller Campaign 创建活动 API 状态（更新至 2026-07-03）

本阶段只做官方资料口径落地和本地预检包，不执行任何外部写入。

官方文档 URL：

- `https://global-selling.mercadolibre.com/devsite/seller-campaign`

主管线程已重新读取官方文档并确认：

- 创建路径：`POST /marketplace/seller-promotions/seller-campaign/{USER_ID}`。
- Header：`version:v2`。
- Body：`promotion_type=SELLER_CAMPAIGN`、`name`、`sub_type=FLEXIBLE_PERCENTAGE`、`start_date`、`finish_date`。
- 结束日期：按官网日历口径限制在开始日期所在月份内，例如 2026-07-08 开始时最大为 2026-07-31。

当前程序处理：

- 支持填写站点、`child_user_id`、活动名称、`start_date`、`finish_date`、`sub_type`，生成 request preview 和 409 主管确认包。
- 本地预检检查必填项、ISO 时间格式、结束时间晚于开始时间、结束日期不超过开始月份最后一天。
- 真实创建活动属于外部写入；本轮即使参数完整也不执行 Mercado POST，只返回预检包。

## 固定 4 商品真实报名冒烟准备（2026-07-03）

本阶段只准备执行器和确认包，不执行任何 Mercado Libre 写接口。

固定目标：

- DEAL：`P-MLB17489058 / MLB4685849149`，body preview `{ promotion_id:'P-MLB17489058', promotion_type:'DEAL', deal_price:12.30 }`。
- SELLER_CAMPAIGN：`C-MLM1209743 / MLM3061896345`，body preview `{ promotion_id:'C-MLM1209743', promotion_type:'SELLER_CAMPAIGN', deal_price:11.31 }`。
- LIGHTNING：`LGH-MLM1000 / MLM2942567755`，body preview `{ deal_id:'LGH-MLM1000', deal_price:144.45, original_price:168.93, promotion_type:'LIGHTNING', stock:5 }`。
- SMART：`P-MLB17755282 / MLB6729392606`，body preview `{ promotion_id:'P-MLB17755282', promotion_type:'SMART', offer_id:'CANDIDATE-MLB6729392606-76453189919' }`；默认 `blocked_by_policy`，除非后续主管特别下派 SMART_RELEASE。

接口：

- `GET /api/real-enroll-smoke/target`：返回固定候选包、payload preview、回查计划和 `enabled=false`。
- `POST /api/real-enroll-smoke/precheck`：返回 409 confirmation package，不执行。
- `POST /api/real-enroll-smoke/execute`：默认返回 409 disabled，不执行；后续若要真实冒烟，必须由主管下派一次性 release 机制。

回查计划：每个 item 执行后只读 GET 对应 promotion 的 `candidate/pending/started`，核对 item 是否从 `candidate` 转到 `pending` 或 `started`；失败只记录，不自动重试，不自动取消。

## 并发设置边界（2026-07-02）

- `readConcurrency`：只读活动/商品拉取并发，压测档位覆盖 1、2、3、4、5、8、10、15、20；日常提交优先使用上次只读压测建议。
- `previewConcurrency`：活动任务/本地计算并发，高级保护参数；涉及外部只读请求时仍受只读压测建议和读取并发限制。
- `writeConcurrency`：真实报名、更新、取消的写入并发。界面应区分当前设置值、已验证稳定档和日常建议；当前真实测试线程回传 350 两次稳定，日常建议保守 300-320，仍不得称为 Mercado 平台最大值。
- 任何本地并发数字都不是 Mercado 官方限制，也不是平台最大值。真实写入可用并发需要从小样本开始验证，遇到 429、超时或失败增多时应降低。

当前产品口径：PySide 桌面主流程由用户点击“提交执行”并确认后进入持久 submission/execution group；WinForms 仅作为 Legacy 回退保留。程序验证不主动触发 Mercado 写接口。`writeConcurrency` 只表示真实执行分支使用的本地并发设置，不代表 Mercado 平台承诺或实测极限。

## 全量报名口径修正（2026-07-02）

- `maxItemsPerPromotion` 仅作为测试/预览的样本读取上限，默认 50；不能再把样本预览显示成“全部报活动”。
- 全量报名预检必须先完成 candidate 全量读取。预检包会展示每个活动的 `platform_total`、`saved_count`、`is_full_fetch` 和 `sample_only`。
- 如果 `platform_total > saved_count` 且请求全量报名预检，接口必须阻断并提示先执行“全量读取候选”。
- 新增后台任务式全量读取接口，避免大批量 candidate 分页读取占用单个同步请求；任务只做只读 GET 和本地保存，不执行 Mercado 写接口。
