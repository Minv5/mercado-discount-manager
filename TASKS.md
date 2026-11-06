# 《本地待刊登产品库系统开发任务卡》（TASKS.md）

> **当前状态**：【四步走工程流水线 · 第 2 阶段完成】  
> **依据规格**：[SPEC.md](file:///Users/minv5/Documents/美客多折扣管家/SPEC.md)（已由用户确认封板）  
> **执行原则**：安全绳单卡执行、严格 TDD 验证、每次单卡完成后必须进行自动化机器质检与 Git 提交。

---

## 任务卡总览

```
[Card 1: 本地数据持久化与三维入库引擎] ──────→ [x] 已完成
                ↓
[Card 2: 顶栏三行组合筛选面板与 AI 核价栏] ──→ [x] 已完成
                ↓
[Card 3: 商品卡片流网格、选区计数与批量设置] ─→ [x] 已完成
                ↓
[Card 4: 更多操作、双重高危删除与图片预上传] → [x] 已完成
                ↓
[Card 5: AI标题差异化优化、CBT批量上传与总集成] → [ ] 待执行
```

---

### 任务卡 1：本地 SQLite 数据库底座与三维入库引擎
* **状态**：`[x] 已完成`
* **目标与交付物**：
  * 构建本地待刊登产品库专用数据库引擎 `desktop-pyside/items/vault_db.py`；
  * 创建表结构 `product_vault` 与 `product_vault_groups`（支持多语种分词检索、软删除、变体 JSON、类目属性 JSON）；
  * 实现三维入库管道：
    1. 采集插件 JSON 数据包直接解析入库；
    2. Excel / CSV 表格批量解析与导入；
    3. 库内商品克隆复制。
  * 提供增删改查、批量更新、动态多条件分页检索接口。
* **涉及文件**：
  * 新建：`desktop-pyside/items/vault_db.py`
  * 新建：`desktop-pyside/tests/test_vault_db.py`
* **机器质检命令**：
  ```bash
  pytest desktop-pyside/tests/test_vault_db.py -v
  ```

---

### 任务卡 2：顶栏三行组合筛选面板与 AI 核价胶囊控制条
* **状态**：`[x] 已完成`
* **目标与交付物**：
  * 实现符合圆佑视觉排布的 3 行组合筛选器组件 `desktop-pyside/items/vault_filters.py`；
    * **第 1 行**：店铺多选、站点下拉、状态下拉、创建/更新时间范围日历、产品分组选择；
    * **第 2 行**：自动识别语言的智能标题检索框（输入中/西/葡/英智能分词过滤）、商品 ID 输入框、原美客多 CBT ID 输入框、CBT 分类级联选择；
    * **第 3 行**：售价/成本/利润/重量 4 组双数值区间、是否有变体、是否有尺寸、多维排序下拉、`[重置]` / `[查询]` / `[收起]` / `[📥 导入成本]` / `[➕ 表格创建]`；
  * 实现 **AI 核价台控制条**：显示 8 色状态胶囊计数（本页全部、待核价、待回填、已回填、存疑待审、尺寸异常、侵权跳过、失败）。
* **涉及文件**：
  * 新建：`desktop-pyside/items/vault_filters.py`
  * 新建：`desktop-pyside/tests/test_vault_filters.py`
* **机器质检命令**：
  ```bash
  pytest desktop-pyside/tests/test_vault_filters.py -v
  ```

---

### 任务卡 3：商品卡片流网格（Card Grid）、选区计数与【批量设置 ∨】四大弹窗
* **状态**：`[x] 已完成`
* **目标与交付物**：
  * 构建**卡片式商品组件** `ProductCardWidget`（与圆佑 100% 对齐的 6 层紧凑卡片）：
    1. 顶部复选框 + 正方形主图 + 右上角变体小角标；
    2. 国旗站点（如 `按 🇧🇷 巴西`）+ 高低阈值胶囊；
    3. 平台徽标 + 商品 ID + 快捷复制按钮 + `[🤖 核价]` 按钮；
    4. 6 项核心业务指标方格矩阵（售价、成本高亮、利润、重量、长宽高尺寸、变体数）；
    5. 双行商品标题（检索词自动高亮）；
    6. 分组名称、创建时间与 `[编辑]` 按钮；
  * 实现**响应式卡片流瀑布流网格** `ProductCardGrid`，自适应宽度排布；
  * 实现工具栏基础交互：`[全选]`、`[反选]`、`已选 X/Y 条` 动态计数指示器；
  * 实现快捷复制三件套：`[📋 复制标题]`、`[📋 复制ID]`、`[📋 复制原美客多ID]`（换行格式写入系统剪贴板）；
  * 实现 `[✏️ 批量设置 ∨]` 4 大细则弹窗 `desktop-pyside/items/vault_batch_settings.py`：
    1. **设置价格**：售价/成本/利润 × 微调/按百分比/按固定价格，带高危警示与二次确认；
    2. **设置库存**：数字输入框（单位“个”，整数，支持 0）；
    3. **设置重量**：固定重量 / 区间内随机 / 微调重量（正负增减）；
    4. **尺寸 >**：手动设置长宽高（默认 20×20×20） / 按重量智能估算体积（智能填充 vs 强制覆盖）。
* **涉及文件**：
  * 新建：`desktop-pyside/items/vault_card.py`
  * 新建：`desktop-pyside/items/vault_batch_settings.py`
  * 新建：`desktop-pyside/tests/test_vault_card.py`
  * 新建：`desktop-pyside/tests/test_vault_batch_settings.py`
* **机器质检命令**：
  ```bash
  pytest desktop-pyside/tests/test_vault_card.py desktop-pyside/tests/test_vault_batch_settings.py -v
  ```

---

### 任务卡 4：【更多操作 ∨】保留项、双重高危删除与图片预上传
* **状态**：`[x] 已完成`
* **目标与交付物**：
  * 实现 `[⋮ 更多操作 ∨]` 弹窗组件 `desktop-pyside/items/vault_more_actions.py`：
    1. **生成变体**：目标变体数（只增不减）、预设开关与自定义 tags 变体值输入、Default 自动补齐；
    2. **修改产品属性**：类目公共属性（品牌、型号、材质、颜色等）动态配置与批量覆盖；
    3. **替换关键字**：关键字、替换词（留空删除）、忽略大小写单选、标题/描述勾选；
    4. **修改分类**：CBT 类目级联选择与属性级联联动；
    5. **复制产品**：选择目标分组，克隆生成独立草稿；
    6. **导入自己的产品库**：跨分组/外部草稿归集入库；
    7. **按筛选删除产品**：无需勾选，直接针对当前筛选结果全量删除，弹窗强二次警示与确认；
  * 实现 **`[🗑️ 删除已选]`**：备份删除（保留3天）与直接删除双模式，支持勾选同时清理关联图片；
  * 实现 **`[☁️ 自建图片预上传]`**：宽屏图片列表向导、状态监控（待上传/上传中/成功/失败）、公网图床 URL 自动回填。
* **涉及文件**：
  * 新建：`desktop-pyside/items/vault_more_actions.py`
  * 新建：`desktop-pyside/tests/test_vault_more_actions.py`
* **机器质检命令**：
  ```bash
  pytest desktop-pyside/tests/test_vault_more_actions.py -v
  ```

---

### 任务卡 5：【AI标题翻译/差异化】、CBT【批量上传】发布向导与工作台总集成
* **状态**：`[ ] 待执行`
* **目标与交付物**：
  * 实现单一保留项 **`[AI标题翻译/差异化]`** 4 步向导弹窗（语言检测、目标语言、直译/差异化策略、一键复制 Prompt 与数据、结果一键回填解析与更新）；
  * 实现 **`[🚀 批量上传]`** 刊登向导与后台 Worker：
    * 支持多店铺与站点映射选择；
    * Premium/Classic 销售类型切换、售价/净收益价格模式切换；
    * 强制覆盖库存、UP 变体上限、尺码表绑定、保修类型与时长、前后缀追加、自动追加国内运费；
    * 调用官方 CBT 刊登 API（支持并发与重试），实时输出进度条与错误日志；
  * 实现商品管理主页面 `desktop-pyside/items/product_vault_page.py`，串联表格展示、分页器（总数、每页50/100条、跳转页码），并挂载至桌面端主窗口导航。
* **涉及文件**：
  * 新建：`desktop-pyside/items/vault_ai_dialog.py`
  * 新建：`desktop-pyside/items/vault_publisher.py`
  * 新建：`desktop-pyside/items/product_vault_page.py`
  * 修改：`desktop-pyside/main_window.py`（安全挂载“待刊登产品库”入口）
  * 新建：`tests/test_product_vault_integration.py`
* **机器质检命令**：
  ```bash
  pytest tests/test_product_vault_integration.py -v
  ```
