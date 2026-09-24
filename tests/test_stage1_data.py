"""Stage 1 business invariants; no model calls or answer leakage."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from data.dataset import fixture, generate, local, validate
from data.reference import calculate, check_manual_fixture, group_by_category, manual_cases


def test_fixture_manual_answers_and_boundaries():
    assert len(manual_cases()) >= 10
    check_manual_fixture()
    data = fixture()
    assert (
        calculate(data, local("2026-09-01", 0, 0), local("2026-10-01", 0, 0))["period_net_receipt"]
        == 0
    )
    assert (
        calculate(data, local("2026-08-05", 0, 0), local("2026-08-06", 0, 0))["average_order_value"]
        is None
    )


def test_category_order_count_is_not_additive():
    data = fixture()
    start, end = local("2026-07-01", 0, 0), local("2026-08-01", 0, 0)
    by_category = group_by_category(data, start, end)
    total = calculate(data, start, end)
    assert total["paid_order_count"] == 1
    assert by_category["数码"]["paid_order_count"] == 1
    assert by_category["家居"]["paid_order_count"] == 1
    assert by_category["数码"]["average_order_value"] == 6000
    assert by_category["家居"]["average_order_value"] == 4000
    assert total["average_order_value"] == 10000


def test_generator_is_deterministic_and_valid():
    first, second = generate(), generate()
    assert first == second
    validate(first)
    assert len(first["orders"]) == 3000
    assert len({o["order_id"] for o in first["orders"]}) == 3000


def test_invalid_refund_total_is_rejected_before_load():
    data = deepcopy(fixture())
    data["refunds"].append(
        {
            "refund_id": 99,
            "item_id": 1,
            "amount_cents": 3001,
            "completed_at": local("2026-09-06"),
            "status": "succeeded",
        }
    )
    with pytest.raises(AssertionError):
        validate(data)


def test_metric_dictionary_has_five_complete_definitions():
    path = Path(__file__).resolve().parents[1] / "data" / "metrics.yaml"
    dictionary = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert dictionary["timezone"] == "Asia/Shanghai"
    assert set(dictionary["metrics"]) == {
        "paid_amount",
        "successful_refund",
        "period_net_receipt",
        "paid_order_count",
        "average_order_value",
    }
    for metric in dictionary["metrics"].values():
        assert {"definition", "grain", "unit", "time_field", "view", "dimensions"} <= metric.keys()
        assert metric["view"].startswith("analytics.")
