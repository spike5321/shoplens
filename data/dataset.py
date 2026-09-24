"""Hand verified edge cases plus a fixed-seed synthetic order generator."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
SEED = 20260924


def local(day: str, hour: int = 12, minute: int = 0) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=TZ)


def fixture() -> dict[str, list[dict]]:
    """Nine manually auditable orders. Amounts are integer CNY fen."""
    products = [
        {"product_id": 1, "name": "耳机", "category": "数码"},
        {"product_id": 2, "name": "水杯", "category": "家居"},
        {"product_id": 3, "name": "充电器", "category": "数码"},
    ]
    specs = [
        (1, "2026-07-31", 23, 59, "自然", [(1, 6000), (2, 4000)]),
        (2, "2026-08-01", 0, 0, "广告", [(3, 10000)]),
        (3, "2026-08-15", 12, 0, "自然", [(2, 5000)]),
        (4, "2026-08-20", 12, 0, "广告", []),  # cancelled, never paid
        (5, "2026-09-30", 23, 59, "自然", [(1, 8000)]),
        (6, "2026-10-01", 0, 0, "广告", [(1, 9000)]),  # exclusive end boundary
        (7, "2026-08-10", 12, 0, "自然", [(1, 3000)]),
        (8, "2026-08-11", 12, 0, "广告", [(2, 2000)]),
        (9, "2026-08-12", 12, 0, "自然", [(2, 10000)]),  # same total as order 2
    ]
    orders, items = [], []
    item_id = 1
    for oid, day, hour, minute, channel, lines in specs:
        paid_at = local(day, hour, minute) if lines else None
        orders.append(
            {
                "order_id": oid,
                "channel": channel,
                "created_at": local(day, hour, minute) - timedelta(minutes=5),
                "paid_at": paid_at,
                "status": "paid" if lines else "cancelled",
                "paid_cents": sum(amount for _, amount in lines),
            }
        )
        for pid, amount in lines:
            items.append(
                {
                    "item_id": item_id,
                    "order_id": oid,
                    "product_id": pid,
                    "quantity": 1,
                    "paid_cents": amount,
                }
            )
            item_id += 1
    refunds = [
        (1, 1, 1000, "2026-08-02", "succeeded"),
        (2, 1, 2000, "2026-09-02", "succeeded"),
        (3, 2, 4000, "2026-09-05", "succeeded"),
        (4, 4, 5000, "2026-08-20", "succeeded"),
        (5, 7, 500, None, "pending"),
        (6, 8, 2000, "2026-09-01", "succeeded"),
    ]
    return {
        "products": products,
        "orders": orders,
        "order_items": items,
        "refunds": [
            {
                "refund_id": rid,
                "item_id": iid,
                "amount_cents": amount,
                "completed_at": local(day, 0, 0) if day else None,
                "status": status,
            }
            for rid, iid, amount, day, status in refunds
        ],
    }


def generate(total_orders: int = 3000) -> dict[str, list[dict]]:
    """Return the same data on every call; first nine rows are the hand fixture."""
    if total_orders < 9:
        raise ValueError("total_orders must include all nine fixture orders")
    data = fixture()
    rng = random.Random(SEED)
    for pid in range(4, 44):
        data["products"].append(
            {
                "product_id": pid,
                "name": f"演示商品{pid}",
                "category": ("数码", "家居", "服饰", "食品")[pid % 4],
            }
        )
    item_id = max(row["item_id"] for row in data["order_items"]) + 1
    refund_id = max(row["refund_id"] for row in data["refunds"]) + 1
    start = local("2026-07-01", 0, 0)
    for oid in range(10, total_orders + 1):
        paid_at = start + timedelta(minutes=rng.randrange(92 * 24 * 60))
        count = rng.randint(1, 3)
        amounts = [rng.randint(500, 18000) for _ in range(count)]
        cancelled = rng.random() < 0.06
        data["orders"].append(
            {
                "order_id": oid,
                "channel": rng.choice(("自然", "广告", "站外")),
                "created_at": paid_at - timedelta(minutes=rng.randint(1, 90)),
                "paid_at": None if cancelled else paid_at,
                "status": "cancelled" if cancelled else "paid",
                "paid_cents": 0 if cancelled else sum(amounts),
            }
        )
        if cancelled:
            continue
        for amount in amounts:
            this_item = item_id
            data["order_items"].append(
                {
                    "item_id": this_item,
                    "order_id": oid,
                    "product_id": rng.randint(1, 43),
                    "quantity": 1,
                    "paid_cents": amount,
                }
            )
            item_id += 1
            if rng.random() < 0.12:
                refund_amount = rng.randint(1, amount)
                refund_at = paid_at + timedelta(days=rng.randint(1, 25))
                data["refunds"].append(
                    {
                        "refund_id": refund_id,
                        "item_id": this_item,
                        "amount_cents": refund_amount,
                        "completed_at": refund_at,
                        "status": "succeeded",
                    }
                )
                refund_id += 1
    return data


def validate(data: dict[str, list[dict]]) -> None:
    """Check cross-row invariants before loading a database."""
    products = {p["product_id"] for p in data["products"]}
    orders = {o["order_id"]: o for o in data["orders"]}
    items = {i["item_id"]: i for i in data["order_items"]}
    paid_by_order = {oid: 0 for oid in orders}
    refunded_by_item = {iid: 0 for iid in items}
    for item in items.values():
        assert item["product_id"] in products
        assert item["order_id"] in orders
        assert item["paid_cents"] > 0 and item["quantity"] > 0
        paid_by_order[item["order_id"]] += item["paid_cents"]
    for order in orders.values():
        assert paid_by_order[order["order_id"]] == order["paid_cents"]
        assert (order["paid_at"] is not None) == (order["status"] == "paid")
    for refund in data["refunds"]:
        iid = refund["item_id"]
        assert iid in items and refund["amount_cents"] > 0
        if refund["status"] == "succeeded":
            assert refund["completed_at"] is not None
            refunded_by_item[iid] += refund["amount_cents"]
        else:
            assert refund["completed_at"] is None
    for iid, amount in refunded_by_item.items():
        assert amount <= items[iid]["paid_cents"]
