"""Migrate, load only an empty demo database, verify, then set up checkpoints."""

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from seed_data import seed  # noqa: E402
from setup_db import main as setup_database  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.query_checkpoint import postgres_checkpointer  # noqa: E402


def main():
    setup_database()
    with psycopg.connect(get_settings().dsn("admin")) as conn:
        empty = all(
            conn.execute(f"SELECT COUNT(*) FROM business.{table}").fetchone()[0] == 0
            for table in ("products", "orders", "order_items", "refunds")
        )
    if empty:
        print("Fresh database: loading fixed-seed synthetic data.")
        print(seed())
    else:
        print("Existing database: seed skipped; data and history preserved.")
    # A separate process uses the independent reference calculation, outside model context.
    import subprocess

    subprocess.run([sys.executable, str(ROOT / "scripts/check_reference.py")], check=True)
    with postgres_checkpointer(get_settings(), setup=True):
        print("Checkpoint tables ready; initialization passed.")


if __name__ == "__main__":
    main()
