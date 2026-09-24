# ShopLens

基于 **LangChain + LangGraph** 的电商经营数据分析助手。

> ⚠️ **本项目使用自行生成、固定随机种子的合成电商数据**，用于演示与评测。
> 界面上会明确标注「合成演示数据」，不代表任何真实业务。

## 当前状态：阶段 1（数据、口径和参考答案）已完成

已完成：

- 项目骨架：`backend/` `frontend/` `data/` `evals/` `docs/` `tests/` `scripts/`
- 依赖配置与锁文件、`.gitignore`、无真实密钥的 `.env.example`
- 后端 `GET /healthz`（存活）与 `GET /readyz`（真实探测 PostgreSQL）
- 前端最小连通页（Vite + React + TypeScript）
- 独立学习样例：LangChain `@tool` 契约 + LangGraph 条件边小图（离线可跑）
- 真实模型冒烟脚手架 `scripts/smoke_real_model.py`：
  `--stub` 自证「请求/响应协议接线正确」（4/4 通过）；
  无 Key 时明确报**「未验证」+ 退出码 2**，不用 stub 结果冒充真实模型。
  **2026-09-24 已用 `deepseek-chat` 真机跑通 4/4**（C1 工具调用 / C2 结构化输出 /
  C3 多轮闭环 / C4 歧义澄清），`create_agent` 预构建 Agent 同时验证通过。
- 本地 PostgreSQL 18.1 免安装实例（本机无 Docker，走官方二进制替代路径）
- 四张业务表、`analytics` 下三个分析视图与数据库级金额约束（Alembic 迁移）
- 九单手工边界样例、固定种子的 3000 单合成数据、五指标 YAML 字典
- 14 个手算问题；独立 Python 原始记录计算与真实 PostgreSQL SQL 交叉核对
- `analytics_reader` 只能读取分析视图，`shoplens_app` 仅获应用状态 schema 权限

**后续阶段**：任意模型 SQL 的 AST 校验与受限执行、LangGraph 查询流程、澄清、图表、持久化和模型评测尚未实现。阶段 1 的参考 SQL 只使用固定、人工编写的聚合查询，不接受模型或用户输入的 SQL。

## 快速开始

```powershell
# 1) 后端依赖（已锁版本）
python -m venv .venv
# ⚠️ 本机全局 pip 源在当前网络下不可达，且 pip 的配置文件必须只含 ASCII。
#    新建 venv 后先写这一行，否则会出现「pip 跑了但一个包都没装」的静默失败：
Set-Content -Path .venv/pip.ini -Value @('[global]', 'index-url = https://pypi.org/simple') -Encoding Ascii
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt

# 2) 本地 PostgreSQL（本机无 Docker）
.\scripts\pg.ps1 init          # initdb + 启动 + 建库 + 生成 .env
.\scripts\pg.ps1 status

# 2a) 阶段 1：建角色、迁移、灌入固定种子数据并交叉核对
.\.venv\Scripts\python.exe scripts/setup_db.py
.\.venv\Scripts\python.exe scripts/seed_data.py
.\.venv\Scripts\python.exe scripts/check_reference.py
# 想单独检查可手算小样例时：
.\.venv\Scripts\python.exe scripts/seed_data.py --fixture-only
.\.venv\Scripts\python.exe scripts/check_reference.py --fixture-only
# 检查后再次运行不带 --fixture-only 的 seed_data.py，恢复 3000 单数据。

# 3) 后端
Push-Location backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
Pop-Location
#    http://127.0.0.1:8000/healthz
#    http://127.0.0.1:8000/readyz

# 4) 前端
Push-Location frontend
npm install
npm run dev                                    # http://127.0.0.1:5173
Pop-Location

# 5) 统一验收：静态检查、离线测试、真实 PostgreSQL、前端构建
.\.venv\Scripts\python.exe scripts/verify.py
```

`verify.py` 复用 Ruff、pytest 标记、离线 LangGraph 样例、模型协议 stub、
`check_reference.py` 和 `npm run build`。每项输出 `PASS` / `FAIL` / `NOT_RUN`；
退出码 `0` 为已请求的检查通过，`1` 为至少一项失败，`2` 为必需检查因环境未执行。
默认**不调用真实模型**，`NOT_RUN` 会显示在汇总中，但不会让默认验收返回 `2`。
若 `npm` 不在 `PATH`，可加 `--npm "C:\path\to\npm.cmd"`。

真实模型只在显式指定时调用；该选项会为子进程选择 `deepseek`，不改变 `.env` 中的离线默认值：

```powershell
.\.venv\Scripts\python.exe scripts/verify.py --real-model
# 如果临时没有 PostgreSQL，可显式跳过它；输出仍标为 NOT_RUN：
.\.venv\Scripts\python.exe scripts/verify.py --skip-postgres
```

## 代码规范基线

```powershell
.\.venv\Scripts\python.exe -m ruff check backend scripts data tests
.\.venv\Scripts\python.exe -m ruff format --check backend scripts data tests
```

`E501` 是刻意关闭的（中文注释容易超长），行长由 `ruff format` 兜底，
所以两条都要跑，只看 `check` 会漏。

## 阶段交付

每阶段完成后按 [AGENTS.md](AGENTS.md) 运行验收、更新进度，检查提交内容并推送 GitHub。远端未确认前不把阶段标为已发布。

## 文档

| 文件 | 内容 |
|---|---|
| `docs/decisions.md` | 版本、服务商适配、数据库运行方式、目录结构等决策与理由 |
| `docs/progress.md` | 每阶段交接记录：已实现 / 验证结果 / 未验证项 / 下一阶段 |
| `docs/references.md` | 第三方参考的借鉴范围、许可与署名 |
| `data/metrics.yaml` | 五个指标的业务口径、时间字段、粒度、维度及关联规则 |
| `planning/` | 产品、数据口径、架构与验收规范（项目计划的原始文件） |
