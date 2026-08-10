# 美客多活动管家

本项目是直接对接 Mercado Libre 官方 API 的本地桌面程序。双击 exe 后会打开独立工作台窗口，后台使用本机服务承载数据和接口；不依赖圆佑 DLL、圆佑 GUI、圆佑接口、任务队列或网页坐标点击。

## 项目定位

美客多活动管家面向需要管理 Mercado Libre 活动的独立卖家和运营人员，重点处理多账号 CBT 活动的读取、计划、报名、更新和取消。它直接使用 Mercado Libre 官方 API，并把安全边界作为产品功能的一部分：默认生成 dry-run 计划；任何真实市场写入都需要独立的显式确认。

这是一个仍在积极维护的早期项目。欢迎通过 [Issue](../../issues/new/choose) 报告可复现的问题，或通过功能建议模板说明真实工作流中的缺口。提交代码前请阅读 [贡献指南](CONTRIBUTING.md)；安全问题请遵循 [安全政策](SECURITY.md)。

## 开源许可

本项目采用 [Apache License 2.0](LICENSE) 授权。除非该许可证要求保留，否则不得将作者姓名或贡献者姓名用于推广衍生产品。

## 运行

从当前用户开始菜单打开“美客多活动管家”快捷方式，启动正式 PySide 工作台：

```text
美客多活动管家.exe
```

项目内正式候选是完整 one-dir 目录：

```text
dist-pyside/美客多活动管家/
```

目录内包含桌面程序、Qt 依赖、经 SHA256 锁定的 Node 和业务服务文件。发布或安装时必须整体复制目录，不能只复制主 EXE。运行数据继续位于：

```text
%LOCALAPPDATA%/MercadoDiscountManagerStandalone
```

也可以手动运行：

```powershell
npm start
```

后台服务地址仅用于维护排查，日常使用从开始菜单打开程序：

```text
http://127.0.0.1:28758
```

该地址的根页面只显示本地服务状态，不提供计划、提交或其它业务操作。OAuth 回调仍由本地服务处理，桌面授权流程不受影响。

## 活动变化回调

活动变化回调默认关闭。启用时由独立公网回调服务将已验证 Mercado 通知转换为内部 schema v2，并通过本机 HMAC 签名发送到 `/api/integrations/activity-callback`。运行环境需要同时提供 `MDM_ACTIVITY_CALLBACK_ENABLED=1`、指向仓库外共享密钥文件的 `MDM_ACTIVITY_CALLBACK_SECRET_FILE` 和当前 Mercado 应用标识 `MDM_ACTIVITY_CALLBACK_APPLICATION_ID`。明文 `MDM_ACTIVITY_CALLBACK_SECRET` 只保留测试兼容，正式部署不使用。

通知归属只按 `account_id + child_user_id + site_id` 解析，店铺显示名不参与路由。资源 GET 失败或身份无法确认时不会使用旧商品缓存；事件由上游持久队列重试。回调只负责精确标脏或站点目录失效，不替代每日目录、三日商品完整校准及提交前 live 复核。

## Legacy 回退版

`standalone/` WinForms 源码和旧安装版仅作为回退保留，不再承担正式产品功能对齐、日常 Quick/RealWrite 验证或默认发布。只有明确维护回退版本时才使用：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -Mode Release -AllowPackageMutation -PackageTarget Legacy
```

正式产品、默认验证和发布目标均为 PySide。

## 验证

```powershell
# 日常验证：语法和 JS/PySide 测试；服务未运行时 health 记为 SKIP
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -Mode Quick

# 真实写入前验证：所有 required 检查强制执行，health 必须通过；不会调用 Mercado 写接口
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -Mode RealWrite

# 发布验证会生成 PySide 候选包并执行隔离安装/回滚测试，必须显式允许产物变更
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -Mode Release -AllowPackageMutation -PackageTarget PySide
```

验证结果只在控制台显示 `PASS`、`FAIL`、`SKIP` 摘要。完整输出保存在
`data/validation-evidence/<run_id>/`；输入文件 SHA256 和 Node、.NET、Python 等环境指纹都未变化时，
Quick 模式可安全跳过已经成功的检查。Release 和 RealWrite 模式不会复用 Quick 缓存。

失败时只展开指定检查的末尾 120 行：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -ShowFailure <run_id> -Check npm-test -Tail 120
```

只有明确需要完整原始输出时才追加 `-Full`。Quick 默认不打包、不启动或停止服务，所有模式都不会调用
Mercado 写接口；`REAL_SUBMIT`、主管确认、业务预检和真实写入保护仍由现有业务流程负责。

## 当前工作台能力

- 默认使用当前 Windows 用户的 `%USERPROFILE%\Documents\美客多授权` standalone 授权；可通过 `ML_STANDALONE_AUTH_DIR` 指定其它目录。
- 显示当前账号、token 到期时间、站点概览和活动统计。
- 支持刷新全部 CBT 子账号活动，活动保存 `site_id`、`child_user_id`、`logistic_type`。
- 支持按站点、活动类型、状态、名称筛选活动。
- 支持读取选中活动或筛选范围内活动的 `candidate`、`started`、`pending` 商品。
- 支持只读活动/商品拉取并发探测，当前探测档位覆盖 1、2、3、4、5、8、10、15、20；日常提交优先使用上次只读压测建议。
- 写入并发分三层显示：当前设置值、真实测试线程已验证稳定档、日常建议值。当前已重复验证稳定档为 350；日常建议保守 300-320，追求速度可手动设 350，并保留停止/回查。
- 支持自建活动默认 5%、官方活动默认 6%，也支持单次覆盖折扣和直接活动价；单次输入优先于设置默认值。
- 支持报名、更新、取消的 dry-run 计划；真实提交必须强确认。
- 支持 Seller Campaign 创建活动预检，能生成官方创建请求预览；真实创建仍属于外部写入，必须经过最终确认。
- 如果平台返回 `total > 0` 但不返回商品明细，程序会标记为 `api_incomplete/blocking`，不会当作“无商品”放行报名。
- 当前桌面只通过持久化 execution job 执行真实报名、更新和取消；旧同步写入口返回 410。execution job 仍必须通过 `REAL_SUBMIT` 确认门，并在执行前核对账号、站点、活动、商品状态、折扣和商品数量。
- 支持执行历史和 CSV 导出。
- 支持基于 5%/6%、完整执行递增、最高 10%、10% 后取消的周期建议。

## 安全边界

- 默认先生成测试模式计划，不提交真实报名、改价或取消。
- 真实提交接口必须显式传入 `mode=real` 和确认字段 `REAL_SUBMIT`；满足条件后会调用 Mercado 写接口。
- `access_token`、`refresh_token` 和 `Client Secret` 加密保存到本机数据库，不写入日志或导出。
- 本机已有 standalone 授权默认从 `%USERPROFILE%\Documents\美客多授权` 运行时读取，也可由 `ML_STANDALONE_AUTH_DIR` 覆盖；程序不复制 token 到项目文件。
- 当前加密方案使用本地随机密钥文件 `data/local.key` 加 AES-256-GCM，适合单机本地第一版；后续建议升级到 Windows DPAPI 或系统凭据库。

## CBT 账号活动读取

CBT 父账号不能直接用普通 seller-promotions 活动接口。程序会先调用：

```text
GET /marketplace/users/{merchant_id}
```

获取子 marketplace users 后，再对每个子账号调用：

```text
GET /marketplace/seller-promotions/users/{child_user_id}
```

活动会保存 `child_user_id`、`site_id`、`logistic_type`，后续拉商品、生成计划和真实提交保护都会使用活动对应的子账号上下文。

## 授权来源

默认直接读取现有授权目录：

```text
%USERPROFILE%\Documents\美客多授权
```

需要使用其它位置时，在启动程序前设置 `ML_STANDALONE_AUTH_DIR`。已有授权有效时不需要重新输入 `Client ID`、`Client Secret` 或重新 OAuth；只有授权目录失效或刷新脚本失败时，才需要单独处理授权问题。
