# 本地容器交付

## 首次启动

需要 Docker Engine 的 Linux 容器模式和 Docker Compose，以及联网拉取基础镜像/依赖。初始化凭据脚本只用 Python 标准库；已有项目 `.venv` 可直接运行。

```powershell
git clone https://github.com/spike5321/shoplens.git
cd shoplens
python scripts/compose_env.py
docker compose --env-file .local/compose.env config --quiet
docker compose --env-file .local/compose.env build
docker compose --env-file .local/compose.env up -d --wait --wait-timeout 120
docker compose --env-file .local/compose.env ps -a
```

打开 [工作台](http://127.0.0.1:5173)。默认 `offline-query-v1` 只支持有限中文句式，实际 SQL 查询 PostgreSQL。固定演示参考日 2026-09-30；不要用机器今天推算演示数据日期。

Windows 上若 Docker 未运行，可先 `docker desktop start --timeout 45`。Docker Desktop CLI 仅用于本机启动，不修改系统虚拟化设置。端口 5173 要空闲，运行统一浏览器测试前应先停容器 web；容器库没有发布数据库端口，不影响本机 55432 数据库。

## 启动顺序与凭据

`db` 健康 → `init` 成功退出 → `api` 就绪 → `web` 健康。数据库 `pg_isready` 检查连接；API 健康同时要求真实 app 连接成功和 Workspace 已初始化。`/healthz` 为存活，`/readyz` 的 JSON 为就绪依据（保留 HTTP 200，必须检查字段）。

- 三套独立随机密码在被忽略的 `.local/compose.env`，不会改本机 `.env`；脚本拒绝覆盖已有文件。
- `init` 使用 admin 迁移/建角色，app 初始化 checkpoint，固定种子灌数；每次用独立 Python 原始记录计算交叉核对 SQL。
- 只有四张业务表全部为空时灌数；已有数据不重灌。部分或损坏的数据不会被悄悄替换，参考检查失败则阻断启动。迁移和正常重启保留历史。
- API 仅获 app/reader 密码及显式配置的模型 Key。无 admin 密码、无挂载宿主源码/`.env`/本机数据。运行 Python UID 10001；Nginx 使用官方镜像。
- 密码更换需要同时变更文件并重新运行 init；不要在已有卷上重新生成全套密码，PostgreSQL 已有 admin 密码不会由 POSTGRES_PASSWORD 自动更换。
- `docker compose config` 的完整输出、`docker inspect` 环境和日志可能包含敏感信息；验收仅用 `config --quiet`、选定非敏感字段和布尔审计。不要分享完整配置输出。

## 容器内真实 PostgreSQL 验收

集成测试会启动自己的 Workspace，因此先停止 api/web，保留数据库：

```powershell
docker compose --env-file .local/compose.env stop web api
docker compose --env-file .local/compose.env --profile checks build checks
docker compose --env-file .local/compose.env --profile checks run --rm --no-deps checks
docker compose --env-file .local/compose.env --profile checks run --rm --no-deps checks python scripts/demo_sql_guard.py
docker compose --env-file .local/compose.env up -d --wait --wait-timeout 120
```

额外的真实重建检查：`.\.venv\Scripts\python.exe scripts/check_container_restart.py`。它用同一个 Cookie jar 创建离线查询，执行本项目 `down`/`up`（保留卷），核对结果、历史和模型调用数未变化。若用了自定义 Compose 项目名，加 `--project <名称>`；先确保端口上运行的也是这个项目。

测试使用当前合成数据。不要把此命令指向真实业务库。宿主机的完整统一验收仍为 `scripts/verify.py`，覆盖静态、离线、真实 PostgreSQL、前端构建和浏览器，真实模型必须 `--real-model`。容器内检查不替代这些项目。

## 实际页面采集与评分

装好前端开发依赖和 Edge（Windows）或 Playwright Chromium 后，对已经启动的容器执行：

```powershell
cd frontend
npm ci
node e2e/delivery.mjs
cd ..
python scripts/check_delivery.py .local/stage6-container-browser/results.json
```

脚本走真实页面、HTTP、查询图和 PostgreSQL，采集普通查数、澄清、跨期退款、拒绝写操作、历史刷新和会话隔离；评分在浏览器请求结束后按原始合成记录计算。截图含合成数值，不含 Cookie 或 Key。

## 显式真实模型验证

真实模型会产生调用费用。容器默认不开启；在本机 `.env` 中配置 Key 后，用交付专用 API 启动器复用已批准预算账本。它调用生产适配器原本的提示词，不注入阶段 5 对照示例，最多再调用 20 次，并逐次预留 Token、到上限拒绝。账本为单进程锁；不得同时运行多个模型检查进程。

```powershell
# 先停容器，启动本机 55432 数据库（已迁移/灌数/checkpoint）
docker compose --env-file .local/compose.env stop web api
.\scripts\pg.ps1 start
.\.venv\Scripts\python.exe scripts/delivery_api.py --real-model --budget .local/eval-budget-20261008.json
# 第二个终端：仅本次进程配置代理
$env:SHOPLENS_API_PROXY='http://127.0.0.1:18006'
cd frontend
npm run dev
# 第三个终端：显式真实模型浏览器检查
cd frontend
node e2e/delivery.mjs --real-model
cd ..
.\.venv\Scripts\python.exe scripts/check_delivery.py .local/stage6-real-browser/results.json
```

这是本机 API 的真实模型链路验证；不据此宣称容器内真实模型联网验证。容器使用模型时可在 `.local/compose.env` 配置 `SHOPLENS_MODEL_PROVIDER=deepseek` 与本地 Key，再重建 API 容器；模型开关与凭据不会写入镜像。

## 停止与恢复

```powershell
docker compose --env-file .local/compose.env down
# 保留命名数据卷；再次 up 使用相同密码，保留查询历史
docker compose --env-file .local/compose.env up -d --wait --wait-timeout 120
```

不要在保留数据/历史时加 `--volumes`。重建干净演示环境应另用 `-p shoplens-clean` 新项目名；这会创建独立卷。当前没有公网部署、域名、HTTPS、正式登录、备份恢复或并发负载 SLA。

## 运行成本和交付限制

离线模式无需模型费用，耗用本机 Docker CPU、内存、磁盘。真实模型计费只记录供应商返回 Token；阶段 5 共 572 次调用/666,561 Token，单次请求最多五次模型调用，费用随提示词长度、结果和修复变化。价格未核实，不填人民币金额。三配置延迟和 Token 明细见 [评测报告](../evals/REPORT.md)。本项目尚未测压力负载、长期磁盘增长或资源最低配置。

阶段 6 的真实模型页面冒烟为 8 次调用/9,652 Token；与阶段 5 同一账本累计 580 次/676,213 Token，低于授权的 800 次/200 万 Token。[页面原始记录](evidence/stage6-real-browser.json) 与 [独立评分](evidence/stage6-real-summary.json) 均保留。这是四个固定场景的链路证据，不能据此声称通用准确率 100%。

基础镜像固定版本标签，首次构建拉取对应 digest；标签仍可能被上游更新。依赖已锁版本，但 Python lock 没有 wheel 哈希，不能宣称二进制字节完全可复现。当前镜像沿用完整开发 lock，未进行体积优化、SBOM 或完整镜像漏洞扫描。前端首次构建报告的 source-map-js 1.2.1 已更新到 1.2.2，完整 npm audit 复验为 0 项；这不等于整个项目零漏洞。

实际验证环境、命令与结果以 [最新进度](progress.md) 为准。
