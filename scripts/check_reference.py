"""Cross-check independent Python reference against aggregate SQL on actual views."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from data.dataset import fixture, generate, local  # noqa: E402
from data.reference import calculate, check_manual_fixture, manual_cases  # noqa: E402


def sql_metrics(conn: psycopg.Connection, start, end, category=None, channel=None) -> dict:
    # Independent aggregates preserve sales-line and refund-event grain.
    # Category filter is repeated in each aggregate; raw views are never joined.
    paid, count = conn.execute(
        """
        SELECT COALESCE(SUM(paid_cents), 0), COUNT(DISTINCT order_id)
        FROM analytics.sales_lines
        WHERE paid_at >= %s AND paid_at < %s
          AND (%s::text IS NULL OR category = %s)
          AND (%s::text IS NULL OR channel = %s)
        """,
        (start, end, category, category, channel, channel),
    ).fetchone()
    refund = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0)
        FROM analytics.refund_events
        WHERE completed_at >= %s AND completed_at < %s
          AND (%s::text IS NULL OR category = %s)
          AND (%s::text IS NULL OR channel = %s)
        """,
        (start, end, category, category, channel, channel),
    ).fetchone()[0]
    return {
        "paid_amount": paid,
        "successful_refund": refund,
        "period_net_receipt": paid - refund,
        "paid_order_count": count,
        "average_order_value": paid / count if count else None,
    }


def check(*, fixture_only: bool) -> int:
    check_manual_fixture()
    data = fixture() if fixture_only else generate()
    cases = (
        manual_cases()
        if fixture_only
        else [
            {"start": a, "end": b, "category": c, "channel": ch}
            for a, b in (
                ("2026-07-01", "2026-08-01"),
                ("2026-08-01", "2026-09-01"),
                ("2026-09-01", "2026-10-01"),
                ("2026-08-05", "2026-08-06"),
            )
            for c in (None, "数码", "家居", "服饰", "食品")
            for ch in (None, "自然", "广告", "站外")
        ]
    )
    settings = get_settings()
    with psycopg.connect(settings.dsn("reader")) as conn:
        for case in cases:
            start, end = local(case["start"], 0, 0), local(case["end"], 0, 0)
            expected = calculate(
                data, start, end, category=case.get("category"), channel=case.get("channel")
            )
            actual = sql_metrics(conn, start, end, case.get("category"), case.get("channel"))
            exact_keys = (
                "paid_amount",
                "successful_refund",
                "period_net_receipt",
                "paid_order_count",
            )
            exact_match = all(expected[key] == actual[key] for key in exact_keys)
            expected_aov, actual_aov = (
                expected["average_order_value"],
                actual["average_order_value"],
            )
            ratio_match = (expected_aov is None and actual_aov is None) or (
                expected_aov is not None
                and actual_aov is not None
                and math.isclose(float(expected_aov), float(actual_aov), rel_tol=0, abs_tol=1e-9)
            )
            if not exact_match or not ratio_match:
                raise AssertionError(f"{case}: Python={expected}, SQL={actual}")
            cash = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents), 0) FROM analytics.cash_events
                WHERE occurred_at >= %s AND occurred_at < %s
                  AND (%s::text IS NULL OR category = %s)
                  AND (%s::text IS NULL OR channel = %s)
                """,
                (
                    start,
                    end,
                    case.get("category"),
                    case.get("category"),
                    case.get("channel"),
                    case.get("channel"),
                ),
            ).fetchone()[0]
            if cash != expected["period_net_receipt"]:
                raise AssertionError(f"{case}: cash_events={cash}, Python={expected}")
    return len(cases)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-only", action="store_true")
    args = parser.parse_args()
    count = check(fixture_only=args.fixture_only)
    print(f"PASS: {count} Python/SQL cases; 14 manually expected fixture cases")
