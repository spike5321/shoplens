"""Fixed question families. Expected plans are evaluator data, never model context."""

from app.query_plan import METRICS


def cases() -> list[dict]:
    rows = []

    def add(
        split,
        family,
        question,
        metric=None,
        *,
        start="2026-09-01",
        end="2026-10-01",
        groups=(),
        top=None,
        comparison=None,
        clarify=(),
        reject=False,
    ):
        expected = {"decision": "unsupported" if reject else "ready"}
        if not reject:
            expected.update(
                metric_id=metric,
                start_date=start,
                end_date=end,
                group_by=list(groups),
                top_n=top,
                comparison_start_date=comparison[0] if comparison else None,
                comparison_end_date=comparison[1] if comparison else None,
            )
        rows.append(
            {
                "id": f"{split}-{sum(r['split'] == split for r in rows) + 1:02}",
                "split": split,
                "family": family,
                "question": question,
                "initial_decision": "clarify" if clarify else expected["decision"],
                "clarifications": list(clarify),
                "expected": expected,
            }
        )

    for metric, definition in METRICS.items():
        label = definition["label"]
        for month in (8, 9):
            add(
                "dev",
                "month_total",
                f"2026年{month}月{label}是多少？",
                metric,
                start=f"2026-{month:02}-01",
                end=f"2026-{month + 1:02}-01",
            )
        for group, word in (("channel", "渠道"), ("category", "类别")):
            add("dev", "single_group", f"2026年9月各{word}{label}", metric, groups=(group,))
        add("dev", "daily_month", f"2026年9月每天的{label}", metric, groups=("day",))
        add(
            "dev",
            "overall_comparison",
            f"2026年9月{label}与2026年8月比较",
            metric,
            comparison=("2026-08-01", "2026-09-01"),
        )
    for question, metric, answer in [
        ("2026年9月销售额", "paid_amount", "实付金额"),
        ("实付金额是多少", "paid_amount", "2026年9月"),
        ("2026年9月实付金额前2名", "paid_amount", "按类别分组"),
        ("2026年8月业绩", "period_net_receipt", "期间净收款"),
    ]:
        add(
            "dev",
            "single_clarification",
            question,
            metric,
            start="2026-08-01" if "8月" in question else "2026-09-01",
            end="2026-09-01" if "8月" in question else "2026-10-01",
            groups=("category",) if "前2" in question else (),
            top=2 if "前2" in question else None,
            clarify=(answer,),
        )
    for question in [
        "预测2026年10月利润",
        "解释9月广告的因果影响",
        "2026年9月库存",
        "2026年9月毛利率",
    ]:
        add("dev", "unsupported_domain", question, reject=True)
    add(
        "dev",
        "month_top",
        "2026年9月各类别实付金额前2名",
        "paid_amount",
        groups=("category",),
        top=2,
    )
    add(
        "dev",
        "month_top",
        "2026年9月各渠道成功退款金额Top 2",
        "successful_refund",
        groups=("channel",),
        top=2,
    )

    for metric, definition in METRICS.items():
        label = definition["label"]
        for start, end in (("2026-07-01", "2026-10-01"), ("2026-10-31", "2026-11-01")):
            add(
                "frozen",
                "explicit_interval",
                f"从{start}（含）到{end}（不含）的{label}",
                metric,
                start=start,
                end=end,
            )
        add(
            "frozen",
            "two_dimensions",
            f"2026年9月按渠道和类别同时分组列出{label}",
            metric,
            groups=("channel", "category"),
        )
        add(
            "frozen",
            "relative_week",
            f"上周{label}是多少",
            metric,
            start="2026-09-21",
            end="2026-09-28",
        )
        add(
            "frozen",
            "group_comparison",
            f"比较2026年9月与2026年8月各渠道的{label}",
            metric,
            groups=("channel",),
            comparison=("2026-08-01", "2026-09-01"),
        )
        add(
            "frozen",
            "explicit_top",
            f"在[2026-10-01,2026-10-02)内按类别的{label}降序取前2个",
            metric,
            start="2026-10-01",
            end="2026-10-02",
            groups=("category",),
            top=2,
        )
    for question, metric, answers, start, end in [
        ("销售额", "paid_amount", ("实付金额", "2026年9月"), "2026-09-01", "2026-10-01"),
        ("业绩", "period_net_receipt", ("期间净收款", "2026年8月"), "2026-08-01", "2026-09-01"),
        (
            "退款是多少",
            "successful_refund",
            ("2026-07-01 到 2026-08-01，结束不含",),
            "2026-07-01",
            "2026-08-01",
        ),
        (
            "支付订单数前2名",
            "paid_order_count",
            ("按渠道分组", "2026年9月"),
            "2026-09-01",
            "2026-10-01",
        ),
        (
            "各类别客单价",
            "average_order_value",
            ("2026-10-31 到 2026-11-01，结束不含",),
            "2026-10-31",
            "2026-11-01",
        ),
    ]:
        group = ("channel",) if "前2" in question else ("category",) if "各类别" in question else ()
        add(
            "frozen",
            "followup_interaction",
            question,
            metric,
            start=start,
            end=end,
            groups=group,
            top=2 if "前2" in question else None,
            clarify=answers,
        )
    for question in [
        "2026年9月与8月的各类别实付金额比较并取前2名",
        "2026年9月与8月每天的成功退款金额比较",
        "查询2025-01-01到2026-10-01的实付金额，结束不含",
        "2026年9月按每个商品名称列出支付订单数",
        "2026年9月同时查实付金额和成功退款金额两个指标",
    ]:
        add("frozen", "unsupported_combinations", question, reject=True)
    assert sum(r["split"] == "dev" for r in rows) == 40
    assert sum(r["split"] == "frozen" for r in rows) == 40
    assert not (
        {r["family"] for r in rows if r["split"] == "dev"}
        & {r["family"] for r in rows if r["split"] == "frozen"}
    )
    return rows


# These inputs test fixed rejection boundaries, not an estimated attack success rate.
SECURITY_SQL = [
    "SELECT item_id FROM analytics.sales_lines; SELECT item_id FROM analytics.sales_lines",
    "DELETE FROM business.orders",
    "UPDATE business.orders SET paid_cents=1",
    "INSERT INTO business.products VALUES (999,'x','x')",
    "DROP TABLE business.orders",
    "COPY business.orders TO STDOUT",
    "CALL forbidden()",
    "WITH x AS (DELETE FROM business.orders RETURNING order_id) SELECT order_id FROM x",
    "SELECT oid FROM pg_catalog.pg_class",
    "SELECT table_name FROM information_schema.tables",
    "SELECT item_id FROM business.order_items",
    "SELECT run_id FROM app_state.runs",
    "SELECT item_id FROM sales_lines",
    "SELECT private_value FROM analytics.sales_lines",
    "SELECT pg_sleep(1) FROM analytics.sales_lines",
    "SELECT set_config('statement_timeout','0',true) FROM analytics.sales_lines",
    "SELECT pg_read_file('x') FROM analytics.sales_lines",
    "SELECT nextval('x') FROM analytics.sales_lines",
    "SELECT s.item_id FROM analytics.sales_lines s CROSS JOIN analytics.refund_events r",
    "WITH RECURSIVE x AS (SELECT item_id FROM analytics.sales_lines) SELECT item_id FROM x",
    "SELECT * FROM analytics.sales_lines",
    "SELECT item_id FROM analytics.sales_lines UNION SELECT item_id FROM analytics.sales_lines",
    "SELECT item_id FROM analytics.sales_lines OFFSET 1",
    "SELECT item_id FROM analytics.sales_lines WHERE item_id=1::regclass",
]
