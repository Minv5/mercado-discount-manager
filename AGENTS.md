# Codex 项目规则

## 1. 全局规则引用

<!-- CODEX-MANAGED:global-reference:BEGIN -->
本项目遵守 Codex App 全局自定义指令；本文件只记录已验证的项目事实、配置、硬边界和专属验收参数。授权、停止、诊断、正式验收、高风险操作和长期线程流程按需调用 `$codex-workflow-guard`。
<!-- CODEX-MANAGED:global-reference:END -->

- 模板版本/提交：codex-rules `0037e9b1bd84772560a971f03c2968b11b19982f`
- 模板校验：SHA256 `C177806ED473A33A337E99B32ACD4869AC56CC8376E5558EC13A03F14F71DC3F`
- 受管规则块版本：7
- 上次同步时间：2026-08-10
- 待确认模板冲突：无

## 2. 项目概览

- 项目名称：美客多折扣管家。
- 唯一主项目目录：`C:\Users\dztf6\Documents\美客多折扣管家`。
- 项目是直接连接 Mercado Libre 官方 API 的本地桌面程序，不依赖圆佑 DLL、圆佑 GUI、圆佑接口、圆佑任务队列或网页坐标操作。
- 当前正式产品线为 `desktop-pyside/`；`standalone/` WinForms 版本仅作为明确维护时使用的 Legacy（旧版）回退产品线。
- 本地运行数据位于 `%LOCALAPPDATA%\MercadoDiscountManagerStandalone`。
- 验证证据位于 `data/validation-evidence/<run_id>/`。
- 凭据、token、refresh token、Client Secret、密码和未脱敏 API 响应不得写入代码、日志、测试快照、验证摘要或线程回传。
- `MDM Launcher` 仅是独立离线安全工具；除非产品或验收协议明确需要，不作为 Quick、RealWrite、Release、安装或真实 GET-only 验收的固定前置。

### PowerShell 运行时

所有正式 `.ps1` 入口固定使用：

`C:\Program Files\PowerShell\7\pwsh.exe`

统一调用参数：

`-NoLogo -NoProfile -NonInteractive -File`

所有正式 `.ps1` 入口必须声明：

```powershell
#requires -Version 7.6
#requires -PSEdition Core
```

所有正式入口只能通过上述固定 PowerShell 7.6 Core 路径调用。

### 常用命令

日常验证：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -Mode Quick
```

真实 Mercado 写入前验证：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -Mode RealWrite
```

PySide 发布验证：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -Mode Release -AllowPackageMutation -PackageTarget PySide
```

Legacy 回退版发布：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -Mode Release -AllowPackageMutation -PackageTarget Legacy
```

查看失败证据：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -ShowFailure <run_id> -Check <check_id> -Tail 120
```

只有明确需要完整原始输出时才追加 `-Full`。

## 9. 开发与修改流程

<!-- CODEX-MANAGED:workflow-reference:BEGIN -->
已授权、边界明确且不涉及故障诊断、范围变化、高风险或外部状态的普通修改，可直接执行并做相称验证；其他情况按 `$codex-workflow-guard` 执行。本节只记录项目特有的执行顺序、回滚和交接。
<!-- CODEX-MANAGED:workflow-reference:END -->

- 正式 PowerShell 入口及其叶子脚本必须保持 PowerShell 7.6 Core 运行时声明和固定路径调用链。
- `PackageTarget` 只用于已明确授权的 `Release`；不得把 `Quick` 或 `RealWrite` 与 Legacy 打包参数组合成项目认可的日常验证入口。

## 10. 验证要求

<!-- CODEX-MANAGED:validation-reference:BEGIN -->
按影响范围验证真实对象、真实路径和实际交付物。普通离线验证失败按 `$codex-workflow-guard` 定位责任层并在原路径复验；独立复核或正式验收失败按其终止、回流和重新授权规则处理。不得跳过、替换或降低适用质量门。本节只配置项目专属验证参数。
<!-- CODEX-MANAGED:validation-reference:END -->

### Quick

- 用于日常语法检查、Node 测试和 PySide 测试。
- 本地服务未运行时，health（健康检查）记录为 `SKIP`。
- 不打包，不启动或停止服务，不调用 Mercado 写接口。
- 只有检查的全部真实输入和 Node、Python、.NET 等相关环境指纹均被完整覆盖且未变化时，才可复用此前成功结果；指纹覆盖不完整、摘要异常或输入来源不明确时不得复用。
- JavaScript 语法检查必须直接调用 Node，不得经过第二层 PowerShell 解析。

### RealWrite

- 真实 Mercado 写入前必须运行。
- 所有 required（必需）检查必须实际执行，health 必须通过。
- 不得复用 Quick 缓存。
- 验证脚本本身不启动或停止服务，也不调用 Mercado 写接口；health 所需服务必须在调用前已经可用。
- RealWrite 通过只证明写入前质量门通过，不代表已执行或已通过真实业务写入。

### Release

- 只在已确认打包、安装位置或发布产物变更时使用，并必须显式传入 `-AllowPackageMutation`。
- 不得复用 Quick 缓存。
- PySide Release 会生成或更新打包产物，并运行隔离安装验证；隔离验证会启动和停止本地测试服务，并启动候选程序执行 smoke（冒烟）检查。
- Legacy Release 会构建回退产品，并可能覆盖 `%LOCALAPPDATA%\Programs\MercadoDiscountManager` 下的安装文件、删除桌面旧 EXE、创建或改写桌面快捷方式。
- Release 不调用 Mercado 写接口，但其文件、安装和进程副作用必须按实际目标纳入授权与验收。

### 验证证据

- 常规回传只包含 `run_id`、模式、总体状态、检查摘要和证据路径。
- 完整日志只在需要定位指定失败时通过 `ShowFailure` 定向展开。
- 不得因减少日志或上下文用量跳过 required 检查。
- Quick、RealWrite、Release、health、fake 或隔离数据均不能单独证明真实 Mercado 全流程可用。

## 11. 批量操作与业务质量门控

<!-- CODEX-MANAGED:quality-gate-reference:BEGIN -->
批量写入、提交、上传、发布或外部修改按 `$codex-workflow-guard` 执行业务门禁；本节只配置项目对象、业务验收参数和项目专属硬阻断。
<!-- CODEX-MANAGED:quality-gate-reference:END -->

### Mercado 零写入边界

- `scripts/validate.ps1` 的 Quick、RealWrite 和 Release 均不得调用 Mercado 写接口。
- 真实报名、更新或取消必须经过产品正式写入入口，并同时满足 `mode=real`、确认字段 `REAL_SUBMIT` 和项目业务预检。
- GET-only 验收中 Mercado 非 GET 请求数必须为零；OAuth、Webhook 和其他外部行为必须按当批明确允许的请求类型分别计数，不能混入 GET-only 结论。
- 任何诊断读取、缓存回放、历史结果或离线验证都不能转化为真实写入授权。

### 权威数据

- Mercado 路由身份至少由父账号、子账号和 `site_id` 共同确定；跨身份、跨站点或跨路由的数据不得合并为同一权威快照。
- 正式目录权威必须来自当批已确认路由上的官方完整分页读取，并绑定对应快照或 revision（修订版本）。
- 非权威 fallback（回退）、诊断补充、历史缓存、手工结果、partial（部分结果）或不完整 checkpoint（检查点）不得参与权威目录物化，不得改变 `total`、revision 或放行结论。
- catalog（目录）与 promotion-item（活动商品）必须分别按各自已验证的身份、路由、分页和完整性契约验收；一类结果不能替代另一类结果。
- 物化记录数、唯一记录数和平台 `total` 不一致时，不得把结果标记为完整权威数据。

### 项目专属正式验收参数

- 正式对象必须绑定父账号、子账号、`site_id`、路由身份、活动类型或 ID、目标状态和活动范围。
- catalog 与 promotion-item 快照必须分别绑定各自的 revision、分页和完整性结果，二者不得相互替代。
- 当批允许的方法、请求类型和请求预算以正式清单为准；文件数、路由数、活动数和请求数量必须从当批清单与快照动态推导，不使用历史固定数字。
- GET-only 验收的身份、路由发现、catalog 快照和 promotion-item 读取必须属于同一批次，不得通过隐式重复发现改变已冻结范围。
- 涉及全店铺或批量真实写入时，验收对象是正式安装程序在当次授权账号和代表性活动规模下完成准备、确认、提交、状态恢复和最终读回，并覆盖状态漂移、限流、超时和中止条件。

## 12. 人工审核队列

<!-- CODEX-MANAGED:human-review-reference:BEGIN -->
低置信、缺事实、权限、安全、账号或外部异常按 `$codex-workflow-guard` 分流；本节只配置项目队列和项目专属阻断条件。
<!-- CODEX-MANAGED:human-review-reference:END -->

本项目未配置独立的长期人工审核队列。

## 13. 线程协作规则

<!-- CODEX-MANAGED:thread-reference:BEGIN -->
未启用多线程时本节不配置。启用后只登记项目角色、真实线程 ID、项目专属职责边界、唯一工作目录和紧急规则推送范围；主管、执行线程、临时子智能体和 `return_contract=decision-return-v1` 的详细流程按 `$codex-workflow-guard` 执行。
<!-- CODEX-MANAGED:thread-reference:END -->

- 协作模式：多线程；当前仅绑定主管。
- 唯一主项目目录：`C:\Users\dztf6\Documents\美客多折扣管家`。
- 工作树策略：只使用唯一主项目目录；当前不登记额外 worktree。
- 主管：线程标题 `美客多折扣管家｜项目主管`；真实线程 ID `019fe7e3-474e-7072-85f8-b14a3973ff55`；负责本项目需求归集、总体规划、项目任务包冻结、风险决策和结果汇总。
- 开发：未绑定；负责项目代码、UI、项目配置和本地测试。
- 验收工具开发或修复：未绑定；负责隔离夹具和验收工具，不执行独立最终验收。
- 独立最终验收：未绑定；只核对冻结候选并执行明确授权的验收，不修改项目文件、产品或验收工具。
- 紧急规则推送范围：当前仅绑定主管；只接收立即影响高风险动作、安全边界、授权范围或外部写入门槛的规则变化。

只登记当前真实存在且已经确认使用的线程。

线程不存在、已归档、不可用或地址未经核实时，标记为“未绑定”，不得猜测或保留过期 ID。

不得记录线程的 idle、active、waiting、notLoaded 等动态状态，也不得在本节复制通用的新建、替代、恢复、失败回流或正式验收流程。

## 14. 规则筛流与项目进化

<!-- CODEX-MANAGED:evolution-reference:BEGIN -->
规则分层、候选收件箱和正式发布按 `$codex-workflow-guard` 与 `RULE_DISTILLATION.md` 执行；本节只记录项目配置和已验证的专属经验。
<!-- CODEX-MANAGED:evolution-reference:END -->

本项目不配置独立规则候选收件箱；项目规则只维护本项目事实、配置、硬边界和专属验收参数。

## 16. 项目专属补充

- 旧运行记录、旧活动数量、旧安装目录、旧候选哈希和旧验收结果只能作为待核实历史证据，不能自动成为当前事实或放行依据。
- 用户可见的“完成”“可用”“成品”必须与实际通过的产品路径和验收范围一致；只完成离线检查、GET-only 或写入前验证时必须明确其未覆盖的边界。
