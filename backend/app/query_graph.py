"""显式 LangGraph 查询流程；模型不能决定执行权限和修复次数。"""

from __future__ import annotations

import logging
import re
import time
import uuid
from operator import add
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from app.config import Settings
from app.query_executor import QueryError, QueryExecutor, QueryResult
from app.query_model import ModelError, query_model
from app.query_plan import (
    METRICS,
    Clarification,
    QueryPlan,
    SqlDraft,
    build_report,
    validate_plan_sql,
)

logger = logging.getLogger(__name__)
ACTIVE_BUDGET_MS = 90_000
MAX_MODEL_CALLS = 5
MAX_CLARIFICATIONS = 2


class QueryState(TypedDict, total=False):
    owner_id: str
    run_id: str
    question: str
    followup: dict | None
    reference_date: str
    provider: str
    model_id: str
    clarifications: Annotated[list[str], add]
    clarification_count: int
    plan: dict
    metric_context: dict
    sqls: list[str]
    query_results: list[dict]
    report: dict
    repair_count: int
    model_calls: int
    tokens: dict | None
    active_ms: int
    status: str
    error_code: str | None
    events: Annotated[list[dict], add]


def build_query_graph(model, executor: QueryExecutor, settings: Settings, checkpointer):
    def timed(name, fn, *, model_call=False):
        def node(state):
            started = time.monotonic()
            usage = None
            attempts = 0
            update = {}
            if name == "repair":
                update["repair_count"] = state["repair_count"] + 1
            try:
                if state["active_ms"] >= ACTIVE_BUDGET_MS or (
                    model_call and state["model_calls"] >= MAX_MODEL_CALLS
                ):
                    raise ModelError("budget_exceeded")
                attempts = int(model_call)
                reply = fn(state)
                usage = reply.pop("_usage", None)
                update.update(reply)
            except ModelError as exc:
                update.update(status="failed", error_code=exc.code)
            except ValidationError:
                update.update(status="failed", error_code="model_protocol_error")
            except QueryError as exc:
                update.update(error_code=exc.code.value)
            elapsed = round((time.monotonic() - started) * 1000)
            total = state["active_ms"] + elapsed
            if total > ACTIVE_BUDGET_MS:
                update.update(status="failed", error_code="budget_exceeded")
            tokens = state["tokens"]
            if model_call and attempts:
                if usage is None or tokens is None:
                    tokens = None
                else:
                    tokens = {key: tokens[key] + int(usage.get(key, 0)) for key in tokens}
            event = {
                "run_id": state["run_id"],
                "node": name,
                "elapsed_ms": elapsed,
                "model_calls": attempts,
                "usage": usage,
                "error_code": update.get("error_code"),
                "provider": state["provider"],
            }
            update.update(
                active_ms=total,
                model_calls=state["model_calls"] + attempts,
                tokens=tokens,
                events=[event],
            )
            logger.info(
                "run_id=%s node=%s elapsed_ms=%s model_calls=%s error=%s",
                state["run_id"],
                name,
                elapsed,
                attempts,
                event["error_code"],
            )
            return update

        return node

    def plan_node(state):
        options = {}
        if state.get("followup"):
            options["base_plan"] = state["followup"]["plan"]
        reply = model.plan(
            state["question"],
            state["clarifications"],
            state["reference_date"],
            remaining_ms=ACTIVE_BUDGET_MS - state["active_ms"],
            **options,
        )
        plan = QueryPlan.model_validate(reply.value)
        text = state["question"] + "\n" + "\n".join(state["clarifications"])
        if plan.decision == "ready":
            if any(w in text for w in ["销售额", "业绩"]) and not any(
                w in text for w in ["实付", "净收款", "退款", "订单数", "客单价"]
            ):
                plan = QueryPlan(
                    decision="clarify", clarification_question="销售额是指实付金额还是期间净收款？"
                )
            elif not state.get("followup") and not re.search(
                r"\d{1,2}月|\d{4}-\d{2}-\d{2}|今天|上月|上周", text
            ):
                plan = QueryPlan(decision="clarify", clarification_question="请明确日期范围。")
        if plan.decision == "clarify" and state["clarification_count"] >= MAX_CLARIFICATIONS:
            raise ModelError("clarification_limit")
        return {"plan": plan.model_dump(mode="json"), "error_code": None, "_usage": reply.usage}

    def route_plan(state):
        if state["error_code"]:
            return "fail"
        return {"clarify": "waiting", "unsupported": "reject", "ready": "context"}[
            state["plan"]["decision"]
        ]

    def waiting(state):
        return {"status": "awaiting_clarification"}

    def clarify(state):
        # interrupt 前没有模型调用或写入；节点重放不会重复扣预算或执行 SQL。
        answer = interrupt(
            {"question": state["plan"]["clarification_question"]}, response_schema=Clarification
        )
        answer = Clarification.model_validate(answer)
        return {
            "clarifications": [answer.text],
            "clarification_count": state["clarification_count"] + 1,
            "status": "running",
            "events": [
                {
                    "run_id": state["run_id"],
                    "node": "clarify",
                    "elapsed_ms": 0,
                    "model_calls": 0,
                    "usage": None,
                    "error_code": None,
                    "provider": state["provider"],
                }
            ],
        }

    def context(state):
        return {"metric_context": METRICS[state["plan"]["metric_id"]]}

    def draft(state, repair=False):
        reply = model.draft(
            QueryPlan.model_validate(state["plan"]),
            repair=repair,
            previous_sqls=state.get("sqls"),
            error=state.get("error_code"),
            remaining_ms=ACTIVE_BUDGET_MS - state["active_ms"],
        )
        candidate = SqlDraft.model_validate(reply.value)
        return {
            "sqls": candidate.sqls,
            "query_results": [],
            "error_code": None,
            "_usage": reply.usage,
        }

    def validate(state):
        validate_plan_sql(QueryPlan.model_validate(state["plan"]), state["sqls"])
        return {"error_code": None}

    def execute(state):
        results = []
        for sql in state["sqls"]:
            result = executor.execute(sql, top_n=state["plan"]["top_n"])
            results.append(
                {
                    "columns": list(result.columns),
                    "rows": [[None if v is None else str(v) for v in row] for row in result.rows],
                    "truncated": result.truncated,
                    "row_cap": result.row_cap,
                    "requested_top_n": result.requested_top_n,
                }
            )
        return {"query_results": results, "error_code": None}

    def route_error(state, success):
        if not state["error_code"]:
            return success
        recoverable = state["error_code"] in {
            "invalid_sql",
            "unsupported_sql",
            "forbidden_source",
            "forbidden_column",
            "invalid_limit",
            "plan_mismatch",
            "database_error",
        }
        return (
            "repair"
            if recoverable and state["repair_count"] < settings.max_repair_count
            else "fail"
        )

    def summarize(state):
        results = [
            QueryResult(
                columns=tuple(r["columns"]),
                rows=tuple(tuple(row) for row in r["rows"]),
                truncated=r["truncated"],
                row_cap=r["row_cap"],
                requested_top_n=r["requested_top_n"],
            )
            for r in state["query_results"]
        ]
        return {
            "report": build_report(QueryPlan.model_validate(state["plan"]), results),
            "status": "succeeded",
        }

    graph = StateGraph(QueryState)
    for name, fn, is_model in [
        ("plan", plan_node, True),
        ("waiting", waiting, False),
        ("context", context, False),
        ("draft", draft, True),
        ("validate", validate, False),
        ("execute", execute, False),
        ("repair", lambda s: draft(s, repair=True), True),
        ("summarize", summarize, False),
        ("fail", lambda _s: {"status": "failed"}, False),
        ("reject", lambda _s: {"status": "rejected"}, False),
    ]:
        graph.add_node(name, timed(name, fn, model_call=is_model))
    graph.add_node("clarify", clarify)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_plan, ["fail", "waiting", "reject", "context"])
    graph.add_edge("waiting", "clarify")
    graph.add_edge("clarify", "plan")
    graph.add_edge("context", "draft")
    graph.add_conditional_edges(
        "draft", lambda s: "fail" if s["error_code"] else "validate", ["fail", "validate"]
    )
    graph.add_conditional_edges(
        "validate", lambda s: route_error(s, "execute"), ["execute", "repair", "fail"]
    )
    graph.add_conditional_edges(
        "execute", lambda s: route_error(s, "summarize"), ["summarize", "repair", "fail"]
    )
    graph.add_conditional_edges(
        "repair", lambda s: "fail" if s["error_code"] else "validate", ["fail", "validate"]
    )
    for name in ["summarize", "fail", "reject"]:
        graph.add_edge(name, END)
    return graph.compile(checkpointer=checkpointer)


class QueryService:
    """CLI/API 共用内核。owner 由调用方服务端提供，内部 thread_id 不接收用户输入。"""

    def __init__(self, executor: QueryExecutor, settings: Settings, checkpointer, *, model=None):
        self.settings = settings
        self.model = model or query_model(settings)
        self.graph = build_query_graph(self.model, executor, settings, checkpointer)

    @staticmethod
    def config(run_id):
        if not isinstance(run_id, str) or not re_full_uuid(run_id):
            raise ValueError("invalid_run_id")
        return {"configurable": {"thread_id": f"shoplens:{run_id}"}, "recursion_limit": 35}

    def start(self, question: str, *, owner_id: str) -> dict[str, Any]:
        initial = self.initial_state(question, owner_id=owner_id)
        self.graph.invoke(initial, self.config(initial["run_id"]))
        return self.read(initial["run_id"], owner_id=owner_id)

    def initial_state(
        self,
        question: str,
        *,
        owner_id: str,
        run_id: str | None = None,
        followup: dict | None = None,
    ) -> dict:
        """服务端创建元数据后可提供自己的随机 run_id；不用于浏览器输入。"""
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
            raise ValueError("invalid_question")
        if not owner_id or len(owner_id) > 128:
            raise ValueError("invalid_owner")
        run_id = run_id or uuid.uuid4().hex
        self.config(run_id)
        return {
            "owner_id": owner_id,
            "run_id": run_id,
            "question": question.strip(),
            "followup": followup,
            "reference_date": self.settings.demo_reference_date,
            "provider": self.model.provider,
            "model_id": self.model.model_id,
            "clarifications": [],
            "clarification_count": 0,
            "sqls": [],
            "query_results": [],
            "repair_count": 0,
            "model_calls": 0,
            "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "active_ms": 0,
            "status": "running",
            "error_code": None,
            "events": [],
        }

    def read(self, run_id: str, *, owner_id: str) -> dict:
        snapshot = self.graph.get_state(self.config(run_id))
        if not snapshot.values or snapshot.values["owner_id"] != owner_id:
            raise PermissionError("run_not_accessible")
        return dict(snapshot.values)

    def resume(self, run_id: str, text: str, *, owner_id: str) -> dict:
        state = self.read(run_id, owner_id=owner_id)
        if state["status"] != "awaiting_clarification":
            raise ValueError("run_not_waiting")
        if state["provider"] != self.model.provider or state["model_id"] != self.model.model_id:
            raise ValueError("provider_changed")
        response = Clarification(text=text.strip())
        self.graph.invoke(Command(resume=response.model_dump()), self.config(run_id))
        return self.read(run_id, owner_id=owner_id)


def re_full_uuid(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)
