"""Scoring, dataset isolation and budget tests against concrete evaluation risks."""

import pytest

from app.config import get_settings
from app.query_executor import QueryExecutor
from app.query_model import ModelError
from app.query_plan import QueryPlan, canonical_queries
from data.dataset import fixture, generate
from evals.cases import SECURITY_SQL, cases
from evals.models import Budget
from evals.reference import expected_tables, score, tables_equal


def test_case_families_are_disjoint_and_fixed():
    rows = cases()
    assert len(rows) == len({r["id"] for r in rows}) == 80
    assert sum(r["split"] == "dev" for r in rows) == 40
    assert len(SECURITY_SQL) >= 20
    assert not (
        {r["family"] for r in rows if r["split"] == "dev"}
        & {r["family"] for r in rows if r["split"] == "frozen"}
    )
    for row in rows:
        if row["expected"]["decision"] == "ready":
            QueryPlan(**row["expected"])


def test_hand_fixture_reference_preserves_cross_period_and_zero_denominator():
    base = {"decision": "ready", "start_date": "2026-08-01", "end_date": "2026-09-01"}
    assert expected_tables(fixture(), base | {"metric_id": "paid_amount"})[0]["rows"] == [["30000"]]
    assert expected_tables(fixture(), base | {"metric_id": "period_net_receipt"})[0]["rows"] == [
        ["24000"]
    ]
    assert expected_tables(fixture(), base | {"metric_id": "paid_order_count"})[0]["rows"] == [
        ["5"]
    ]
    zero = base | {
        "metric_id": "average_order_value",
        "start_date": "2026-08-05",
        "end_date": "2026-08-06",
    }
    assert expected_tables(fixture(), zero)[0]["rows"] == [[None]]
    category = base | {
        "metric_id": "paid_order_count",
        "start_date": "2026-07-01",
        "end_date": "2026-08-01",
        "group_by": ["category"],
    }
    assert expected_tables(fixture(), category)[0]["rows"] == [["家居", "1"], ["数码", "1"]]


def test_scoring_rejects_partial_rows_reordered_ties_null_zero_and_wrong_money():
    expected = [{"columns": ["category", "value"], "rows": [["a", "100"], ["b", "100"]]}]
    for rows in ([["b", "100"], ["a", "100"]], [["a", "100"]], [["a", "101"], ["b", "100"]]):
        assert not tables_equal(
            [{"columns": ["category", "value"], "rows": rows}], expected, "paid_amount"
        )
    assert not tables_equal([expected[0] | {"truncated": True}], expected, "paid_amount")
    assert not tables_equal(
        [{"columns": ["value"], "rows": [["0"]]}],
        [{"columns": ["value"], "rows": [[None]]}],
        "average_order_value",
    )
    assert tables_equal(
        [{"columns": ["value"], "rows": [["6000.0000000001"]]}],
        [{"columns": ["value"], "rows": [["6000"]]}],
        "average_order_value",
    )


def test_wrong_plan_is_not_correct_even_if_numbers_happen_to_match():
    case = cases()[0]
    plan = dict(case["expected"], metric_id="successful_refund")
    state = {"status": "succeeded", "plan": plan, "report": {"tables": []}}
    assert score(case, state, "succeeded", fixture())["failure_class"] == "wrong_plan"


def test_budget_persists_failure_reservations_and_never_resets(tmp_path):
    path = tmp_path / "budget.json"
    budget = Budget(path, max_calls=2, max_tokens=100)
    budget.reserve(60)
    budget.finish(60, None)
    resumed = Budget(path, max_calls=2, max_tokens=100)
    assert resumed.data["unknown_tokens_reserved"] == 60
    with pytest.raises(ModelError, match="evaluation_budget_exceeded"):
        resumed.reserve(41)
    resumed.reserve(40)
    resumed.finish(40, {"total_tokens": 10})
    with pytest.raises(ModelError):
        resumed.reserve(1)
    assert resumed.data["known_tokens"] == 10


@pytest.mark.integration
@pytest.mark.parametrize(
    "metric",
    [
        "paid_amount",
        "successful_refund",
        "period_net_receipt",
        "paid_order_count",
        "average_order_value",
    ],
)
def test_independent_two_dimension_reference_matches_real_reader(metric):
    fields = {
        "decision": "ready",
        "metric_id": metric,
        "group_by": ["channel", "category"],
        "start_date": "2026-09-01",
        "end_date": "2026-10-01",
    }
    plan = QueryPlan(**fields)
    with QueryExecutor(get_settings()) as executor:
        result = executor.execute(canonical_queries(plan)[0])
    actual = [
        {
            "columns": list(result.columns),
            "rows": [[str(v) if v is not None else None for v in row] for row in result.rows],
        }
    ]
    assert tables_equal(actual, expected_tables(generate(), fields), metric)
