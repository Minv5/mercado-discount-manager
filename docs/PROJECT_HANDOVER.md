# 美客多活动管家（Mercado Discount Manager）全量项目交接文档

- **当前交接版本**：v2.0.80
- **交接日期**：2026-10-06
- **主技术栈**：原生 PySide6 桌面端（`desktop-pyside/`），纯 Python 3.12 架构
- **质量门禁**：234 项自动化单元测试全量通过（Ran 234 tests, OK）
- **产物交付状态**：已完成本地编译、代码签名与冒烟验证，应用 `~/Desktop/美客多活动管家.app` 与 `/Applications/美客多活动管家.app` 已就绪且正常运行中（PID 已验证）。

---

## 一、项目概况与架构定位

### 1.1 项目定位
本项目为面向 **Mercado Libre（美客多）跨境 CBT（Cross-Border Trade）卖家** 的专业本地桌面运营软件。
程序直接连接美客多官方 REST API，**无任何中间云服务器、第三方转发服务或外部代理**，全量店铺密钥、Access Token、商品缓存与操作记录严格留存于卖家本地机器，保障资金与店铺资产安全。

### 1.2 核心技术栈
- **UI 框架**：PySide6（Qt 6 for Python），全面采用 Dark Mode（暗色主题）与统一高对比度调色，支持高分屏缩放与非阻塞多线程异步响应。
- **运行环境**：Python 3.12 纯本地环境（支持 macOS Apple Silicon arm64 与 Windows 10/11 x64）。
- **本地数据层**：SQLite 3 + WAL（Write-Ahead Logging）预写日志模式，支持高并发读写、断点恢复与进程锁安全。
- **安全加密层**：AES-GCM（AES 256 位对称加密），加密密钥位于 `local.key`，所有 Token 与私钥密文存储。
- **网络与通信**：`requests.Session` 连接池 + HTTP Keep-Alive 复用，配备 22 QPS 令牌桶预约时间片限流保护（RateLimiter），消除并发锁争用。
- **主线程安全分发**：自研 `GuiDispatcher`，后台 Worker 线程事件安全压入队列并在主线程 UI 上下文中执行更新，杜绝跨线程操作 UI 崩溃。

---

## 二、纳管店铺与分站点资产

系统纳管 3 个官方 CBT 全球母账号，各母账号下挂 6 个拉美核心分站点子账号：

### 2.1 纳管店铺主档案
1. **广东店**（`CNGUANGZHOULINGTANGMINB`）
   - 母账号 `account_id`：`3332096437`
   - 活跃 CBT 母体商品池：约 35,400+ 件
2. **湖北店**（`CNHUBEISHENGRUIHESHANGM`）
   - 母账号 `account_id`：`2651442567`
   - 活跃 CBT 母体商品池：约 35,800+ 件
3. **湖南店**（`CNLIUYANGSHIZHEPINGDIAN`）
   - 母账号 `account_id`：`3408885754`
   - 活跃 CBT 母体商品池：约 10,200+ 件

### 2.2 覆盖分站点与币种
- **MLM**（墨西哥 Mexico）：主营主站，计价基准（西语，MXN）
- **MLB**（巴西 Brazil）：核心高流量站，独立语系（葡语，BRL）
- **MCO**（哥伦比亚 Colombia）：西语站点（COP）
- **MLC**（智利 Chile）：西语站点（CLP）
- **MLA**（阿根廷 Argentina）：西语站点（ARS）
- **MLU**（乌拉圭 Uruguay）：小语种补充站点（UYU）

---

## 三、六大核心业务系统与实现原理

### 3.1 模块一：活动报名与价格中枢（Enrollment & Pricing Engine）
- **双定价体系（Dual-pricing Engine）**：支持平台展示售价（Sale Price）与卖家实际净收益（Net Proceeds）分离核算，严密推导利润空间，保障折扣报名不亏本；
- **智能折扣推荐**：自动拉取美客多官方针对单品的 `suggested_discounted_price`（建议折后价）与 `min_discounted_price`（官方底线）；
- **任务执行器（Task Executor）**：支持全店/多站点大促批量报名、断点续传、失败单品自动隔离重试与聚合报告。

### 3.2 模块二：活动管理大厅（Campaign Management）
- **全活动类型支持**：涵盖 `seller_campaign`（卖家自建促销）、`deals`（官方秒杀）、`lightning`（闪购）与 `volume`（满减折扣）；
- **全量活动刷新**：后台多线程获取店铺所有在线与往期活动，一键展示活动状态、起止时间与已报名商品量；
- **安全底线防护**：低于卖家设定亏本红线的商品自动拦截并标黄告警。

### 3.3 模块三：按ID操作活动（Targeted Action by Item ID）
- **单品/批量定向操作**：支持直接输入或粘贴一个或多个 `item_id`（以空格或换行分隔），执行针对性操作：
  - 撤销自建活动
  - 撤销官方活动
  - 刷新商品美客多官方最新价格与活动资格缓存
- **界面与体验重构（v2.0.76+）**：
  - 嵌入式主视图卡片设计，消除嵌套双边框；
  - 控件字体统一定制为 13px 清晰字号，提升录入与校验可读性；
  - 自包含专属运行日志框，与主工作台日志解耦。

### 3.4 模块四：商品风控与清理管家（Item Cleaner & Risk Engine）
针对长期无销量无流量的“僵尸品”白白占用店铺刊登额度的问题，构建了整套扫描、风控、聚类与清理闭环：

1. **扫描与多维度过滤（Multi-channel Filters）**：
   - **违规失效通道**：定向捕捉 `under_review` + `forbidden`（45 天无动销停用品）、`waiting_for_patch`（待整改违规品）；
   - **在售流量诊断通道**：多线程并发检索在售品在近 30/45 天的官方真实访问量（`/visits/time_window`）与 Listing 质量得分（`/health`）；
   - **上架时长门槛独立解耦**：上架天数（如仅处理超过 30 天商品）可作为全局门槛独立生效，不强制捆绑浏览量过滤，联动子控件自动禁用；
   - **清店模式（Wipe Store Mode）**：一键检索店铺全量在售/下架商品并设为待删除候选，支持动态醒目提示横幅。
2. **核心资产防护红线（不可妥协安全机制）**：
   - **出单商品绝对保护**：历史销售量 `sold_quantity > 0` 的单品永远标灰锁定，严禁系统批量删除勾选；暗色主题下呈现专属低饱和暖琥珀深底（`#2d2019`）与珊瑚橙文字（`#ff8a65`），彻底消除刺眼白条；
   - **冷启动新品保护期**：上架未满 7 天（可配置）的商品自动豁免，避免误删正在起量的新品。
3. **跨店跨站点图片指纹聚类（Thumbnail Fingerprint Deduplication）**：
   - 以美客多图床 CDN 资产哈希 `thumbnail_id` 为桥梁完成跨语言、跨店铺款式归一，彻底去重。
4. **交互落地：【复制0浏览ID】**：
   - 自动过滤排除所有违规品与出单保护品，提取纯净的 45 天 0 浏览商品；
   - 弹窗内按 500 个一组（Batch）分行展示，一键复制进剪贴板，无缝对接 ERP 进行批量下架。
5. **表格与删除体验规范**：
   - 批量删除按钮固定显示纯净 **“批量删除”** 四个字，去除动态 `(N)` 冗余后缀，防止频繁拉伸界面；
   - 全局汇总与店铺分项统计公式 $100\%$ 严格闭合，明细透出已出单保护、待清理删除与未勾选商品数。

### 3.5 模块五：开发者应用与设置管理（Settings & Developer Apps）
- **嵌入式主设置视图**：Tab 等宽字距、保存置于右上角、无底部多余残余按键；
- **多应用绑定识别**：自动比对已配置的开发者应用（App ID / Client ID）与店铺授权映射，明确标记“已绑定店铺”或“待登录授权”；
- **店铺别名双向动态同步**：支持为店铺重命名别名（如“广东店” -> “广州旗舰店”），经营站点列表即时联动刷新；
- **经营站点批量勾选**：新增“全选站点”与“全不选站点”便捷操作。

### 3.6 模块六：独立日志分流系统（Dedicated Log Routing System）
将所有业务模块运行日志解耦至各自独立的 `LogViewer` 组件中：
- `enrollment_log_box`：活动报名专属日志
- `activity_log_box`：活动管理专属日志
- `targeted_log_box`：按ID操作专属日志
- `cleaner_log_box`：商品清理专属日志
- `log_box`：主工作台系统级日志
各模块在独立视图内清晰回显，互不交叉污染。

---

## 四、本地数据目录与持久化规范

### 4.1 核心数据路径
- **项目代码库**：`/Users/minv5/Documents/美客多折扣管家/`
- **正式应用数据目录（Local AppData）**：
  - macOS: `~/Library/Application Support/MercadoDiscountManagerStandalone/data/`
  - Windows: `%LOCALAPPDATA%\MercadoDiscountManagerStandalone\data\`

### 4.2 核心数据库表结构（`discount-manager.sqlite`）
| 数据表名 | 用途 | 核心字段 |
| :--- | :--- | :--- |
| `oauth_tokens` | 授权凭据加密表 | `account_id`, `site_id`, `access_token_cipher`, `refresh_token_cipher`, `expires_at` |
| `account_profiles` | 店铺主档案 | `account_id`, `display_name`, `site_id` |
| `marketplace_sites` | 分站点路由映射 | `account_id`, `child_user_id`, `site_id`, `logistic_type` |
| `promo_campaigns` | 活动排期汇总 | `promotion_id`, `promotion_type`, `status`, `start_date`, `finish_date` |
| `promo_items` | 参与活动的单品明细 | `item_id`, `status`, `original_price`, `price`, `suggested_discounted_price` |
| `item_price_cache` | 单品价格与净收益快照 | `item_id`, `price`, `dimensions_json`, `weight_json`, `raw_json` |
| `item_cleaner_info_cache` | 清理管家基础档案快照 | `item_id`, `title`, `sold_quantity`, `status`, `sub_status`, `site_id` |
| `item_cleaner_visits_cache` | 真实流量缓存池 | `item_id`, `visits`, `updated_at` |
| `item_cleaner_score_cache` | 质量评分缓存池 | `item_id`, `score`, `level_wording`, `updated_at` |

### 4.3 扫描草稿机制（`cleaner_draft.json`）与测试物理隔离
- 扫描完成的候选记录自动暂存至本地 `cleaner_draft.json`；
- 软件重启后秒级恢复，支持“断点继续处理”、“即焚清空”与“重新扫描”；
- **防污染双重拦截**：
  - `load_cleaner_draft` 与 `save_cleaner_draft` 强制过滤 `ACC1` / `ITEM1` 等测试桩记录；
  - 全套单元测试（`test_ui.py`, `test_item_cleaner.py`）使用 `tempfile.TemporaryDirectory` 独立重定向 `MDM_DATA_DIR`，保证测试数据绝不污染生产草稿。

---

## 五、开发、测试与构建运维手册

### 5.1 本地开发与启动
```bash
# 进入项目目录并激活虚拟环境
cd /Users/minv5/Documents/美客多折扣管家
source .venv/bin/activate

# 启动原生桌面程序
python desktop-pyside/app.py
```

### 5.2 自动化测试（质量门禁）
```bash
# 运行 PySide 全套 234 项自动化单元测试
.venv/bin/python -m unittest discover -s desktop-pyside/tests -p "test_*.py"

# 单独验证清理引擎逻辑
.venv/bin/python -m unittest desktop-pyside/tests/test_item_cleaner.py

# 单独验证版本号一致性与原生引擎
.venv/bin/python -m unittest desktop-pyside/tests/test_release_mode.py
.venv/bin/python -m unittest desktop-pyside/tests/test_native_engine.py
```

### 5.3 打包发版入口（全平台）
- **macOS 打包（Apple Silicon 原生应用）**：
  ```bash
  ./scripts/build-macos.sh
  ```
  执行流程：校验 `package.json` 版本号 ➔ 准备 runtime-staging ➔ 调用 PyInstaller ➔ 进行 ad-hoc 签名 ➔ 运行 Smoke 冒烟健康检查 ➔ 生成 release-manifest.json ➔ 自动同步分发至 `dist-mac/`、`~/Desktop/美客多活动管家.app` 与 `/Applications/`。
- **Windows 打包（x64 EXE）**：
  ```cmd
  python scripts/build-windows.py
  ```

### 5.4 版本递增强制守则（Version Sync Protocol）
每次任何功能调整、逻辑修复或界面改动，**必须严格单调递增版本号**，并同步更新以下所有位置：
1. `package.json` 中的 `"version"`
2. `desktop-pyside/main_window.py` 中的 `product_version()` 返回值
3. `desktop-pyside/tests/test_release_mode.py` 中的版本断言
4. `desktop-pyside/tests/test_ui.py` 中的版本断言（多处）
5. `desktop-pyside/mercado_discount_manager_pyside.spec` 打包配置

---

## 六、关键演进与版本里程碑清单（v2.0.54 -> v2.0.77）

| 版本区间 | 核心演进与改动事项 |
| :--- | :--- |
| **v2.0.54 - v2.0.60** | 新增【复制0浏览ID】分段提取弹窗；建立 cross-store / cross-site 图片指纹去重机制；多店铺并发扫描限流引擎优化。 |
| **v2.0.61 - v2.0.70** | 全局独立日志系统分流（`LogViewer` 引入）；设置页面全面嵌入重构；开发者应用多绑定识别与店铺别名动态同步；经营站点批量勾选。 |
| **v2.0.71 - v2.0.75** | 按ID操作页面重构为自包含卡片页面，统一 13px 清晰字体；清理管家引入“清店模式”；404/not a cbt item 异常单品标准化为已删除并自动排除。 |
| **v2.0.76** | 解耦上架时长门槛与浏览量过滤；增加子控件联动禁用与动态提示横幅；拓宽扫描按钮（140px）与优化留白解决文字偶发截断。 |
| **v2.0.77 (当前)** | 彻底清除与物理隔离单元测试脏数据（`ACC1`）；暗色主题“白条”适配（深暖琥珀色 `#2d2019` + 亮橙文字 `#ff8a65`）；批量删除按钮去除冗余 `(N)` 后缀；扫描分项与汇总计数等式 $100\%$ 严格闭环；全量 234 项测试无损通过。 |

---

## 七、运维注意事项与硬阻断边界

1. **Mercado 零写入安全防线**：
   - 日常分析、列表检索、风控扫描、0 浏览提取均属于 GET-only 权威读取，绝不触发平台写接口；
   - 真实报名、更新或删除必须经过严格的用户二次确认（Confirmation Dialog）与预检保护。
2. **出单商品绝对保护不可妥协**：
   - 任何情况下，`has_sales == True`（`sold_quantity > 0`）的商品严禁被系统批量删除。这是店铺运营的核心资产红线。
3. **ERP 账号对应性与站点前缀**：
   - 在使用【复制0浏览ID】导出 ID 并导入 ERP 时，注意站点前缀对应性（如墨西哥站 `MLM...`，巴西站 `MLB...`）。
4. **Token 自动保活与过期处置**：
   - 系统内置 Token 自动续期调度器；若遇卖家长期关机导致 Refresh Token 完全失效，界面顶部会自动呈现醒目的“重新授权”黄条，点击一键唤起授权浏览器即可完成静默恢复。
