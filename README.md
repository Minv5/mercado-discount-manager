# 美客多折扣管家

本项目是直接对接 Mercado Libre 官方 API 的本地桌面程序。双击 exe 后会打开独立工作台窗口，后台使用本机服务承载数据和接口；不依赖圆佑 DLL、圆佑 GUI、圆佑接口、任务队列或网页坐标点击。

## 运行

双击桌面上的完整单文件：

```text
美客多折扣管家.exe
```

项目内完整 exe 输出在：

```text
dist-full/美客多折扣管家-完整版.exe
```

这个 exe 内置 Node.js、桌面窗口壳和程序文件，不依赖项目目录里的 `node_modules` 或系统 Node。运行时会把内置服务解压到：

```text
%LOCALAPPDATA%/MercadoDiscountManagerStandalone
```

也可以手动运行：

```powershell
npm start
```

后台服务地址仅用于维护排查，日常使用直接打开桌面 exe：

```text
http://127.0.0.1:28758
```

## 验证

```powershell
npm test
```

## 当前工作台能力

- 默认使用 `C:\Users\dztf6\Documents\美客多授权` 的 standalone 授权。
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
- 输入 `REAL_SUBMIT` 后，单活动和批量真实执行会调用 Mercado 写接口；执行前必须核对账号、站点、活动、商品状态、折扣和商品数量。
- 支持执行历史和 CSV 导出。
- 支持基于 5%/6%、完整执行递增、最高 10%、10% 后取消的周期建议。

## 安全边界

- 默认先生成测试模式计划，不提交真实报名、改价或取消。
- 真实提交接口必须显式传入 `mode=real` 和确认字段 `REAL_SUBMIT`；满足条件后会调用 Mercado 写接口。
- `access_token`、`refresh_token` 和 `Client Secret` 加密保存到本机数据库，不写入日志或导出。
- 本机已有 standalone 授权会从 `C:\Users\dztf6\Documents\美客多授权` 运行时读取，不要求重新 OAuth，也不复制 token 到项目文件。
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
C:\Users\dztf6\Documents\美客多授权
```

不需要重新输入 `Client ID`、`Client Secret` 或重新 OAuth。只有授权目录失效或刷新脚本失败时，才需要单独处理授权问题。
