"""Followup behavior; PostgreSQL cases use the real reader and persisted workspace."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from test_stage4_workspace import client, create, wait  # noqa: F401

from app.config import Settings
from app.main import app
from app.query_model import DeepSeekQueryModel, ModelReply, OfflineQueryModel
from app.query_plan import QueryPlan
from data.dataset import generate, local
from data.reference import calculate


def base(**changes):
    return QueryPlan.model_validate(
        {
            "decision": "ready",
            "metric_id": "average_order_value",
            "start_date": "2026-09-01",
            "end_date": "2026-10-01",
            **changes,
        }
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    "question,changes",
    [
        ("那各渠道呢？", {"group_by": ["channel"]}),
        ("改成2026年8月", {"start_date": "2026-08-01", "end_date": "2026-09-01"}),
        ("那每天呢？", {"group_by": ["day"]}),
        ("那各类别呢？", {"group_by": ["category"]}),
        ("改为整体", {"group_by": []}),
        ("改看支付订单数", {"metric_id": "paid_order_count"}),
        ("改看成功退款金额", {"metric_id": "successful_refund"}),
        ("改看实付金额", {"metric_id": "paid_amount"}),
        ("改看期间净收款", {"metric_id": "period_net_receipt"}),
    ],
)
def test_offline_followup_changes_only_explicit_conditions(question, changes):
    original = base()
    before = deepcopy(original)
    result = OfflineQueryModel().plan(question, [], "2026-09-30", base_plan=original).value
    assert result.model_dump(mode="json") == {**original, **changes}
    assert original == before


def test_followup_ambiguity_and_clarification_keep_period():
    model = OfflineQueryModel()
    assert (
        model.plan("那销售额呢？", [], "2026-09-30", base_plan=base()).value.decision == "clarify"
    )
    done = model.plan("那销售额呢？", ["期间净收款"], "2026-09-30", base_plan=base()).value
    assert done.metric_id == "period_net_receipt"
    assert str(done.start_date) == "2026-09-01"
    assert model.plan("改成13月", [], "2026-09-30", base_plan=base()).value.decision == "clarify"


@pytest.mark.parametrize(
    "question",
    [
        "只看抖音渠道",
        "为什么下降",
        "预测下月",
        "随便聊聊",
        "那退款率呢？",
        "改成昨天，各渠道",
        "那各渠道利润率呢？",
        "按渠道，看转化数",
    ],
)
def test_followup_does_not_silently_ignore_unsupported_change(question):
    result = OfflineQueryModel().plan(question, [], "2026-09-30", base_plan=base()).value
    assert result.decision in {"clarify", "unsupported"}


def test_top_n_comparison_and_clear_semantics():
    model = OfflineQueryModel()
    original = base(group_by=["channel"], top_n=2)
    cleared = model.plan("不排名，改为整体", [], "2026-09-30", base_plan=original).value
    assert cleared.group_by == [] and cleared.top_n is None
    compared = model.plan("与2026年8月比较", [], "2026-09-30", base_plan=base()).value
    assert str(compared.start_date) == "2026-09-01"
    assert str(compared.comparison_start_date) == "2026-08-01"
    cleared = model.plan(
        "取消比较", [], "2026-09-30", base_plan=compared.model_dump(mode="json")
    ).value
    assert cleared.comparison_start_date is None


def test_date_clarification_overrides_invalid_followup_date():
    result = (
        OfflineQueryModel().plan("改成13月", ["2026年8月"], "2026-09-30", base_plan=base()).value
    )
    assert result.decision == "ready"
    assert str(result.start_date) == "2026-08-01"


def test_clarification_resolves_invalid_top_n_and_multiple_periods():
    model = OfflineQueryModel()
    ranked = model.plan("各渠道前300", ["前2"], "2026-09-30", base_plan=base()).value
    assert ranked.decision == "ready" and ranked.top_n == 2 and ranked.group_by == ["channel"]
    dated = model.plan("改成2026年7月和8月", ["2026年8月"], "2026-09-30", base_plan=base()).value
    assert dated.decision == "ready" and str(dated.start_date) == "2026-08-01"


def test_patch_rejects_privileges_and_incomplete_dates():
    from pydantic import ValidationError

    from app.query_followup import FollowupPatch, merge_followup

    with pytest.raises(ValidationError):
        FollowupPatch(decision="ready", owner_id="chosen")
    result = merge_followup(base(), FollowupPatch(decision="ready", start_date="2026-08-01"))
    assert result.decision == "clarify"


def test_empty_or_conflicting_change_requests_need_clarification():
    from app.query_followup import FollowupPatch, merge_followup

    for patch in [
        FollowupPatch(decision="ready"),
        FollowupPatch(decision="ready", group_by=[], top_n=2),
        FollowupPatch(decision="ready", top_n=2, clear_top_n=True),
    ]:
        assert merge_followup(base(), patch).decision == "clarify"


def test_cancel_comparison_with_new_date_and_unknown_filter_are_not_ignored():
    model = OfflineQueryModel()
    original = base(comparison_start_date="2026-08-01", comparison_end_date="2026-09-01")
    result = model.plan("取消比较，改成2026年7月", [], "2026-09-30", base_plan=original).value
    assert str(result.start_date) == "2026-07-01"
    assert result.comparison_start_date is None
    filtered = model.plan("改成2026年8月，只统计抖音渠道", [], "2026-09-30", base_plan=base()).value
    assert filtered.decision != "ready"


def test_worker_releases_capacity_when_initial_snapshot_read_fails():
    import threading

    from app.workspace import Workspace

    worker = Workspace.__new__(Workspace)
    worker.slots = threading.BoundedSemaphore(2)
    worker.slots.acquire()

    def fail_read(*args):
        raise TimeoutError

    worker.read = fail_read
    saved = []
    worker.save = lambda *args, **kwargs: saved.append(args[1])
    worker.work("a" * 32, "alice", "那各渠道呢？", None)
    assert worker.slots.acquire(False)
    assert worker.slots.acquire(False)
    assert saved[0]["status"] == "failed"


def test_followup_still_blocks_bad_sql_before_execution():
    from langgraph.checkpoint.memory import InMemorySaver
    from test_stage3_query_graph import RecordingExecutor

    from app.config import get_settings
    from app.query_graph import QueryService

    executor = RecordingExecutor()
    service = QueryService(
        executor, get_settings(), InMemorySaver(), model=OfflineQueryModel(fault="always_bad")
    )
    initial = service.initial_state(
        "那各渠道呢？",
        owner_id="alice",
        followup={"run_id": "a" * 32, "question": "2026年9月整体客单价", "plan": base()},
    )
    service.graph.invoke(initial, service.config(initial["run_id"]))
    result = service.read(initial["run_id"], owner_id="alice")
    assert result["status"] == "failed"
    assert result["repair_count"] == 1
    assert executor.sqls == []


def test_real_adapter_merges_typed_change_without_original_question(monkeypatch):
    from app.query_followup import FollowupPatch

    observed = []

    def invoke(self, schema, system, payload, **kwargs):
        observed.append((schema, payload))
        return ModelReply(FollowupPatch(decision="ready", group_by=["channel"]))

    monkeypatch.setattr(DeepSeekQueryModel, "_invoke", invoke)
    result = (
        DeepSeekQueryModel(Settings(_env_file=None, deepseek_api_key="test-placeholder"))
        .plan(
            "那各渠道呢？",
            [],
            "2026-09-30",
            base_plan=base(),
            remaining_ms=1000,
        )
        .value
    )
    assert result.model_dump(mode="json") == base(group_by=["channel"])
    assert observed[0][0] is FollowupPatch
    assert observed[0][1]["base_plan"] == base()
    assert "question" in observed[0][1] and "report" not in observed[0][1]


def follow(http, headers, source, text):
    response = http.post(
        f"/api/runs/{source['run_id']}/followup", json={"question": text}, headers=headers
    )
    assert response.status_code == 202, response.text
    return wait(http, response.json()["run_id"])


@pytest.mark.integration
def test_persisted_followup_and_independent_query(client):  # noqa: F811
    http, headers = client
    original = create(http, headers, "2026年9月整体客单价是多少？")
    result = follow(http, headers, original, "那各渠道呢？")
    assert result["status"] == "succeeded", result
    assert result["source_run_id"] == original["run_id"]
    assert result["source_question"] == original["question"]
    assert result["plan"]["group_by"] == ["channel"]
    data = generate()
    expected = {
        c: calculate(data, local("2026-09-01", 0, 0), local("2026-10-01", 0, 0), channel=c)[
            "average_order_value"
        ]
        for c in {o["channel"] for o in data["orders"]}
    }
    actual = {c: float(v) for c, v in result["report"]["tables"][0]["rows"]}
    assert actual == pytest.approx(expected)
    assert http.get(f"/api/runs/{original['run_id']}").json() == original
    assert http.get(f"/api/runs/{result['run_id']}").json() == result
    assert (
        next(r for r in http.get("/api/runs").json()["runs"] if r["run_id"] == result["run_id"])[
            "source_run_id"
        ]
        == original["run_id"]
    )
    august = follow(http, headers, result, "改成2026年8月")
    assert august["plan"]["start_date"] == "2026-08-01"
    assert august["plan"]["group_by"] == ["channel"]
    ambiguous = follow(http, headers, original, "那销售额呢？")
    assert ambiguous["status"] == "awaiting_clarification"
    response = http.post(
        f"/api/runs/{ambiguous['run_id']}/clarify",
        json={"text": "期间净收款", "revision": ambiguous["revision"]},
        headers=headers,
    )
    assert response.status_code == 202
    done = wait(http, ambiguous["run_id"])
    assert done["status"] == "succeeded"
    assert done["plan"]["start_date"] == "2026-09-01"
    assert done["source_run_id"] == original["run_id"]
    independent = create(http, headers, "那各渠道呢？")
    assert independent["status"] != "succeeded" and independent["source_run_id"] is None


@pytest.mark.integration
def test_followup_authorization_status_input_and_retry(client):  # noqa: F811
    http, headers = client
    original = create(http, headers, "2026年9月客单价")
    path = f"/api/runs/{original['run_id']}/followup"
    other = TestClient(app)
    token = other.get("/api/session").json()["csrf_token"]
    try:
        assert (
            other.post(
                path, json={"question": "那各渠道呢？"}, headers={"x-csrf-token": token}
            ).status_code
            == 404
        )
        assert other.get("/api/runs").json()["runs"] == []
        assert http.post(path, json={"question": "那各渠道呢？"}).status_code == 403
        assert (
            http.post(
                path,
                json={"question": "那各渠道呢？", "base_plan": base(), "owner_id": "chosen"},
                headers=headers,
            ).status_code
            == 422
        )
        rejected = create(http, headers, "预测下月")
        assert (
            http.post(
                f"/api/runs/{rejected['run_id']}/followup",
                json={"question": "各渠道"},
                headers=headers,
            ).status_code
            == 409
        )
        ambiguous = follow(http, headers, original, "那销售额呢？")
        with app.state.workspace.pool.connection() as conn:
            conn.execute(
                "UPDATE app_state.runs SET clarification_expires_at=now()-interval '1 minute' WHERE run_id=%s",
                (ambiguous["run_id"],),
            )
        response = http.post(f"/api/runs/{ambiguous['run_id']}/retry", json={}, headers=headers)
        assert response.status_code == 202
        retried = wait(http, response.json()["run_id"])
        assert retried["run_id"] != ambiguous["run_id"]
        assert retried["source_run_id"] == original["run_id"]
        assert retried["status"] == "awaiting_clarification"
    finally:
        import hashlib

        from app.workspace import COOKIE

        with app.state.workspace.pool.connection() as conn:
            conn.execute(
                "DELETE FROM app_state.sessions WHERE token_hash=%s",
                (hashlib.sha256(other.cookies[COOKIE].encode()).hexdigest(),),
            )
        other.close()


@pytest.mark.integration
def test_failed_worker_read_keeps_source_for_retry(client, monkeypatch):  # noqa: F811
    import threading

    http, headers = client
    original = create(http, headers, "2026年9月客单价")
    workspace = app.state.workspace
    read = workspace.read
    failed_once = False

    def fail_once(run_id, owner):
        nonlocal failed_once
        if threading.current_thread().name.startswith("shoplens-query") and not failed_once:
            failed_once = True
            raise TimeoutError
        return read(run_id, owner)

    monkeypatch.setattr(workspace, "read", fail_once)
    failed = follow(http, headers, original, "那各渠道呢？")
    assert failed["status"] == "failed"
    assert failed["source_run_id"] == original["run_id"]
    response = http.post(f"/api/runs/{failed['run_id']}/retry", json={}, headers=headers)
    assert response.status_code == 202
    retried = wait(http, response.json()["run_id"])
    assert retried["status"] == "succeeded"
    assert retried["source_run_id"] == original["run_id"]
    assert retried["plan"]["group_by"] == ["channel"]
