"""受限分析查询：先验证 AST，再以 reader 身份在只读事务中执行。

这是 ShopLens 演示数据上的有限 SQL 子集，不是通用 SQL 沙箱。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import psycopg
import sqlglot
from psycopg_pool import ConnectionPool
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.config import Settings, get_settings

VIEW_COLUMNS = {
    "sales_lines": frozenset(
        {"item_id", "order_id", "channel", "category", "paid_at", "paid_cents", "quantity"}
    ),
    "refund_events": frozenset(
        {
            "refund_id",
            "item_id",
            "order_id",
            "channel",
            "category",
            "completed_at",
            "amount_cents",
        }
    ),
    "cash_events": frozenset(
        {"event_type", "event_id", "order_id", "channel", "category", "occurred_at", "amount_cents"}
    ),
}

# 只列出本项目查询真正需要的语法；新的节点一律先拒绝、审查后再加入。
ALLOWED_NODES = (
    exp.Add,
    exp.Alias,
    exp.And,
    exp.AtTimeZone,
    exp.Avg,
    exp.Between,
    exp.Boolean,
    exp.Case,
    exp.Cast,
    exp.Column,
    exp.Coalesce,
    exp.Count,
    exp.CTE,
    exp.DataType,
    exp.Distinct,
    exp.Div,
    exp.EQ,
    exp.From,
    exp.Group,
    exp.GT,
    exp.GTE,
    exp.Having,
    exp.Identifier,
    exp.If,
    exp.In,
    exp.Is,
    exp.Limit,
    exp.Literal,
    exp.LT,
    exp.LTE,
    exp.Max,
    exp.Min,
    exp.Mul,
    exp.NEQ,
    exp.Neg,
    exp.Not,
    exp.Null,
    exp.Nullif,
    exp.Or,
    exp.Order,
    exp.Ordered,
    exp.Paren,
    exp.Select,
    exp.Star,
    exp.Sub,
    exp.Subquery,
    exp.Sum,
    exp.Table,
    exp.TableAlias,
    exp.TimestampTrunc,
    exp.Var,
    exp.Where,
    exp.With,
)

ALLOWED_CASTS = {"DATE", "TIMESTAMP", "TIMESTAMPTZ", "INT", "BIGINT", "NUMERIC", "TEXT"}
ALLOWED_DATE_PARTS = {"day", "week", "month"}


class QueryErrorCode(StrEnum):
    INVALID_SQL = "invalid_sql"
    UNSUPPORTED_SQL = "unsupported_sql"
    FORBIDDEN_SOURCE = "forbidden_source"
    FORBIDDEN_COLUMN = "forbidden_column"
    INVALID_LIMIT = "invalid_limit"
    TIMEOUT = "timeout"
    DATABASE_ERROR = "database_error"


class QueryError(Exception):
    """面向调用方的安全错误；不包含 SQL、DSN 或数据库异常详情。"""

    def __init__(self, code: QueryErrorCode, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    top_n: int | None


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool
    row_cap: int
    requested_top_n: int | None

    @property
    def complete_for_full_aggregate(self) -> bool:
        """截断或显式 Top N 都不能当成完整数据集计算总体指标。"""
        return not self.truncated and self.requested_top_n is None


def _reject(code: QueryErrorCode, message: str) -> None:
    raise QueryError(code, message)


def _is_in_clause(node: exp.Expression, clause_type: type[exp.Expression]) -> bool:
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, clause_type):
            return True
        parent = parent.parent
    return False


def _source_columns(source: Scope | exp.Table) -> frozenset[str]:
    if isinstance(source, Scope):
        names = source.expression.named_selects
        if not names or any(not name or name == "*" for name in names):
            _reject(QueryErrorCode.UNSUPPORTED_SQL, "CTE/子查询必须明确命名输出列")
        lowered = [name.lower() for name in names]
        if len(lowered) != len(set(lowered)):
            _reject(QueryErrorCode.UNSUPPORTED_SQL, "CTE/子查询输出列名不能重复")
        return frozenset(lowered)
    if (
        source.db.lower() != "analytics"
        or source.catalog
        or source.name.lower() not in VIEW_COLUMNS
    ):
        _reject(QueryErrorCode.FORBIDDEN_SOURCE, "只允许读取指定 analytics 分析视图")
    return VIEW_COLUMNS[source.name.lower()]


def _validate_scope(scope: Scope) -> None:
    select = scope.expression
    if not isinstance(select, exp.Select):
        _reject(QueryErrorCode.UNSUPPORTED_SQL, "仅支持 SELECT 查询块")
    if not scope.selected_sources:
        _reject(QueryErrorCode.UNSUPPORTED_SQL, "每个查询块都必须有数据来源")
    if len(scope.selected_sources) != 1 or select.args.get("joins"):
        _reject(QueryErrorCode.UNSUPPORTED_SQL, "当前阶段不支持 JOIN 或多个 FROM 来源")

    source_name, (_, source) = next(iter(scope.selected_sources.items()))
    columns = _source_columns(source)
    aliases = {name.lower() for name in select.named_selects if name}
    for column in scope.columns:
        name = column.name.lower()
        if column.db or column.catalog:
            _reject(QueryErrorCode.FORBIDDEN_COLUMN, "列只能使用当前查询块的来源别名")
        if column.table and column.table.lower() != source_name.lower():
            _reject(QueryErrorCode.FORBIDDEN_COLUMN, "列必须来自当前查询块的数据来源")
        if name in columns:
            continue
        if (
            not column.table
            and name in aliases
            and (_is_in_clause(column, exp.Order) or _is_in_clause(column, exp.Group))
        ):
            continue
        _reject(QueryErrorCode.FORBIDDEN_COLUMN, "列不属于获准视图或子查询输出")


def validate_query(sql: str, *, top_n: int | None = None) -> ValidatedQuery:
    """用 PostgreSQL 方言解析单条 SELECT，并逐节点检查支持范围。"""
    if not sql or not sql.strip():
        _reject(QueryErrorCode.INVALID_SQL, "SQL 不能为空")
    if len(sql) > 10_000:
        _reject(QueryErrorCode.INVALID_SQL, "SQL 超过长度上限")
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except (SqlglotError, ValueError):
        raise QueryError(QueryErrorCode.INVALID_SQL, "SQL 解析失败") from None
    if len(statements) != 1 or statements[0] is None:
        _reject(QueryErrorCode.INVALID_SQL, "只允许一条 SQL 语句")
    query = statements[0]
    if not isinstance(query, exp.Select):
        _reject(QueryErrorCode.UNSUPPORTED_SQL, "只允许 SELECT")

    for node_count, node in enumerate(query.walk(), start=1):
        if node_count > 500:
            _reject(QueryErrorCode.UNSUPPORTED_SQL, "SQL 结构超过复杂度上限")
        if not isinstance(node, ALLOWED_NODES):
            _reject(QueryErrorCode.UNSUPPORTED_SQL, "SQL 包含当前阶段不支持的构造或函数")
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            _reject(QueryErrorCode.UNSUPPORTED_SQL, "只允许 COUNT(*)，不允许 SELECT *")
        if (
            isinstance(node, exp.DataType)
            and node.sql(dialect="postgres").upper() not in ALLOWED_CASTS
        ):
            _reject(QueryErrorCode.UNSUPPORTED_SQL, "类型转换不在允许列表中")
        if isinstance(node, exp.TimestampTrunc):
            unit = node.args.get("unit")
            if (
                not isinstance(unit, (exp.Var, exp.Literal))
                or unit.name.lower() not in ALLOWED_DATE_PARTS
            ):
                _reject(QueryErrorCode.UNSUPPORTED_SQL, "日期截断只支持 day/week/month")
        if isinstance(node, exp.Var) and not isinstance(node.parent, exp.TimestampTrunc):
            _reject(QueryErrorCode.UNSUPPORTED_SQL, "不支持变量引用")
        if isinstance(node, exp.AtTimeZone):
            zone = node.args.get("zone")
            if not isinstance(zone, exp.Literal) or zone.this != "Asia/Shanghai":
                _reject(QueryErrorCode.UNSUPPORTED_SQL, "业务时区必须是 Asia/Shanghai")
        if isinstance(node, exp.Subquery) and not isinstance(node.parent, exp.From):
            _reject(QueryErrorCode.UNSUPPORTED_SQL, "仅支持 FROM 中的非关联子查询")
        if isinstance(node, exp.CTE):
            alias = node.args.get("alias")
            if alias and alias.args.get("columns"):
                _reject(QueryErrorCode.UNSUPPORTED_SQL, "CTE 请在 SELECT 中命名输出列")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            _reject(QueryErrorCode.UNSUPPORTED_SQL, "不支持递归 CTE")
        if isinstance(node, exp.Limit) and node is not query.args.get("limit"):
            _reject(QueryErrorCode.INVALID_LIMIT, "子查询内不支持 LIMIT")

    scopes = traverse_scope(query)
    if not scopes:
        _reject(QueryErrorCode.UNSUPPORTED_SQL, "无法验证查询来源")
    for scope in scopes:
        _validate_scope(scope)

    limit = query.args.get("limit")
    if top_n is None:
        if limit is not None:
            _reject(QueryErrorCode.INVALID_LIMIT, "仅明确的用户 Top N 可使用 LIMIT")
    else:
        if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
            _reject(QueryErrorCode.INVALID_LIMIT, "Top N 必须是正整数")
        if not query.args.get("order"):
            _reject(QueryErrorCode.INVALID_LIMIT, "Top N 必须有 ORDER BY")
        literal = limit.args.get("expression") if limit else None
        if not isinstance(literal, exp.Literal) or not literal.is_int or int(literal.this) != top_n:
            _reject(QueryErrorCode.INVALID_LIMIT, "SQL 的 LIMIT 必须等于用户指定的 Top N")

    return ValidatedQuery(sql=query.sql(dialect="postgres"), top_n=top_n)


class QueryExecutor:
    """只持有 reader 凭据；池内每次借出的连接都开启独立只读事务。"""

    def __init__(self, settings: Settings | None = None, *, max_pool_size: int = 4):
        self.settings = settings or get_settings()
        self.pool = ConnectionPool(
            conninfo=self.settings.dsn("reader"),
            min_size=0,
            max_size=max_pool_size,
            kwargs={"autocommit": True, "connect_timeout": 3},
            timeout=5,
            open=False,
        )

    def __enter__(self) -> QueryExecutor:
        self.pool.open(wait=True)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.pool.close()

    def execute(self, sql: str, *, top_n: int | None = None) -> QueryResult:
        validated = validate_query(sql, top_n=top_n)
        cap = self.settings.sql_max_rows
        if top_n is not None and top_n > cap:
            _reject(QueryErrorCode.INVALID_LIMIT, "Top N 超过服务端行数上限")
        query = validated.sql
        if top_n is None:
            # 只追加服务端保护上限；原 SQL 不得自带 LIMIT。
            parsed = sqlglot.parse_one(query, read="postgres")
            query = parsed.limit(cap + 1).sql(dialect="postgres")
        try:
            with self.pool.connection() as conn, conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(self.settings.sql_statement_timeout_ms),),
                )
                with conn.execute(query) as cursor:
                    columns = tuple(col.name for col in cursor.description or ())
                    fetched = cursor.fetchall()
        except psycopg.errors.QueryCanceled:
            raise QueryError(QueryErrorCode.TIMEOUT, "查询超过服务端时间上限") from None
        except (psycopg.Error, OSError):
            raise QueryError(QueryErrorCode.DATABASE_ERROR, "数据库查询失败") from None
        truncated = len(fetched) > cap
        return QueryResult(
            columns=columns,
            rows=tuple(tuple(row) for row in fetched[:cap]),
            truncated=truncated,
            row_cap=cap,
            requested_top_n=top_n,
        )
