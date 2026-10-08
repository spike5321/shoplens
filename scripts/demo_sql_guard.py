"""Demonstrate actual executor refusal; never expose arbitrary SQL through HTTP."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.query_executor import QueryError, QueryExecutor


def main():
    with QueryExecutor() as executor:
        before = executor.execute("SELECT COUNT(*) AS n FROM analytics.sales_lines").rows
        for statement in [
            "DROP TABLE business.orders",
            "SELECT * FROM business.orders",
            "SELECT pg_sleep(10)",
        ]:
            try:
                executor.execute(statement)
            except QueryError as exc:
                print(f"PASS blocked: {statement} => {exc.code}")
            else:
                print("FAIL: an illegal statement was accepted")
                return 1
        after = executor.execute("SELECT COUNT(*) AS n FROM analytics.sales_lines").rows
        if before != after:
            print("FAIL: sales row count changed")
            return 1
    print("PASS: approved view remains readable and row count unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
