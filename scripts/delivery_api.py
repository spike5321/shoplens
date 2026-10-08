"""Local delivery-only API launcher: unchanged production prompts, bounded real calls."""

import argparse
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn  # noqa: E402

import app.query_graph as graph  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.query_model import DeepSeekQueryModel, ModelError  # noqa: E402
from evals.models import Budget  # noqa: E402


class BudgetedProductionModel(DeepSeekQueryModel):
    def __init__(self, settings, budget):
        super().__init__(settings)
        self.budget = budget
        self.initial_calls = budget.data["calls"]
        self.call_lock = threading.Lock()

    def _invoke(self, schema, system, payload, *, remaining_ms):
        maximum = (
            len(system.encode())
            + len(json.dumps(payload, ensure_ascii=False).encode())
            + len(json.dumps(schema.model_json_schema(), ensure_ascii=False).encode())
            + 16_384
            + 2048
        )
        with self.call_lock:
            if self.budget.data["calls"] - self.initial_calls >= 20:
                raise ModelError("delivery_call_limit")
            self.budget.reserve(maximum)
        usage = None
        try:
            reply = super()._invoke(schema, system, payload, remaining_ms=remaining_ms)
            usage = reply.usage
            return reply
        finally:
            self.budget.finish(maximum, usage)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--budget", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    if args.real_model:
        if not settings.deepseek_api_key_configured or not args.budget:
            parser.error("Real delivery requires a configured Key and existing budget ledger")
        if not args.budget.exists():
            parser.error("Reuse the approved ledger; do not start a new budget")
        settings.model_provider = "deepseek"
        model = BudgetedProductionModel(settings, Budget(args.budget))
        graph.query_model = lambda _settings: model
    else:
        settings.model_provider = "offline"
    uvicorn.run("app.main:app", host="127.0.0.1", port=18006, access_log=False)


if __name__ == "__main__":
    main()
