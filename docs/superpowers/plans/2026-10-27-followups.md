# ShopLens Followups Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. 用户指定 D:/Project_02，授权自主完成常规实现、测试、提交和推送。

**Goal:** 成功结果支持有明确来源、可持久化且安全的追问。
**Architecture:** 服务端读取来源计划，结构化变更合并生成新计划，沿用现有查询图。
**Tech Stack:** FastAPI/Pydantic/LangGraph/PostgreSQL/React/Playwright。
**Spec:** docs/followups.md

## Global Constraints

- 只使用固定种子合成数据；参考答案留在评分端。
- 主查询独立，追问须同会话成功来源；保留原安全/预算限制。
- 所有命令显式使用 D:/Project_02，保留旧容器和卷，不使用临时worktree。

## Review Focus

- 未明确的改变不能静默继承：测试歧义、不支持过滤和无效日期。
- 追问重试/澄清不能丢来源：真实数据库及进程重启验证。
- 旧记录兼容：原查询历史和独立输入回归。
- 空列表/空值清除条件：整体、取消比较和取消Top N测试。
- 失败/跨会话来源不能进入模型或SQL：HTTP拒绝与记录数检查。

## Tasks

- [x] 1. 先写 tests/test_followups.py（F01–F03、F06–F11）、真实PG接口测试及浏览器F04/F08；运行记录预期失败。
- [x] 2. 新增 query_followup.py 的 FollowupPatch/merge；query_model.py增加离线/真实结构化变更规划；query_graph.py传递服务端基础计划。运行单测及原查询回归。
- [x] 3. workspace.py保存来源/基础计划、同会话成功校验、新端点、重试兼容；tests真实PG验证与独立评分。
- [x] 4. api.ts/App.tsx/styles.css增加来源结果、追问表单、历史标记和回看；浏览器验证刷新、隔离及重启。
- [x] 5. 完整verify（PYTHONUTF8/npm PATH/--frozen-used-for-iteration），预算内真实模型F01–F03；记录所有结果、限制和预算。
- [x] 6. 独立代码审阅、必要修复验证，更新README/progress，扫描凭据及产物，提交推送并核对远端。

## Execution ledger

- 初始：分支codex/production-baseline，HEAD 3c323910f4c375b11c1d7bf84522d7775411a982，工作区干净；预算790/800，inflight=0。
- 用户已明确入口和范围并要求自主完成，因此按上述设计连续实施，不增加方案审批轮次。
- Tasks 1–5 complete：30项新增追问测试、158项总pytest、统一验收10/0/2；真实定向6/6、9API，预算799/800。
- Task 6审阅完成：3类Important已按失败回归修复；额外评分shape检查已复核既有真实记录4/4。凭据扫描与提交推送回执待最后完成。
- 交付环境：仅重建原项目api/web，新镜像浏览器5/5，历史/视图摘要不变，init及数据卷未动。
- Task 6 complete：29文件凭据/产物扫描及diff检查通过；主体f08f7a59f6141cf0a384eb3620cd25aa6231f045已推送并由ls-remote确认。本机PG正常停止，原Docker项目新镜像健康运行。
