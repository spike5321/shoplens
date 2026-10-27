"""Registered followup HTTP scenarios; real model is explicit and shares the original budget."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from delivery_api import BudgetedProductionModel  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.query_graph as graph  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from data.dataset import generate, local  # noqa: E402
from data.reference import calculate  # noqa: E402
from evals.models import Budget  # noqa: E402
from evals.runner import check_snapshot  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    output = (ROOT / args.out).resolve()
    if output.exists() or not output.is_relative_to(ROOT / "evals/runs"):
        parser.error("--out must be new and under evals/runs")
    settings = get_settings()
    ledger = ROOT / ".local/eval-budget-20261008.json"
    if args.real_model and (not settings.deepseek_api_key_configured or not ledger.exists()):
        print("NOT_RUN: configured key and existing shared budget required")
        return 2
    settings.model_provider = "deepseek" if args.real_model else "offline"
    budget = Budget(ledger) if args.real_model else None
    if budget and budget.data["max_calls"] - budget.data["calls"] < 9:
        print("NOT_RUN: registered scenario sequence requires at least 9 available calls")
        return 2
    if budget:
        model = BudgetedProductionModel(settings, budget)
        graph.query_model = lambda _settings: model
    data = generate()
    check_snapshot(settings, data)
    output.mkdir(parents=True)

    def save(name, value):
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "provider": settings.model_provider,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_sha256": {
            str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
                p.read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for p in [
                *sorted((ROOT / "backend/app").glob("*.py")),
                Path(__file__),
                ROOT / "docs/followups.md",
            ]
        },
        "note": "F01/F02/F03 registered before implementation; directed regression, not blind/full quality. HTTP/TestClient + real PostgreSQL; not a real-model browser test.",
        "budget_before": dict(budget.data) if budget else None,
    }
    save("manifest.json", manifest)
    records, checks = [], []
    with TestClient(app) as http:
        session = http.get("/api/session")
        if session.status_code != 200:
            print("FAIL: workspace unavailable")
            return 1
        headers = {"x-csrf-token": session.json()["csrf_token"]}

        def request(path, body):
            response = http.post(path, json=body, headers=headers)
            if response.status_code != 202:
                raise RuntimeError("scenario_http_failed")
            run = response.json()
            for _ in range(1000):
                run = http.get(f"/api/runs/{run['run_id']}").json()
                if run["status"] != "running":
                    records.append(run)
                    save("results.json", records)
                    return run
                time.sleep(0.1)
            raise RuntimeError("scenario_timeout")

        def score(run, metric, month, groups):
            if run["status"] != "succeeded":
                return False
            plan = run["plan"]
            if (
                plan["comparison_start_date"] is not None
                or plan["comparison_end_date"] is not None
                or plan["top_n"] is not None
                or len(run["report"]["tables"]) != 1
            ):
                return False
            start, end = f"2026-{month:02}-01", f"2026-{month + 1:02}-01"
            if (plan["metric_id"], plan["start_date"], plan["end_date"], plan["group_by"]) != (
                metric,
                start,
                end,
                groups,
            ):
                return False
            expected = {}
            channels = sorted({o["channel"] for o in data["orders"]}) if groups else [None]
            for channel in channels:
                expected[channel] = calculate(
                    data, local(start, 0, 0), local(end, 0, 0), channel=channel
                )[metric]
            table = run["report"]["tables"][0]
            actual = {(row[0] if groups else None): row[-1] for row in table["rows"]}
            return (
                not table["truncated"]
                and set(actual) == set(expected)
                and all(
                    (actual[k] is None and v is None)
                    or (
                        actual[k] is not None
                        and v is not None
                        and math.isclose(float(actual[k]), v, rel_tol=0, abs_tol=1e-8)
                    )
                    for k, v in expected.items()
                )
            )

        original = request("/api/runs", {"question": "2026年9月整体客单价是多少？"})
        checks.append({"id": "source", "passed": score(original, "average_order_value", 9, [])})
        if checks[-1]["passed"]:
            channel = request(
                f"/api/runs/{original['run_id']}/followup", {"question": "那各渠道呢？"}
            )
            checks.append(
                {
                    "id": "F01",
                    "passed": score(channel, "average_order_value", 9, ["channel"])
                    and channel["source_run_id"] == original["run_id"],
                }
            )
            if checks[-1]["passed"]:
                august = request(
                    f"/api/runs/{channel['run_id']}/followup", {"question": "改成2026年8月"}
                )
                checks.append(
                    {"id": "F02", "passed": score(august, "average_order_value", 8, ["channel"])}
                )
            ambiguous = request(
                f"/api/runs/{original['run_id']}/followup", {"question": "那销售额呢？"}
            )
            checks.append(
                {"id": "F03-pause", "passed": ambiguous["status"] == "awaiting_clarification"}
            )
            if checks[-1]["passed"]:
                done = request(
                    f"/api/runs/{ambiguous['run_id']}/clarify",
                    {"text": "期间净收款", "revision": ambiguous["revision"]},
                )
                checks.append(
                    {
                        "id": "F03-resume",
                        "passed": score(done, "period_net_receipt", 9, [])
                        and done["source_run_id"] == original["run_id"],
                    }
                )
            checks.append(
                {
                    "id": "F04-source-preserved",
                    "passed": http.get(f"/api/runs/{original['run_id']}").json() == original,
                }
            )
    summary = {
        "completed_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "passed": sum(c["passed"] for c in checks),
        "expected_checks": 6,
        "api_calls": budget.data["calls"] - manifest["budget_before"]["calls"] if budget else 0,
        "budget_after": dict(budget.data) if budget else None,
    }
    save("summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(checks) == 6 and all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
