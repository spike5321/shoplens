"""阶段 2：AST 限制与真实 PostgreSQL 执行边界。"""

from __future__ import annotations

import time

import psycopg
import pytest

from app.config import get_settings
from app.query_executor import QueryError, QueryErrorCode, QueryExecutor, validate_query


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT category, SUM(paid_cents) AS total FROM analytics.sales_lines "
        "GROUP BY category ORDER BY total DESC",
        "SELECT COUNT(DISTINCT order_id) AS orders FROM analytics.sales_lines",
        "WITH grouped AS (SELECT category, SUM(paid_cents) AS total "
        "FROM analytics.sales_lines GROUP BY category) "
        "SELECT category, total FROM grouped",
        "SELECT x.category FROM (SELECT category FROM analytics.sales_lines) AS x",
        "SELECT DATE_TRUNC('day', paid_at AT TIME ZONE 'Asia/Shanghai') AS day, "
        "COUNT(*) AS n FROM analytics.sales_lines GROUP BY day",
        "SELECT item_id FROM analytics.sales_lines "
        "WHERE paid_at >= '2026-09-01 00:00:00+08'::timestamptz",
    ],
)
def test_valid_readonly_subset(sql):
    assert validate_query(sql).sql


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT * FROM analytics.sales_lines", QueryErrorCode.UNSUPPORTED_SQL),
        ("SELECT pg_sleep(1) FROM analytics.sales_lines", QueryErrorCode.UNSUPPORTED_SQL),
        (
            "SELECT set_config('x', 'y', true) FROM analytics.sales_lines",
            QueryErrorCode.UNSUPPORTED_SQL,
        ),
        ("SELECT item_id FROM business.order_items", QueryErrorCode.FORBIDDEN_SOURCE),
        ("SELECT oid FROM pg_catalog.pg_class", QueryErrorCode.FORBIDDEN_SOURCE),
        ("SELECT item_id FROM sales_lines", QueryErrorCode.FORBIDDEN_SOURCE),
        ("SELECT private_value FROM analytics.sales_lines", QueryErrorCode.FORBIDDEN_COLUMN),
        ("SELECT x.item_id FROM analytics.sales_lines AS s", QueryErrorCode.FORBIDDEN_COLUMN),
        (
            "WITH x AS (SELECT item_id FROM business.order_items) SELECT item_id FROM x",
            QueryErrorCode.FORBIDDEN_SOURCE,
        ),
        (
            "SELECT x.private_value FROM (SELECT item_id FROM analytics.sales_lines) AS x",
            QueryErrorCode.FORBIDDEN_COLUMN,
        ),
        (
            "WITH x AS (DELETE FROM business.orders RETURNING order_id) SELECT order_id FROM x",
            QueryErrorCode.UNSUPPORTED_SQL,
        ),
        ("DELETE FROM business.orders", QueryErrorCode.UNSUPPORTED_SQL),
        (
            "SELECT item_id FROM analytics.sales_lines; DROP TABLE business.orders",
            QueryErrorCode.INVALID_SQL,
        ),
        (
            "SELECT s.item_id FROM analytics.sales_lines s CROSS JOIN analytics.refund_events r",
            QueryErrorCode.UNSUPPORTED_SQL,
        ),
        (
            "WITH RECURSIVE x AS (SELECT item_id FROM analytics.sales_lines) SELECT item_id FROM x",
            QueryErrorCode.UNSUPPORTED_SQL,
        ),
        ("SELECT item_id FROM analytics.sales_lines LIMIT 2", QueryErrorCode.INVALID_LIMIT),
        (
            "SELECT item_id FROM analytics.sales_lines ORDER BY item_id LIMIT 2",
            QueryErrorCode.INVALID_LIMIT,
        ),
        (
            "SELECT item_id FROM analytics.sales_lines WHERE item_id = 1::regclass",
            QueryErrorCode.UNSUPPORTED_SQL,
        ),
    ],
)
def test_rejected_sql(sql, code):
    with pytest.raises(QueryError) as caught:
        validate_query(sql)
    assert caught.value.code == code


def test_top_n_requires_matching_limit_and_order():
    sql = "SELECT item_id FROM analytics.sales_lines ORDER BY item_id LIMIT 2"
    assert validate_query(sql, top_n=2).top_n == 2
    for invalid_sql, top_n in [
        (sql, 3),
        ("SELECT item_id FROM analytics.sales_lines LIMIT 2", 2),
    ]:
        with pytest.raises(QueryError) as caught:
            validate_query(invalid_sql, top_n=top_n)
        assert caught.value.code == QueryErrorCode.INVALID_LIMIT


@pytest.mark.integration
def test_executor_aggregate_empty_and_row_cap():
    settings = get_settings().model_copy(update={"sql_max_rows": 2})
    with QueryExecutor(settings) as executor:
        count = executor.execute("SELECT COUNT(*) AS n FROM analytics.sales_lines")
        assert count.columns == ("n",)
        assert count.rows == ((5641,),)
        assert count.complete_for_full_aggregate

        cte = executor.execute(
            "WITH grouped AS (SELECT category, SUM(paid_cents) AS total "
            "FROM analytics.sales_lines GROUP BY category) "
            "SELECT COUNT(*) AS n FROM grouped"
        )
        assert cte.rows == ((4,),)

        october = executor.execute(
            "SELECT COUNT(*) AS n FROM analytics.sales_lines "
            "WHERE paid_at >= '2026-10-01 00:00:00+08'::timestamptz"
        )
        assert october.rows == ((1,),)

        empty = executor.execute("SELECT item_id FROM analytics.sales_lines WHERE item_id = -1")
        assert empty.rows == ()
        assert not empty.truncated

        limited = executor.execute("SELECT item_id FROM analytics.sales_lines ORDER BY item_id")
        assert limited.rows == ((1,), (2,))
        assert limited.truncated
        assert not limited.complete_for_full_aggregate

        top = executor.execute(
            "SELECT item_id FROM analytics.sales_lines ORDER BY item_id LIMIT 2", top_n=2
        )
        assert top.rows == ((1,), (2,))
        assert not top.truncated
        assert top.requested_top_n == 2
        assert not top.complete_for_full_aggregate

        with pytest.raises(QueryError) as caught:
            executor.execute(
                "SELECT item_id FROM analytics.sales_lines ORDER BY item_id LIMIT 3", top_n=3
            )
        assert caught.value.code == QueryErrorCode.INVALID_LIMIT

        with pytest.raises(QueryError) as caught:
            executor.execute("SELECT paid_cents / 0 AS broken FROM analytics.sales_lines")
        assert caught.value.code == QueryErrorCode.DATABASE_ERROR
        assert caught.value.__cause__ is None
        assert settings.pg_reader_password.get_secret_value() not in str(caught.value)


@pytest.mark.integration
def test_reader_cannot_write_application_state_or_business_tables():
    settings = get_settings()
    with psycopg.connect(settings.dsn("reader")) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE app_state.forbidden_stage2 (id integer)")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE business.orders SET paid_cents = 0 WHERE order_id = 1")


@pytest.mark.integration
def test_statement_timeout_is_enforced_by_postgres():
    settings = get_settings().model_copy(update={"sql_statement_timeout_ms": 100})
    with psycopg.connect(settings.dsn("admin")) as blocker:
        blocker.execute("LOCK TABLE business.order_items IN ACCESS EXCLUSIVE MODE")
        try:
            with QueryExecutor(settings) as executor:
                started = time.monotonic()
                with pytest.raises(QueryError) as caught:
                    executor.execute("SELECT COUNT(*) FROM analytics.sales_lines")
                assert caught.value.code == QueryErrorCode.TIMEOUT
                assert time.monotonic() - started < 3
                assert settings.pg_admin_password.get_secret_value() not in str(caught.value)
        finally:
            blocker.rollback()
    with QueryExecutor(settings) as executor:
        assert executor.execute("SELECT COUNT(*) FROM analytics.sales_lines").rows == ((5641,),)
