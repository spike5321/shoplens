"""ShopLens FastAPI 入口：运维探针与阶段 4 查询工作台。

两个探针刻意分开：

* ``/healthz`` —— 存活探针。不依赖任何外部资源，进程起来就返回 200。
* ``/readyz``  —— 就绪探针。真的去连一次 PostgreSQL，报告数据库是否可用。

分开的原因：阶段 0 的验收标准是「后端启动」，不应该因为本地数据库没起就失败；
但「数据库能不能连」是需要被真实验证的，不能靠 mock 蒙过去。

安全约定：两个探针都不返回任何密码，只返回布尔值和去掉密码的连接串。
"""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import get_settings
from .workspace import lifespan, router

app = FastAPI(
    title="ShopLens API",
    description="基于 LangChain + LangGraph 的电商经营数据分析助手（合成演示数据）",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(router)


@app.middleware("http")
async def private_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# 前端开发服务器默认 5173；`npm run preview`（看构建产物）默认 4173。
# 阶段 0 只放行这两个本地来源。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, object]:
    """存活探针：不碰数据库，永远轻量。"""
    settings = get_settings()
    return {
        "status": "ok",
        "service": "shoplens-api",
        "version": __version__,
        "env": settings.env,
        # 只暴露「是否配置」，绝不返回 Key 内容
        "model_provider": settings.model_provider,
        "real_model_key_configured": settings.deepseek_api_key_configured,
    }


@app.get("/readyz", tags=["ops"])
def readyz() -> dict[str, object]:
    """就绪探针：真实连接一次 PostgreSQL，测量耗时。

    用 ``SET statement_timeout`` 保证探测本身不会挂死；
    连接失败时返回 200 + ``database.status = "unreachable"``，
    让前端能明确展示「后端起来了但数据库没起来」，而不是整个接口 500。
    """
    settings = get_settings()
    result: dict[str, object] = {
        "service": "shoplens-api",
        "query_service_ready": getattr(app.state, "workspace", None) is not None,
        "database": {
            "host": settings.pg_host,
            "port": settings.pg_port,
            "dbname": settings.pg_db,
            # 去掉密码的连接串，可以安全展示
            "dsn": settings.safe_dsn("app"),
            "status": "unknown",
            "latency_ms": None,
            "server_version": None,
            "error": None,
        },
    }

    start = time.perf_counter()
    try:
        import psycopg

        with (
            psycopg.connect(settings.dsn("app"), connect_timeout=3) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SET statement_timeout = 3000")
            cur.execute("SELECT version(), current_setting('TimeZone')")
            row = cur.fetchone()
        db = result["database"]
        assert isinstance(db, dict)
        db["status"] = "ok"
        db["server_version"] = (row[0] or "").split(" on ")[0] if row else None
        db["timezone"] = row[1] if row else None
    except Exception as exc:
        # 探针的职责就是把任何失败都转成可见状态，所以这里故意捕获宽异常，
        # 但只保留异常类型和截断后的信息；psycopg 的报错里不含密码。
        db = result["database"]
        assert isinstance(db, dict)
        db["status"] = "unreachable"
        db["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    db = result["database"]
    assert isinstance(db, dict)
    db["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return result
