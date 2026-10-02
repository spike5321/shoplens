"""查询契约、限定 SQL 形状和有依据的结果展示；不读取评测答案。"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlglot import exp

from app.query_executor import QueryError, QueryErrorCode, QueryResult, validate_query

METRICS_PATH = Path(__file__).resolve().parents[2] / "data" / "metrics.yaml"
METRICS = yaml.safe_load(METRICS_PATH.read_text(encoding="utf-8"))["metrics"]
Dimension = Literal["day", "channel", "category"]


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ready", "clarify", "unsupported"]
    metric_id: str | None = None
    group_by: list[Dimension] = Field(default_factory=list, max_length=3)
    start_date: date | None = None
    end_date: date | None = None
    comparison_start_date: date | None = None
    comparison_end_date: date | None = None
    top_n: int | None = Field(default=None, ge=1, le=200)
    clarification_question: str | None = Field(default=None, max_length=400)
    unsupported_reason: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def validate_contract(self):
        if len(self.group_by) != len(set(self.group_by)):
            raise ValueError("重复分组维度")
        if self.decision == "ready":
            if self.metric_id not in METRICS:
                raise ValueError("指标不在字典中")
            for start, end in self.periods():
                if start is None or end is None or not 0 < (end - start).days <= 366:
                    raise ValueError("日期必须是完整、递增且不超过一年")
            if self.top_n and (not self.group_by or self.comparison_start_date):
                raise ValueError("Top N 需分组，首版不支持同时比较期间")
            if self.comparison_start_date and "day" in self.group_by:
                raise ValueError("首版期间比较只支持总体、渠道或类别")
        elif self.decision == "clarify" and not self.clarification_question:
            raise ValueError("澄清必须提供问题")
        elif self.decision == "unsupported" and not self.unsupported_reason:
            raise ValueError("拒绝必须提供原因")
        if bool(self.comparison_start_date) != bool(self.comparison_end_date):
            raise ValueError("比较期间必须同时提供起止日期")
        return self

    def periods(self):
        periods = [(self.start_date, self.end_date)]
        if self.comparison_start_date or self.comparison_end_date:
            periods.append((self.comparison_start_date, self.comparison_end_date))
        return periods


class SqlDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sqls: list[str] = Field(min_length=1, max_length=2)


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)


class ChartData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["line", "bar"]
    unit: str
    labels: list[str]
    values: list[str | None]

    @model_validator(mode="after")
    def matching_lengths(self):
        if len(self.labels) != len(self.values):
            raise ValueError("图表标签与数值数量不一致")
        return self


def period_bound(value: date) -> str:
    return datetime.combine(value, time.min, ZoneInfo("Asia/Shanghai")).isoformat()


def canonical_queries(plan: QueryPlan) -> list[str]:
    """根据经过校验的计划构造允许的业务形状，不包含任何标准答案。"""
    metric = METRICS[plan.metric_id]
    field = metric["time_field"]
    amount = (
        "paid_cents" if plan.metric_id in {"paid_amount", "average_order_value"} else "amount_cents"
    )
    aggregate = f"COALESCE(SUM({amount}), 0)"
    if plan.metric_id == "paid_order_count":
        aggregate = "COUNT(DISTINCT order_id)"
    elif plan.metric_id == "average_order_value":
        aggregate = "CAST(SUM(paid_cents) AS NUMERIC) / NULLIF(COUNT(DISTINCT order_id), 0)"
    dimensions = [
        f"CAST({field} AT TIME ZONE 'Asia/Shanghai' AS DATE) AS day" if d == "day" else d
        for d in plan.group_by
    ]
    queries = []
    for start, end in plan.periods():
        assert start is not None and end is not None
        sql = (
            f"SELECT {', '.join([*dimensions, aggregate + ' AS value'])} FROM {metric['view']} "
            f"WHERE {field} >= CAST('{period_bound(start)}' AS TIMESTAMPTZ) "
            f"AND {field} < CAST('{period_bound(end)}' AS TIMESTAMPTZ)"
        )
        if plan.group_by:
            sql += " GROUP BY " + ", ".join(plan.group_by)
            order = ["value DESC NULLS LAST"] if plan.top_n else []
            order += [f"{d} ASC NULLS LAST" for d in plan.group_by]
            sql += " ORDER BY " + ", ".join(order)
        if plan.top_n:
            sql += f" LIMIT {plan.top_n}"
        queries.append(sql)
    return queries


def _normalized_ast(sql: str) -> exp.Expression:
    tree = sqlglot.parse_one(sql, read="postgres")
    for table in tree.find_all(exp.Table):
        table.set("alias", None)
    for column in tree.find_all(exp.Column):
        column.set("table", None)
    for identifier in tree.find_all(exp.Identifier):
        identifier.set("quoted", False)
    for cast in tree.find_all(exp.Cast):
        if cast.args["to"].this == exp.DataType.Type.TIMESTAMPTZ and isinstance(
            cast.this, exp.Literal
        ):
            value = datetime.fromisoformat(cast.this.this.replace("Z", "+00:00"))
            if value.tzinfo is None:
                raise ValueError("时间边界必须含时区")
            cast.this.set("this", value.astimezone(ZoneInfo("Asia/Shanghai")).isoformat())
    return tree


def validate_plan_sql(plan: QueryPlan, sqls: list[str]) -> None:
    expected = canonical_queries(plan)
    if len(expected) != len(sqls):
        raise QueryError(QueryErrorCode.INVALID_SQL, "SQL 数量与期间计划不符")
    for candidate, canonical in zip(sqls, expected, strict=True):
        validate_query(candidate, top_n=plan.top_n)
        try:
            matches = _normalized_ast(candidate) == _normalized_ast(canonical)
        except ValueError:
            matches = False
        if not matches:
            raise QueryError(QueryErrorCode.PLAN_MISMATCH, "SQL 的指标、范围、分组或排序与计划不符")


def _number(value) -> str | None:
    return None if value is None else str(value)


def build_report(plan: QueryPlan, results: list[QueryResult]) -> dict:
    """文字和变化率只由执行结果计算；不接受模型自由编写数字。"""
    metric = METRICS[plan.metric_id]
    tables = [
        {
            "columns": list(r.columns),
            "rows": [
                [v.isoformat() if isinstance(v, date) else _number(v) for v in row]
                for row in r.rows
            ],
            "truncated": r.truncated,
            "requested_top_n": r.requested_top_n,
        }
        for r in results
    ]
    report = {
        "metric": {"id": plan.metric_id, **metric},
        "plan": plan.model_dump(mode="json"),
        "tables": tables,
        "chart": None,
        "comparison": [],
        "explanation": "",
    }
    if any(r.truncated for r in results):
        report["explanation"] = (
            "结果超过保护行数上限，已截断。请缩小日期范围或分组；不据此计算全量总额、变化率或排名。"
        )
        return report
    if plan.group_by and len(results) == 1:
        report["chart"] = ChartData(
            type="line" if plan.group_by == ["day"] else "bar",
            unit=metric["unit"],
            labels=[" / ".join(str(v) for v in row[:-1]) for row in results[0].rows],
            values=[_number(row[-1]) for row in results[0].rows],
        ).model_dump()
    if len(results) == 2:
        current = {tuple(row[:-1]): row[-1] for row in results[0].rows}
        baseline = {tuple(row[:-1]): row[-1] for row in results[1].rows}
        missing = None if plan.metric_id == "average_order_value" else 0
        for key in sorted(current.keys() | baseline.keys(), key=str):
            a, b = current.get(key, missing), baseline.get(key, missing)
            delta = Decimal(a) - Decimal(b) if a is not None and b is not None else None
            pct = delta / Decimal(b) * 100 if delta is not None and b else None
            report["comparison"].append(
                {
                    "dimensions": [str(v) for v in key],
                    "current": _number(a),
                    "baseline": _number(b),
                    "delta": _number(delta),
                    "change_pct": _number(pct),
                }
            )
    pieces = [f"合成演示数据；{metric['label']}。口径：{metric['definition']}。"]
    for index, result in enumerate(results):
        start, end = plan.periods()[index]
        pieces.append(f"期间 [{start}, {end})，Asia/Shanghai，{len(result.rows)} 行。")
        for row in result.rows:
            label = " / ".join(str(v) for v in row[:-1]) or "总体"
            value = row[-1]
            display = "不可计算（零分母或无数据）" if value is None else str(value)
            if value is not None and metric["unit"] in {"fen", "fen_per_order"}:
                display = f"{Decimal(value) / 100:.2f} 元"
            elif value is not None:
                display += " 单"
            pieces.append(f"{label}：{display}。")
    if plan.top_n:
        pieces.append(f"这是用户指定的 Top {plan.top_n}，不能作为完整总体计算。")
    if len(results) == 2:
        pieces.append("比较数值见 comparison；基期为零或空值时变化率不可计算。差额不代表因果原因。")
    report["explanation"] = "\n".join(pieces)
    return report
