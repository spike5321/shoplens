"""Budget/telemetry wrapper around the unchanged product adapter, without eval examples."""

from __future__ import annotations

import hashlib
import json
import time

from app.query_model import DeepSeekQueryModel, ModelError


class ProductionModel(DeepSeekQueryModel):
    def __init__(self, settings, budget):
        super().__init__(settings)
        self.budget = budget
        self.calls = []

    def _invoke(self, schema, system, payload, *, remaining_ms):
        if remaining_ms <= 0:
            raise ModelError("budget_exceeded")
        serialized = json.dumps(payload, ensure_ascii=False)
        maximum = (
            len(system.encode())
            + len(serialized.encode())
            + len(json.dumps(schema.model_json_schema(), ensure_ascii=False).encode())
            + 16_384
            + 2048
        )
        self.budget.reserve(maximum)
        started, usage, error_code = time.monotonic(), None, None
        try:
            reply = super()._invoke(schema, system, payload, remaining_ms=remaining_ms)
            usage = reply.usage
            return reply
        except ModelError as exc:
            error_code = exc.code
            raise
        except Exception:
            error_code = "unexpected_product_error"
            raise
        finally:
            # The product does not expose raw usage after protocol failure. Keep
            # the reservation as unknown consumption; never report it as free.
            self.budget.finish(maximum, usage)
            self.calls.append(
                {
                    "contract": schema.__name__,
                    "usage": usage,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "error_code": error_code,
                    "prompt_sha256": hashlib.sha256((system + serialized).encode()).hexdigest(),
                }
            )
