# 开发进度与阶段交接

> 记录规则（总提示词第 11 条）：每阶段写**实际执行的命令、输出摘要、未验证项、已知限制**。
> 禁止用「理论可用」代替运行验证。

---

## 阶段 0：环境与最小技术验证

完成日期：2026-09-24

### 1. 已实现的行为和关键文件

| 文件 | 作用 |
|---|---|
| `backend/app/__init__.py` | 应用包与版本号 |
| `backend/app/config.py` | 配置：三套数据库凭据分离、模型服务商开关、服务端硬约束；`safe_dsn()` 脱敏 |
| `backend/app/main.py` | FastAPI 入口：`GET /healthz`（存活）、`GET /readyz`（真实探测 PostgreSQL） |
| `backend/requirements.txt` | 顶层依赖，版本为实测可用版本 |
| `backend/requirements.lock.txt` | 完整依赖锁文件（`pip freeze` 生成，80 行） |
| `frontend/` | Vite 8 + React 19 + TS 7 最小连通页（`npm run build` 已通过） |
| `scripts/pg.ps1` | 本地 PostgreSQL 免安装实例的生命周期管理（init / start / stop / status / psql / reset） |
| `scripts/learning_demo.py` | 阶段 0 独立学习样例：LangChain `@tool` 契约 + LangGraph 条件边小图 |
| `scripts/smoke_real_model.py` | 真实模型冒烟：`--stub` 自证协议层，无 Key 时明确报「未验证」（§8） |
| `tests/` | 42 项测试：配置脱敏、健康接口、工具契约、图分支与有界重试、冒烟脚本自身行为、真实模型开关判定（25 + 13 + 4） |
| `docs/decisions.md` | 版本、服务商、数据库运行方式、目录结构等决策与理由 |
| `docs/references.md` | 第三方参考的借鉴范围与许可政策 |
| `docs/assets/stage0_connectivity.png` | 前端真实渲染截图（后端与数据库双连通） |
| `pyproject.toml` | pytest（`pythonpath = ["backend"]`）与 ruff 配置 |

**没有实现任何业务功能。** 查数、指标口径、SQL 校验与执行、图查询流程、澄清、图表、持久化、评测全部未开始。

### 2. 实际执行的命令与输出摘要

#### 2.1 环境探测

```bash
which -a docker podman docker-compose psql pg_ctl postgres
# 全部 not found —— Docker 与系统 PostgreSQL 都不可用

python --version              # Python 3.13.14（PATH），venv 由 3.13.12 托管运行时创建
node --version                # v22.22.2
npm --version                 # 10.9.7
git --version                 # 2.55.0.windows.3
```

结论：**Docker 不可用**，因此按计划要求记录并落地本地 PostgreSQL 替代路径（见 §3）；
未把任何 SQLite 检查当作 PostgreSQL 集成验收。

#### 2.2 依赖安装

```bash
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
# Successfully installed ...（79 个包）
.venv/Scripts/python.exe -m pip freeze > backend/requirements.lock.txt
```

实测关键版本：`fastapi 0.141.1` / `pydantic 2.13.5` / `sqlalchemy 2.0.54` / `psycopg 3.3.6` /
`langchain 1.4.2` / `langchain-core 1.6.4` / `langgraph 1.2.12` /
`langgraph-checkpoint-postgres 3.1.2` / `langchain-deepseek 1.1.1` / `sqlglot 30.19.0` /
`pytest 9.1.1` / `ruff 0.16.8`。

```bash
cd frontend && npm install && npm run build
# tsc --noEmit 通过；vite v8.3.1 built in 2.64s
# dist/assets/index-*.js 224.66 kB │ gzip: 70.53 kB
```

#### 2.3 本地 PostgreSQL

```bash
curl -L -o .local/downloads/pgsql-binaries.zip \
  https://get.enterprisedb.com/postgresql/postgresql-18.1-1-windows-x64-binaries.zip
# HTTP 200, 334,929,717 bytes
unzip -q downloads/pgsql-binaries.zip -d .local
./.local/pgsql/bin/postgres.exe --version   # postgres (PostgreSQL) 18.1
```

```powershell
./scripts/pg.ps1 init
```

实际连接验证（psql 直连）：

```
server_version | PostgreSQL 18.1 on x86_64-windows, compiled by msvc-19.44.35219, 64-bit
session_tz     | UTC
db             | shoplens
listen_addresses | 127.0.0.1     port | 55432
```

#### 2.4 三项验收

| 验收项 | 命令 | 结果 |
|---|---|---|
| 后端启动 | `cd backend && python -m uvicorn app.main:app --port 8000` | ✅ 通过 |
| 前端构建 | `cd frontend && npm run build` | ✅ 通过 |
| 离线图执行 | `python scripts/learning_demo.py` | ✅ 通过（4 个场景全部符合预期） |

真实 HTTP 响应：

```json
GET /healthz
{"status":"ok","service":"shoplens-api","version":"0.1.0","env":"local",
 "model_provider":"offline","real_model_key_configured":false}

GET /readyz
{"service":"shoplens-api","database":{"host":"127.0.0.1","port":55432,"dbname":"shoplens",
 "dsn":"postgresql://postgres:***@127.0.0.1:55432/shoplens","status":"ok",
 "latency_ms":306.73,"server_version":"PostgreSQL 18.1","error":null,"timezone":"UTC"}}
```

密钥泄露检查：`/healthz` 与 `/readyz` 的完整响应中不含数据库密码，也不含任何 `sk-` 前缀字符串。

```bash
.venv/Scripts/python.exe -m pytest -q       # 25 passed（阶段 0 当时的点数；§8 追加后为 42 passed）
.venv/Scripts/python.exe -m ruff check backend scripts tests   # All checks passed!
```

前端页面在无头 Edge 中真实渲染（截图见 `docs/assets/stage0_connectivity.png`）：
后端显示「已连通」，数据库显示「已连通（322.67 ms）」，模型服务商显示「offline · 离线假模型」，
真实模型 Key 显示「未配置（真实模型检查未验证）」。

### 3. 本次搭建中修掉的 4 个真实缺陷

这些都**不是**理论风险，是跑起来才暴露的，值得记住：

1. **pip 配置文件含非 ASCII 字符会被整份丢弃。**
   `.venv/pip.ini` 里写了中文注释，pip 报 `Configuration file contains invalid cp936 characters`
   并退回全局源（当前网络下不可达），表现为「pip 命令执行了但一个包都没装」。
   → 修法：该文件只写 ASCII。

2. **Windows PowerShell 5.1 按 ANSI(cp936) 读取无 BOM 的 `.ps1`。**
   `scripts/pg.ps1` 里的中文消息被打乱，其中一处字节序列直接让脚本解析失败。
   → 修法：脚本存为 **UTF-8 with BOM**（`EF BB BF`），PS 5.1 与 PS 7 都能正确识别。

3. **`.env` 用相对路径会随启动目录漂移。**
   `SettingsConfigDict(env_file=".env")` 是相对**进程工作目录**解析的。
   从 `backend/` 启动 uvicorn 时读不到仓库根目录的 `.env`，
   `/readyz` 报 `fe_sendauth: no password supplied` —— 配置明明填了却连不上库。
   → 修法：`_REPO_ROOT = Path(__file__).resolve().parents[2]`，用绝对路径。
   → 加了回归测试 `test_env_file_is_anchored_to_repo_root_not_cwd`。

4. **Pydantic v2 生成的 JSON Schema 默认不禁止多余字段。**
   `model_json_schema()` 里**没有** `additionalProperties: false`，默认行为是**忽略**多余键。
   后果：模型把参数名写错（例如 `refund` 而不是 `refund_cents`）不报错，
   工具拿默认值照常返回一个「看起来正常」的数字。对查数系统这是最危险的一类 bug。
   → 修法：给 `args_schema` 显式加 `ConfigDict(extra="forbid")`，
   并用两个测试固定住：schema 必须含 `additionalProperties: false`；
   传 `currency` 必须抛 `ValidationError`。

另外两个环境层面的坑记录在 `docs/decisions.md`：pip 镜像不可达、Docker 不可用。

### 4. 未验证项（明确列出，不假装完成）

> 2026-09-24 晚更新：Key 已配置，前两项**已由未验证转为已验证**，证据见 §8.4。

| 事项 | 状态 | 说明 |
|---|---|---|
| 真实模型工具调用与结构化输出 | **✅ 已验证** | 2026-09-24，`deepseek-chat`，C1–C4 **4/4 通过**，退出码 0。证据与耗时见 §8.4。 |
| `create_agent` 预构建 Agent | **✅ 已验证** | 同日，消息条数 6，最终回答 `1000000 分（¥10000.00）`。见 §8.4。 |
| Docker Compose | **未验证（计划内、非欠账）** | Docker 未安装。计划把它列为**阶段 6 第 1 条交付**，且原文写明「Docker 不可用则明确未验证」（`planning/IMPLEMENTATION_PROMPTS.md:155`）。本机为 Windows 11（build 26200），装 Docker Desktop 需管理员权限 + WSL2/Hyper-V，属阶段 6 前的一次性决策。 |
| Token 与费用统计 | **未采集** | 冒烟脚本没有统计 `usage`。阶段 3 冻结评测配置时必须补上，否则「运行成本说明」这条交付物无据可依。 |
| LangGraph checkpointer 持久化 | **未实现** | 阶段 3/4 的范围。依赖已装（`langgraph-checkpoint-postgres 3.1.2`），但没有跑过。 |
| PostgreSQL 权限角色 / 超时 / 只读事务 | **未实现** | 阶段 2 的范围。目前只有一个 `postgres` 超级用户，`app` / `reader` 角色在 `.env` 里留空待建。 |
| Python 3.12 | **未采用** | 计划写 3.12，本机只有 3.13。全部依赖以官方 wheel 直接支持 3.13，见 `docs/decisions.md` §1。 |

### 5. 三个必须理解的知识点

#### 知识点 1：LangChain 工具的核心产物是「契约」，不是函数

`@tool` 真正产出的是三样东西：**名字 + 描述 + 参数 JSON Schema**。
模型只能看到这三样，它据此决定调不调用、参数怎么填。
函数体是给**你的代码**执行的，模型碰不到。

所以 `description` 不是注释，是提示词的组成部分；
`args_schema` 不是类型标注，是模型与代码之间唯一的安全边界。
本阶段实测证明：光有 Pydantic 类型还不够 ——
不加 `extra="forbid"` 时，模型填错参数名会被**静默忽略**，
必须显式收紧契约才能让错误暴露出来。

#### 知识点 2：LangGraph 的「状态 + 条件边」是两件独立的事

- **状态**决定数据怎么合并。默认 reducer 是**覆盖**；
  写成 `Annotated[list[str], add]` 才会累加。
  `scripts/learning_demo.py` 里 `log` 字段用 `add`，所以能保留完整节点轨迹；
  `repair_count` 不用 reducer，所以每次覆盖 —— 正好符合「计数器」的语义。
- **条件边**决定下一步去哪。`add_conditional_edges(node, fn, mapping)` 里的 `fn`
  返回的是**目标节点名**，和状态合并完全无关。

最容易被忽略的一点：**图的循环必须有界**。
`validate -> repair -> validate` 这条回边本身是没有终止条件的，
终止条件是**代码**给的：`if repair_count < MAX_REPAIR_COUNT`。
把 `repair_count` 放在状态里、由服务端节点递增，模型就无法通过任何输出把它改小。
这就是「不要靠系统提示词保证安全」在 LangGraph 里的具体落法。

#### 知识点 3：LangChain 组件 / 自定义 LangGraph / `create_agent` 的分工

官方把三者定位成三层：

| 层 | 是什么 | 谁决定下一步 | 适合什么 |
|---|---|---|---|
| LangChain 组件 | 零件：模型、消息、工具、结构化输出 | 不适用（没有流程概念） | 所有项目的地基 |
| 自定义 LangGraph | 编排：状态 + 节点 + 边 | **你的代码**（条件边函数） | 流程固定、约束强、需要审计 |
| `create_agent` | 整车：模型 + 工具 + 提示词，循环由框架执行 | **模型**自主决定 | 探索型任务、工具集开放 |

本项目选前两层、**不用** `create_agent` 跑主流程，原因很具体：
「SQL AST 校验」「只读数据库角色」「修复次数上限」「owner_id 不可被模型改写」
这四件事必须由代码和数据库强制执行。
把主流程交给 `create_agent` 的自由循环，等于把校验节点的位置交给模型决定。

面试时如果被问「为什么不用现成的 Agent」，答案就是这句：
**框架替你管循环，但也意味着循环里每一步的权限边界不再由你控制。**

### 6. 一个你可以亲手改动并观察结果的小练习

**目标**：亲眼看到「修复次数上限」是代码在管，不是提示词在管。

打开 `scripts/learning_demo.py`，找到顶部这一行：

```python
MAX_REPAIR_COUNT = 1
```

分三步做：

1. **先跑一遍原样**，记下 `bad-bad` 场景的结果：

   ```bash
   python scripts/learning_demo.py | grep -A 8 "修复上限确实生效"
   ```
   预期：`最终状态: failed`，`修复次数: 1`，`validate` 出现 2 次。

2. **把 `MAX_REPAIR_COUNT` 改成 `2`**，再跑一次。
   预期变化：`repair` 节点跑两次（第一次修掉 `nonexistent_column`，第二次修掉 `another_bad_column`），
   `validate` 出现 3 次，最终状态变成 **`succeeded`**。

3. **再把上限改回 `1`，但把 `node_repair` 里这行**

   ```python
   sql = sql.replace(col, "paid_cents", 1)
   ```
   改成一次修掉所有坏列名（比如用循环把 `DISALLOWED_COLUMNS` 全替换掉）。
   预期：在 `MAX_REPAIR_COUNT = 1` 不变的前提下，结果也变成 `succeeded` ——
   因为一次修复就修干净了。

**这个练习要说明的事**：上限控制的是「最多重试几次」，不控制「一次能修多少」。
真实项目里 `node_repair` 会让模型重新生成一整段 SQL，
所以「一次修复能否修完」是不可控的 —— 这也正是必须有硬上限的原因。

改完记得跑 `python -m pytest tests/learning -q`，
你会发现有几条测试会失败 —— 那正是测试在保护 `MAX_REPAIR_COUNT = 1` 这个约定。
这就是「测试固定的是行为，不是实现」的例子。

### 7. 交给阶段 1 的交接记录

**可以直接复用的东西**

- Python 环境：`D:\Project_02\.venv`（Python 3.13.12，依赖已按 `backend/requirements.lock.txt` 锁定）。
  注意 `pip.ini` 必须是纯 ASCII。
- 配置读取：`from app.config import get_settings`，
  `settings.dsn("admin"|"app"|"reader")` 与 `settings.safe_dsn(role)` 已就绪。
  三套凭据字段已定义，阶段 1 需要真正 `CREATE ROLE` 并把密码写回 `.env`。
- 数据库：`./scripts/pg.ps1 init|start|stop|status|psql`，
  实例在 127.0.0.1:55432，数据目录 `.local/pgdata`，服务端时区 UTC。
  覆盖配置在 `.local/pgdata/shoplens.conf`（被 `postgresql.conf` 末尾 include）。
- 测试骨架：`tests/`，`pyproject.toml` 里 `pythonpath = ["backend"]`，
  标记 `@pytest.mark.integration` 已注册，用于需要真实数据库的测试。
- 学习样例里的两个工具（`net_receipt_cents` / `format_cents_as_cny`）示范了正确的
  `args_schema` + `extra="forbid"` 写法，阶段 1 的参考计算脚本可以沿用这个模式。

**阶段 1 需要新建的东西**

- `data/` 下的固定种子生成器与手工 fixture。
- `products` / `orders` / `order_items` / `refunds` 四张业务表 + `analytics` 下的分析视图
  （迁移用 Alembic 管理，接 `settings.dsn("admin")`）。
- `analytics_reader`（只读视图）与 `shoplens_app`（读写应用状态）两个角色 —— 这是阶段 2 的前置条件。
- 五个指标的 YAML 字典，以及独立于待测 SQL 的确定性参考计算脚本。

**阶段 1 绝对不要做的事**

- 不要调用任何 LLM。
- 不要把标准答案写进任何会被模型读到的文件。
- 不要因为某个边界样例算不对就把它删掉。

**当前环境状态提示**：本文件写完后，本地 PostgreSQL 实例、后端 uvicorn、前端 preview
三个进程都可能已被回收。阶段 1 开始时先执行：

```powershell
./scripts/pg.ps1 status      # 未运行则 ./scripts/pg.ps1 start
```

---

### 8. 补充：把「未验证」变成「可验证」（2026-09-24 追加）

阶段 0 列出的未验证项，有三类原因：**缺 Key**、**缺 Docker**、**还没到那个阶段**。
这一节只处理第一类里能自己解决的部分 —— 做法不是「假装验证」，而是**把协议层和真实模型层切开**。

#### 8.1 思路：先证明「接线对」，再证明「模型行」

一个容易自欺的做法是：没有 Key，就写个假模型跑通，然后宣称「真实模型已验证」。
另一种是：干脆什么都不做，一直挂着「未验证」。
两条路都不好。正确的切法是把问题拆成两个独立问题：

| 问题 | 能不能自己答 | 怎么答 |
|---|---|---|
| 我们发出去的请求格式对不对？返回的消息我们解析得对不对？ | **能** | 起一个本地 stub 服务端，冒充 OpenAI 兼容接口，检查**出站请求体** |
| deepseek 这家服务商真的能按预期回 tool_calls / 结构化输出吗？ | **不能** | 只能等 Key。缺 Key 时必须**明确报未验证** |

#### 8.2 实际执行的命令与输出

```bash
# 模式一：协议层自证（不联网，不花钱）
.venv/Scripts/python.exe scripts/smoke_real_model.py --stub
```

```
[PASS] C1 工具调用
       tool_call=net_receipt_cents({'paid_cents': 1234500, 'refund_cents': 234500})
       · 出站 tools[0].function.name = net_receipt_cents
       · 出站 schema 含 additionalProperties=false
[PASS] C2 结构化输出
       metric_id=paid_amount group_by=['channel'] 区间=2026-09-01~2026-10-01
[PASS] C3 多轮 tool_call 闭环
       工具结果 1000000 分已回喂，最终回答长度 15
[PASS] C4 歧义澄清
       追问：你说的「销售额」是指实付金额，还是期间净收款？
结果：4/4 通过
退出码=0
```

```bash
# 模式二：真实模型（无 Key 时的诚实路径）
.venv/Scripts/python.exe scripts/smoke_real_model.py
```

```
真实模型冒烟：**未验证**
  原因：未配置 SHOPLENS_DEEPSEEK_API_KEY。
  这不是失败，是「没做」。本脚本不会用 stub 的结果冒充真实模型结果。
退出码=2
```

四个检查项不是随手挑的，对应阶段 3 会真正依赖的四件事：
**C1 工具调用**（查数流程靠它）、**C2 结构化输出**（意图/计划解析靠它）、
**C3 多轮闭环**（工具结果必须能回喂给模型）、**C4 歧义澄清**（「销售额」到底指哪个指标）。

#### 8.3 顺带修掉的两个工程缺陷

1. **`conftest` / `app` 被 ruff 当成第三方包。**
   不声明的话，`backend/app`（自研代码）会跟 `fastapi`、`pytest` 混在同一段，
   导入顺序取决于 ruff 的启发式判断，会随版本漂移。
   → 修法：`pyproject.toml` 加 `[tool.ruff.lint.isort] known-first-party = ["app", "conftest"]`。
   代价是 `tests/test_healthz.py` 需调整两行（外部依赖在前、自研在后）。

2. **`# noqa: N802` 是死指令。**
   `N` 规则集没启用，这条 noqa 永远不生效，反过来被 `RUF100` 报为无用指令。
   → 修法：去掉 noqa，把「为什么必须是 `do_POST`」写成普通注释。
   这类注释要留住 —— 它是给下一个人看的，不是给 linter 看的。

同时把 `ruff format` 的基线拉平（6 个文件重排换行，纯格式，无逻辑变化）。

复检结果：

```bash
.venv/Scripts/python.exe -m pytest -q                              # 42 passed（阶段 0 的 25 项 + 冒烟脚本 13 项 + 真实模型开关判定 4 项）
.venv/Scripts/python.exe -m ruff check backend scripts tests        # All checks passed!
.venv/Scripts/python.exe -m ruff format --check backend scripts tests  # 11 files already formatted
```

新增测试文件 `tests/test_smoke_real_model.py` 测的是**工具自身的行为**，理由是：
冒烟脚本如果自己坏了（stub 逻辑写错、或没 Key 时静默返回 0），
它会给出**假的通过信号** —— 那比没有冒烟更糟。
所以固定住三件事：stub 必须真的 4/4 通过；stub 服务端两轮行为确定性；
无 Key 必须返回退出码 2 并明确否认「用 stub 冒充真实模型」。

#### 8.4 真实模型验证结果（2026-09-24，已完成）

Key 已加入项目根目录 `.env`。用**一次性环境变量覆盖**跑，不动项目默认值：

```bash
SHOPLENS_MODEL_PROVIDER=deepseek .venv/Scripts/python.exe scripts/smoke_real_model.py
```

```
模型冒烟：real:deepseek model=deepseek-chat

[PASS] C1 工具调用
       tool_call=net_receipt_cents({'paid_cents': 1234500, 'refund_cents': 234500})
       · 耗时 846 ms
[PASS] C2 结构化输出
       metric_id=paid_amount group_by=['channel'] 区间=2026-09-01~2026-09-30
       · 耗时 924 ms
[PASS] C3 多轮 tool_call 闭环
       工具结果 1000000 分已回喂，最终回答长度 75
       · 最终回答：净收款为 **1,000,000 分**（即 10,000.00 元）。
[PASS] C4 歧义澄清
       追问：「销售额」想指哪一种口径？是实付金额（用户实际支付的钱），
             还是期间净收款（扣除退款后的收款）？另外「上月」请确认具体范围
       · 耗时 1525 ms

结果：4/4 通过   退出码=0
```

`create_agent` 预构建 Agent 同时验掉：

```bash
SHOPLENS_MODEL_PROVIDER=deepseek .venv/Scripts/python.exe scripts/learning_demo.py
# 第 3 部分：调用成功，消息条数：6
# 最后一条：1234500 分 − 234500 分 = 1000000 分，即 **¥10000.00**。
# 退出码=0（provider=offline 时则打印「跳过」，见下文第 3 条缺陷）
```

**登记信息**（供阶段 3 冻结评测配置时对照）

| 项 | 值 |
|---|---|
| 日期 | 2026-09-24 |
| 模型 ID | `deepseek-chat`（`SHOPLENS_DEEPSEEK_MODEL`） |
| 服务商 | DeepSeek，`base_url=https://api.deepseek.com/v1` |
| 通过项 | C1 工具调用 / C2 结构化输出 / C3 多轮闭环 / C4 歧义澄清，**4/4** |
| 耗时 | 单次请求 846–1570 ms |
| Token 与费用 | 未采集（脚本未统计 usage）。属阶段 3 评测时要补的字段 |

**为什么用一次性覆盖而不是改 `.env` 默认值**：`offline` 是项目默认，
保证离线开发与测试不联网、不花钱；真实模型要**显式**表态才启用。
脚本和样例都刻意不替你做这个决定 —— 免得跑个测试就把 token 花掉。

#### 8.5 真机跑出来的三个新缺陷（都是「口径不一致」这一类）

跑真机才暴露的问题，比 stub 阶段暴露的更有价值：

1. **冒烟脚本的出站断言在真实模式下必然失败（自造假阴性）。**
   早先真实模式下用一个空的 `_RecordingList` 冒充抓包结果，
   于是 C1/C3 里「检查出站请求体」那一段永远拿不到数据、必然判 FAIL。
   表现是：真实模型明明正确返回了 `tool_call`，却被记成 FAIL ——
   而我可能把它当成「模型不行」写进文档，去修一个根本不存在的问题。
   → 修法：`requests` 在真实模式下就是 `None`；
   断言分成**语义组**（两种模式都查）与**出站组**（仅 stub 可查），
   出站组在真实模式下如实标「本模式不覆盖」，**不判失败**。
   → 加 5 条回归测试，其中 3 条验证「stub 模式下出站断言仍然生效」，防止矫枉过正。

2. **C3 的测试用例自身有歧义，被 stub 的顺从掩盖了。**
   提示词原本是「帮我算一下净收款。」——**没给数字**。
   真实模型拒绝凭空编数字、于是不调工具，被判成失败；
   而 stub 无脑回 `tool_call`，反而"通过"了。
   **测试用例里有歧义时，要先修用例，不能去修模型。**
   → 修法：提示词给出两个具体数字，让「调工具」成为明确正确的下一步。

3. **同一份配置两处读，口径不一致。**
   `scripts/learning_demo.py` 直接读 `os.environ.get("SHOPLENS_DEEPSEEK_API_KEY")`，
   **绕过了 `.env`**。于是同一个 Key：冒烟脚本读得到、学习样例读不到，
   被报成「未配置 SHOPLENS_DEEPSEEK_API_KEY」——一个完全不存在的假问题。
   同一个文件还缺 `sys.path` 引导，`python scripts/learning_demo.py` 会
   `ModuleNotFoundError: No module named 'app'`（Python 只把**脚本所在目录**放进
   `sys.path`，不是当前工作目录；pytest 靠 `pythonpath` 绕过了，脚本得自己解决）。
   → 修法：统一走 `get_settings()`；补 `sys.path` 引导。
   → 顺带把「跳过（provider=offline）」和「未验证（没有 Key）」分成两种不同输出：
   前者是「你不让我跑」，后者是「我没法跑」，混在一起会让人去查错方向。
   → 加 4 条回归测试固定住这三种判定。

---

## 阶段 1：数据、口径与参考答案

完成日期：2026-09-24 至 2026-09-25。本阶段未调用项目模型接口或外部 LLM 服务生成业务答案。

### 已实现

- `backend/migrations/versions/0001_business.py` 和 `0002_guard_item_updates.py`：Alembic 管理四张业务表、主外键、正金额/状态约束、订单明细合计和退款累计约束；发布 `analytics.sales_lines`、`analytics.refund_events`、`analytics.cash_events`。
- `scripts/setup_db.py`：建立 `analytics_reader` 与 `shoplens_app`，凭据保存在忽略提交的 `.env`；授予 reader 分析视图只读权限和 app 应用状态 schema 权限。重复执行不会重新生成已有密码。
- `data/dataset.py`：九单手工边界样例，另用固定种子扩展到 3000 单。覆盖跨类别、一单两明细且同一明细分两次退款、跨月退款、全额退款、未支付取消、零成交日、月末/零点边界、两个订单金额相同。
- `scripts/seed_data.py`：专用演示数据库中事务性替换数据，重复运行不会叠加。`--fixture-only` 可单独装载手工样例。
- `data/metrics.yaml`：五指标的定义、粒度、单位、时间字段、可用维度和视图；明确退款按完成时间计入、类别订单数不能相加、总体客单价不能平均类别客单价。
- `data/reference.py`：直接遍历四类原始记录计算指标，避免拿待测 SQL 当唯一标准答案。`manual_cases()` 里预先列明 14 个可手算的预期值。
- `scripts/check_reference.py`：只读角色运行固定 SQL，交叉核对 Python 原始记录计算和分析视图；同时核对 `cash_events` 的净收款。

### 实际执行与结果

```text
scripts/pg.ps1 start                         PostgreSQL 18.1 启动成功
python scripts/setup_db.py                 角色、迁移、授权完成；重复执行成功
python scripts/seed_data.py --fixture-only  3 商品 / 9 订单 / 9 明细 / 6 退款
python scripts/check_reference.py --fixture-only
                                             14/14 可手算案例与 Python/SQL 一致
python scripts/seed_data.py                 43 商品 / 3000 订单 / 5641 明细 / 685 退款
python scripts/check_reference.py           80/80 期间×类别×渠道组合一致
python scripts/seed_data.py                 再次运行，仍为 3000 订单
python -m pytest -q                        50 passed（含真实 PostgreSQL 权限/约束测试）
python -m ruff check backend scripts data tests  All checks passed!
python -m ruff format --check backend scripts data tests  All files formatted
```

真实 PostgreSQL 测试确认：reader 可读分析视图而不可读业务表，也没有分析视图 DELETE 权限；超过商品明细金额的退款被拒；支付订单总额与明细不符时，延迟约束在事务结束前拒绝。金额聚合按整数分精确比较，客单价作为比值用绝对误差 `1e-9` 比较。

### 为什么不能直接 JOIN

手工订单 1 在 7 月 31 日 23:59 支付 **100 元**：耳机 60 元、水杯 40 元。耳机后来分两笔退款，8 月 10 元、9 月 20 元；水杯 9 月退款 40 元。若把 `orders → order_items → refunds` 展开后 `SUM(orders.paid_cents)`，该订单会出现三行，错误地算成 **300 元**。即使 `SUM(DISTINCT orders.paid_cents)`，8 月两个不同订单各付 100 元又会被错误合成 100 元。

本项目的 `sales_lines` 每行是一条已支付明细，`refund_events` 每行是一笔成功退款。支付按支付时间汇总，退款按完成时间单独汇总，再用聚合后的结果计算净收款。7 月订单的 8 月、9 月退款因此分别进入对应月份，不把支付额复制三遍。

### 未验证与限制

- Docker Compose 仍未验证，本机没有 Docker；阶段 6 再处理。
- `analytics_reader` 的数据库权限已验证，但模型生成 SQL 的 AST 允许列表、只读事务、超时与行数保护属于阶段 2，当前未实现。
- `shoplens_app` 的专用 schema 权限已建，应用状态表和图 checkpoint 属于阶段 4，未实现。
- 3000 单为固定种子合成演示数据，唯一一笔 10 月 1 日订单是边界样例；不能作为真实业务统计或性能证据。
- 阶段 0 原有学习脚本在 Windows 默认 GBK 下打印 `¥` 会抛编码错误，本阶段给脚本输出设置了可回退编码，原有测试恢复通过。

### 交给阶段 2

先运行 `scripts/pg.ps1 start`、`scripts/setup_db.py`、`scripts/seed_data.py`。阶段 2 的 SQL 执行器只能使用 `settings.dsn("reader")`；解析并限制单条只读 SQL、允许的分析视图/字段/函数、只读事务、3 秒超时及 200 行上限。参考脚本里的固定 SQL 是校验基线，不是可直接暴露给用户的通用查询接口。执行器验证时保留本阶段的原始记录参考计算与手工边界样例。

---

## 开发导航与统一验收（2026-09-25）

新增根目录 `AGENTS.md`：指向 README、计划、最新进度、指标字典与测试，记录合成数据、密钥、数据库权限和如实验收规则。新增 `scripts/verify.py`，按项目现有命令分别执行 Ruff 检查、离线 pytest、离线图样例、协议 stub、真实 PostgreSQL 集成 pytest/参考结果核对和 `npm run build`。真实模型默认不调用，只在 `--real-model` 时运行。状态分别是 `PASS`、`FAIL`、`NOT_RUN`；退出码 0/1/2 分别表示所请求检查通过、存在失败、必需检查因环境未执行。

实际检查：

```text
python scripts/verify.py
  静态、离线、PostgreSQL 均 PASS；npm 不在 PATH，前端 NOT_RUN；真实模型默认 NOT_RUN；退出码 2。

python scripts/verify.py --npm C:\Users\spike\.workbuddy\binaries\node\versions\22.22.2-3\npm.cmd
  Ruff 2项、离线 3项、PostgreSQL 2项、前端 1项，共 8 PASS；真实模型 NOT_RUN；退出码 0。
  离线 pytest 47 passed；PostgreSQL 集成 pytest 3 passed，参考结果核对通过。

python scripts/verify.py --skip-postgres --npm <node.exe（故意给错可执行文件）>
  前端 FAIL，PostgreSQL 两项显式 NOT_RUN；退出码 1，验证失败不会被跳过状态掩盖。
```

环境记录：本次命令行的 `PATH` 有 Node 但没有 npm，已有的 npm 安装可通过 `--npm` 指定，因此前端构建最终已实际通过。`scripts/pg.ps1 status` 在当前受限环境报告未运行，但 reader 真实连接、3 项集成测试及 SQL 对照都通过；统一脚本以实际数据库连接探测为准。真实模型未请求，因此没有调用或产生本次模型验证结论。

---

## 阶段 2：受限 SQL 执行器

完成日期：2026-10-02。基于阶段 1 的固定种子合成数据和真实 PostgreSQL 18.1 验证；没有调用真实模型。

### 已实现

- `backend/app/query_executor.py`：用 SQLGlot 的 PostgreSQL 方言解析单条 `SELECT`，按 AST 节点允许列表和每个查询块的来源、字段检查。仅允许 `analytics.sales_lines`、`analytics.refund_events`、`analytics.cash_events`，CTE 与 `FROM` 子查询也逐层检查；未知语法、函数和类型转换一律拒绝。
- `QueryExecutor` 只取得 `settings.dsn("reader")`。查询放在 PostgreSQL 只读事务里，以 `SET LOCAL` 等价的 `set_config(..., true)` 设置事务级 `statement_timeout`；连接池大小有限。服务端追加 `max_rows + 1` 的保护 LIMIT，检测截断后仅返回 `max_rows` 行。
- 显式 Top N 必须由调用方给出 `top_n`，SQL 同时包含 `ORDER BY` 与匹配的 `LIMIT`，且 N 不超过服务端行数上限。结构化结果含 `truncated`、`requested_top_n` 和 `complete_for_full_aggregate`；截断与 Top N 都不能充当完整数据集聚合。
- `tests/test_stage2_query_executor.py`：离线检查合法聚合、CTE、子查询与各类非法 SQL；真实数据库检查结果、空集、行数截断、Top N、reader 对业务表和状态 schema 的拒绝、通过锁等待触发的实际 100 ms 语句超时，以及 10 月 1 日零点的 `+08` 时间边界。

### 实际执行与结果

```text
scripts/pg.ps1 start                         PostgreSQL 18.1 已启动
python -m pytest -q -p no:cacheprovider tests/test_stage2_query_executor.py -m 'not integration'
                                             25 项通过
python -m pytest -q -p no:cacheprovider tests/test_stage2_query_executor.py -m integration
                                             3 项通过
python scripts/verify.py                     Ruff 2 项、离线 3 项、PostgreSQL 2 项、前端构建 1 项：
                                             PASS=8 FAIL=0 NOT_RUN=1，退出码 0
                                             NOT_RUN 仅为未请求的真实模型冒烟
```

独立测试还确认：`SELECT COUNT(*)` 得到 5641 条支付明细，合法 CTE 的类别数为 4，`2026-10-01 00:00:00+08` 起有 1 条边界支付明细。超时测试由 admin 事务持有业务表排他锁，reader 查询在设置的 100 ms 超时后返回 `timeout`，释放锁后再查询成功。完整阶段 1 参考计算仍通过。

### 限制与未验证

- 当前明确不支持 `JOIN`、`UNION`、递归 CTE、`SELECT *`、关联/标量子查询、OFFSET、任意函数和未经审查的类型转换；因此范围是有限的项目查询子集，不是通用 SQL 沙箱。
- AST 允许列表负责语法与来源边界，不保证指标定义正确。阶段 3 必须按 `data/metrics.yaml` 规划时间、维度和口径，并继续以独立参考结果核验。
- 尚未接入 LangGraph、模型或 HTTP 查询接口；真实模型本阶段未请求、未验证。Docker Compose 和持久化仍分别属于后续阶段。

### 必须理解的 3 个知识点

1. `SELECT` 开头不足以证明安全：CTE、子查询与函数可能访问其他对象或产生副作用，因此逐节点检查、reader 角色和只读事务必须共同生效。
2. `LIMIT 200` 只限制返回量，不保证查询及时结束或结果完整。数据库超时限制执行时间；多读一行才能知道结果是否被保护上限截断。
3. 用户明确的 Top N 和执行器截断语义不同。前者需要确定性排序与匹配的 N；两者的返回行都不能当作全量数据计算总体指标。

### 可亲手修改的小练习

把 `tests/test_stage2_query_executor.py` 中行数上限测试里的 `sql_max_rows=2` 改成 3，运行该测试，观察普通查询返回 3 行且仍标 `truncated`，再改回 2。这个练习只改测试参数，不改变生产约束。

### 交给阶段 3

用 `QueryExecutor` 的上下文管理器执行模型候选 SQL；把 `QueryErrorCode` 作为图的可见错误类别，至多修复一次。计划节点须从 `data/metrics.yaml` 选指标与业务时间区间；模型不能覆盖 reader 凭据、SQL 验证、行数上限或超时。解释数字前先检查 `truncated` 与 `complete_for_full_aggregate`。

提交与推送：阶段 2 代码及本交接主体已提交为 `41fd792`（`feat: add constrained SQL query executor`）；`git push origin main` 成功，远端从 `4a4f9ce` 更新至 `41fd792`。本段推送回执随后单独提交。

---

## 阶段 3：LangChain + LangGraph 查询内核

实现与验收日期：2026-10-02。离线流程、协议接线与真实 PostgreSQL 已验证；当前 `.env` 没有模型 Key，阶段 3 真实模型验证未执行。

### 已实现行为和关键文件

- `backend/app/query_plan.py`：禁止额外字段的 Pydantic 查询计划、SQL 草稿与澄清契约；五指标、日期、分组、Top N、两个期间比较。候选 SQL 通过 AST 检查后，还与根据计划生成的业务形状核对，检查聚合表达式、视图、日期边界、分组、排序。该过程不读取手算或评测答案。
- `backend/app/query_model.py`：`DeepSeekQueryModel` 使用 LangChain 的结构化工具输出并取得 raw usage；`OfflineQueryModel` 明确标为 `offline-query-v1`，只支持有限中文演示句式。输入只含用户问题、已确认计划、指标字典和公开 Schema；参考计算不进入模型上下文。SQL 故障注入仅在测试/demo 适配器里启用。
- `backend/app/query_graph.py`：显式节点 `plan → context → draft → validate → execute → summarize`，澄清经过 `waiting → clarify → plan`，失败最多进入 `repair` 一次。owner、run_id、内部 thread_id、修复计数、模型调用与时间预算由服务端决定。节点事件记录 run_id、耗时、调用数、可用 Token 和错误类别，不输出内部思维链或凭据。
- 文字说明、变化率和类型化柱状/折线图数据由查询结果确定性计算。金额及比值保留十进制字符串；零分母返回空值，基期零值不计算百分比；截断时停止生成图表及全量比较。Top N 使用稳定维度排序打破并列，明确不能当作总体。
- `backend/app/query_checkpoint.py` 和 `scripts/setup_checkpoints.py`：app 凭据在 `app_state` 创建及使用官方 saver 的版本化表。reader 不能访问 checkpoint。澄清中断前不调用模型或执行 SQL，恢复后的事件不会重复登记等待步骤。
- `scripts/query_cli.py`：本机 ask/resume/show/demo；`QueryService` 是后续 API 可复用的内核，读取和继续均检查 owner，内部 thread_id 由服务端 run_id 派生。CLI owner 参数仅供本机演示，不是 HTTP 身份认证。
- `scripts/smoke_query_graph.py`：预留真实模型正常查询、澄清恢复、跨期退款、明确拒绝四场景；在脚本外层以独立原始记录计算核对答案。真实 Key 缺失返回 NOT_RUN/2；`scripts/verify.py --real-model` 同时调用原有能力冒烟和新的图冒烟。

### 实际命令与输出

```text
python scripts/setup_checkpoints.py
  Checkpoint tables ready in app_state; no analytics credentials used.
python scripts/query_cli.py demo
  normal / clarify / cross_period / repair / repair_exhausted / unsupported：6/6 符合预期
  repair 成功和耗尽两例 repair_count 都是 1；非法候选没有进入 execute
python -m pytest -q -p no:cacheprovider tests/test_stage3_query_graph.py
  18 项全部通过，其中 3 项真实 PostgreSQL 集成测试
python scripts/verify.py
  PASS=8 FAIL=0 NOT_RUN=2，退出码 0
  离线 pytest 87 项通过；真实 PostgreSQL pytest 9 项通过；原有参考核对和前端构建通过
  NOT_RUN 为未请求的真实能力冒烟、真实查询图冒烟
python scripts/smoke_query_graph.py --real-model
  NOT_RUN：当前未配置 Key；脚本退出码 2，没有调用模型
```

真实集成验证包括：五指标与独立 Python 原始记录结果一致；日分组图表点来自相同表格；两个期间差额由返回值计算；Top N 返回与标记正确；无数据客单价为空。暂停测试启动一个 CLI 子进程，退出后在第二个 CLI 子进程继续同一 run_id，结果成功且 owner、模型调用计数保留。它证明 PostgreSQL checkpoint 的进程间恢复，不代表浏览器会话或完整任务管理已实现。

本地 HTTP stub 实际走 `ChatDeepSeek.with_structured_output`，验证 QueryPlan/SqlDraft 的工具契约、参数解析和 usage 提取（假 usage 为测试输入）。该结果只证明协议接线，不代表真实 DeepSeek 输出质量。

本阶段修复的兼容问题：SQLGlot 将 `NUMERIC` 规范化为 `DECIMAL`，阶段 2 的字符串类型允许列表误拒了合法客单价查询。现补入该固定数值类型，正常与零分母客单价在真实库上均验证通过，没有放开未经批准的类型或函数。

### 当前约束与未验证

- 当前没有 Key；真实模型的规划准确率、SQL 生成及一次修复效果全部未验证，不能用离线 6/6 代替。也没有执行开发/冻结评测集，不报告准确率或费用。
- SQL 业务核对使用限定单层形状，不是任意等价 SQL 的语义证明。别名限定和等价时区边界可归一化，其余不同写法可能被拒绝并最多修复一次。支持中文显式月份/日期及今天、上月、上周的演示范围；日期歧义和缺失会澄清。
- 首版期间比较仅支持总体、渠道、类别，暂不与 Top N 或日分组组合；每题单个指标，不支持任意过滤、预测或因果解释。
- 每任务最多五次适配器调用、两轮澄清、一次 SQL 修复；网络自动重试为零，单次模型请求不超过 20 秒或剩余预算。90 秒活动预算在节点前后检查，不计等待澄清和 checkpoint I/O，不能抢占正在运行的同步节点；数据库每条语句有独立超时。checkpoint 语句另设 5 秒超时。
- Token 缺失或离线时为 null，费用不估算。HTTP 查询接口、匿名 Cookie 会话、历史列表、浏览器工作台与运行任务的重启管理属于阶段 4；本阶段没有宣称这些已完成。

### 3 个必须理解的知识点

1. 模型选择指标、范围和候选 SQL；图的条件边和确定性代码决定是否执行、是否修复、何时停止。模型输出契约没有 owner、凭据或计数器字段。
2. `interrupt` 恢复时会从节点开头重放。等待状态登记放在前一个节点，interrupt 节点在暂停前没有外部副作用，恢复后只收集澄清文本再回到规划。
3. SQL 执行成功不表示口径正确。AST 检查限制访问，业务形状核对约束计划；最终还要以独立参考计算验证结果，标准答案始终留在评测侧。

### 可亲手运行的小练习

运行 `python scripts/query_cli.py ask "2026年9月销售额"`，查看 `awaiting_clarification` 和返回的 run_id；退出该命令后运行 `python scripts/query_cli.py resume <run_id> "期间净收款"`。观察同一 run_id 从 waiting/clarify 返回 plan，最后查询的是 cash_events，修复次数仍为 0。再提交同一澄清应被拒绝。

### 交给阶段 4

复用 `QueryService.start/read/resume`、类型化计划/图表及 PostgreSQL saver。HTTP 层从安全随机服务端会话取得 owner，不能接受浏览器自报 owner 或 thread_id；添加应用 runs/events/sessions 元数据和历史读取，限制单进程并发，启动时标记失去执行者的 running 任务为 interrupted。已完成结果和等待澄清任务可利用现有 checkpoint，仍需真实浏览器、两个独立会话及完整服务重启验收。真实模型验证待本机 Key 配置后显式执行。

提交与推送：阶段 3 实现及验收文档提交为 `ea0fb02`（`feat: add LangGraph query flow and PostgreSQL checkpoints`）；`git push origin main` 成功，远端从 `91a7c4e` 更新到 `ea0fb02`。本段回执随后单独提交。

---

## 阶段 4：HTTP 工作台、会话与持久化

实现与验收日期：2026-10-02。工作台、离线查询流程、真实 PostgreSQL 及 Windows/Edge 浏览器验收通过。阶段 3/4 的真实模型验证仍待本机 Key，阶段 5 评测尚未开始。

### 已实现行为与关键文件

- `backend/migrations/versions/0003_workspace.py`：app_state 的 sessions/runs/events、状态约束、会话历史索引、事件唯一序号；`scripts/setup_db.py` 迁移后只给 app 状态表 DML，reader 仍只能读 analytics。checkpoint 保持官方 saver 管理。
- `backend/app/workspace.py`：服务端随机匿名 Cookie、哈希存储、7 天会话、CSRF/Origin 校验、全部操作的 owner 约束；创建/读取/澄清/历史/重试接口。澄清按 owner、状态、revision、24 小时有效期原子 UPDATE，重复或过期返回 409；客户端额外 owner/thread_id 被请求模型拒绝。
- lifespan 创建 app 连接池、reader 执行器、两项并发的工作线程及 PostgreSQL advisory lock。第二个 API 进程不能启动；容量已满返回 429。启动时把遗留 running 改为 interrupted，不自动续跑。运行时从 LangGraph stream 保存节点快照/事件，只有图已结束或 interrupt 才发布完成、失败或待澄清。
- `backend/app/query_graph.py` 增加内部初始状态构造，允许 HTTP 元数据先登记服务端随机 run_id；原有 CLI start/read/resume 保持兼容。每个工作线程独立创建 PostgreSQL saver，模型 SQL 仍只经过 QueryExecutor 的 reader 查询流程。
- `frontend/src/App.tsx`、`api.ts`、`Chart.tsx`、`styles.css`：查询输入、示例、合成/模型/参考日期、指标口径、金额卡片、精确表格、按需加载的条形/折线图、期间比较、澄清、错误/重试、SQL/节点轨迹和历史。650 ms 短轮询与 URL 中的 run 实现刷新读取；连接错误 2 秒后重试；StrictMode 共用建会话 Promise。
- 数据覆盖不是硬编码成 9 月末：通过受限 reader 查询计算实际现金事件的日期范围，当前种子覆盖 `[2026-07-01, 2026-10-25)`，保留 10 月边界与跨期退款；业务参考日期仍为 2026-09-30。表格/卡片以 BigInt 格式化十进制分，图表取同一结果，绘制坐标用 JS 数值，不运行模型 JS。
- `tests/test_stage4_workspace.py`：四项真实数据库测试，覆盖 Cookie/访问隔离、CSRF/Origin/请求字段、并发重复澄清只领取一次、过期澄清、失败/拒绝、有限并发、单进程锁和 reader 不能访问三张状态表。
- `frontend/e2e/workspace.spec.ts`：一条完整真实浏览器验收，覆盖正常/日趋势/期间比较/澄清/失败/拒绝/刷新/历史、两个独立 context 的越权拒绝、重复澄清及两次真正的后端进程终止/重启。`scripts/browser_fixture.py` 锁真实业务表让查询正在执行，杀死后端后释放锁，重新启动确认 interrupted，再通过重试得到新的成功 run。清理只针对测试 Cookie 对应会话，Cookie 经 stdin 传入，不落日志/命令行。

### 实际命令与结果

```text
.\.venv\Scripts\python.exe scripts/setup_db.py
  0003_workspace 迁移与 app 状态表授权成功；未重新灌入或更改业务数据
npm install --save-exact echarts@6.1.0
npm install --save-dev --save-exact @playwright/test@1.63.0
npm install --save-dev --save-exact @types/node@26.6.4
npm install --save-dev --save-exact prettier@3.9.9
  依赖分别安装并更新 package-lock；版本固定
.\.venv\Scripts\python.exe -m pytest tests/test_stage4_workspace.py -q -p no:cacheprovider
  4 项真实 PostgreSQL 测试通过
npm run test:e2e
  Windows/Edge 无头：1 条完整浏览器流程通过（首轮约 26 秒）
.\.venv\Scripts\python.exe scripts/verify.py
  最终验收 PASS=9 FAIL=0 NOT_RUN=2，退出码 0
  离线 pytest 87 项、真实 PostgreSQL pytest 13 项全部通过（共 100 项）
  Ruff check/format、学习样例、协议 stub、独立参考核对、前端构建和浏览器验收全部通过
  两项 NOT_RUN 是未请求的真实能力冒烟与真实查询图冒烟
git diff --check
  无空白错误
```

构建和浏览器在本机受限执行环境中需要允许创建 Vite 临时配置与浏览器/测试子进程；已实际在允许的本机进程环境执行，未把权限受限失败算作通过。初次失败用例提供了不存在的日期，流程正确要求澄清；验收改用有效日期但逆序区间触发规划契约失败，没有改变日期或澄清约束。

浏览器截图实际生成于忽略目录 `.local/screenshots/stage4-workspace.png`、`stage4-mobile.png`，已逐张查看。补充刷新后等待 chart canvas 渲染的断言，避免把按需加载占位符截图当成图表完成。桌面 1440 像素、移动布局 390 像素已检查，无页面横向溢出及 pageerror。真实杀进程期间出现一次预期的 Vite ECONNREFUSED，页面重新连接后读取持久化状态。

### 限制与未执行

- 未调用真实模型：当前没有 Key，不能把离线浏览器流程或协议 stub 当作 DeepSeek 质量验证；未跑开发/冻结评测集，不报告准确率、修复质量或费用。也未验证容器、公网和其他系统/浏览器。
- 仅支持一个 API 进程、两项并发；启动开发服务前停止旧实例，统一验收前停止 API/5173 前端。测试自己的 API 用 18004。进程锁、数据库及 saver 初始化需要真实 PostgreSQL，数据库恢复后需要重新启动业务服务。
- graph checkpoint 和 runs 快照不在一个事务。崩溃窗口遵循 metadata 的 running → interrupted，即使 checkpoint 已更靠后也不自动恢复，用户重试创建新 run；不承诺自动续跑或恰好执行一次。正常等待澄清的 checkpoint 恢复已实测。
- 历史最多 50 项；Cookie 7 天到期或清除后旧历史不可访问，持久化数据尚无自动清理。澄清有效期 24 小时，超时从原问题重新查询。没有登录、多租户、外部队列或 SSE。
- 金额卡片/表格保留分精度，图表坐标为 JS 数值近似；精确值以表格/API 为准。主 JS 约 236 kB，图表 chunk 约 517 kB（压缩前），后者仍触发 Vite 的 500 kB 提示，未提高阈值掩盖警告。
- 重启验收专用服务把 SQL timeout 设置为 15 秒以稳定制造被锁阻塞的查询；产品默认仍为 3 秒，原有真实 PostgreSQL 超时测试保持通过。

### 3 个必须理解的知识点

1. 图状态描述当前规划与节点执行；runs 描述对外任务状态与会话归属；浏览器只展示 runs。图节点流写入快照，但待澄清/完成必须等调用真正退出，防止浏览器在 checkpoint 尚未暂停时抢先恢复。
2. Cookie 里的随机 token 才是会话访问凭据，run_id 只是定位符。服务器将 Cookie 映射为 owner，再同时用 run_id/owner 查询；知道别人 run_id 也不能读取、澄清或重试。CSRF/Origin 校验保护写接口。
3. “先读 waiting 再更新”可能让两个提交同时恢复同一 checkpoint；带版本/状态/有效期条件的 UPDATE 才能原子领取。进程重启与人工重试又是另一层问题，不能从有 checkpoint 推导自动续跑或恰好一次语义。

### 可亲手操作的小练习

打开工作台，问 `9月销售额是多少？`，看到澄清后刷新页面，再补充 `实付金额`。查看同一 URL 中的 run 编号、结果与展开的 waiting/clarify/plan 事件。另开浏览器无痕窗口访问这个结果 URL，观察查询不存在或不属于当前会话的提示。

### 交给阶段 5

可复用 QueryService、受限 SQL 执行器、五指标定义、精确报告和节点事件，以及 HTTP/浏览器的完整产品流程。下一阶段按计划建立开发题、模板族隔离的冻结题和约束/边界用例；参考 SQL/答案留在评测侧，不进入模型上下文。先离线跑评测链路，真实评测等本机 Key 与调用预算配置后执行。仍需记录错误拒绝、澄清质量、执行成功与结果正确的区别，不能从这次功能验收推导模型准确率。

提交与推送：验收已通过，提交前检查及推送回执待执行；本段将在远端确认后补充提交号与实际推送结果。
