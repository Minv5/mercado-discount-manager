# PySide6 桌面迁移映射

## 边界

- 桌面层独立位于 `desktop-pyside/`，保留 `standalone/` WinForms 和旧安装版作为回退。
- Mercado 业务逻辑继续由现有 Node 服务提供；PySide6 只负责业务界面、状态编排、最终确认和服务生命周期。
- PySide6 正式包只输出到 `dist-pyside/美客多活动助手/`；安装目录保持兼容，正式安装器只维护当前用户开始菜单快捷方式，不创建或删除桌面快捷方式。

## 功能与接口

| 桌面功能 | 现有接口 | PySide6 入口 |
|---|---|---|
| 服务健康 | `GET /api/health` | `service_manager.py` |
| 设置/账号 | `GET/POST /api/settings`, `GET /api/accounts` | 设置窗、启动加载 |
| 今日折扣 | `GET /api/today/global-discount` | 启动即显示；与动作冲突解耦 |
| 今日动作 | `POST /api/today/decision` | 自动判断；冲突时阻断提交 |
| 店铺站点活动 | accounts/sites/promotions 系列接口 | 顶部筛选、活动管理 |
| 历史 | `GET /api/tasks`, `GET /api/tasks/details` | 工作台与历史懒加载 |
| 自建活动创建 | batch-precheck / batch-create | 三态引导、二次确认 |
| 报名/更新/取消 | execution jobs start/status/cancel | 最终确认、后台轮询、停止 |
| OAuth 授权 | oauth start/complete | 设置窗账号授权 |

## 安全门

- 商品执行请求保留 `confirmText=REAL_SUBMIT`。
- 自建活动创建请求保留 `confirmText=CREATE_SELLER_CAMPAIGN`。
- 自动测试只验证拒绝确认，不发送真实创建或商品写请求。
- 关闭窗口只结束本程序自己启动的 Node；复用已有健康服务时不结束别的进程。
