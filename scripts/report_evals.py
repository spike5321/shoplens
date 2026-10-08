"""Render a report from all recorded outcomes; no model calls or result selection."""

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from data.dataset import generate  # noqa: E402
from evals.cases import cases  # noqa: E402
from evals.reference import expected_tables  # noqa: E402


def read(name, file):
    return json.loads((ROOT / "evals/runs" / name / file).read_text(encoding="utf-8"))


def records(name):
    return [
        json.loads(line)
        for line in (ROOT / "evals/runs" / name / "results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def rate(value):
    return f"{value['passed']}/{value['total']}" + (
        f"（{value['rate']:.1%}）" if value["rate"] is not None else "（无样本）"
    )


def main():
    full = "20261008-real-full"
    summary, manifest, rows = read(full, "summary.json"), read(full, "manifest.json"), records(full)
    assert summary["completed"] == summary["requested"] == len(rows) == 240
    assert len({(r["case_id"], r["profile"]) for r in rows}) == 240
    before, after = records("20261008-real-pilot"), records("20261008-real-retest")
    before = {(r["case_id"], r["profile"]): r for r in before}
    after = {(r["case_id"], r["profile"]): r for r in after}
    by_id = {r["id"]: r for r in cases()}
    budget = summary["budget_after"]
    started = (
        datetime.fromisoformat(manifest["started_at"])
        .astimezone(ZoneInfo("Asia/Hong_Kong"))
        .isoformat()
    )
    ended = (
        datetime.fromisoformat(summary["completed_at"])
        .astimezone(ZoneInfo("Asia/Hong_Kong"))
        .isoformat()
    )
    lines = [
        "# 阶段 5 评测报告",
        "",
        "评测日期：2026-10-08（Asia/Hong_Kong）。",
        "",
        "完整真实评测为 80 题 × 3 配置，共 240 个结果；所有失败保留。固定版本 `86d129b`，`deepseek-chat`、温度 0、每调用输出最多 2048 Token、网络重试 0、两个工作线程。",
        "",
        f"运行时间（Asia/Hong_Kong）：`{started}` 至 `{ended}`；数据摘要 `{manifest['data_sha256']}`，题库摘要 `{manifest['cases_sha256']}`。完整源码、依赖和权限参数见 [manifest](runs/20261008-real-full/manifest.json)。",
        "",
        "## 正确与执行行为",
        "",
        "结果正确率仅以可回答题为分母：开发集 36 题，冻结集 35 题。另有开发集 4 题、冻结集 5 题应拒绝。SQL 成功是通过验证后执行的结果，不能代表所有生成的 SQL 正确。",
        "",
        "| 配置/集合 | 结果正确 | SQL 成功/可回答 | 初始澄清 | 正确拒绝 | 错误拒绝数 | 修复成功 |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, m in summary["metrics"].items():
        lines.append(
            f"| {key} | {rate(m['result_correct'])} | {rate(m['sql_success_answerable'])} | {rate(m['clarification_initial'])} | {rate(m['rejection_correct'])} | {m['false_rejections']} | {rate(m['repair_success'])} |"
        )
    lines += [
        "",
        "结论：增加指标语义/示例后，冻结集从 2/35 提升至 31/35；允许一次修复为 30/35，没有进一步提升。完整评测 8 次修复均未得到正确结果。初次试跑曾有一次修复成功，该个例不能代替完整评测结论。",
        "",
        "## 延迟、调用和 Token",
        "",
        "| 配置/集合 | p50 ms | p95 ms | API 调用 | 已知输入 Token | 已知输出 Token | 总 Token |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, m in summary["metrics"].items():
        u = m["known_tokens"]
        lines.append(
            f"| {key} | {m['p50_ms']} | {m['p95_ms']} | {m['api_calls']} | {u['input_tokens']} | {u['output_tokens']} | {u['total_tokens']} |"
        )
    lines += [
        "",
        f"本次阶段累计（含 4 场景冒烟及两轮试跑）：**{budget['calls']} 次调用、{budget['known_tokens']:,} Token**；上限 800 次 / 2,000,000 Token。未知 usage 调用为 0，未触及预算。完整 240 次评测使用 489 次 API 调用、571,334 Token。",
        "",
        "未核实带日期价格配置，费用为 NULL。分位数样本小，API 缓存和并发会影响延迟；温度 0 和模型别名不能保证服务商每次返回相同结果。",
        "",
        "## 失败分类",
        "",
    ]
    for key, m in summary["metrics"].items():
        lines.append(
            f"- {key}：`{json.dumps(m['failure_classes'], ensure_ascii=False)}`；额外澄清 {m['unnecessary_clarifications']} 次。"
        )
    lines += [
        "",
        "## 开发集修改与逐题复测",
        "",
        "唯一实现改动：公开 Schema 的视图名从 `sales_lines/refund_events/cash_events` 改为 `analytics.*`，没有放宽 AST 或业务形状验证。以下六个失败案例共享这个来源错误；不把它们称为六种独立根因。",
        "",
        "| 问题 | 预期 | 初次实际/证据 | 修改 | 同题复测 |",
        "|---|---|---|---|---|",
    ]
    for case_id in ("dev-01", "dev-09", "dev-16", "dev-26", "dev-31", "dev-40"):
        case, a, b = by_id[case_id], before[(case_id, "schema")], after[(case_id, "schema")]
        expected = case["expected"]
        wanted = f"{expected['metric_id']} [{expected['start_date']},{expected['end_date']}) {expected['group_by']}"
        if case["clarifications"]:
            wanted = "先澄清；" + wanted
        evidence = next(
            name for name in ("sales_lines", "refund_events", "cash_events") if name in a["sqls"][0]
        )
        lines.append(
            f"| {case['question']} | {wanted} | `{a['failure_class']}`：`FROM {evidence}` 无 schema，无数据库结果 | 公开 Schema 加 `analytics.` | `{b['failure_class']}`，来源错误消除但仍未得到正确结果 |"
        )
    lines += [
        "",
        "这项小改动没有提高六题总体正确数：schema 0/6→0/6，semantic 4/6→4/6，repair 5/6→4/6。剩余失败涉及聚合、重复别名、误分组及模型契约；两轮小样本不能归因出修正本身的质量收益。",
        "",
        "独立原始记录计算的六题预期表格（不进入模型提示词）：",
        "",
        "```json",
    ]
    data = generate()
    lines.append(
        json.dumps(
            {
                i: expected_tables(data, by_id[i]["expected"])
                for i in ("dev-01", "dev-09", "dev-16", "dev-26", "dev-31", "dev-40")
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    lines += [
        "```",
        "",
        "## 其他失败及限制",
        "",
        "- `dev-25/dev-26/dev-30`：预期总体客单价，模型在 `group_by=[]` 时仍生成 `day/GROUP BY day`。完整 repair 先 validate 失败，再 repair 后仍 `plan_mismatch`，没有进入 execute。当前改动未处理这一根因；下一步可在开发集验证明确的空分组输出契约。",
        "- `dev-40`：按渠道 Top 2 是已支持请求，模型却返回 unsupported，理由把它误认为比较组合。规划拒绝不能被 SQL 修复挽回。保留错误拒绝，未增加自动改写规划；可在未来开发集检查支持范围说明。",
        "- `frozen-02/frozen-20`：单个期间的 SqlDraft 返回两条查询，实际 `invalid_sql` 是数量与期间计划不符；修复后数量仍不符。没有将其中一条悄悄取出执行。未用冻结题修改提示词。",
        "- `frozen-38`：超过一年应明确拒绝，模型输出无法通过 QueryPlan 契约，系统以 model_protocol_error 失败并未查库，拒绝表达不合格。usage 从 raw 返回值单独保存，未将该失败计为零成本。",
        "",
        "## 约束、冻结与可复现边界",
        "",
        "24/24 固定非法 SQL 被 AST 阻断，不能外推成现实攻击阻断率。独立参考检查/真实 reader/权限、超时和时区另由统一验收覆盖。离线开发集三配置均 40/40 行为通过，是同一假模型的接线验证，不能替代真实质量。",
        "",
        "冻结题的问法与交互家族没有用于此次开发集迭代；指标/视图和部分 SQL 聚合形状共享。本轮 `frozen_used_for_iteration=false`、冻结摘要匹配，完整真实运行前未执行冻结题。结果发布后该集已暴露，未来据此调试必须标记迭代，不能称为盲测。评分在当前严格 SQL 形状约束下进行，不能外推至任意等价 SQL 或通用 BI。",
        "",
        "三配置是评测对照，不自动切换产品默认配置。默认真实模型适配器不包含评测的三条示例；这些数值不能直接宣称为已部署浏览器版本准确率。真实 HTTP/浏览器模型链路、容器和公网未在本阶段验收。",
        "",
        "## 原始结果与复现",
        "",
        "- [初次开发试跑](runs/20261008-real-pilot/results.jsonl)、[修改后复测](runs/20261008-real-retest/results.jsonl)、[完整评测](runs/20261008-real-full/results.jsonl)：成功和失败均保留。",
        "- [完整汇总](runs/20261008-real-full/summary.json)、[图冒烟](runs/20261008-real-smoke.json)、[冻结配置](freeze.json)。",
        "- 命令/评分/预算规则见 [评测说明](README.md)。报告可用 `python scripts/report_evals.py` 从上述原始结果重新生成；初次版本为 `56210b8`，完整版本为 `86d129b`。",
    ]
    (ROOT / "evals/REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Report generated from all 240 results: evals/REPORT.md")


if __name__ == "__main__":
    main()
