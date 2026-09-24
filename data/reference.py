"""Independent Python reference calculations over raw records, without SQL/views."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from data.dataset import fixture, local


def calculate(
    data: dict[str, list[dict]],
    start: datetime,
    end: datetime,
    *,
    category: str | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    """Compute each metric from source records, respecting each event's own clock."""
    if start >= end:
        raise ValueError("empty or reversed interval")
    products = {p["product_id"]: p for p in data["products"]}
    orders = {o["order_id"]: o for o in data["orders"]}
    items = {i["item_id"]: i for i in data["order_items"]}
    paid = refund = 0
    order_ids: set[int] = set()
    for item in items.values():
        order = orders[item["order_id"]]
        product = products[item["product_id"]]
        if category is not None and product["category"] != category:
            continue
        if channel is not None and order["channel"] != channel:
            continue
        when = order["paid_at"]
        if order["status"] == "paid" and when is not None and start <= when < end:
            paid += item["paid_cents"]
            order_ids.add(order["order_id"])
    for event in data["refunds"]:
        if event["status"] != "succeeded":
            continue
        item = items[event["item_id"]]
        order = orders[item["order_id"]]
        product = products[item["product_id"]]
        if category is not None and product["category"] != category:
            continue
        if channel is not None and order["channel"] != channel:
            continue
        if start <= event["completed_at"] < end:
            refund += event["amount_cents"]
    count = len(order_ids)
    return {
        "paid_amount": paid,
        "successful_refund": refund,
        "period_net_receipt": paid - refund,
        "paid_order_count": count,
        "average_order_value": paid / count if count else None,
    }


def manual_cases() -> list[dict]:
    """Expected values were calculated by hand, not by `calculate` or SQL."""
    return [
        {
            "question": "7月实付多少？",
            "start": "2026-07-01",
            "end": "2026-08-01",
            "metric": "paid_amount",
            "expected": 10000,
        },
        {
            "question": "8月实付多少？两个100元订单是否都计入？",
            "start": "2026-08-01",
            "end": "2026-09-01",
            "metric": "paid_amount",
            "expected": 30000,
        },
        {
            "question": "8月成功退款多少？",
            "start": "2026-08-01",
            "end": "2026-09-01",
            "metric": "successful_refund",
            "expected": 6000,
        },
        {
            "question": "8月期间净收款多少？",
            "start": "2026-08-01",
            "end": "2026-09-01",
            "metric": "period_net_receipt",
            "expected": 24000,
        },
        {
            "question": "8月支付订单数多少？",
            "start": "2026-08-01",
            "end": "2026-09-01",
            "metric": "paid_order_count",
            "expected": 5,
        },
        {
            "question": "8月客单价多少分？",
            "start": "2026-08-01",
            "end": "2026-09-01",
            "metric": "average_order_value",
            "expected": 6000,
        },
        {
            "question": "9月实付多少？9月30日23:59计入吗？",
            "start": "2026-09-01",
            "end": "2026-10-01",
            "metric": "paid_amount",
            "expected": 8000,
        },
        {
            "question": "9月退款多少？包括7月订单和8月订单的跨期退款吗？",
            "start": "2026-09-01",
            "end": "2026-10-01",
            "metric": "successful_refund",
            "expected": 8000,
        },
        {
            "question": "9月期间净收款多少？",
            "start": "2026-09-01",
            "end": "2026-10-01",
            "metric": "period_net_receipt",
            "expected": 0,
        },
        {
            "question": "7月数码类别支付订单数？",
            "start": "2026-07-01",
            "end": "2026-08-01",
            "category": "数码",
            "metric": "paid_order_count",
            "expected": 1,
        },
        {
            "question": "7月家居类别支付订单数？同一订单跨两类",
            "start": "2026-07-01",
            "end": "2026-08-01",
            "category": "家居",
            "metric": "paid_order_count",
            "expected": 1,
        },
        {
            "question": "7月数码类别客单价多少分？",
            "start": "2026-07-01",
            "end": "2026-08-01",
            "category": "数码",
            "metric": "average_order_value",
            "expected": 6000,
        },
        {
            "question": "8月5日没有成交，客单价？",
            "start": "2026-08-05",
            "end": "2026-08-06",
            "metric": "average_order_value",
            "expected": None,
        },
        {
            "question": "10月1日零点的支付是否被9月排除？",
            "start": "2026-10-01",
            "end": "2026-10-02",
            "metric": "paid_amount",
            "expected": 9000,
        },
    ]


def check_manual_fixture() -> None:
    data = fixture()
    for case in manual_cases():
        result = calculate(
            data,
            local(case["start"], 0, 0),
            local(case["end"], 0, 0),
            category=case.get("category"),
        )
        actual = result[case["metric"]]
        if actual != case["expected"]:
            raise AssertionError(f"{case['question']}: expected {case['expected']}, got {actual}")


def group_by_category(
    data: dict[str, list[dict]], start: datetime, end: datetime
) -> dict[str, dict]:
    categories = {p["category"] for p in data["products"]}
    return {category: calculate(data, start, end, category=category) for category in categories}


def group_by_channel(
    data: dict[str, list[dict]], start: datetime, end: datetime
) -> dict[str, dict]:
    channels = {o["channel"] for o in data["orders"]}
    return {channel: calculate(data, start, end, channel=channel) for channel in channels}


def assert_generator_constraints(data: dict[str, list[dict]]) -> None:
    """Additional snapshot checks beyond per-row validation."""
    from data.dataset import validate

    validate(data)
    assert len(data["orders"]) == 3000
    assert len({o["order_id"] for o in data["orders"]}) == 3000
    counts = defaultdict(int)
    for refund in data["refunds"]:
        if refund["status"] == "succeeded":
            counts[refund["item_id"]] += 1
    assert counts[1] == 2
