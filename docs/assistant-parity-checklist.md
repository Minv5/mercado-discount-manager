# 美客多折扣助手功能对照清单

更新时间：2026-07-02

目标：新直连版对标旧“美客多折扣助手”的日常操作能力，但底层只走 Mercado Libre 官方 API 和本项目本地数据库，不调用圆佑 DLL、圆佑 GUI、圆佑接口或圆佑任务队列。

| 旧助手功能 | 新直连等价实现 | 当前状态 | 缺口/边界 | 验证方式 |
| --- | --- | --- | --- | --- |
| 设置桥程序 exe、圆佑 DLL 目录、圆佑账号文件、输出目录 | 设置 Mercado standalone 授权目录、输出目录、默认自建/官方折扣、取消复查最大轮数、样本读取上限、只读并发、预检并发、真实写入并发、默认筛选 | 已补齐 | 圆佑路径不作为新功能保留；token/secret 不写入设置；样本读取上限不等于全量报名上限；真实写入并发由用户点击提交执行并确认后生效 | `/api/settings` 读取/保存，页面设置区 |
| 判断今日 | `/api/today/decision` 基于 cycle state、活动和已拉商品状态判断 enroll/update/cancel/已完成/需补跑 | 已补齐 | 需要先有活动和商品只读数据；缺数据时提示先预览读取 | 自动测试 + 只读验证 |
| 日常使用：预览后提交执行 | “预览今日”生成批量 dry-run；“提交执行预检”只返回 409 确认包 | 已补齐 | 真实写入仍需主管一次性放行机制 | `/api/today/preview`、`/api/today/precheck` |
| 周期 5/6 到 10 后取消 | 自建默认 5%，官方默认 6%，完整后递增，10% 且有 started 进入 cancel | 已有并增强 | 真实完成状态只能由未来真实写入回查后更新 | cycle 单元测试 |
| 批量报活动 | candidate + enroll dry-run/预检，按自建/官方折扣，记录跳过原因；全量报名必须先完成 candidate 全量读取 | 已有并增强 | 自建 FLEXIBLE_PERCENTAGE marketplace candidate 可能出现稀疏 `searchAfter` 分页；可读子集标记为 `partial_api_sparse_marketplace_candidate`，不能冒充全量；SMART 仅生成 offer_id 实验 preview，不放行真实提交 | batch plan/precheck |
| 批量更新 | started + update dry-run/预检，当前价等于目标价跳过 | 已有 | 真实 update 未执行 | batch plan/precheck |
| 批量取消 | started + cancel dry-run/预检，预检包包含取消后复查方式 | 已补齐筛选入口 | 真实取消复查仅在未来真实放行后执行；当前只做预检 | 筛选取消预览/预检 |
| 筛选取消 | 按业务店铺、站点、多关键词、活动类型、状态筛选活动；预览取消所选；执行取消所选预检 | 已补齐 | 桌面店铺下拉显示业务店铺名，内部映射到 Mercado account_id/child/site；当前湖北店映射账号 2651442567 | 页面和接口验证 |
| 过滤能力 | 全部店铺/单店铺、全部/单站点、多站点、多关键词、类型 include/exclude、status | 已补齐 | 店铺是业务维度，不直接展示 Mercado 授权账号；账号信息只在日志/状态/设置中辅助展示 | `filterPromotions` 测试 |
| 输出结果 | 输出目录生成 shops/sites、activities、preview tasks、precheck results、history CSV/JSON | 已补齐 | 不输出 token/secret | `/api/export/workspace` |
| 店铺站点列表 | 账号、child_user_id、site_id、logistic_type、活动数展示和导出 | 已有并可导出 | 无 | 站点概览 + 导出 |
| 错误中文化 | 常见 Mercado 错误和本地阻断原因中文展示 | 已有并扩展 | 新错误需持续补充映射 | 单元测试/页面日志 |
| 本地服务根页面 | 仅显示服务状态和桌面授权返回说明 | 已收敛 | 旧浏览器工作台已退役，不提供计划、提交、SMART 或压测入口 | 静态页面测试 |
| 测试并发 | 只读活动/商品读取支持 1、2、3、4、5、8、10、15、20 档位压测；真实写入执行器支持受限并发 | 已补齐机制 | 读取并发使用上次只读压测建议；写入并发展示区分当前设置、已验证稳定档和日常建议。真实测试线程已回传 350 两次稳定，日常建议保守 300-320；这仍不是 Mercado 平台最大值。 | `mapLimited`/只读压测接口 + fake executor 测试 |
| 折扣设置 | 设置默认自建 5%、官方 6%；操作区可单次覆盖自建/官方折扣；价格规则可选择直接活动价 | 已补齐 | 单次输入优先于设置默认值；直接活动价绕过百分比计算但仍做边界检查 | 设置保存、今日预览、dry-run |
| 新建活动 | Seller Campaign 创建预检：填写站点、child_user_id、名称、开始/结束时间、sub_type，生成官方 request preview 和 409 主管确认包 | 已补齐预检 | 真实创建属于外部写入；结束日期按官网日历口径限制在开始日期所在月份内 | `/api/promotion-creation/status` + `/api/promotion-creation/precheck` + 页面预检 |

## 不应补的圆佑依赖

- 不保留桥程序 exe、圆佑 DLL 目录、圆佑账号文件作为主流程设置。
- 不调用旧 `YuanyouDiscountBridge.exe`、`YuanyouDiscountDesktop.exe`、`采集工具.dll`。
- 不读取旧圆佑账号密码；新授权默认来源是 `%USERPROFILE%\Documents\美客多授权` 的 standalone Mercado OAuth token，也可通过 `ML_STANDALONE_AUTH_DIR` 覆盖。

## 当前安全边界

- SMART 有 `offer_id` 时可生成实验 preview payload，但 `can_submit=false`，必须等待主管安排单商品真实验证。
- SMART 单商品真实验证已有执行前准备包，但 release code 本轮未启用；不支持批量 SMART 放行。
- LIGHTNING 官方报名 body 已确认，可生成 preview payload `{deal_id, deal_price, original_price, promotion_type:'LIGHTNING', stock}`；stock 默认取 candidate `stock.min`。真实写入仍需小样本验证和主管最终确认，不能批量放行。
- 平台 `total>0` 但无商品明细的 marketplace candidate 活动进入 `api_incomplete_marketplace_candidate` 阻断，禁止近似 status fallback。
- 平台 `total>saved_count` 且能读取部分 candidate 的 marketplace 稀疏分页活动进入 `partial_api_sparse_marketplace_candidate`：可做可读子集 dry-run，但“全部报活动”预检必须阻断，页面和预检包必须显示平台 total、已读取、空页数和未返回明细数量。
- 所有真实写入接口仍返回主管确认包，不执行 Mercado 写接口。
- 测试时允许并发的是只读拉取和本地计算；真实写入并发也可设置，由用户点击提交执行并确认后在真实执行分支生效。
- 新建 Seller Campaign 当前只做 request preview 和主管确认包；即使参数有效也不执行 API 写入。
