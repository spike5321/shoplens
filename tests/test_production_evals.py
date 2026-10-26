"""Production baseline must measure the unchanged product and retain budget failures."""

import pytest

from app.config import Settings
from app.query_model import DeepSeekQueryModel, ModelError, ModelReply
from app.query_plan import QueryPlan, SqlDraft
from evals import runner
from evals.models import PROFILES, Budget


def settings(**changes):
    return Settings(_env_file=None, deepseek_api_key="test-placeholder", **changes)


def test_production_uses_identical_product_prompts_and_payloads(monkeypatch, tmp_path):
    from evals.production import ProductionModel

    observed = []

    def invoke(self, schema, system, payload, *, remaining_ms):
        observed.append((schema, system, payload, remaining_ms))
        value = (
            QueryPlan(decision="unsupported", unsupported_reason="test")
            if schema is QueryPlan
            else SqlDraft(sqls=["SELECT 1"])
        )
        return ModelReply(value, {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11})

    monkeypatch.setattr(DeepSeekQueryModel, "_invoke", invoke)
    config = settings()
    budget = Budget(tmp_path / "budget.json")
    direct, wrapped = DeepSeekQueryModel(config), ProductionModel(config, budget)
    plan = QueryPlan(
        decision="ready", metric_id="paid_amount", start_date="2026-09-01", end_date="2026-10-01"
    )
    for model in (direct, wrapped):
        model.plan("9月实付", [], "2026-09-30", remaining_ms=1234)
        model.draft(
            plan, repair=True, previous_sqls=["SELECT 1"], error="invalid_sql", remaining_ms=567
        )
    assert observed[:2] == observed[2:]
    assert all("protocol_examples" not in call[2] for call in observed)
    assert budget.data["calls"] == len(wrapped.calls) == 2
    assert budget.data["known_tokens"] == 22
    assert budget.data["unknown_tokens_reserved"] == 0


def test_production_failure_preserves_unknown_usage_and_error(monkeypatch, tmp_path):
    from evals.production import ProductionModel

    def fail(*args, **kwargs):
        raise ModelError("model_protocol_error")

    monkeypatch.setattr(DeepSeekQueryModel, "_invoke", fail)
    budget = Budget(tmp_path / "budget.json")
    model = ProductionModel(settings(), budget)
    with pytest.raises(ModelError, match="model_protocol_error"):
        model.plan("9月实付", [], "2026-09-30", remaining_ms=1000)
    assert budget.data["calls"] == 1
    assert budget.data["unknown_tokens_reserved"] > 0
    assert budget.data["inflight_reserved"] == 0
    assert model.calls[0]["usage"] is None
    assert model.calls[0]["error_code"] == "model_protocol_error"


def test_exhausted_budget_never_calls_product(monkeypatch, tmp_path):
    from evals.production import ProductionModel

    def forbidden(*args, **kwargs):
        pytest.fail("provider called after budget exhausted")

    monkeypatch.setattr(DeepSeekQueryModel, "_invoke", forbidden)
    budget = Budget(tmp_path / "budget.json", max_calls=1, initial_calls=1)
    model = ProductionModel(settings(), budget)
    with pytest.raises(ModelError, match="evaluation_budget_exceeded"):
        model.plan("9月实付", [], "2026-09-30", remaining_ms=1000)
    assert model.calls == []
    assert budget.data["calls"] == 1


def test_profile_settings_preserve_product_limits_and_legacy_profiles():
    assert hasattr(runner, "profile_settings"), "production profile selection is missing"
    config = settings(max_repair_count=0, sql_max_rows=51, demo_reference_date="2026-09-29")
    assert runner.profile_settings("production", config).model_dump() == config.model_dump()
    assert runner.profile_settings("repair", config).max_repair_count == 1
    assert runner.profile_settings("semantic", config).max_repair_count == 0
    assert PROFILES == {"schema": 0, "semantic": 0, "repair": 1}


def test_summary_includes_production_failures_in_denominator():
    row = {
        "profile": "production",
        "split": "dev",
        "answerable": True,
        "repair_count": 0,
        "expected_initial": "ready",
        "events": [],
        "elapsed_ms": 10,
        "api_calls": [],
        "correct": False,
        "status": "failed",
        "statuses": ["failed"],
        "model_calls": 0,
        "initial_behavior_correct": True,
        "failure_class": "model_protocol_error",
    }
    summary = runner.summarize([row])
    assert "production/dev" in summary, "production results are missing from summary"
    assert summary["production/dev"]["result_correct"] == {"passed": 0, "total": 1, "rate": 0}
    assert summary["production/dev"]["failure_classes"] == {"model_protocol_error": 1}
