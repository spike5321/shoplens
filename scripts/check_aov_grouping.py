"""Focused AOV regression; real calls require --real-model and the existing shared budget."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.query_executor import QueryExecutor  # noqa: E402
from app.query_plan import QueryPlan  # noqa: E402
from data.dataset import generate  # noqa: E402
from evals.cases import cases  # noqa: E402
from evals.models import Budget  # noqa: E402
from evals.runner import check_snapshot, evaluate, security_check  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    output = (ROOT / args.out).resolve()
    if output.exists() or not output.is_relative_to(ROOT / "evals/runs"):
        parser.error("--out must be a new directory under evals/runs")
    settings = get_settings()
    if args.real_model and not settings.deepseek_api_key_configured:
        print("NOT_RUN: real model key missing")
        return 2
    settings = settings.model_copy(
        update={"model_provider": "deepseek" if args.real_model else "offline"}
    )
    source = ROOT / "evals/aov_grouping_holdout.json"
    registered = json.loads(source.read_text(encoding="utf-8"))
    if settings.demo_reference_date != registered["reference_date"]:
        parser.error("reference date differs from registered cases")
    selected = [r for r in cases() if r["id"] in {"dev-26", "frozen-26", "frozen-28"}]
    for entry in registered["cases"]:
        plan = QueryPlan(
            decision="ready",
            metric_id="average_order_value",
            start_date=entry["start_date"],
            end_date=entry["end_date"],
            group_by=entry["group_by"],
        )
        selected.append(
            {
                "id": entry["id"],
                "split": "registered",
                "family": "aov-grouping",
                "question": entry["question"],
                "initial_decision": "ready",
                "clarifications": [],
                "expected": plan.model_dump(
                    mode="json", exclude={"clarification_question", "unsupported_reason"}
                ),
            }
        )
    budget = Budget(ROOT / ".local/eval-budget-20261008.json") if args.real_model else None
    data = generate()
    check_snapshot(settings, data)
    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "provider": settings.model_provider,
        "model_id": settings.deepseek_model if args.real_model else "offline-query-v1",
        "reference_date": settings.demo_reference_date,
        "temperature": 0,
        "max_repair_count": settings.max_repair_count,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for path in (
                "backend/app/query_model.py",
                "backend/app/query_plan.py",
                "backend/app/query_graph.py",
                "evals/aov_grouping_holdout.json",
                "scripts/check_aov_grouping.py",
                "evals/production.py",
                "evals/runner.py",
                "evals/reference.py",
                "evals/cases.py",
                "data/metrics.yaml",
            )
        },
        "case_ids": [case["id"] for case in selected],
        "note": "Three exposed regressions and four preregistered focused cases; not a blind or full quality benchmark. Independent raw-record scoring stays evaluator-side.",
        "budget_before": dict(budget.data) if budget else None,
    }
    output.mkdir(parents=True)

    def save(name, value):
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    save("manifest.json", manifest)
    records = []
    with QueryExecutor(settings) as executor:
        constraints = security_check(executor)
        save("constraints.json", constraints)
        if not all(row["blocked"] for row in constraints):
            print("FAIL: execution constraints")
            return 1
        with (output / "results.jsonl").open("w", encoding="utf-8") as log:
            for case in selected:
                result = evaluate(
                    case, "production", settings, executor, data, budget, args.real_model
                )
                records.append(result)
                log.write(json.dumps(result, ensure_ascii=False) + "\n")
                log.flush()
                print(
                    f"{result['case_id']}: {result['correct']} {result['failure_class'] or ''}",
                    flush=True,
                )
    summary = {
        "completed_at": datetime.now(UTC).isoformat(),
        "requested": len(selected),
        "completed": len(records),
        "correct": sum(row["correct"] for row in records),
        "repairs": sum(row["repair_count"] for row in records),
        "api_calls": sum(len(row["api_calls"]) for row in records),
        "constraints_passed": sum(row["blocked"] for row in constraints),
        "budget_after": dict(budget.data) if budget else None,
    }
    save("summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(row["correct"] for row in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
