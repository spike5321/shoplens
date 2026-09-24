"""Replace the dedicated synthetic demo dataset, atomically and repeatably."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from data.dataset import fixture, generate, validate  # noqa: E402


def seed(*, fixture_only: bool = False) -> dict[str, int]:
    data = fixture() if fixture_only else generate()
    validate(data)
    settings = get_settings()
    with psycopg.connect(settings.dsn("admin")) as conn:
        # This database is dedicated to synthetic demos. TRUNCATE+load is one
        # transaction, so rerunning does not duplicate records or expose a half load.
        conn.execute(
            "TRUNCATE business.refunds, business.order_items, business.orders, business.products"
        )
        for table, columns in (
            ("products", ("product_id", "name", "category")),
            ("orders", ("order_id", "channel", "created_at", "paid_at", "status", "paid_cents")),
            ("order_items", ("item_id", "order_id", "product_id", "quantity", "paid_cents")),
            ("refunds", ("refund_id", "item_id", "amount_cents", "completed_at", "status")),
        ):
            placeholders = ", ".join("%s" for _ in columns)
            column_list = ", ".join(columns)
            statement = f"INSERT INTO business.{table} ({column_list}) VALUES ({placeholders})"
            with conn.cursor() as cursor:
                cursor.executemany(
                    statement, [tuple(row[col] for col in columns) for row in data[table]]
                )
    return {table: len(rows) for table, rows in data.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load synthetic demonstration data")
    parser.add_argument("--fixture-only", action="store_true")
    args = parser.parse_args()
    print(seed(fixture_only=args.fixture_only))
