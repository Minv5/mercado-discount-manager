# Codex 项目规则

## 1. 全局规则引用

<!-- CODEX-MANAGED:global-reference:BEGIN -->
本项目遵守 Codex App 全局自定义指令；本文件只记录已验证的项目事实、配置、硬边界和专属流程。正式规则、高风险或长期线程流程按需调用 `$codex-workflow-guard`。
<!-- CODEX-MANAGED:global-reference:END -->

- 模板版本/提交：codex-rules `66c31aee18fa852433aab770cc5042b7451a26d3`
- 模板校验：SHA256 `3c12ee67002fe888546de59449cdcd9a9fe20b55c7a37144e724c03bfb834d3d`
- 受管规则块版本：5
- 上次同步时间：2026-07-16
- 待确认模板冲突：无

## 2. 项目概览

## 3. 当前状态

- 当前阶段：待填写（需求讨论 / 技术选型 / 开发 / 修改 / 测试 / UI 调整 / 打包发布 / 维护）

## 4. 需求与范围

- 需求变更规则：需求、范围、验收标准发生变化时，必须先说明影响，再等待用户确认。
- 范围核对规则：执行项目任务、规则同步或线程下派前，必须核对用户原话中的对象、范围、边界和禁止项；不得把用户要求从 A 扩大成 A+B。任何范围扩展都必须先说明影响并等待用户确认。

## 5. 技术栈与运行环境

未知项保持空白或标注待确认；不得凭空填写。

## 6. 常用命令

- 日常验证：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate.ps1 -Mode Quick`
- 真实 Mercado 写入前验证：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate.ps1 -Mode RealWrite`
- 发布验证：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate.ps1 -Mode Release -AllowPackageMutation -PackageTarget PySide`；仅在已确认打包或安装位置变更时使用。旧 WinForms 回退包仅在明确指定 `-PackageTarget Legacy` 时构建。
- 查看失败证据：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate.ps1 -ShowFailure <run_id> -Check <check_id> -Tail 120`；仅明确需要完整原始输出时追加 `-Full`。

命令必须来自项目文件、README、脚本配置或用户确认；不能猜测。缺少命令时，先标注待确认。

## 7. 目录结构与关键文件

如果项目结构不同，应按真实结构调整本节。

## 8. 项目边界

## 9. 开发与修改流程

<!-- CODEX-MANAGED:workflow-reference:BEGIN -->
普通修改直接执行并做相称验证；本节只记录项目特有的执行顺序、回滚和交接。
<!-- CODEX-MANAGED:workflow-reference:END -->

## 10. 验证要求

<!-- CODEX-MANAGED:validation-reference:BEGIN -->
按影响范围运行真实验证；失败先定位责任层，并回到原失败路径和交付对象复验，不跳过、替换或降标。
<!-- CODEX-MANAGED:validation-reference:END -->

本项目验证口径：
- 日常开发验证使用 `Quick`。输入文件 SHA256 和 Node、.NET、Python 等环境指纹均未变化时，可复用已成功检查并标记为 `SKIP`；早期结构不完整或检查摘要异常不得作为缓存或放行依据。
- 真实 Mercado 写入前必须使用 `RealWrite`，所有 required 检查必须执行且 health（健康检查）必须通过；`RealWrite` 不得复用 `Quick` 缓存。
- `Release` 仅用于已确认的打包或安装位置变更，并且必须显式传入 `-AllowPackageMutation`；`Release` 不得复用 `Quick` 缓存。
- 验证回传只包含 run_id、模式、总体状态、检查摘要和证据路径 `data/validation-evidence/<run_id>/`。完整日志按需使用 `ShowFailure` 定向展开指定检查的末尾 120 行；不得默认全文回传日志。
- 不得因节省 Token（上下文用量）跳过 required 检查。验证脚本不启动或停止服务，也不调用 Mercado 写接口；`REAL_SUBMIT`、业务预检、主管确认和真实写入保护保持不变。
- 涉及全店铺或批量报名流程时，正式验收对象是正式安装程序在当次授权的真实目标账号和代表性活动规模下完成准备、确认、提交状态恢复与最终读回，并覆盖状态漂移、限流、超时和中止条件；未获真实写入授权时只能标记到写前验收，`Quick`、`RealWrite`、`Release`、health、fake 或隔离数据均不能单独证明全流程可用。

## 11. 批量操作与业务质量门控

<!-- CODEX-MANAGED:quality-gate-reference:BEGIN -->
批量写入、提交、上传、发布或外部修改按 `$codex-workflow-guard` 执行业务门禁；本节只配置项目验收。
<!-- CODEX-MANAGED:quality-gate-reference:END -->

## 12. 人工审核队列

<!-- CODEX-MANAGED:human-review-reference:BEGIN -->
低置信、缺事实、权限、安全、账号或外部异常按 `$codex-workflow-guard` 分流；本节只配置项目队列。
<!-- CODEX-MANAGED:human-review-reference:END -->

## 13. 线程协作规则

<!-- CODEX-MANAGED:thread-reference:BEGIN -->
未启用多线程时本节不配置。启用后只登记项目角色、真实线程 ID、职责和紧急规则推送范围；主管、执行线程、临时子智能体和 `return_contract=decision-return-v1` 的详细流程按 `$codex-workflow-guard` 执行。
<!-- CODEX-MANAGED:thread-reference:END -->
- 主管线程名称：主管
- 主管线程 ID：`019f1e9b-bb5d-7ec2-a27b-ad880e79fe99`
- 规则线程名称：规则（线程标题：添加Codex规则）
- 规则线程 ID：`019f1e95-53bc-7091-a5a3-a8d6719fe827`

## 14. 规则筛流与项目进化

<!-- CODEX-MANAGED:evolution-reference:BEGIN -->
规则分层、候选收件箱和正式发布按 `$codex-workflow-guard` 与 `RULE_DISTILLATION.md` 执行；本节只记录项目配置和已验证的专属经验。
<!-- CODEX-MANAGED:evolution-reference:END -->

- 项目规则不会自动静默成长。Codex 只能主动发现事实、流程、约束或经验并提出沉淀建议；建议必须说明写入哪一层、原因、预期效果和副作用，等待用户确认后才能写入。

## 15. 完成标准
- 产物生命周期收尾已量化并处理或等待确认：

## 16. 项目专属补充
