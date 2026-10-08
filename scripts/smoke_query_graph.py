"""Opt-in real model checks; reference calculations stay outside model context."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.query_checkpoint import postgres_checkpointer  # noqa: E402
from app.query_executor import QueryExecutor  # noqa: E402
from app.query_graph import QueryService  # noqa: E402
from data.dataset import generate, local  # noqa: E402
from data.reference import calculate  # noqa: E402


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-model", action="store_true")
    args = parser.parse_args()
    settings = get_settings().model_copy(update={"model_provider": "deepseek"})
    if not args.real_model or not settings.deepseek_api_key_configured:
        print(
            "NOT_RUN: 真实查询图未验证；需 --real-model 和本地配置的模型 Key。不会用离线结果代替。"
        )
        return 2
    reference = calculate(generate(), local("2026-09-01", 0), local("2026-10-01", 0))
    cases = [
        ("normal", "2026年9月实付金额", "paid_amount", None),
        ("clarify", "2026年9月销售额", "period_net_receipt", "期间净收款"),
        ("cross_period_refund", "2026年9月成功退款金额", "successful_refund", None),
        ("unsupported", "预测2026年10月实付金额并解释广告的因果影响", None, None),
    ]
    passed = 0
    with QueryExecutor(settings) as executor, postgres_checkpointer(settings) as saver:
        service = QueryService(executor, settings, saver)
        for name, question, metric, clarification in cases:
            result = service.start(question, owner_id="real-graph-smoke")
            ok = True
            if clarification:
                ok = result["status"] == "awaiting_clarification"
                if ok:
                    result = service.resume(
                        result["run_id"], clarification, owner_id="real-graph-smoke"
                    )
            if metric:
                ok = (
                    ok and result["status"] == "succeeded" and result["plan"]["metric_id"] == metric
                )
                if ok:
                    ok = result["report"]["tables"][0]["rows"] == [[str(reference[metric])]]
            else:
                ok = result["status"] == "rejected" and "execute" not in [
                    e["node"] for e in result["events"]
                ]
            passed += int(ok)
            print(
                json.dumps(
                    {
                        "case": name,
                        "pass": ok,
                        "status": result["status"],
                        "provider": result["provider"],
                        "model_id": result["model_id"],
                        "model_calls": result["model_calls"],
                        "tokens": result["tokens"],
                        "active_ms": result["active_ms"],
                        "error_code": result["error_code"],
                    },
                    ensure_ascii=False,
                )
            )
    print(f"Real graph smoke: {passed}/4 passed; cost not estimated without dated prices.")
    return int(passed != 4)


if __name__ == "__main__":
    raise SystemExit(main())
