# Production Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 测量实际产品查询配置，并保留可复核的错误和预算记录。
**Architecture:** 现有 runner 增加显式 production 配置；预算包装调用原生产适配器；现有独立评分与数据库保护复用。
**Tech Stack:** Python / LangGraph / PostgreSQL / pytest。
**Spec:** docs/superpowers/specs/2026-10-26-production-baseline-design.md

## Global Constraints

- 固定种子合成数据，整数分、Asia/Shanghai 左闭右开区间。
- 产品提示词、查询执行器、评分答案不改；标准答案不得进入模型输入。
- 沿用现有账本累计 800 次/200 万 Token；真实模型显式启用。
- 历史对照配置和默认入口保持原行为；新报告不得称盲测。

## Review Focus

- production 提示词/载荷是否与产品原调用相同，尤其不能混入对照示例。
- 真实错误没有 usage 时不得归零，预算耗尽不得发起请求。
- production 修复次数与 Settings 一致，不能悄悄改成评测常量。
- 汇总必须包含所有选择的 profile，原默认三配置不能扩大调用预算。
- 原始结果和报告暴露范围清楚；80 题内核结果不能冒充浏览器/并发测试。

## Task 1: 保真预算包装与 profile 选择

**Files:** create evals/production.py, tests/test_production_evals.py; modify evals/runner.py.
**Interfaces:** ProductionModel(settings, budget) 提供原 plan/draft 和 calls；profile_settings(profile, settings) 保留 production 的实际设置。

- [x] 先写并运行测试，确认新增 profile 和模块缺失导致失败。
- [x] 实现 ProductionModel._invoke，调用 super，保守结算预算，记录成功与异常。
- [x] runner 增加 --profiles，默认仍为 schema semantic repair；manifest 记录选择的配置与新源码摘要。
- [x] 汇总动态包含 production，测试覆盖原配置不变及 production 设置保真。
- [x] 运行新增与已有评测单元测试、Ruff；预期全部通过。

## Task 2: 执行基准与记录

**Files:** evals/runs/20261026-production-*/，evals/PRODUCTION_BASELINE.md，evals/README.md，docs/progress.md。
**Interfaces:** 复用 Task 1 的 --profiles production；沿用同一预算账本、评分及已有 80 题。

- [x] 原始合成记录与实际数据库快照核对；人工截图金额独立核验。
- [x] production 离线 80 题接线验证，预期无真实 API 调用。
- [ ] 6 道既有开发题真实试跑；无基础环境/协议接线阻塞后完整 80 题，失败仍计入报告。
- [ ] 记录正确率分母、拒绝、澄清、修复、耗时、Token、错误类别、未知 usage、完整性和预算。
- [x] 统一验收；不足项如实保留。
- [ ] 独立代码审阅并处理重要发现；扫描待提交内容中的凭据及本地产物，提交并推送工作分支，确认远端哈希。

## 执行记录

- 2026-10-26：原有离线评测单元测试 6 项通过。现有容器原始记录快照及用户截图金额独立核对通过，参考检查 80 组/14 手算通过。
- 工作区：native worktree 创建后不在当前可写范围，初始化失败；使用 D:/Project_02 的 codex/production-baseline 分支继续。临时 worktree 未写入业务文件，归档工具返回 primary checkout 保护错误，暂保留。
- Task 1 complete：5项新增测试 RED→GREEN，旧6项评测单元测试通过；统一验收116项通过。实现提交28762b9。
- Task 2进行中：独立原始数据核对及离线80题完成；真实试跑因外部载荷自动审批拒绝未启动，不绕过，等待用户明确发送授权。
- Final review (Task 1)：独立只读审阅411563d..28762b9，无Critical/Important/Minor发现。Ruling：真实模型正确率/真实usage继续待授权；评分独立性沿用已执行原始记录核对；HTTP持久化/负载不属于内核基准；Task 2及推送未完成；测试结果以主执行者实际116项验收记录为准。成本：以上边界不能从代码审阅或离线80题推导为已验证。
