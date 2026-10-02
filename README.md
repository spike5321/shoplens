# ShopLens

基于 **LangChain + LangGraph** 的电商经营数据分析助手。

> ⚠️ **本项目使用自行生成、固定随机种子的合成电商数据**，用于演示与评测。
> 界面上会明确标注「合成演示数据」，不代表任何真实业务。

## 当前状态：阶段 3（查询内核）已实现，真实模型验证待 Key

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
- 独立 SQL 执行器：PostgreSQL 方言 AST 允许列表、分析视图/字段检查、reader 连接池、只读事务、3 秒语句超时、200 行保护上限与显式 Top N 区分；真实 PostgreSQL 集成测试覆盖权限、超时、截断和时区边界
- Pydantic 查询计划、LangChain 结构化输出适配与明确标识的离线模型；LangGraph 显式规划、澄清、加载字典、生成 SQL、校验、执行、最多一次修复和确定性结果说明
- 五指标、日/渠道/类别分组、Top N、总体/渠道/类别的两个期间比较；图表配置和变化率由同一查询结果生成
- `app_state` 下的 PostgreSQL checkpoint，使用 app 凭据；实际通过两个独立进程的暂停/恢复测试。CLI/API 共用内核检查任务 owner，尚未提供浏览器会话接口

**后续阶段**：HTTP 查询接口、浏览器工作台、会话/历史与重启任务管理属于阶段 4；模型质量评测和容器交付尚未实现。当前没有 Key，阶段 3 真实模型四场景冒烟明确未验证；离线模型及协议 stub 不代表模型质量。

执行器当前支持指定分析视图上的单条 `SELECT`、聚合、普通 CTE 和 `FROM` 子查询。明确拒绝 `JOIN`、`UNION`、递归 CTE、`SELECT *`、未批准函数及视图以外的数据源。模型生成的 SQL 以后只能经此模块执行；它不是通用 SQL 沙箱，也不判断指标口径是否正确。

```python
from app.query_executor import QueryExecutor

with QueryExecutor() as executor:
    result = executor.execute("SELECT COUNT(*) AS n FROM analytics.sales_lines")
    print(result.rows, result.truncated)
```

`result.truncated` 为真时，返回行不能用来声称完整总额、占比或排名。明确的 Top N 需同时传 `top_n=N`，SQL 必须有 `ORDER BY` 和对应的 `LIMIT N`；Top N 结果也不代表完整数据集。

阶段 3 还按确认的计划验证指标聚合、日期边界、分组和排序。当前只接受限定的单层 SQL 形状，SQL 写法虽合法但不符合形状时会拒绝，不能宣称验证了任意 SQL 的业务语义。期间比较暂不和 Top N 或日分组组合。

## 查询内核 CLI

先完成下文的数据库迁移/种子步骤，再初始化 checkpoint（使用 app 角色，不用 reader）：

```powershell
.\.venv\Scripts\python.exe scripts/setup_checkpoints.py
.\.venv\Scripts\python.exe scripts/query_cli.py demo
.\.venv\Scripts\python.exe scripts/query_cli.py ask "2026年9月各渠道实付金额"
.\.venv\Scripts\python.exe scripts/query_cli.py ask "2026年9月销售额"
# 上一条返回 awaiting_clarification；复制其 run_id 后补充口径
.\.venv\Scripts\python.exe scripts/query_cli.py resume <run_id> "期间净收款"
.\.venv\Scripts\python.exe scripts/query_cli.py show <run_id>
```

CLI 默认是 `offline-query-v1` 离线假模型，支持有限中文演示句式，但数值来自真实 PostgreSQL。它以固定参考日期 `2026-09-30` 解释相对时间，日期区间为左闭右开。CLI 的 `--owner` 仅供本机演示，不能当作 HTTP 身份认证；阶段 4 会从服务端会话取得 owner。

在本机 `.env` 配好 Key 后，显式启用真实模型（不要把 Key 发到聊天里）：

```powershell
.\.venv\Scripts\python.exe scripts/query_cli.py --real-model ask "2026年9月实付金额"
.\.venv\Scripts\python.exe scripts/smoke_query_graph.py --real-model
```

查询记录含节点耗时、模型调用数、可用 Token 和错误类别；未返回 usage 时 Token 为 null。网络自动重试关闭，SQL 修复最多一次，模型调用最多五次，澄清最多两轮，节点活动时间预算 90 秒（等待澄清不计入；在节点边界检查）。费用尚未估算。

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
