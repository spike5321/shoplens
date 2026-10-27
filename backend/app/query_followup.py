"""Typed condition changes; only server-owned plans are eligible inheritance sources."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.query_plan import Dimension, QueryPlan


class FollowupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ready", "clarify", "unsupported"]
    metric_id: str | None = None
    group_by: list[Dimension] | None = None
    start_date: date | None = None
    end_date: date | None = None
    comparison_start_date: date | None = None
    comparison_end_date: date | None = None
    top_n: int | None = Field(default=None, ge=1, le=200)
    clear_comparison: bool = False
    clear_top_n: bool = False
    clarification_question: str | None = Field(default=None, max_length=400)
    unsupported_reason: str | None = Field(default=None, max_length=400)


def _unknown_demo_text(text: str) -> bool:
    """A finite demo grammar must account for the whole request before it can execute."""
    remaining = re.sub(
        r"\d{4}-\d{2}-\d{2}|(?:(\d{4})年\s*)?\d{1,2}月|(?:Top\s*|前\s*)\d+", "", text, flags=re.I
    )
    terms = [
        "成功退款金额",
        "退款金额",
        "实付金额",
        "期间净收款",
        "支付订单数",
        "订单数",
        "客单价",
        "实付",
        "退款",
        "净收款",
        "销售额",
        "业绩",
        "取消比较",
        "取消对比",
        "不比较",
        "比较",
        "对比",
        "相比",
        "与",
        "比",
        "和",
        "各渠道",
        "按渠道",
        "各类别",
        "按类别",
        "各品类",
        "按品类",
        "每天",
        "按日",
        "整体",
        "总体",
        "不分组",
        "取消分组",
        "取消排名",
        "不排名",
        "取消Top",
        "取消top",
        "全部",
        "上月",
        "今天",
        "上周",
        "改成",
        "改为",
        "改看",
        "换成",
        "换为",
        "查询",
        "统计",
        "请问",
        "帮我",
        "看看",
        "多少",
        "怎么",
        "看",
        "那",
        "呢",
        "的",
        "是",
        "为",
        "请",
        "到",
        "至",
    ]
    for term in sorted(terms, key=len, reverse=True):
        remaining = remaining.replace(term, "")
    return bool(re.sub(r"[\s，。？！、,.;:：?！!（）()]+", "", remaining))


def merge_followup(base_plan: dict, patch: FollowupPatch) -> QueryPlan:
    base = QueryPlan.model_validate(base_plan)
    if base.decision != "ready":
        raise ValueError("followup_source_not_ready")
    if patch.decision != "ready":
        return QueryPlan(
            decision=patch.decision,
            clarification_question=patch.clarification_question,
            unsupported_reason=patch.unsupported_reason,
        )
    edits = patch.model_dump(
        exclude_none=True,
        exclude={
            "decision",
            "clear_comparison",
            "clear_top_n",
            "clarification_question",
            "unsupported_reason",
        },
    )
    if (
        (not edits and not patch.clear_comparison and not patch.clear_top_n)
        or (patch.top_n is not None and (patch.clear_top_n or patch.group_by == []))
        or (patch.clear_comparison and patch.comparison_start_date is not None)
    ):
        return QueryPlan(
            decision="clarify",
            clarification_question="请明确要修改的条件，比较或排名的要求不能互相冲突。",
        )
    for start, end in [
        (patch.start_date, patch.end_date),
        (patch.comparison_start_date, patch.comparison_end_date),
    ]:
        if (start is None) != (end is None):
            return QueryPlan(
                decision="clarify", clarification_question="请明确完整的起止日期范围。"
            )
    updates = patch.model_dump(
        exclude_none=True,
        exclude={
            "clear_comparison",
            "clear_top_n",
            "clarification_question",
            "unsupported_reason",
        },
    )
    if patch.clear_comparison:
        updates.update(comparison_start_date=None, comparison_end_date=None)
    if patch.clear_top_n or patch.group_by == []:
        updates["top_n"] = None
    try:
        return QueryPlan.model_validate(base.model_dump() | updates)
    except ValidationError:
        return QueryPlan(
            decision="clarify",
            clarification_question="条件组合不支持或日期无效，请明确一年以内的有效期间、分组及比较/排名方式。",
        )


def offline_patch(question: str, clarifications: list[str], reference: str) -> FollowupPatch:
    """Limited, conservative demo grammar; never concatenates the source question."""
    changes: dict = {"decision": "ready"}
    date_issue = False
    top_issue = False
    names = {
        "实付": "paid_amount",
        "退款": "successful_refund",
        "净收款": "period_net_receipt",
        "订单数": "paid_order_count",
        "客单价": "average_order_value",
    }
    for text in [question, *clarifications]:
        if any(
            w in text
            for w in [
                "预测",
                "因果",
                "库存",
                "广告",
                "利润",
                "为什么",
                "原因",
                "流量",
                "地区",
                "只",
                "仅",
                "筛选",
                "过滤",
            ]
        ):
            return FollowupPatch(
                decision="unsupported",
                unsupported_reason="首版追问只支持五指标、日期、分组、期间比较和Top N；不支持该分析或筛选。",
            )
        hits = [(text.rfind(word), metric) for word, metric in names.items() if word in text]
        ambiguous = max(text.rfind("销售额"), text.rfind("业绩"))
        if ambiguous >= 0 and (not hits or ambiguous > max(hits)[0]):
            changes["metric_id"] = None
        elif hits:
            changes["metric_id"] = max(hits)[1]
        groups = [
            dimension
            for words, dimension in [
                (["每天", "按日"], "day"),
                (["各渠道", "按渠道"], "channel"),
                (["各类别", "按类别", "各品类", "按品类"], "category"),
            ]
            if any(word in text for word in words)
        ]
        if groups:
            changes["group_by"] = groups
        elif any(word in text for word in ["整体", "总体", "不分组", "取消分组"]):
            changes["group_by"] = []
        cancelling_comparison = any(w in text for w in ["取消比较", "不比较", "取消对比"])
        if cancelling_comparison:
            changes["clear_comparison"] = True
        if any(w in text for w in ["取消排名", "不排名", "全部", "取消Top", "取消top"]):
            changes["clear_top_n"] = True
            top_issue = False
        match = re.search(r"(?:Top\s*|前\s*)(\d+)", text, re.I)
        if match:
            if not 1 <= int(match[1]) <= 200:
                top_issue = True
            else:
                top_issue = False
                changes.update(top_n=int(match[1]), clear_top_n=False)
        periods = []
        explicit = re.findall(r"\d{4}-\d{2}-\d{2}", text)
        months = re.findall(r"(?:(\d{4})年\s*)?(\d{1,2})月", text)
        ref = date.fromisoformat(reference)
        try:
            if explicit:
                if len(explicit) != 2:
                    raise ValueError
                periods = [(date.fromisoformat(explicit[0]), date.fromisoformat(explicit[1]))]
            elif months:
                if re.search(r"月\s*\d+\s*[日号]", text):
                    raise ValueError
                for year, month in months:
                    y, m = int(year or ref.year), int(month)
                    periods.append((date(y, m, 1), date(y + (m == 12), 1 if m == 12 else m + 1, 1)))
            elif "上月" in text:
                end = ref.replace(day=1)
                periods = [((end - timedelta(days=1)).replace(day=1), end)]
            elif "今天" in text:
                periods = [(ref, ref + timedelta(days=1))]
            elif "上周" in text:
                end = ref - timedelta(days=ref.weekday())
                periods = [(end - timedelta(days=7), end)]
            elif any(w in text for w in ["日期", "明年", "去年", "下月", "这月", "昨天", "前天"]):
                raise ValueError
            if any(not 0 < (end - start).days <= 366 for start, end in periods):
                raise ValueError
        except ValueError:
            date_issue = True
            continue
        if _unknown_demo_text(text):
            return FollowupPatch(
                decision="unsupported",
                unsupported_reason="离线演示未完整识别这次修改，请使用明确的五指标、整月/起止日期和分组表达。",
            )
        comparing = not cancelling_comparison and any(
            w in text for w in ["比较", "对比", "相比", "比上", "比8", "比9"]
        )
        if periods:
            date_issue = False
            if len(periods) > 2 or (len(periods) == 2 and not comparing):
                date_issue = True
                continue
            if comparing:
                changes.update(
                    comparison_start_date=periods[-1][0],
                    comparison_end_date=periods[-1][1],
                    clear_comparison=False,
                )
            if not comparing or len(periods) == 2:
                changes.update(start_date=periods[0][0], end_date=periods[0][1])
    if top_issue:
        return FollowupPatch(
            decision="clarify", clarification_question="Top N 请使用1至200的整数。"
        )
    if date_issue:
        return FollowupPatch(
            decision="clarify",
            clarification_question="请提供有效整月或YYYY-MM-DD起止日期（左闭右开）。",
        )
    if changes.get("metric_id", "unchanged") is None:
        return FollowupPatch(
            decision="clarify", clarification_question="销售额是指实付金额还是期间净收款？"
        )
    if len(changes) == 1:
        return FollowupPatch(
            decision="clarify",
            clarification_question="请明确要修改的指标、日期或分组，例如：那各渠道呢？",
        )
    return FollowupPatch(**changes)
