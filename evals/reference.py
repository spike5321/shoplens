"""Independent raw-record answer comparison, without generated or canonical SQL."""

from datetime import date, timedelta
from decimal import Decimal

from app.query_plan import QueryPlan
from data.dataset import TZ, local
from data.reference import calculate


def expected_tables(data, fields):
    plan = QueryPlan(**fields)
    products = {r["product_id"]: r for r in data["products"]}
    orders = {r["order_id"]: r for r in data["orders"]}
    items = {r["item_id"]: r for r in data["order_items"]}
    events = []
    if plan.metric_id != "successful_refund":
        for item in items.values():
            order = orders[item["order_id"]]
            if order["status"] == "paid":
                events.append(
                    (order["paid_at"], order["channel"], products[item["product_id"]]["category"])
                )
    if plan.metric_id in {"successful_refund", "period_net_receipt"}:
        for refund in data["refunds"]:
            if refund["status"] == "succeeded":
                item = items[refund["item_id"]]
                order = orders[item["order_id"]]
                events.append(
                    (
                        refund["completed_at"],
                        order["channel"],
                        products[item["product_id"]]["category"],
                    )
                )
    tables = []
    for start, end in plan.periods():
        left, right = local(start.isoformat(), 0), local(end.isoformat(), 0)
        keys = {
            tuple(
                {
                    "day": when.astimezone(TZ).date().isoformat(),
                    "channel": channel,
                    "category": category,
                }[group]
                for group in plan.group_by
            )
            for when, channel, category in events
            if left <= when < right
        }
        if not plan.group_by:
            keys = {()}
        rows = []
        for key in sorted(keys):
            dimensions = dict(zip(plan.group_by, key, strict=True))
            a, b = left, right
            if "day" in dimensions:
                day = date.fromisoformat(dimensions["day"])
                a, b = local(day.isoformat(), 0), local((day + timedelta(days=1)).isoformat(), 0)
            values = calculate(
                data, a, b, category=dimensions.get("category"), channel=dimensions.get("channel")
            )
            value = values[plan.metric_id]
            if plan.metric_id == "average_order_value" and values["paid_order_count"]:
                value = Decimal(values["paid_amount"]) / values["paid_order_count"]
            rows.append([*key, None if value is None else str(value)])
        if plan.top_n:
            rows.sort(
                key=lambda r: (
                    r[-1] is None,
                    -Decimal(r[-1]) if r[-1] is not None else 0,
                    tuple(r[:-1]),
                )
            )
            rows = rows[: plan.top_n]
        tables.append({"columns": [*plan.group_by, "value"], "rows": rows})
    return tables


def tables_equal(actual, expected, metric):
    if len(actual) != len(expected):
        return False
    for a, b in zip(actual, expected, strict=True):
        if a.get("truncated") or a["columns"] != b["columns"] or len(a["rows"]) != len(b["rows"]):
            return False
        for ar, br in zip(a["rows"], b["rows"], strict=True):
            if ar[:-1] != br[:-1] or (ar[-1] is None) != (br[-1] is None):
                return False
            if ar[-1] is not None:
                difference = abs(Decimal(ar[-1]) - Decimal(br[-1]))
                if difference > (Decimal("1e-9") if metric == "average_order_value" else 0):
                    return False
    return True


def score(case, state, initial_status, data):
    expected = case["expected"]
    wanted = {"ready": "succeeded", "clarify": "awaiting_clarification", "unsupported": "rejected"}
    initial_ok = initial_status == wanted[case["initial_decision"]]
    if case["initial_decision"] == "ready":
        initial_ok = (
            initial_status in {"succeeded", "failed"}
            and state.get("plan", {}).get("decision") == "ready"
        )
    correct, reason = False, state.get("error_code") or "wrong_behavior"
    if expected["decision"] == "unsupported":
        correct = initial_ok and state["status"] == "rejected" and not state.get("query_results")
    elif state["status"] == "succeeded":
        actual_plan = state["plan"]
        matches = all(actual_plan.get(key) == value for key, value in expected.items())
        if not initial_ok:
            reason = "unexpected_initial_behavior"
        elif not matches:
            reason = "wrong_plan"
        else:
            correct = tables_equal(
                state["report"]["tables"], expected_tables(data, expected), expected["metric_id"]
            )
            reason = "wrong_result" if not correct else None
    elif state["status"] == "rejected":
        reason = "false_rejection"
    elif state["status"] == "awaiting_clarification":
        reason = "unexpected_clarification"
    return {
        "correct": correct,
        "failure_class": None if correct else reason,
        "initial_behavior_correct": initial_ok,
        "answerable": expected["decision"] == "ready",
        "expected_initial": case["initial_decision"],
    }
