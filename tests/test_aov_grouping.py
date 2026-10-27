"""Selected grouping must be unambiguous at the production SQL-generation boundary."""

from copy import deepcopy

import pytest

from app.config import Settings
from app.query_executor import QueryError, QueryErrorCode
from app.query_model import DeepSeekQueryModel, ModelReply
from app.query_plan import METRICS, QueryPlan, SqlDraft, canonical_queries, validate_plan_sql


def aov_plan(groups=()):
    return QueryPlan(
        decision="ready",
        metric_id="average_order_value",
        start_date="2026-09-01",
        end_date="2026-10-01",
        group_by=list(groups),
    )


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("groups", [[], ["channel"], ["category"], ["day", "channel"]])
def test_draft_keeps_selected_groups_without_available_dimension_menu(monkeypatch, repair, groups):
    before = deepcopy(METRICS)
    selected = aov_plan(groups)
    observed = []

    def capture(self, schema, system, payload, *, remaining_ms):
        observed.append(payload)
        return ModelReply(SqlDraft(sqls=canonical_queries(selected)))

    monkeypatch.setattr(DeepSeekQueryModel, "_invoke", capture)
    model = DeepSeekQueryModel(Settings(_env_file=None, deepseek_api_key="test-placeholder"))
    previous = canonical_queries(aov_plan(["day"])) if repair else None
    model.draft(
        selected,
        repair=repair,
        previous_sqls=previous,
        error="plan_mismatch" if repair else None,
        remaining_ms=1000,
    )
    payload = observed[0]
    assert "dimensions" not in payload["metric"], "available dimensions leak into SQL drafting"
    assert payload["plan"] == selected.model_dump(mode="json")
    assert payload["metric"] == {
        key: value for key, value in before["average_order_value"].items() if key != "dimensions"
    }
    assert payload["previous_sqls"] == previous
    assert payload["repair"] == repair
    assert before == METRICS


@pytest.mark.parametrize("groups", [["day"], ["day", "channel", "category"]])
def test_original_extra_grouping_is_still_rejected(groups):
    with pytest.raises(QueryError) as caught:
        validate_plan_sql(aov_plan(), canonical_queries(aov_plan(groups)))
    assert caught.value.code == QueryErrorCode.PLAN_MISMATCH
