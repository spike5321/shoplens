# 技术决策记录（ADR）

> 本文只记录**实际验证过**的事实。凡是没跑过的一律写「未验证」。
> 核对日期：2026-09-24。所有版本号来自本机实际 `pip index versions` / `npm view` 查询与安装结果。

## 0. 环境实测结论

| 项目 | 实测结果 | 结论 |
|---|---|---|
| 操作系统 | Windows（中文环境，用户名 spike） | 路径与编码坑按 Windows 处理 |
| Python | PATH 上为 3.13.12（`.workbuddy` 托管运行时），系统另有 3.13.14 | 计划写的是 3.12，本机**没有** 3.12；见 §1 |
| Node.js | v22.22.2（`.workbuddy` 托管运行时） | 满足 Vite 8 要求（`^20.19 \|\| >=22.12`） |
| 包管理器（Python） | pip 26.2.1（装进项目 venv 后） | 无 uv / pnpm / yarn，不引入 |
| 包管理器（Node） | npm 10.9.7 | 够用，不额外装 pnpm |
| **Docker** | **不可用**（`docker` / `podman` 命令均不存在） | 走 §3 的本地 PostgreSQL 替代路径 |
| 系统 PostgreSQL | **不存在**（无 `psql` / `postgres` / `pg_ctl`，无 PostgreSQL 安装目录） | 同上 |
| git | 2.55.0.windows.3 | 可用 |
| 网络 | 走本机 HTTP 代理；`pypi.org`、`registry.npmjs.org` 可达 | 见 §2 的镜像问题 |
| PostgreSQL（替代路径） | 官方免安装二进制 18.1，已实测启动并连接成功 | 见 §3 |

### 探测命令（可复现）

```bash
which -a docker podman docker-compose psql pg_ctl postgres     # 全部 not found
python --version; node --version; npm --version
"C:/Users/spike/.workbuddy/binaries/python/versions/3.13.12/python.exe" --version
```

## 1. Python 版本：3.13 而非计划里的 3.12

**决定**：使用 Python 3.13.12（项目 venv 由托管运行时创建）。

**原因**：PROJECT_PLAN.md 写的是 "Python 3.12 + FastAPI + Pydantic"，但本机不存在 3.12 运行时，
且计划本身写明「实现前核对当前官方文档、运行最小兼容性检查并锁定版本，不把本文件当具体包版本的依据」。
3.13 已被本次安装的全部依赖（FastAPI / SQLAlchemy / psycopg / LangChain / LangGraph）以官方 wheel 直接支持，
安装过程无编译、无 `EBADENGINE` 警告，因此不额外装 3.12。

**残余风险**：与计划文本不一致，在 `docs/progress.md` 中标注。若阶段 3 的
`langgraph-checkpoint-postgres` 或阶段 2 的 SQLGlot 在 3.13 上出现异常，回退到 3.12 只需换解释器重建 venv。

## 2. 依赖版本与锁文件

### 后端（`backend/requirements.txt` 为顶层依赖，`backend/requirements.lock.txt` 为完整锁定）

顶层依赖版本在 2026-09-24 实测存在并安装成功：

| 包 | 版本 | 用途 |
|---|---|---|
| fastapi | 0.141.1 | HTTP 接口与 Pydantic 严格结构化 |
| uvicorn[standard] | 0.53.0 | ASGI 服务器 |
| pydantic | 2.13.5 | 数据校验与结构化输出 |
| pydantic-settings | 见锁文件 | 环境变量配置 |
| sqlalchemy | 2.0.54 | 应用状态表 ORM |
| alembic | 1.20.0 | 应用状态表迁移 |
| psycopg[binary] | 3.3.6 | PostgreSQL 驱动（**psycopg 3**，不是 psycopg2） |
| langchain | 1.4.2 | 模型 / 工具 / 结构化输出组件 |
| langchain-core | 1.6.4 | 消息、工具基类 |
| langchain-openai | 1.6.6 | OpenAI 兼容协议适配（备用） |
| langchain-deepseek | 1.1.1 | 默认真实服务商适配 |
| langgraph | 1.2.12 | 状态图编排 |
| langgraph-checkpoint-postgres | 见锁文件 | 图状态持久化（阶段 3/4 使用） |
| sqlglot | 30.19.0 | SQL AST 解析（阶段 2 使用） |
| httpx | 0.28.1 | 测试与 HTTP 客户端 |
| pytest | 9.1.1 | 测试 |
| python-dotenv / pytest-asyncio / ruff | 见锁文件 | 开发工具 |

**锁文件生成方式**：`pip freeze > backend/requirements.lock.txt`，在安装完成后的真实 venv 里生成。

### 前端（`frontend/package.json`，`frontend/package-lock.json` 为锁定）

| 包 | 版本 | 说明 |
|---|---|---|
| react / react-dom | 19.3.0 | |
| vite | 8.3.1 | 要求 Node `^20.19 \|\| >=22.12`，本机 22.22.2 满足 |
| @vitejs/plugin-react | 6.1.1 | peer 要求 `vite ^8.0.0`，已对齐 |
| typescript | 7.0.2 | 当前 latest |
| @types/react / @types/react-dom | 19.3.0 | |

### pip 镜像问题（踩坑记录）

本机全局 pip 配置指向清华镜像 `https://pypi.tuna.tsinghua.edu.cn/simple`。
在当前网络代理下该镜像不可达（`Tunnel connection failed: 502 Bad Gateway`），
表现为 `ERROR: No matching distribution found for <包名>` —— 这个报错很容易被误读成「包不存在」。

**处理**：项目 venv 内写 `D:\Project_02\.venv\pip.ini` 固定官方源：

```ini
[global]
index-url = https://pypi.org/simple
```

第二个坑：该 `pip.ini` **必须是纯 ASCII**。写入中文注释后 pip 会报
`Configuration file contains invalid cp936 characters` 并整份丢弃配置，退回到不可达的清华源，
最终表现为「pip 命令执行了但什么都没装」。这是本次搭建中唯一一次真正的静默失败。

## 3. 数据库运行方式：Docker 不可用 → PostgreSQL 官方免安装二进制

**决定**：使用 PostgreSQL 18.1（EnterpriseDB 官方 Windows binaries zip），解压到 `.local/pgsql`，
数据目录 `.local/pgdata`，由 `scripts/pg.ps1` 管理生命周期。

**为什么不用其它方案**：

| 方案 | 否决原因 |
|---|---|
| SQLite | **计划明确禁止**把 SQLite 测试当作 PostgreSQL 集成验收。权限角色、`statement_timeout`、`timestamptz` 时区行为都无法对齐，阶段 2 的权限测试会变成假测试。 |
| `winget install PostgreSQL` | EDB 安装器需要管理员权限去注册 Windows 服务；本会话无法确认能提权，且会改动系统状态。 |
| Docker Desktop | 未安装；安装需要管理员 + 重启。 |
| 云数据库 | 需要外部凭据（违反「没有 Key 就离线工作」的精神，且会成为阶段 1/2 的阻塞项）。 |

**免安装二进制的关键事实（均已实测，见 progress.md）**：
- 不需要管理员权限，不注册服务，不影响机器上任何已有 PostgreSQL；
- 端口用 **55432**（不是 5432），避免与将来可能安装的实例冲突；
- 只监听 `127.0.0.1`；
- 服务端 `timezone = 'UTC'`；业务时区 `Asia/Shanghai` 由应用层解释（PROJECT_PLAN.md 第 3 节要求）。
  这样存的是 `timestamptz`，比较与显示的口径由应用控制，不依赖服务器本地时区。
- `--auth-host=scram-sha-256`：TCP 连接必须密码，密码在 `init` 时随机生成并写入被 gitignore 的 `.env`。

**下载与启动命令**（`.local/` 整体被 `.gitignore` 忽略，属于本机运行时依赖，不进仓库）：

```bash
mkdir -p .local/downloads && cd .local/downloads
curl -L -o pgsql-binaries.zip \
  https://get.enterprisedb.com/postgresql/postgresql-18.1-1-windows-x64-binaries.zip
cd .. && unzip -q downloads/pgsql-binaries.zip -d .
```

```powershell
./scripts/pg.ps1 init      # initdb + 启动 + 建库 + 生成 .env
./scripts/pg.ps1 status
./scripts/pg.ps1 psql
./scripts/pg.ps1 stop
```

**配置文件布局**：initdb 会按机器时区往 `postgresql.conf` 写一行
`timezone = 'Asia/Shanghai'`。如果我们也往同一文件追加 `timezone`，
就会出现两行、靠「后者生效」碰运气。因此覆盖项写在独立文件
`.local/pgdata/shoplens.conf`，`postgresql.conf` 末尾只有一行
`include = 'shoplens.conf'`。这样「哪些是默认值、哪些是我们改的」一目了然。

**脚本编码要求**：`scripts/pg.ps1` 必须存为 **UTF-8 with BOM**。
Windows PowerShell 5.1 会按 ANSI(cp936) 读取无 BOM 的 `.ps1`，
脚本里的中文消息会被打乱，其中一处字节序列甚至直接让整个脚本解析失败
（报 `数组索引表达式丢失或无效` / `语句块或类型定义中缺少右"}"`，看起来像语法错了，
实际是编码问题）。加 BOM 后 PS 5.1 与 PS 7 都能正确识别。
另一个 Windows 专属坑：PS 5.1 跑在 .NET Framework 上，没有
`RandomNumberGenerator.Fill`（那是 .NET Core 3.0+ 的静态方法），
生成随机密码要用 `Create().GetBytes()`。

**未验证项**：Docker Compose 从未运行过。阶段 6 要求「从干净环境构建并验收」，届时若 Docker 仍不可用，
必须明确标注为未验证，不允许因为「配置写好了」就写「容器可用」。

## 4. 模型服务商适配

**默认：离线假模型（`SHOPLENS_MODEL_PROVIDER=offline`）**
- 不需要任何 Key，可完成全部离线开发与测试；接口返回的 `model_provider` 字段会明确标注 `offline`，
  前端会打上「离线假模型」标签，绝不伪装成真实调用。

**已验证的服务商：DeepSeek（`deepseek`）**
- 适配器：`langchain-deepseek` 1.1.1 的 `ChatDeepSeek`；
- 官方文档：https://docs.langchain.com/oss/python/integrations/chat/deepseek
- 选择原因：计划指定「主模型优先使用已熟悉的国内服务商，DeepSeek 的 LangChain 集成可作为默认候选」；
  国内可直连，不需要额外网关；LangChain 有官方维护的集成包。
- 备用：`langchain-openai` 1.6.6 可对接任何 OpenAI 兼容端点（`ChatOpenAI(base_url=...)`），
  阶段 3 如果 DeepSeek 的结构化输出能力不达标，可在不改动业务代码的前提下切换。
- **Key 处理**：只从环境变量 `SHOPLENS_DEEPSEEK_API_KEY` 读取，通过 `pydantic.SecretStr` 承载；
  配置模块提供 `deepseek_api_key_configured` 布尔属性和 `safe_dsn()` 这类脱敏方法，
  测试 `tests/test_config.py`、`tests/test_healthz.py` 会断言密码与 Key 不出现在任何输出里。

**验证状态（2026-09-24 更新：已转为「已验证」）**

Key 已配置到项目根目录 `.env`，真实模型冒烟 **4/4 通过**（`deepseek-chat`）：

| 检查项 | 结果 |
|---|---|
| C1 工具调用 | ✅ 正确选工具、按整数分填对参数 |
| C2 结构化输出 | ✅ 按 Pydantic 模型返回，字段与日期区间都对 |
| C3 多轮 tool_call 闭环 | ✅ tool_call → ToolMessage → 文字回答，闭环终止 |
| C4 歧义澄清 | ✅ 面对「销售额」主动追问口径与期间 |

外加 `create_agent` 预构建 Agent 验证通过（消息条数 6，最终回答 `1000000 分（¥10000.00）`）。
完整输出与耗时见 `docs/progress.md` §8.4。

**唯一仍缺的是 Token/费用统计** —— 冒烟脚本没采集 `usage`。
阶段 3 冻结评测配置前必须补上，否则「运行成本说明」这条交付物没有依据。

**离线默认 + 显式切换（设计取舍）**

`SHOPLENS_MODEL_PROVIDER` 默认仍是 `offline`，即使 `.env` 里已经有 Key。
理由：离线开发与测试（`pytest`、学习样例、后续前端联调）不该联网、不该花钱，
更不该因为「机器上碰巧有 Key」而让每次测试都产生成本。
要跑真实模型必须**显式**表态，两种方式都可以：

```bash
# 只对这一次生效，不动项目默认值
SHOPLENS_MODEL_PROVIDER=deepseek python scripts/smoke_real_model.py
# 或把 .env 里的 SHOPLENS_MODEL_PROVIDER 改成 deepseek
```

脚本和样例都**不会**替你自动切换。判定逻辑把三种情况分开报，
因为处理方式完全不同：**「跳过（你不让我跑）」≠「未验证（我没法跑）」≠「配置错误（你写错了）」**。

### 4.1 真实模型验证的两层切法（2026-09-24 追加）

**问题**：没有 Key 的时候，怎么算「验证过」？

**否决的两个做法**：
- *写个假模型跑通，然后说「真实模型已验证」* —— 这是伪造证据，本项目明令禁止。
- *什么都不做，一直挂着「未验证」* —— 那等于把「有没有 Key」当成「能不能推进」的开关，
  实际上请求格式、schema 收紧、多轮消息结构这些**不依赖网络**的部分完全可以先固定住。

**采用的做法**：切成两层，分别给结论。

| 层 | 回答什么问题 | 怎么做 | 当前结论 |
|---|---|---|---|
| 协议层 | 我们发出去的请求体格式对吗？回来的消息我们解析得对吗？ | `--stub`：起本地 `ThreadingHTTPServer` 冒充 OpenAI 兼容端点，**断言出站请求体**（工具名、`additionalProperties=false`、消息结构），并模拟两轮 tool_call 闭环 | ✅ 4/4 通过 |
| 服务商层 | DeepSeek 真机真的能按预期回 tool_calls / 结构化输出吗？ | 需要真实 Key。没有就**明确报「未验证」+ 退出码 2** | ⏳ 未验证（缺 Key） |

**关键设计约束**：无 Key 时**必须返回退出码 2，而不是 0**。
理由是脚本存在的意义就是给出可信信号 —— 如果它在没验证的情况下返回 0，
CI 或阶段交接的人会误以为「真实模型已通过」，那比没有冒烟更糟。
所以 `scripts/smoke_real_model.py` 的 `build_model()` 在缺 Key 时直接
`raise RuntimeError("missing-key")`，由 `main()` 转成退出码 2 + 一段说明文字。
`tests/test_smoke_real_model.py` 用 6 项测试把这个约定固定住，包括
「必须输出『不会用 stub 的结果冒充』这句话」。

**副产物**：这条链路顺带验证了一件真事 —— 用一个**假 Key** 打真实端点会拿到 **401**，
说明网络可达、请求格式与鉴权头都是对的，缺的只是凭证。如果 401 变成连接超时或 404，
就能立刻区分出是网络问题还是协议问题。这类「用一个已知错误的输入去探测链路」的手法，
比「等有 Key 了再说」有用得多。

### 4.2 代码规范的基线（2026-09-24 追加）

两处修正，都属于「让约定显式化」而不是「让 linter 闭嘴」：

1. **`app` / `conftest` 在 ruff 眼里是第三方包。**
   不声明的话，`backend/app`（自研代码）会与 `fastapi`、`pytest` 分在同一段，
   顺序取决于 ruff 的启发式判断，未来升级 ruff 可能整体漂移。
   → `pyproject.toml` 增加 `[tool.ruff.lint.isort] known-first-party = ["app", "conftest"]`。
   副作用是 `tests/test_healthz.py` 需调整为「外部依赖在前、自研在后」，这是更符合直觉的顺序。

2. **未启用的 `noqa` 是负债。**
   `scripts/smoke_real_model.py` 里 `# noqa: N802` 因 `N` 规则集未启用而永远无效，
   反过来被 `RUF100` 报为无用指令。修法是删掉 noqa、把原因写成普通注释 ——
   `do_POST` 必须叫这个名字是因为 `BaseHTTPRequestHandler` 靠 `"do_" + method` 反射调度。
   注释是给下一个读代码的人看的，不该依赖 linter 存在。

**基线命令**（阶段交接时都应保持绿）：

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check backend scripts tests
.venv/Scripts/python.exe -m ruff format --check backend scripts tests
```

项目里 `E501` 是刻意关闭的（中文注释容易超长），**行长由 `ruff format` 兜底**，
所以 `format --check` 必须一起跑，只看 `check` 会漏。

### 4.3 真机跑出来的三个「口径不一致」缺陷（2026-09-24 追加）

跑真机暴露的问题比 stub 阶段暴露的更有价值，因为它们全都**不会在离线时出现**。

**① 断言在真实模式下必然失败 —— 自造假阴性**

早先真实模式下用一个空的 `_RecordingList` 冒充抓包结果，
于是 C1/C3 里「检查出站请求体」那一段永远拿不到数据、**必然判 FAIL**。
症状最具欺骗性：真实模型明明正确返回了 `tool_call`，却被记成 FAIL，
而我极可能把它当作「模型不行」写进文档，去修一个不存在的问题。

**假阴性比假阳性危险**，因为它会让你浪费时间并且降低对测试的信任。

→ 约定：断言分两组。
**语义组**（模型行为）两种模式都查；**出站组**（请求体形状）只有 stub 能查，
真实模式下如实标「本模式不覆盖」，**不判失败**。
→ 同时加回归测试反向固定「stub 模式下出站断言仍然生效」，防止矫枉过正把断言删光。

**② 测试用例自身有歧义，被 stub 的顺从掩盖**

C3 的提示词原本是「帮我算一下净收款。」——没给任何数字。
真实模型拒绝凭空编数字、于是不调用工具，被判成失败；
stub 无脑回 `tool_call`，反而"通过"。

**这是测试用例的错，不是模型的错。** 一条规则记住：
**用例里有歧义时，先修用例，不能去修模型。**

**③ 同一份配置两处读，口径不一致**

`scripts/learning_demo.py` 直接读 `os.environ.get("SHOPLENS_DEEPSEEK_API_KEY")`，
**绕过 `.env`**。同一个 Key：冒烟脚本读得到、学习样例读不到，
于是报「未配置 Key」——一个根本不存在的假问题，排查方向直接被带偏。

同一文件还缺 `sys.path` 引导：`python scripts/learning_demo.py` 会
`ModuleNotFoundError: No module named 'app'`。
原因是 **Python 只把「脚本所在目录」放进 `sys.path`，不是当前工作目录**；
pytest 靠 `pyproject.toml` 的 `pythonpath` 绕过了，脚本必须自己解决。

→ 约定：**所有脚本统一走 `get_settings()`，禁止直接读 `os.environ`；**
需要 `import app` 的脚本在文件开头显式插入 `backend/` 到 `sys.path`。

**这三条的共同点**：都不是算法问题，而是**同一件事在两处有不同说法**。
配置读取、路径解析、断言口径 —— 只要出现「两处」，就有漂移的风险。
检测手段就是**跑一次真机**：离线可跑的东西永远不会暴露这类问题。

## 5. LangChain 组件、自定义 LangGraph、预构建 create_agent 的区别

这三者最容易被混为一谈，也是面试高频问题（见 `planning/LEARNING_AND_INTERVIEW.md` 第 6 节）。

| 维度 | LangChain 组件 | 自定义 LangGraph | 预构建 `create_agent` |
|---|---|---|---|
| 抽象层级 | 零件：模型、消息、工具、结构化输出 | 编排：状态 + 节点 + 边 | 整车：模型 + 工具 + 提示词 + 循环 |
| 谁决定下一步 | 不适用（没有流程概念） | **你**，在条件边函数里用代码决定 | **模型**，根据工具返回自主决定 |
| 循环在哪 | 无 | 你显式画出来的回边，次数由状态字段约束 | 框架内部的 while 循环 |
| 能否插入确定性校验 | — | 能，校验是独立节点，不通过就进不了执行节点 | 很难；校验只能靠 middleware / 工具内部抛错 |
| 权限边界 | — | 清楚：执行是独立节点，凭据由服务端注入 | 模糊：模型在循环里自主选工具，容易越界 |
| 可观测性 | — | 每个节点、每次状态变更都可记录 | 只有消息序列，中间步骤是模型私有的 |
| 暂停 / 恢复 | — | `interrupt` + checkpointer，天然支持 | 支持，但暂停点不在你的控制内 |
| 适合什么 | 所有项目的地基 | 流程固定、约束强、需要审计的系统 | 探索型任务、工具集开放、约束弱的场景 |

**本项目的选择**：用 LangChain 做零件（`ChatDeepSeek`、`@tool`、Pydantic 结构化输出），
用**自定义 LangGraph** 做编排，**不使用** `create_agent` 跑主流程。

理由直接来自计划：主流程中「SQL AST 校验」「只读数据库角色」「修复次数上限」「owner_id 不可被模型改写」
都必须由代码和数据库强制执行，而不是靠系统提示词。把这条链路交给 `create_agent` 的自由循环，
等于把校验节点的位置交给模型决定 —— 这正是计划第 4 节明确要避免的
（"不要为了出现框架名字把一套 create_agent 自由循环再套进每个图节点"）。

`create_agent` 仍作为**独立学习样例**保留在 `scripts/learning_demo.py` 第 3 部分，
只在配置了真实 Key 时运行，用于对比两者的差异。它不计入核心成果。

### 5.1 工具契约必须显式收紧（实测发现）

用 `@tool` 修饰普通函数时，LangChain 会从函数签名生成 Pydantic 模型，
而 **Pydantic v2 生成的 JSON Schema 默认不含 `additionalProperties: false`**，
默认行为是**忽略**多余字段。实测：

```python
net_receipt_cents.invoke({"paid_cents": 100, "currency": "CNY"})   # 不报错，currency 被丢弃
```

模型把参数名写错时不报错、拿默认值算出「看起来正常」的数字 —— 对查数系统来说
这是最危险的一类 bug。因此本项目统一显式声明 `args_schema` 并加
`ConfigDict(extra="forbid")`，把契约从「默认宽松」改成「显式严格」。
这条约定从阶段 0 起适用于所有工具。

## 6. 目录结构决策

采用的根目录：`backend/ frontend/ data/ evals/ docs/ tests/ scripts/ planning/`

- `tests/` 统一放测试（不分 `backend/tests` + 根 `tests`），避免两处漂移；
  通过根 `pyproject.toml` 的 `pythonpath = ["backend"]` 让测试能 `import app`。
- 额外增加了 `scripts/`：存放本地 PostgreSQL 生命周期脚本和阶段 0 学习样例。
  这是相对阶段提示词目录清单的**增量**，理由是这些既不属于应用代码也不属于测试。
- `.local/` 存放本机运行时依赖（PostgreSQL 二进制、数据目录、pip/npm 日志）与 `.venv`，
  全部在 `.gitignore` 里。仓库本身保持干净。
- **没有**提前创建 `services/`、`repositories/`、`domain/` 这类空抽象层。目录随模块增长。

## 7. 官方文档与参考

| 主题 | 链接 | 用在哪 |
|---|---|---|
| LangChain 概览 | https://docs.langchain.com/oss/python/langchain/overview | 组件边界、`create_agent` 定位 |
| LangGraph 概览 | https://docs.langchain.com/oss/python/langgraph/overview | 状态编排、持久化、人工介入 |
| LangGraph Graph API | https://docs.langchain.com/oss/python/langgraph/graph-api | `StateGraph` / 条件边 / reducer 的确切签名 |
| 官方 SQL Agent 教程 | https://docs.langchain.com/oss/python/langgraph/sql-agent | 阶段 3 的流程骨架参考 |
| ChatDeepSeek 集成 | https://docs.langchain.com/oss/python/integrations/chat/deepseek | 模型适配器 |
| SQLGlot | https://sqlglot.com/sqlglot.html | 阶段 2 的 AST 解析 |
| PostgreSQL 18 文档 | https://www.postgresql.org/docs/18/ | 角色权限、`statement_timeout`、时区 |
| psycopg 3 文档 | https://www.psycopg.org/psycopg3/docs/ | 连接、只读事务 |
| Vite | https://vite.dev/ | 前端构建 |

三方仓库的借鉴范围单独记在 `docs/references.md`。

## 8. 阶段 4 会话、任务与浏览器决策（2026-10-02）

- 不引入队列服务。FastAPI lifespan 管理一个 ThreadPoolExecutor（两项并发）和 app/reader 连接池；BoundedSemaphore 不接受无界排队。PostgreSQL 会话 advisory lock 拒绝第二个 API 进程，避免误标其他进程的运行任务。
- 服务器生成会话 token（32 随机字节）和 owner UUID，Cookie HttpOnly/SameSite=Lax，非 local 时 Secure；数据库存 token 哈希、CSRF token 与 7 天到期时间。POST 检查 CSRF 和 Origin，所有任务操作按 owner 查库。没有账号系统，Cookie 到期访问权丢失，尚无自动数据清理。
- runs 是 HTTP/历史的事实来源，snapshot 保留图节点输出；events 按 run/sequence 去重。节点流期间对外保持 running；图结束或真正 interrupt 后才发布终态。澄清 UPDATE 同时检查 owner、waiting 状态、revision 和 24 小时有效期，再调度图，因此并发双提交只有一个被领取。
- LangGraph checkpoint 与元数据并非同一个事务。崩溃窗口一律按 runs 的 running → interrupted 处理，不自动重放，即使 checkpoint 已保存了更后面的节点。等待澄清的任务保持可恢复；用户重试从原问题创建新 run，不继承未确认补充，不承诺恰好执行一次。
- React 650 ms 短轮询只读取服务端状态，连接错误 2 秒后重试；终态停止轮询。StrictMode 共享初始建会话 Promise，避免双请求覆盖 Cookie/CSRF。URL 保存选中的 run，刷新由数据库重新读取。历史最多 50 项。
- ECharts 6.1.0 按需加载固定 bar/line 组件。表格/卡片金额用 BigInt 保留分精度；图表坐标转 JS 数值，精确答案以表格/API 十进制字符串为准。当前主 JS 约 236 kB、图表约 517 kB（压缩前）；图表 chunk 仍触发 Vite 500 kB 提示，未改变警告阈值。
- Playwright 1.63.0 在本机 Edge 无头模式验证真实 PostgreSQL 和后端进程重启，不 mock 业务 API。锁测试的 SQL timeout 设为 15 秒以留出终止窗口；产品默认仍是 3 秒，原有真实数据库超时测试继续验证。截图/报告都在忽略目录，测试 Cookie 经 stdin 清理，不写文件或命令行。
- 当前没有真实 Key，阶段 4 浏览器只证明产品流程与持久化，真实模型行为/质量、Linux/Chromium、容器和公网发布都未验证。
