"""Query flow behavior; memory tests do not replace PostgreSQL integration checks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from decimal import Decimal

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr, ValidationError

from app.config import Settings, get_settings
from app.query_checkpoint import postgres_checkpointer
from app.query_executor import QueryError, QueryErrorCode, QueryExecutor, QueryResult
from app.query_graph import QueryService
from app.query_model import DeepSeekQueryModel, ModelReply, OfflineQueryModel
from app.query_plan import QueryPlan, build_report, canonical_queries, validate_plan_sql
from data.dataset import generate, local
from data.reference import calculate


def plan(**updates):
    fields = {
        "decision": "ready",
        "metric_id": "paid_amount",
        "start_date": "2026-09-01",
        "end_date": "2026-10-01",
    }
    return QueryPlan(**(fields | updates))


class RecordingExecutor:
    def __init__(self):
        self.sqls = []

    def execute(self, sql, *, top_n=None):
        self.sqls.append(sql)
        return QueryResult(("value",), ((10000,),), False, 200, top_n)


def memory_service(*, model=None):
    executor = RecordingExecutor()
    return QueryService(
        executor, get_settings(), InMemorySaver(), model=model or OfflineQueryModel()
    ), executor


def test_plan_rejects_privilege_fields_and_invalid_dates():
    for updates in [
        {"owner_id": "attacker"},
        {"repair_count": 0},
        {"metric_id": "revenue"},
        {"end_date": "2026-08-01"},
        {"top_n": 3},
        {"comparison_start_date": "2026-08-01"},
    ]:
        with pytest.raises(ValidationError):
            plan(**updates)


@pytest.mark.parametrize(
    "replacement",
    [
        ("2026-09-01", "2026-08-01"),
        ("SUM(paid_cents)", "SUM(DISTINCT paid_cents)"),
        ("SUM(paid_cents)", "COUNT(DISTINCT order_id)"),
    ],
)
def test_valid_sql_with_wrong_business_semantics_is_rejected(replacement):
    p = plan()
    sql = canonical_queries(p)[0].replace(*replacement)
    with pytest.raises(QueryError) as caught:
        validate_plan_sql(p, [sql])
    assert caught.value.code == QueryErrorCode.PLAN_MISMATCH


def test_alias_and_equivalent_timezone_are_accepted():
    p = plan()
    sql = canonical_queries(p)[0].replace("SUM(paid_cents)", "SUM(s.paid_cents)")
    sql = sql.replace("analytics.sales_lines", "analytics.sales_lines AS s")
    sql = sql.replace("2026-09-01T00:00:00+08:00", "2026-08-31T16:00:00+00:00")
    validate_plan_sql(p, [sql])


def test_clarification_preserves_owner_and_rejects_other_owner_or_duplicate():
    service, executor = memory_service()
    paused = service.start("2026年9月销售额", owner_id="alice")
    assert paused["status"] == "awaiting_clarification"
    assert executor.sqls == []
    with pytest.raises(PermissionError):
        service.resume(paused["run_id"], "实付金额", owner_id="bob")
    done = service.resume(paused["run_id"], "实付金额", owner_id="alice")
    assert done["status"] == "succeeded"
    assert done["owner_id"] == "alice"
    assert done["model_calls"] == 3
    assert len(executor.sqls) == 1
    with pytest.raises(ValueError):
        service.resume(paused["run_id"], "期间净收款", owner_id="alice")


@pytest.mark.parametrize(
    ("fault", "status", "executions"),
    [("missing_column", "succeeded", 1), ("always_bad", "failed", 0)],
)
def test_single_repair_and_invalid_sql_never_reaches_executor(fault, status, executions):
    service, executor = memory_service(model=OfflineQueryModel(fault=fault))
    result = service.start("2026年9月实付金额", owner_id="test")
    assert result["status"] == status
    assert result["repair_count"] == 1
    assert result["model_calls"] == 3
    assert len(executor.sqls) == executions
    assert sum(e["node"] == "repair" for e in result["events"]) == 1


def test_refusal_missing_date_and_fixed_reference_date():
    service, executor = memory_service()
    assert service.start("预测2026年10月实付金额", owner_id="t")["status"] == "rejected"
    assert service.start("实付金额", owner_id="t")["status"] == "awaiting_clarification"
    relative = service.start("上月实付金额", owner_id="t")
    assert relative["plan"]["start_date"] == "2026-08-01"
    assert relative["reference_date"] == "2026-09-30"
    assert len(executor.sqls) == 1


def test_clarification_limit_stops_model_loop():
    service, executor = memory_service()
    result = service.start("销售额", owner_id="t")
    for _ in range(2):
        result = service.resume(result["run_id"], "不知道", owner_id="t")
    assert result["status"] == "failed"
    assert result["error_code"] == "clarification_limit"
    assert result["model_calls"] == 3
    assert not executor.sqls


def test_server_guards_model_hallucinated_period():
    class TooEager(OfflineQueryModel):
        def plan(self, *_args, **_kwargs):
            return ModelReply(plan())

    service, executor = memory_service(model=TooEager())
    assert service.start("实付金额", owner_id="t")["status"] == "awaiting_clarification"
    assert service.start("2026年9月销售额", owner_id="t")["status"] == "awaiting_clarification"
    assert not executor.sqls


def test_active_budget_stops_before_sql(monkeypatch):
    from app import query_graph

    monkeypatch.setattr(query_graph, "ACTIVE_BUDGET_MS", 10)

    class SlowModel(OfflineQueryModel):
        def plan(self, *_args, **_kwargs):
            time.sleep(0.02)
            return ModelReply(plan())

    service, executor = memory_service(model=SlowModel())
    result = service.start("2026年9月实付金额", owner_id="t")
    assert result["status"] == "failed" and result["error_code"] == "budget_exceeded"
    assert not executor.sqls


def test_truncation_blocks_chart_and_comparison_and_zero_baseline():
    p = plan(comparison_start_date="2026-08-01", comparison_end_date="2026-09-01")
    full = QueryResult(("value",), ((Decimal(100),),), False, 200, None)
    partial = QueryResult(("value",), ((Decimal(100),),), True, 200, None)
    report = build_report(p, [partial, full])
    assert report["chart"] is None and report["comparison"] == []
    assert "截断" in report["explanation"]
    zero = QueryResult(("value",), ((0,),), False, 200, None)
    comparison = build_report(p, [full, zero])["comparison"][0]
    assert comparison["delta"] == "100"
    assert comparison["change_pct"] is None


def test_langchain_adapter_extracts_typed_tools_and_usage(monkeypatch):
    from scripts import smoke_real_model

    seen = []
    p = plan()

    def respond(body):
        seen.append(body)
        name = body["tools"][0]["function"]["name"]
        args = p.model_dump(mode="json") if name == "QueryPlan" else {"sqls": canonical_queries(p)}
        return {
            "id": "fake",
            "object": "chat.completion",
            "created": 0,
            "model": "fake",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call1",
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

    monkeypatch.setattr(smoke_real_model, "build_stub_response", respond)
    server, url = smoke_real_model.start_stub()
    try:
        settings = Settings(
            _env_file=None, deepseek_api_key=SecretStr("fake-test-key"), deepseek_base_url=url
        )
        adapter = DeepSeekQueryModel(settings)
        reply = adapter.plan("2026年9月实付金额", [], "2026-09-30", remaining_ms=10000)
        assert reply.value == p
        assert reply.usage["total_tokens"] == 8
        draft = adapter.draft(p, remaining_ms=10000)
        validate_plan_sql(p, draft.value.sqls)
        assert seen[0]["tools"][0]["function"]["parameters"]["additionalProperties"] is False
        assert "manual_cases" not in json.dumps(seen)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.integration
def test_real_graph_matches_independent_raw_records_and_chart():
    settings = get_settings()
    expected = calculate(generate(), local("2026-09-01", 0), local("2026-10-01", 0))
    with QueryExecutor(settings) as executor, postgres_checkpointer(settings, setup=True) as saver:
        service = QueryService(executor, settings, saver)
        for label, metric in [
            ("实付金额", "paid_amount"),
            ("成功退款金额", "successful_refund"),
            ("期间净收款", "period_net_receipt"),
            ("支付订单数", "paid_order_count"),
            ("客单价", "average_order_value"),
        ]:
            result = service.start(f"2026年9月{label}", owner_id="integration")
            assert result["status"] == "succeeded"
            value = Decimal(result["report"]["tables"][0]["rows"][0][0])
            assert abs(value - Decimal(str(expected[metric]))) < Decimal("1e-8")
            saver.delete_thread(service.config(result["run_id"])["configurable"]["thread_id"])
        grouped = service.start("2026年9月每天实付金额", owner_id="integration")
        report = grouped["report"]
        assert report["chart"]["type"] == "line"
        assert report["chart"]["values"] == [row[-1] for row in report["tables"][0]["rows"]]
        saver.delete_thread(service.config(grouped["run_id"])["configurable"]["thread_id"])
        top = service.start("2026年9月按渠道实付金额前2", owner_id="integration")
        assert top["status"] == "succeeded"
        top_table = top["report"]["tables"][0]
        assert len(top_table["rows"]) == 2 and top_table["requested_top_n"] == 2
        assert "Top 2" in top["report"]["explanation"]
        saver.delete_thread(service.config(top["run_id"])["configurable"]["thread_id"])


@pytest.mark.integration
def test_graph_comparison_and_empty_average():
    settings = get_settings()
    with QueryExecutor(settings) as executor, postgres_checkpointer(settings, setup=True) as saver:
        service = QueryService(executor, settings, saver)
        result = service.start("2026年9月期间净收款比2026年8月比较", owner_id="integration")
        assert result["status"] == "succeeded"
        a, b = [Decimal(table["rows"][0][0]) for table in result["report"]["tables"]]
        assert Decimal(result["report"]["comparison"][0]["delta"]) == a - b
        saver.delete_thread(service.config(result["run_id"])["configurable"]["thread_id"])
        empty = service.start("2026-11-01 至 2026-12-01 客单价", owner_id="integration")
        assert empty["report"]["tables"][0]["rows"] == [[None]]
        assert "不可计算" in empty["report"]["explanation"]
        saver.delete_thread(service.config(empty["run_id"])["configurable"]["thread_id"])


@pytest.mark.integration
def test_pause_and_resume_in_separate_processes_and_reader_is_denied():
    owner = "restart-test-" + uuid.uuid4().hex
    settings = get_settings()
    with postgres_checkpointer(settings, setup=True):
        pass
    prefix = [sys.executable, "scripts/query_cli.py", "--owner", owner]
    first = subprocess.run(
        [*prefix, "ask", "2026年9月销售额"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    paused = json.loads(first.stdout)
    assert paused["status"] == "awaiting_clarification"
    second = subprocess.run(
        [*prefix, "resume", paused["run_id"], "期间净收款"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    done = json.loads(second.stdout)
    assert done["status"] == "succeeded"
    assert done["run_id"] == paused["run_id"] and done["owner_id"] == owner
    assert done["model_calls"] == 3
    assert sum(e["node"] == "waiting" for e in done["events"]) == 1
    assert sum(e["node"] == "clarify" for e in done["events"]) == 1
    with (
        psycopg.connect(settings.dsn("reader")) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute("SELECT COUNT(*) FROM app_state.checkpoints")
    with postgres_checkpointer(settings) as saver:
        saver.delete_thread(QueryService.config(paused["run_id"])["configurable"]["thread_id"])


def test_real_graph_smoke_missing_key_is_not_run():
    env = os.environ.copy()
    env["SHOPLENS_DEEPSEEK_API_KEY"] = ""
    result = subprocess.run(
        [sys.executable, "scripts/smoke_query_graph.py", "--real-model"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2 and "NOT_RUN" in result.stdout
