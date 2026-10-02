"""浏览器验收辅助：真实数据库锁和仅当前测试会话的清理，不提供业务接口。"""

import argparse
import hashlib
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import get_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["block", "cleanup"])
    args = parser.parse_args()
    settings = get_settings()
    if args.action == "block":
        with psycopg.connect(settings.dsn("admin")) as conn:
            conn.execute("LOCK TABLE business.order_items IN ACCESS EXCLUSIVE MODE")
            print("LOCKED", flush=True)
            sys.stdin.readline()
            conn.rollback()
        return
    # Cookie 通过 stdin 传入，不进入命令行、日志或截图；只清理这些测试 owner。
    tokens = sys.stdin.read().splitlines()
    with psycopg.connect(settings.dsn("app")) as conn:
        for token in tokens:
            owner = conn.execute(
                "SELECT owner_id FROM app_state.sessions WHERE token_hash=%s",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
            if not owner:
                continue
            ids = conn.execute(
                "SELECT run_id FROM app_state.runs WHERE owner_id=%s", owner
            ).fetchall()
            for (run_id,) in ids:
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    conn.execute(
                        f"DELETE FROM app_state.{table} WHERE thread_id=%s", (f"shoplens:{run_id}",)
                    )
            conn.execute("DELETE FROM app_state.runs WHERE owner_id=%s", owner)
            conn.execute("DELETE FROM app_state.sessions WHERE owner_id=%s", owner)
    print("Test sessions cleaned.")


if __name__ == "__main__":
    main()
