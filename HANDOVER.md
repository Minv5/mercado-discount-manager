# 《美客多折扣管家》全景工程与系统移交总案（Master Handover Document）

---

### 一、项目全景与工程定位

* **项目名称**：美客多折扣管家（Mercado Discount Manager）
* **项目性质**：直接与 Mercado Libre 官方 API 通信的本地桌面生产力工具，支持全店铺活动检索、折扣批量报名/退出、净收益锁定、CBT（跨境商品）多站点级联改价、商品历史快照，以及基于官方 Webhook 的实时监听与自动改价。
* **开发与运行环境**：
  * **操作系统**：macOS（开发主宿主）/ Windows（兼容构建）
  * **GUI & 引擎产品线**：`desktop-pyside/`（Python 3.12 + PySide6 原生引擎，无 Node.js 后台依赖）
  * **主工作区**：`/Users/minv5/Documents/美客多折扣管家`
  * **当前稳定版本**：`v2.1.24`（Commit: `fcc333f`）
* **本地数据与凭据路径**：
  * 基础数据目录：`~/Library/Application Support/MercadoDiscountManagerStandalone/data/`
  * SQLite 数据库：`discount-manager.sqlite`（存储授权凭据、站点映射、商品基准快照 `item_price_cache`）
  * 主配置文件：`settings.json`
  * 消费端鉴权密钥：`consumer-api.secret`
  * 店铺授权目录：`/Users/minv5/Documents/美客多授权`

---

### 二、核心业务架构与计算规则（Business Logic & Rules）

#### 1. 双轨制改价调度体系（Dual Pricing System）
* **体系 1（平台调整运费 -> 保持原折扣重报）**：
  * **判定条件**：卖家净回款（`net_proceeds`）与包装尺寸/重量未变，仅平台运费（`shipping_cost`）或汇率变动导致售价偏离；
  * **动作**：若商品处于活动中，退出旧原价锁死，按最新运费和折扣基准重新报名回原活动。
* **体系 2（卖家主动调整数据 -> 155% 净利润新策略）**：
  * **判定条件**：检测到卖家修改了商品净回款（`abs(cached_net - current_net) >= 0.01`）或包装尺寸/重量变动；
  * **动作**：执行新策略——**立即退出所有正在进行（started）或待开始（pending）的活动，且不再参加任何活动**。
* **CBT 全球商品级联（CBT Cascade）**：
  * CBT 父商品（`CBT...`）收到通知时，通过 `marketplace_items` 扇出同步到各子站点（`MLB`, `MLM`, `MLC`, `MLA`, `MCO` 等）；
  * 内置 60 秒防抖去重机制（`_is_recently_exited`），防止多变体并发冲突或重复退活动。
* **净回款锚定算法（Net Proceeds Engine）**：
  * 严禁二次折扣与虚假扣减；严格基于 `extract_item_net_proceeds` 提取的真实净收益计算活动门槛，杜绝平台费率误差。

---

### 三、三店铺与多站点权威映射契约（Store & Route Contract）

客户端通过 `WebhookWorker._resolve_route` 实现严格多店铺自治，各店商品独立处理、互不干扰：

| 店铺名称 | 父账号 ID | 官方授权应用 ID（Client ID） | 涵盖站点与子账号（`child_user_id`） |
| :--- | :--- | :--- | :--- |
| **湖北店** | `2651442567` | `1817414235813121` | MLC(`2668033833`), MLB(`2668031897`), MCO(`2668034137`), MLM(`2668034127`/`2668033839`), MLA(`3005675422`), MLU(`3184159748`) |
| **广东店** | `3332096437` | `3176806962822417` | MLM(`3333531536`/`3333531550`), MLU(`3333531544`), MLA(`3333531540`), MLB(`3333531560`), MLC(`3333531568`), MCO(`3333530776`) |
| **湖南店** | `3408885754` | `685482544572002` | MCO(`3407224975`), MLC(`3407225955`), MLM(`3407225957`/`3407227823`), MLA(`3407224977`), MLU(`3407225959`), MLB(`3407227825`) |

---

### 四、网络拓扑与外部回调服务基础设施现状

1. **废弃链路**：原腾讯云广州服务器 `119.91.103.69`（原域名 `webhook.xingtupro1020.com`）已关机下线。
2. **现行基础设施**：
   * **公网入口**：`https://minv5.trade`（接入 Cloudflare Anycast CDN，SSL 证书有效）；
   * **真实宿主机**：家庭网关 / 软路由 `192.168.1.2`（OpenWrt，免密登录 `ssh -o BatchMode=yes root@192.168.1.2`）；
   * **隧道容器**：`cf-tunnel`（镜像 `cloudflare/cloudflared`，使用 Token 建立隧道将 `minv5.trade` 反代至 `192.168.1.2:28761`）；
   * **回调服务容器**：`meli-callback`（镜像 `meli-callback:router`，端口映射 `0.0.0.0:28761 -> 28761`）；
   * **服务端挂载源码**：`/mnt/mmcblk2p4/meli-callback-src/`；
   * **服务端运行时数据**：`/mnt/mmcblk2p4/meli-callback-data/`。
3. **接口实测结果**：
   * 美客多推送信箱 `POST https://minv5.trade/webhook/mercado-libre/` **已通**，美客多官方生产通知正源源不断推送至该接口。

---

### 五、新会话首要待修复阻断点（任务执行清单）

新对话无需重新调研网络拓扑，直接针对以下 3 点落地修复即可闭环：

#### 任务 1：服务端放行 3 店铺应用白名单（消除 403 `application_id_mismatch`）
* **修改文件**：`192.168.1.2:/mnt/mmcblk2p4/meli-callback-src/service.mjs`
* **根因**：当前代码仅使用单值匹配湖北店，导致广东店与湖南店被拒收。
* **改动**：在 `validateWebhookPayload` 中支持 3 个合法 App ID：`1817414235813121`（湖北）、`3176806962822417`（广东）、`685482544572002`（湖南），匹配任意一个即允许入队。

#### 任务 2：解除反代 IP 判定死信（消除 `dead_letter: true`）
* **修改文件**：`192.168.1.2:/mnt/mmcblk2p4/meli-callback-src/webhookQueue.mjs`
* **根因**：经 Cloudflare 转发后请求 IP 为网桥 IP `172.31.0.1`，因未在 IP 白名单中被硬编码打上 `dead_letter: true`，导致事件滞留死信队列。
* **改动**：在 `enforceWebhookSourceIps === false` 时，入队状态正常设置为 `state: 'pending'`，且 `dead_letter: false`。

#### 任务 3：服务端增加 Consumer 消费接口，客户端同步拉取地址
* **修改文件**：`192.168.1.2:/mnt/mmcblk2p4/meli-callback-src/service.mjs`
* **鉴权 Token**：`1psp1cUDt5rnJU7VDOGRYzn2RV5L9EcrEdBhBx+6kxOUKDAa5eOfyq5rHuhXnaCM`（客户端 `consumer-api.secret`）
* **接口逻辑**：
  * `POST /meli-callback/consumer/claim`：校验 Bearer Token，调用 `queue.claimDue({ limit: 50 })`，返回格式化事件列表：
    ```json
    {
      "ok": true,
      "events": [
        {
          "event_id": "<eventKey>",
          "lease_id": "<eventKey>",
          "topic": "<payload.topic>",
          "resource": "<payload.resource>",
          "remote_user_id": "<payload.user_id>",
          "application_id": "<payload.application_id>",
          "received_at": "<received_at>"
        }
      ]
    }
    ```
  * `POST /meli-callback/consumer/ack`：校验 Bearer Token，提取 `event_id`，调用 `queue.finishDelivery(event_id, { ok })` 返回 `{"ok": true}`。
* **客户端配置更正**：
  * 修改文件：`~/Library/Application Support/MercadoDiscountManagerStandalone/data/settings.json`
  * 设置：
    * `"activityCallbackClaimUrl": "https://minv5.trade/meli-callback/consumer/claim"`
    * `"activityCallbackAckUrl": "https://minv5.trade/meli-callback/consumer/ack"`

---

### 六、工程发布与代码管理铁律（Engineering Discipline）

1. **版本号同步递增**：任何涉及客户端功能调整、逻辑修复或热更新，必须严格递增版本号（同步修改 `package.json`、`main_window.py`、`.spec` 打包配置、测试用例及 `build-info.json`）。
2. **默认交付闭环**：代码改动并通过单元测试后，默认自动调用 `python3 scripts/build-macos.py` 刷新本地桌面端安装产物，打包完成后主动询问用户是否推送至 GitHub。
3. **解释先于动手**：排查报错和归因分析时先汇报结论，等待指令确认后再执行落盘与打包。
