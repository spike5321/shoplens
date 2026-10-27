"""单进程工作台：服务端会话、原子澄清领取、有限并发和持久化运行状态。"""

from __future__ import annotations

import hashlib
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings
from app.query_checkpoint import postgres_checkpointer
from app.query_executor import QueryError, QueryExecutor
from app.query_graph import QueryService
from app.query_model import ModelError
from app.query_plan import QueryPlan

COOKIE = "shoplens_session"
SESSION_SECONDS = 7 * 24 * 3600
ALLOWED_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
}
router = APIRouter(prefix="/api", tags=["workspace"])


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("question is blank")
        return value.strip()


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    revision: int = Field(ge=0)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("answer is blank")
        return value.strip()


def public_run(row: dict) -> dict:
    """不向浏览器暴露 owner、内部 thread 或指标上下文。"""
    state = row["snapshot"]
    source = state.get("followup") or {}
    return {
        "run_id": row["run_id"],
        "question": row["question"],
        "source_run_id": source.get("run_id"),
        "source_question": source.get("question"),
        "status": row["status"],
        "revision": row["revision"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "clarification_expires_at": (
            row["clarification_expires_at"].isoformat() if row["clarification_expires_at"] else None
        ),
        **{
            key: state.get(key)
            for key in (
                "plan",
                "report",
                "sqls",
                "events",
                "error_code",
                "provider",
                "model_id",
                "model_calls",
                "repair_count",
                "active_ms",
                "tokens",
                "clarifications",
            )
        },
    }


class Workspace:
    def __init__(self, settings):
        self.settings = settings
        self.lock = psycopg.connect(settings.dsn("app"), autocommit=True, connect_timeout=3)
        # 防止第二个进程误把第一个进程的任务标记为中断；本阶段只支持一个 worker 进程。
        if not self.lock.execute("SELECT pg_try_advisory_lock(5321, 4)").fetchone()[0]:
            self.lock.close()
            raise RuntimeError("ShopLens requires one API process; another process owns the lock")
        try:
            self.pool = ConnectionPool(
                settings.dsn("app"),
                min_size=1,
                max_size=6,
                timeout=5,
                open=True,
                kwargs={
                    "row_factory": dict_row,
                    "connect_timeout": 3,
                    "options": "-c statement_timeout=5000",
                },
            )
            self.pool.wait(timeout=5)
            self.reader = QueryExecutor(settings)
            self.reader.__enter__()
            dates = self.reader.execute("""
                SELECT MIN(CAST(occurred_at AT TIME ZONE 'Asia/Shanghai' AS DATE)) AS start_date,
                       MAX(CAST(occurred_at AT TIME ZONE 'Asia/Shanghai' AS DATE)) AS end_date
                FROM analytics.cash_events
            """).rows[0]
            self.data_start = str(dates[0]) if dates[0] else None
            self.data_end = str(dates[1] + timedelta(days=1)) if dates[1] else None
            with self.pool.connection() as conn:
                conn.execute("""
                    UPDATE app_state.runs SET status='interrupted', revision=revision+1,
                        updated_at=now(), clarification_expires_at=NULL,
                        snapshot=jsonb_set(snapshot, '{error_code}', '"process_interrupted"')
                    WHERE status='running'
                """)
            self.slots = threading.BoundedSemaphore(2)
            self.workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="shoplens-query")
        except Exception:
            if hasattr(self, "reader"):
                self.reader.__exit__(None, None, None)
            if hasattr(self, "pool"):
                self.pool.close()
            self.lock.close()
            raise

    def close(self):
        self.workers.shutdown(wait=True)
        self.reader.__exit__(None, None, None)
        self.pool.close()
        self.lock.close()

    def session(self, request: Request, response: Response, *, create=False) -> dict:
        token = request.cookies.get(COOKIE, "")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.pool.connection() as conn:
            row = (
                conn.execute(
                    "SELECT * FROM app_state.sessions WHERE token_hash=%s AND expires_at>now()",
                    (digest,),
                ).fetchone()
                if token
                else None
            )
            if row is None and create:
                token = secrets.token_urlsafe(32)
                row = conn.execute(
                    """INSERT INTO app_state.sessions(owner_id, token_hash, csrf_token, expires_at)
                    VALUES (%s,%s,%s,now()+interval '7 days') RETURNING *""",
                    (
                        uuid.uuid4().hex,
                        hashlib.sha256(token.encode()).hexdigest(),
                        secrets.token_urlsafe(32),
                    ),
                ).fetchone()
                response.set_cookie(
                    COOKIE,
                    token,
                    httponly=True,
                    samesite="lax",
                    max_age=SESSION_SECONDS,
                    secure=self.settings.env != "local",
                    path="/",
                )
        if row is None:
            raise HTTPException(401, "会话已过期，请刷新页面。")
        if request.method != "GET":
            csrf = request.headers.get("x-csrf-token", "")
            origin = request.headers.get("origin")
            own_origin = f"{request.url.scheme}://{request.url.netloc}"
            if not secrets.compare_digest(csrf, row["csrf_token"]) or (
                origin and origin not in ALLOWED_ORIGINS | {own_origin}
            ):
                raise HTTPException(403, "请求校验失败，请刷新页面。")
        return row

    def read(self, run_id, owner):
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM app_state.runs WHERE run_id=%s AND owner_id=%s",
                (run_id, owner),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "查询不存在或不属于当前会话。")
        return row

    def reserve(self):
        if not self.slots.acquire(blocking=False):
            raise HTTPException(429, "已有两项查询正在执行，请稍后再试。")

    def followup(self, run_id, question, owner):
        row = self.read(run_id, owner)
        if row["status"] != "succeeded":
            raise HTTPException(409, "只能基于已完成的成功结果继续追问。")
        try:
            plan = QueryPlan.model_validate(row["snapshot"].get("plan"))
            if plan.decision != "ready":
                raise ValueError
        except ValueError:
            raise HTTPException(409, "原查询条件不完整，请重新查询。") from None
        return self.start(
            question,
            owner,
            followup={
                "run_id": row["run_id"],
                "question": row["question"],
                "plan": plan.model_dump(mode="json"),
            },
        )

    def start(self, question, owner, *, followup=None):
        self.reserve()
        run_id = uuid.uuid4().hex
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO app_state.runs(run_id,owner_id,question,status,snapshot) VALUES (%s,%s,%s,'running',%s)",
                    (run_id, owner, question, Jsonb({"followup": followup})),
                )
            self.workers.submit(self.work, run_id, owner, question, None)
        except Exception:
            self.slots.release()
            raise
        return self.read(run_id, owner)

    def clarify(self, run_id, owner, answer):
        self.reserve()
        try:
            with self.pool.connection() as conn:
                row = conn.execute(
                    """
                    UPDATE app_state.runs SET status='running', revision=revision+1,
                        updated_at=now(), clarification_expires_at=NULL
                    WHERE run_id=%s AND owner_id=%s AND status='awaiting_clarification'
                        AND revision=%s AND clarification_expires_at>now()
                    RETURNING *
                """,
                    (run_id, owner, answer.revision),
                ).fetchone()
                if row is None:
                    self.read(run_id, owner)
                    raise HTTPException(409, "澄清已提交、已过期或状态已改变，请刷新查询。")
            self.workers.submit(self.work, run_id, owner, row["question"], answer.text)
        except Exception:
            self.slots.release()
            raise
        return self.read(run_id, owner)

    def save(self, run_id, state, *, finished=False):
        status = state.get("status", "running") if finished else "running"
        if status not in {"running", "awaiting_clarification", "succeeded", "failed", "rejected"}:
            status = "failed"
        with self.pool.connection() as conn:
            conn.execute(
                """
                UPDATE app_state.runs SET snapshot=%s || jsonb_build_object('followup', snapshot->'followup'), status=%s, updated_at=now(),
                    clarification_expires_at=CASE WHEN %s='awaiting_clarification'
                        THEN now()+interval '24 hours' ELSE NULL END
                WHERE run_id=%s
            """,
                (Jsonb(state), status, status, run_id),
            )
            for sequence, event in enumerate(state.get("events", [])):
                conn.execute(
                    """
                    INSERT INTO app_state.events(run_id,sequence,event) VALUES (%s,%s,%s)
                    ON CONFLICT (run_id,sequence) DO NOTHING
                """,
                    (run_id, sequence, Jsonb(event)),
                )

    def work(self, run_id, owner, question, answer):
        state = {}
        try:
            state = {"followup": self.read(run_id, owner)["snapshot"].get("followup")}
            with postgres_checkpointer(self.settings) as saver:
                service = QueryService(self.reader, self.settings, saver)
                if answer is None:
                    command = service.initial_state(
                        question, owner_id=owner, run_id=run_id, followup=state["followup"]
                    )
                else:
                    state = service.read(run_id, owner_id=owner)
                    if state["status"] != "awaiting_clarification":
                        raise ValueError("run_not_waiting")
                    if (
                        state["provider"] != service.model.provider
                        or state["model_id"] != service.model.model_id
                    ):
                        raise ValueError("provider_changed")
                    command = Command(resume={"text": answer})
                for value in service.graph.stream(
                    command, service.config(run_id), stream_mode="values"
                ):
                    state = {k: v for k, v in value.items() if not k.startswith("__")}
                    self.save(run_id, state)
                state = service.read(run_id, owner_id=owner)
                self.save(run_id, state, finished=True)
        except Exception as exc:
            # 模型/连接错误只有稳定代码进入响应，原始异常可能含凭据。
            code = exc.code if isinstance(exc, ModelError) else "query_service_error"
            if isinstance(exc, ValueError) and str(exc) == "provider_changed":
                code = "provider_changed"
            state.update(status="failed", error_code=code)
            self.save(run_id, state, finished=True)
        finally:
            self.slots.release()


@asynccontextmanager
async def lifespan(app):
    runtime = None
    # 数据库离线时健康探针仍可用，业务接口明确返回 503。
    with suppress(psycopg.Error, TimeoutError, QueryError):
        runtime = Workspace(get_settings())
    app.state.workspace = runtime
    try:
        yield
    finally:
        if runtime:
            runtime.close()


def runtime(request: Request) -> Workspace:
    workspace = getattr(request.app.state, "workspace", None)
    if workspace is None:
        raise HTTPException(503, "查询服务不可用，请检查数据库和迁移。")
    return workspace


@router.get("/session")
def session_info(request: Request, response: Response):
    workspace = runtime(request)
    session = workspace.session(request, response, create=True)
    response.headers["Cache-Control"] = "no-store"
    return {
        "csrf_token": session["csrf_token"],
        "provider": workspace.settings.model_provider,
        "reference_date": workspace.settings.demo_reference_date,
        "business_timezone": workspace.settings.business_timezone,
        "data_start": workspace.data_start,
        "data_end_exclusive": workspace.data_end,
        "synthetic": True,
    }


@router.post("/runs", status_code=202)
def create_run(body: Question, request: Request, response: Response):
    workspace = runtime(request)
    owner = workspace.session(request, response)["owner_id"]
    return public_run(workspace.start(body.question, owner))


@router.get("/runs")
def history(request: Request, response: Response):
    workspace = runtime(request)
    owner = workspace.session(request, response)["owner_id"]
    with workspace.pool.connection() as conn:
        rows = conn.execute(
            "SELECT run_id,question,status,created_at,snapshot->'followup'->>'run_id' AS source_run_id FROM app_state.runs WHERE owner_id=%s ORDER BY created_at DESC LIMIT 50",
            (owner,),
        ).fetchall()
    return {"runs": rows, "limit": 50}


@router.get("/runs/{run_id}")
def read_run(run_id: str, request: Request, response: Response):
    workspace = runtime(request)
    owner = workspace.session(request, response)["owner_id"]
    return public_run(workspace.read(run_id, owner))


@router.post("/runs/{run_id}/clarify", status_code=202)
def clarify_run(run_id: str, body: Answer, request: Request, response: Response):
    workspace = runtime(request)
    owner = workspace.session(request, response)["owner_id"]
    return public_run(workspace.clarify(run_id, owner, body))


@router.post("/runs/{run_id}/retry", status_code=202)
def retry_run(run_id: str, request: Request, response: Response):
    workspace = runtime(request)
    owner = workspace.session(request, response)["owner_id"]
    row = workspace.read(run_id, owner)
    expired = row["clarification_expires_at"] and row["clarification_expires_at"] <= datetime.now(
        UTC
    )
    if row["status"] not in {"failed", "interrupted"} and not expired:
        raise HTTPException(409, "当前查询无需重试。")
    # 新 run 从原问题重新解析，重新澄清；不假称自动恢复或恰好执行一次。
    return public_run(
        workspace.start(row["question"], owner, followup=row["snapshot"].get("followup"))
    )


@router.post("/runs/{run_id}/followup", status_code=202)
def followup_run(run_id: str, body: Question, request: Request, response: Response):
    workspace = runtime(request)
    owner = workspace.session(request, response)["owner_id"]
    return public_run(workspace.followup(run_id, body.question, owner))
