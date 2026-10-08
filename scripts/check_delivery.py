"""Score captured HTTP/browser results using independent raw-record calculations."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data.dataset import generate, local  # noqa: E402
from data.reference import calculate  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    captured = json.loads(args.result.read_text(encoding="utf-8"))
    data = generate()
    start, end = local("2026-09-01", 0), local("2026-10-01", 0)
    checks = []
    for item in captured["results"]:
        name, run = item["name"], item["run"]
        ok = run["status"] == item["expected"]
        if name == "blocked":
            ok = ok and "execute" not in [event["node"] for event in run["events"]]
        else:
            metric = {
                "normal": "paid_amount",
                "clarify": "period_net_receipt",
                "refund": "successful_refund",
            }[name]
            expected = (
                [
                    [channel, str(calculate(data, start, end, channel=channel)[metric])]
                    for channel in sorted({order["channel"] for order in data["orders"]})
                ]
                if name == "normal"
                else [[str(calculate(data, start, end)[metric])]]
            )
            ok = ok and run["plan"]["metric_id"] == metric
            ok = ok and run["report"]["tables"][0]["rows"] == expected
            if name == "clarify":
                ok = ok and item["initial_status"] == "awaiting_clarification"
        checks.append(
            {
                "case": name,
                "status": "PASS" if ok else "FAIL",
                "model_calls": run["model_calls"],
                "tokens": run["tokens"],
            }
        )
    checks.append(
        {
            "case": "history_refresh_isolation",
            "status": "PASS"
            if captured["history_restored"]
            and captured["isolated"]
            and captured["history_count"] == 4
            and not captured["page_errors"]
            else "FAIL",
        }
    )
    summary = {"provider": captured["provider"], "checks": checks}
    args.result.with_name("summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(any(check["status"] == "FAIL" for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
