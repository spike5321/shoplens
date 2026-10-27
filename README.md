# ShopLens

基于 **LangChain + LangGraph** 的电商经营数据分析助手。

> ⚠️ **本项目使用自行生成、固定随机种子的合成电商数据**，用于演示与评测。
> 界面上会明确标注「合成演示数据」，不代表任何真实业务。

## 当前状态：阶段 6（本地交付与面试材料）已完成

[容器启动与验收](docs/delivery.md) · [架构与请求走读](docs/architecture.md) · [三分钟演示](docs/demo.md) · [简历与面试问题](docs/interview.md) · [实测记录](docs/progress.md)

2026-10-27补充：[产品实际配置回归基准](evals/PRODUCTION_BASELINE.md)。沿产品原提示词的80题真实评测完整运行，可回答题68/71正确、应拒绝题8/9正确拒绝；真实浏览器复核5/5。旧题已曝光，属于回归基准；客单价额外分组等4个失败保留，本轮未修改业务逻辑或宣称质量优化完成。

随后完成[客单价分组定向修复](docs/aov-grouping-fix.md)：收紧 SQL 生成上下文，3个原失败问题＋4个预登记问题真实模型7/7通过。此为定向回归，不替代上述80题基准或代表新的总体准确率。对已曝光冻结题迭代后，统一验收需显式加 `--frozen-used-for-iteration`，保留评测防误报检查。

已完成：

- 项目骨架：`backend/` `frontend/` `data/` `evals/` `docs/` `tests/` `scripts/`
- 依赖配置与锁文件、`.gitignore`、无真实密钥的 `.env.example`
- 后端 `GET /healthz`（存活）与 `GET /readyz`（真实探测 PostgreSQL）
- React/TypeScript 查询工作台：示例问题、数据/模型标记、澄清、指标卡片、表格、ECharts 条形/折线图、期间比较、SQL 与节点事件、当前会话历史
- 独立学习样例：LangChain `@tool` 契约 + LangGraph 条件边小图（离线可跑）
- 真实模型冒烟脚手架 `scripts/smoke_real_model.py`：
  `--stub` 自证「请求/响应协议接线正确」（4/4 通过）；
  无 Key 时明确报**「未验证」+ 退出码 2**，不用 stub 结果冒充真实模型。
  **2026-09-24 已用 `deepseek-chat` 真机跑通 4/4**（C1 工具调用 / C2 结构化输出 /
  C3 多轮闭环 / C4 歧义澄清），`create_agent` 预构建 Agent 同时验证通过。
- 本地 PostgreSQL 18.1 免安装实例；另有独立 Docker Compose 交付路径
- 四张业务表、`analytics` 下三个分析视图与数据库级金额约束（Alembic 迁移）
- 九单手工边界样例、固定种子的 3000 单合成数据、五指标 YAML 字典
- 14 个手算问题；独立 Python 原始记录计算与真实 PostgreSQL SQL 交叉核对
- `analytics_reader` 只能读取分析视图，`shoplens_app` 仅获应用状态 schema 权限
- 独立 SQL 执行器：PostgreSQL 方言 AST 允许列表、分析视图/字段检查、reader 连接池、只读事务、3 秒语句超时、200 行保护上限与显式 Top N 区分；真实 PostgreSQL 集成测试覆盖权限、超时、截断和时区边界
- Pydantic 查询计划、LangChain 结构化输出适配与明确标识的离线模型；LangGraph 显式规划、澄清、加载字典、生成 SQL、校验、执行、最多一次修复和确定性结果说明
- 五指标、日/渠道/类别分组、Top N、总体/渠道/类别的两个期间比较；图表配置和变化率由同一查询结果生成
- `app_state` 下的 PostgreSQL checkpoint、sessions/runs/events；HTTP 接口用服务端随机 Cookie 取得 owner，检查读取、澄清、重试和历史归属；原子拒绝重复或过期澄清
- 单进程两项查询并发、短轮询；真实浏览器验证刷新读取、两个会话隔离、后端进程重启后的完成结果/澄清恢复和运行中断
- 40 道开发题、40 道冻结题、24 条固定非法 SQL；独立原始记录评分、持久化调用预算、三配置真实模型对照与完整失败记录
- 2026-10-08 真实查询图冒烟 4/4；完整 240 次评测已运行，结果及至少五个失败案例见 [评测报告](evals/REPORT.md)
- 2026-10-08 干净容器构建/初始化、18 项容器 PostgreSQL 集成测试、容器重建后的历史恢复通过；容器离线页面与本机真实模型浏览器检查各 5/5 通过

**交付范围**：本地可复现演示，提供 PostgreSQL → 迁移/种子 → API → Nginx 的 Compose 健康依赖链，API 镜像不含管理员凭据或评测答案。总体客单价、部分 Top N 和超长区间拒绝存在真实模型失败；默认验收通过也不表示模型全部答对。容器、真实模型浏览器及公网状态分别记录在 `docs/progress.md`，未部署公网。

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

CLI 默认是 `offline-query-v1` 离线假模型，支持有限中文演示句式，但数值来自真实 PostgreSQL。它以固定参考日期 `2026-09-30` 解释相对时间，日期区间为左闭右开。CLI 的 `--owner` 仅供本机演示，不能当作 HTTP 身份认证；HTTP 从服务端会话取得 owner。

在本机 `.env` 配好 Key 后，显式启用真实模型（不要把 Key 发到聊天里）：

```powershell
.\.venv\Scripts\python.exe scripts/query_cli.py --real-model ask "2026年9月实付金额"
.\.venv\Scripts\python.exe scripts/smoke_query_graph.py --real-model
```

查询记录含节点耗时、模型调用数、可用 Token 和错误类别；未返回 usage 时 Token 为 null。网络自动重试关闭，SQL 修复最多一次，模型调用最多五次，澄清最多两轮，节点活动时间预算 90 秒（等待澄清不计入；在节点边界检查）。费用尚未估算。

## 评测

2026-10-08，`deepseek-chat`、温度 0，40 道开发题和 40 道冻结题各比较三配置；冻结题中的 35 道可回答题结果如下：

| 对照配置 | 结果正确 |
|---|---|
| Schema 基线 | 2/35（5.7%） |
| 指标语义与示例 | 31/35（88.6%） |
| 再允许一次修复 | 30/35（85.7%） |

本轮修复没有提升；这些是严格 SQL 形状约束下的对照结果，不直接代表产品默认配置准确率。阶段累计 572 次调用、666,561 Token，低于已授权的 800 次/200 万 Token；未核实价格，不报人民币费用。24/24 固定非法 SQL 阻断不能外推为现实攻击阻断率。

```powershell
# 默认离线模型，实际 SQL 查询 PostgreSQL；输出目录必须全新
.\.venv\Scripts\python.exe scripts/run_evals.py --split dev --out evals/runs/my-offline
# 真实模型必须显式启用，预算账本重跑时继续累计
.\.venv\Scripts\python.exe scripts/run_evals.py --real-model --out evals/runs/my-real
```

评分、冻结规则、预算和复现版本见 [评测说明](evals/README.md)，完整结果、失败分析和原始文件见 [报告](evals/REPORT.md)。冻结结果现在已发布，未来用来迭代必须标记，不能继续称为盲测。

## 浏览器工作台

启动下方的后端和前端后，打开 [本机工作台](http://127.0.0.1:5173)。可以依次试：

1. `9月各渠道实付金额`：同一结果生成表格和条形图。
2. `9月每天的订单数`：按业务日期生成折线图。
3. `9月销售额是多少？`：补充 `实付金额` 后继续同一任务。
4. `9月退款金额与8月比较`：展示两个期间及确定性差额/变化率。

前端默认通过 Vite 的 `/api` 代理访问后端。页面 URL 中的 `run` 支持刷新重新读取；历史最多返回当前会话最近 50 项。Cookie 的访问期限为 7 天，清除 Cookie 或到期后不能再访问旧会话；数据库尚未实现自动清理。

| 接口 | 用途/请求体 |
|---|---|
| `GET /api/session` | 建立匿名会话，返回 CSRF、模型/数据标记和固定参考日期 |
| `POST /api/runs` | `{"question":"9月实付金额"}`，返回 202，后台运行 |
| `GET /api/runs/{run_id}` | 读取状态、结果、SQL 和节点事件 |
| `POST /api/runs/{run_id}/clarify` | `{"text":"实付金额","revision":0}`；原子检查状态、版本与 24 小时有效期 |
| `GET /api/runs` | 当前会话历史 |
| `POST /api/runs/{run_id}/retry` | 失败、中断或澄清到期时，从原问题创建新任务 |

POST 请求均须带当前会话的 `X-CSRF-Token`。Cookie 为服务端安全随机生成的 opaque token，数据库只存其哈希；`HttpOnly; SameSite=Lax; Path=/`，非 local 环境启用 `Secure`，需 HTTPS。所有业务响应禁止缓存，浏览器不能指定 owner 或内部 checkpoint thread_id。

服务仅支持一个 API 进程，以 PostgreSQL advisory lock 防止第二个进程错误接管；同时最多执行两项查询，忙时返回 429。节点输出写入 PostgreSQL，完成/失败/等待澄清只在图调用退出后发布。强制结束进程后，重启把遗留 running 标成 interrupted；已完成结果和等待澄清仍可读取/继续。重试创建新 run，不自动续跑旧任务。

数据日期覆盖在启动时通过 reader 和受限查询从实际现金事件计算，包含 10 月边界订单及跨期退款；当前默认种子是 `[2026-07-01, 2026-10-25)`。参考日期仍固定为 `2026-09-30`。金额在 API 中为精确十进制字符串，表格/卡片使用整数分格式化为元；图表的绘制坐标使用 JS 数值，不能替代表格的精确值。

### 浏览器验收

本机 Windows 使用已安装的 Microsoft Edge 无头浏览器。测试会启动自己的后端，执行真实进程终止/重启，数据库仅清理测试创建的会话，不更改合成业务数据。请先停止正在运行的 ShopLens API 和 5173 端口前端，再运行统一验收。

```powershell
Push-Location frontend
npm run test:e2e
Pop-Location
```

没有 Edge 时，可安装 Playwright Chromium 并选择它：

```powershell
Push-Location frontend
npx playwright install chromium
$env:SHOPLENS_BROWSER_CHANNEL = "chromium"
npm run test:e2e
Pop-Location
```

缺少浏览器时返回 NOT_RUN/2；`--skip-postgres` 同时把数据库测试和浏览器持久化验收标为未执行。已实测浏览器为 Windows/Edge；容器使用 Linux 镜像。统一浏览器验收仍使用离线模型及真实数据库；显式真实模型页面采集与独立评分见 [交付说明](docs/delivery.md)。

## 快速开始：本机开发

容器交付生成凭据只需 Python 标准库，步骤见 [本地容器交付](docs/delivery.md)。以下为保留的 Windows 开发路径。两条路径使用独立数据库，运行浏览器时仅占用一个 5173 端口。

```powershell
# 1) 后端依赖（已锁版本）
python -m venv .venv
# ⚠️ 本机全局 pip 源在当前网络下不可达，且 pip 的配置文件必须只含 ASCII。
#    新建 venv 后先写这一行，否则会出现「pip 跑了但一个包都没装」的静默失败：
Set-Content -Path .venv/pip.ini -Value @('[global]', 'index-url = https://pypi.org/simple') -Encoding Ascii
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt

# 2) 本地 PostgreSQL（免安装开发路径）
.\scripts\pg.ps1 init          # initdb + 启动 + 建库 + 生成 .env
.\scripts\pg.ps1 status

# 2a) 建角色、全部迁移、灌入固定种子数据并交叉核对
.\.venv\Scripts\python.exe scripts/setup_db.py
.\.venv\Scripts\python.exe scripts/seed_data.py
.\.venv\Scripts\python.exe scripts/check_reference.py
.\.venv\Scripts\python.exe scripts/setup_checkpoints.py
# 想单独检查可手算小样例时：
.\.venv\Scripts\python.exe scripts/seed_data.py --fixture-only
.\.venv\Scripts\python.exe scripts/check_reference.py --fixture-only
# 检查后再次运行不带 --fixture-only 的 seed_data.py，恢复 3000 单数据。

# 3) 后端
Push-Location backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Pop-Location
#    http://127.0.0.1:8000/healthz
#    http://127.0.0.1:8000/readyz

# 4) 前端
Push-Location frontend
npm ci
npm run dev                                    # http://127.0.0.1:5173
Pop-Location

# 5) 停止上述开发服务后验收（浏览器测试独占 5173/18004，后端只支持一个进程）
.\.venv\Scripts\python.exe scripts/verify.py
```

`verify.py` 复用 Ruff、pytest 标记、离线 LangGraph 样例、模型协议 stub、
`check_reference.py`、`npm run build` 和 `npm run test:e2e`。每项输出 `PASS` / `FAIL` / `NOT_RUN`；
退出码 `0` 为已请求的检查通过，`1` 为至少一项失败，`2` 为必需检查因环境未执行。
默认**不调用真实模型**，`NOT_RUN` 会显示在汇总中，但不会让默认验收返回 `2`。
若 `npm` 不在 `PATH`，可加 `--npm "C:\path\to\npm.cmd"`。
浏览器验收还会调用 `npm` 启动 Vite，因此也需将该可执行文件所在目录加入当前进程的 `PATH`；本机恢复步骤见 `docs/progress.md` 最新记录。

真实模型只在显式指定时调用；该选项会为子进程选择 `deepseek`，不改变 `.env` 中的离线默认值：

```powershell
.\.venv\Scripts\python.exe scripts/verify.py --real-model
# 如果临时没有 PostgreSQL，可显式跳过它；输出仍标为 NOT_RUN：
.\.venv\Scripts\python.exe scripts/verify.py --skip-postgres
```

## 代码规范基线

```powershell
.\.venv\Scripts\python.exe -m ruff check backend scripts data tests evals
.\.venv\Scripts\python.exe -m ruff format --check backend scripts data tests evals
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
| `docs/delivery.md` | 容器启动、迁移/种子、健康检查、显式模型验证和停止恢复 |
| `docs/architecture.md` | 架构图、数据关系、HTTP 到 SQL 调用链及实现边界 |
| `docs/demo.md` | 三分钟操作脚本和实际页面截图 |
| `docs/interview.md` | 有证据的简历描述、五个深挖问题与走读练习 |
| `data/metrics.yaml` | 五个指标的业务口径、时间字段、粒度、维度及关联规则 |
| `planning/` | 产品、数据口径、架构与验收规范（项目计划的原始文件） |
