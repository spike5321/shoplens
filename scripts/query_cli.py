"""Stage 3 CLI. Ownership identifiers here are for local demos, not HTTP authentication."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings
from app.query_checkpoint import postgres_checkpointer
from app.query_executor import QueryExecutor
from app.query_graph import QueryService
from app.query_model import ModelError, OfflineQueryModel


def demo(executor, settings, saver):
    cases = [
        ("normal", "2026年9月各渠道实付金额", None, "succeeded"),
        ("clarify", "2026年9月销售额", None, "succeeded"),
        ("cross_period", "2026年9月期间净收款比2026年8月按渠道比较", None, "succeeded"),
        ("repair", "2026年9月实付金额", "missing_column", "succeeded"),
        ("repair_exhausted", "2026年9月实付金额", "always_bad", "failed"),
        ("unsupported", "预测2026年10月实付金额", None, "rejected"),
    ]
    failed = 0
    for name, question, fault, expected in cases:
        service = QueryService(executor, settings, saver, model=OfflineQueryModel(fault=fault))
        result = service.start(question, owner_id="cli-demo")
        if result["status"] == "awaiting_clarification":
            result = service.resume(result["run_id"], "期间净收款", owner_id="cli-demo")
        ok = result["status"] == expected and result["repair_count"] <= 1
        failed += int(not ok)
        print(
            json.dumps(
                {
                    "case": name,
                    "pass": ok,
                    "provider": result["provider"],
                    "status": result["status"],
                    "repair_count": result["repair_count"],
                    "model_calls": result["model_calls"],
                    "error_code": result["error_code"],
                    "nodes": [e["node"] for e in result["events"]],
                },
                ensure_ascii=False,
            )
        )
    return int(failed > 0)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ShopLens query kernel, synthetic demo data")
    parser.add_argument("--owner", default="cli-demo", help="local demo owner identifier")
    parser.add_argument(
        "--real-model", action="store_true", help="explicitly enable configured DeepSeek"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask")
    ask.add_argument("question")
    resume = commands.add_parser("resume")
    resume.add_argument("run_id")
    resume.add_argument("text")
    show = commands.add_parser("show")
    show.add_argument("run_id")
    commands.add_parser("demo")
    args = parser.parse_args()
    settings = get_settings().model_copy(
        update={"model_provider": "deepseek" if args.real_model else "offline"}
    )
    try:
        with QueryExecutor(settings) as executor, postgres_checkpointer(settings) as saver:
            if args.command == "demo":
                return demo(executor, settings, saver)
            service = QueryService(executor, settings, saver)
            if args.command == "ask":
                result = service.start(args.question, owner_id=args.owner)
            elif args.command == "resume":
                result = service.resume(args.run_id, args.text, owner_id=args.owner)
            else:
                result = service.read(args.run_id, owner_id=args.owner)
            print(json.dumps(result, ensure_ascii=False))
            return int(result["status"] == "failed")
    except ModelError as exc:
        print(json.dumps({"status": "NOT_RUN", "reason": exc.code}))
        return 2
    except (ValueError, PermissionError):
        print(json.dumps({"status": "failed", "error_code": "invalid_or_inaccessible_request"}))
        return 1
    except psycopg.Error:
        print(json.dumps({"status": "NOT_RUN", "reason": "checkpoint_database_unavailable"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
