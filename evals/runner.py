"""Reproducible evaluation over the real reader executor and raw-record references."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from langgraph.checkpoint.memory import InMemorySaver
from psycopg import sql
from psycopg.rows import dict_row

from app.config import get_settings
from app.query_executor import QueryError, QueryExecutor
from app.query_graph import QueryService
from app.query_model import OfflineQueryModel
from data.dataset import SEED, generate
from data.reference import check_manual_fixture
from evals.cases import SECURITY_SQL, cases
from evals.models import PROFILES, PROMPT_VERSION, Budget, EvalModel
from evals.reference import score

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()


def check_snapshot(settings, data):
    with psycopg.connect(settings.dsn("admin"), connect_timeout=3, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='3s'")
        for table, expected in data.items():
            columns = list(expected[0])
            statement = sql.SQL("SELECT {} FROM business.{} ORDER BY {}").format(
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.Identifier(table),
                sql.Identifier(columns[0]),
            )
            actual = conn.execute(statement).fetchall()
            if actual != expected:
                raise ValueError("dataset_snapshot_mismatch")


def security_check(executor):
    records = []
    for index, statement in enumerate(SECURITY_SQL, 1):
        try:
            executor.execute(statement)
            code, blocked = None, False
        except QueryError as exc:
            code, blocked = exc.code.value, exc.code.value != "database_error"
        records.append(
            {
                "id": f"constraint-{index:02}",
                "sql": statement,
                "blocked": blocked,
                "error_code": code,
            }
        )
    return records


def evaluate(case, profile, settings, executor, data, budget, real):
    model = EvalModel(settings, profile, budget) if real else OfflineQueryModel()
    selected = settings.model_copy(update={"max_repair_count": PROFILES[profile]})
    service = QueryService(executor, selected, InMemorySaver(), model=model)
    started = time.monotonic()
    state = service.start(case["question"], owner_id="evaluation")
    initial_status = state["status"]
    statuses = [initial_status]
    for clarification in case["clarifications"]:
        if state["status"] != "awaiting_clarification":
            break
        state = service.resume(state["run_id"], clarification, owner_id="evaluation")
        statuses.append(state["status"])
    result = {
        "case_id": case["id"],
        "split": case["split"],
        "family": case["family"],
        "question": case["question"],
        "profile": profile,
        "provider": model.provider,
        "model_id": model.model_id,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "status": state["status"],
        "statuses": statuses,
        "plan": state.get("plan"),
        "sqls": state.get("sqls"),
        "tables": state.get("report", {}).get("tables", []),
        "repair_count": state["repair_count"],
        "model_calls": state["model_calls"],
        "tokens": state["tokens"],
        "events": state["events"],
        "api_calls": model.calls if real else [],
        "error_code": state.get("error_code"),
    }
    result.update(score(case, state, initial_status, data))
    return result


def summarize(records):
    summaries = {}
    for profile in PROFILES:
        for split in ("dev", "frozen"):
            rows = [r for r in records if r["profile"] == profile and r["split"] == split]
            if not rows:
                continue
            answerable = [r for r in rows if r["answerable"]]
            repairs = [r for r in answerable if r["repair_count"]]
            clarified = [r for r in rows if r["expected_initial"] == "clarify"]
            rejected = [r for r in rows if not r["answerable"]]
            executed = [r for r in answerable if any(e["node"] == "execute" for e in r["events"])]
            latency = sorted(r["elapsed_ms"] for r in rows)

            def ratio(n, denominator):
                return {
                    "passed": n,
                    "total": len(denominator),
                    "rate": n / len(denominator) if denominator else None,
                }

            api_usage = [c["usage"] for r in rows for c in r["api_calls"]]
            summaries[f"{profile}/{split}"] = {
                "cases": len(rows),
                "result_correct": ratio(sum(r["correct"] for r in answerable), answerable),
                "all_behavior_correct": ratio(sum(r["correct"] for r in rows), rows),
                "sql_success_answerable": ratio(
                    sum(r["status"] == "succeeded" for r in answerable), answerable
                ),
                "sql_success_when_attempted": ratio(
                    sum(r["status"] == "succeeded" for r in executed), executed
                ),
                "clarification_initial": ratio(
                    sum(r["initial_behavior_correct"] for r in clarified), clarified
                ),
                "rejection_correct": ratio(sum(r["correct"] for r in rejected), rejected),
                "false_rejections": sum(r["status"] == "rejected" for r in answerable),
                "unnecessary_clarifications": sum(
                    r["statuses"][0] == "awaiting_clarification"
                    and r["expected_initial"] == "ready"
                    for r in rows
                ),
                "repair_success": ratio(sum(r["correct"] for r in repairs), repairs),
                "p50_ms": latency[(len(latency) - 1) // 2],
                "p95_ms": latency[min(len(latency) - 1, int(len(latency) * 0.95))],
                "latency_note": "small sample; descriptive quantiles only",
                "adapter_calls": sum(r["model_calls"] for r in rows),
                "api_calls": len(api_usage),
                "known_tokens": {
                    key: sum(int(u.get(key, 0)) for u in api_usage if u)
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                },
                "unknown_usage_calls": sum(u is None for u in api_usage),
                "cost": None,
                "failure_classes": dict(
                    Counter(r["failure_class"] for r in rows if not r["correct"])
                ),
            }
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--split", choices=("dev", "frozen", "all"), default="all")
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--budget-state", default=".local/eval-budget-20261008.json")
    parser.add_argument("--initial-calls", type=int, default=0)
    parser.add_argument("--initial-tokens", type=int, default=0)
    parser.add_argument(
        "--frozen-used-for-iteration",
        action="store_true",
        help="declare that frozen questions/results have been used to tune this version",
    )
    args = parser.parse_args()
    output = (ROOT / args.out).resolve()
    if (
        not (
            output.is_relative_to(ROOT / "evals" / "runs") or output.is_relative_to(ROOT / ".local")
        )
        or output.exists()
    ):
        parser.error(
            "--out must be a new directory below evals/runs or .local (previous runs never overwritten)"
        )
    budget_path = (ROOT / args.budget_state).resolve()
    if not budget_path.is_relative_to(ROOT / ".local"):
        parser.error("budget state must stay in .local")
    settings = get_settings()
    if args.real_model and not settings.deepseek_api_key_configured:
        print("NOT_RUN: real model key missing")
        return 2
    settings = settings.model_copy(
        update={"model_provider": "deepseek" if args.real_model else "offline"}
    )
    data = generate()
    check_manual_fixture()
    check_snapshot(settings, data)
    selected = [
        r
        for r in cases()
        if (args.split == "all" or r["split"] == args.split)
        and (not args.ids or r["id"] in args.ids)
    ]
    if not selected or (args.ids and set(args.ids) != {r["id"] for r in selected}):
        parser.error("unknown or empty case selection")
    frozen = any(r["split"] == "frozen" for r in selected)
    freeze = (
        json.loads((ROOT / "evals/freeze.json").read_text(encoding="utf-8")) if frozen else None
    )
    previous_frozen = frozen and any(
        json.loads(path.read_text(encoding="utf-8")).get("provider") == "deepseek"
        and any(
            case_id.startswith("frozen-")
            for case_id in json.loads(path.read_text(encoding="utf-8")).get("case_ids", [])
        )
        for path in (ROOT / "evals/runs").glob("*/manifest.json")
    )
    snapshot_matches = freeze is None or (
        freeze["cases_sha256"] == digest(cases())
        and freeze["prompt_version"] == PROMPT_VERSION
        and all(
            hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == value
            for path, value in freeze["source_sha256"].items()
        )
    )
    frozen_iterated = args.frozen_used_for_iteration or (frozen and not snapshot_matches)
    if frozen and not snapshot_matches and not args.frozen_used_for_iteration:
        parser.error(
            "frozen snapshot changed; explicitly mark --frozen-used-for-iteration (not a blind test)"
        )
    budget = (
        Budget(budget_path, initial_calls=args.initial_calls, initial_tokens=args.initial_tokens)
        if args.real_model
        else None
    )
    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "provider": settings.model_provider,
        "model_id": settings.deepseek_model if args.real_model else "offline-query-v1",
        "temperature": 0,
        "max_output_tokens": 2048,
        "api_retries": 0,
        "workers": args.workers,
        "seed": SEED,
        "data_sha256": digest(data),
        "cases_sha256": digest(cases()),
        "case_ids": [r["id"] for r in selected],
        "prompt_version": PROMPT_VERSION,
        "source_sha256": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in (
                "evals/cases.py",
                "evals/models.py",
                "evals/reference.py",
                "evals/runner.py",
                "backend/app/query_model.py",
                "backend/app/query_graph.py",
                "backend/app/query_plan.py",
            )
        },
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dependencies": {
            p: importlib.metadata.version(p)
            for p in ("langchain-deepseek", "langgraph", "sqlglot", "psycopg")
        },
        "statement_timeout_ms": settings.sql_statement_timeout_ms,
        "row_cap": settings.sql_max_rows,
        "reference_date": settings.demo_reference_date,
        "profiles": PROFILES,
        "frozen_used_for_iteration": frozen_iterated,
        "frozen_exposed_before_run": bool(previous_frozen),
        "frozen_snapshot_matches": snapshot_matches,
        "frozen_note": "Template families held out during development tuning. Published results are exposed; subsequent tuning must declare iteration, never claim blind results.",
        "offline_note": "All profiles use the same deterministic fake model offline; this tests scoring/wiring, not prompt quality.",
        "price_config": None,
        "budget_before": dict(budget.data) if budget else None,
    }
    output.mkdir(parents=True)
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    records = []
    with QueryExecutor(settings) as executor:
        constraints = security_check(executor)
        (output / "constraints.json").write_text(
            json.dumps(constraints, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not all(r["blocked"] for r in constraints):
            print("FAIL: execution constraint regression")
            return 1
        with (
            ThreadPoolExecutor(max_workers=args.workers) as pool,
            (output / "results.jsonl").open("w", encoding="utf-8") as log,
        ):
            jobs = {
                pool.submit(
                    evaluate, case, profile, settings, executor, data, budget, args.real_model
                ): (case, profile)
                for case in selected
                for profile in PROFILES
            }
            for future in as_completed(jobs):
                result = future.result()
                records.append(result)
                log.write(json.dumps(result, ensure_ascii=False) + "\n")
                log.flush()
                if len(records) % 12 == 0 or not result["correct"]:
                    print(
                        f"{len(records)}/{len(jobs)} {result['case_id']} {result['profile']} {result['failure_class'] or 'correct'}",
                        flush=True,
                    )
    summary = {
        "completed_at": datetime.now(UTC).isoformat(),
        "requested": len(selected) * len(PROFILES),
        "completed": len(records),
        "constraints_passed": sum(r["blocked"] for r in constraints),
        "constraints_total": len(constraints),
        "metrics": summarize(records),
        "budget_after": dict(budget.data) if budget else None,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary["metrics"], ensure_ascii=False, indent=2))
    if any(r["error_code"] == "evaluation_budget_exceeded" for r in records):
        print("NOT_RUN: budget exhausted; partial results retained")
        return 2
    print(
        f"Completed evaluation ({settings.model_provider}); accuracy is reported separately, not implied by exit 0."
    )
    return 0
