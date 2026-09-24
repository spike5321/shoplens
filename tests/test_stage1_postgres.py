"""Integration checks against the real PostgreSQL instance and role grants."""

import psycopg
import pytest

from app.config import get_settings
from scripts.check_reference import check


@pytest.mark.integration
def test_full_seed_matches_independent_reference():
    assert check(fixture_only=False) == 80


@pytest.mark.integration
def test_reader_can_read_views_but_not_business_or_write():
    settings = get_settings()
    with psycopg.connect(settings.dsn("reader")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM analytics.sales_lines").fetchone()[0] > 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT COUNT(*) FROM business.orders")
        conn.rollback()
        assert not conn.execute(
            "SELECT has_table_privilege(current_user, 'analytics.sales_lines', 'DELETE')"
        ).fetchone()[0]
    with psycopg.connect(settings.dsn("app")) as conn:
        assert conn.execute(
            "SELECT has_schema_privilege(current_user, 'app_state', 'CREATE')"
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT COUNT(*) FROM business.orders")


@pytest.mark.integration
def test_database_rejects_excess_refund_and_inconsistent_order():
    settings = get_settings()
    with psycopg.connect(settings.dsn("admin")) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                """INSERT INTO business.refunds
                   (refund_id, item_id, amount_cents, completed_at, status)
                   VALUES (999999, 1, 3001, now(), 'succeeded')"""
            )
        conn.rollback()
        conn.execute(
            """INSERT INTO business.orders
               (order_id, channel, created_at, paid_at, status, paid_cents)
               VALUES (999999, '测试', now(), now(), 'paid', 100)"""
        )
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        conn.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE business.order_items SET paid_cents = 2999 WHERE item_id = 1")
        conn.rollback()
