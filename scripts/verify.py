"""Run ShopLens checks without conflating failure and missing prerequisites.

Exit codes: 0 = all requested checks passed; 1 = at least one check failed;
2 = no check failed, but a requested check could not run.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


@dataclass
class Result:
    name: str
    status: str  # PASS / FAIL / NOT_RUN
    detail: str = ""


def redact(value: str) -> str:
    """Remove known credentials from captured command output."""
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        from app.config import get_settings

        settings = get_settings()
        for secret in (
            settings.pg_admin_password,
            settings.pg_app_password,
            settings.pg_reader_password,
            settings.deepseek_api_key,
        ):
            raw = secret.get_secret_value()
            if raw:
                value = value.replace(raw, "***")
    except Exception:
        pass
    return value


def run(name: str, command: list[str], *, cwd: Path = ROOT, env=None) -> Result:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        return Result(name, "NOT_RUN", f"command not found: {exc.filename}")
    output = redact((completed.stdout + "\n" + completed.stderr).strip())
    tail = "\n".join(output.splitlines()[-12:])
    if completed.returncode == 0:
        return Result(name, "PASS", tail)
    if name == "Real model smoke" and completed.returncode == 2:
        return Result(name, "NOT_RUN", tail)
    return Result(name, "FAIL", f"exit code {completed.returncode}\n{tail}")


def postgres_available() -> bool:
    """A read-only connectivity probe; no migration or seed side effects."""
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        import psycopg

        from app.config import get_settings
    except ImportError:
        return False
    try:
        with psycopg.connect(get_settings().dsn("reader"), connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except (OSError, psycopg.Error):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run static, offline, PostgreSQL, and frontend checks"
    )
    parser.add_argument(
        "--real-model", action="store_true", help="call the configured DeepSeek model"
    )
    parser.add_argument("--skip-postgres", action="store_true", help="intentionally skip DB checks")
    parser.add_argument("--npm", help="path to npm executable when npm is not on PATH")
    args = parser.parse_args()

    results = [
        run("Ruff lint", [PYTHON, "-m", "ruff", "check", "backend", "scripts", "data", "tests"]),
        run(
            "Ruff format",
            [PYTHON, "-m", "ruff", "format", "--check", "backend", "scripts", "data", "tests"],
        ),
        run(
            "Offline pytest",
            [PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "not integration"],
        ),
        run("Offline LangGraph demo", [PYTHON, "scripts/learning_demo.py"]),
        run("Offline model protocol stub", [PYTHON, "scripts/smoke_real_model.py", "--stub"]),
    ]

    if args.skip_postgres or not postgres_available():
        reason = (
            "explicit --skip-postgres"
            if args.skip_postgres
            else "reader cannot connect; check scripts/pg.ps1 status/start, .env, and setup_db.py"
        )
        results.extend(
            [
                Result("PostgreSQL integration pytest", "NOT_RUN", reason),
                Result("PostgreSQL reference check", "NOT_RUN", reason),
            ]
        )
    else:
        results.extend(
            [
                run(
                    "PostgreSQL integration pytest",
                    [PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "integration"],
                ),
                run("PostgreSQL reference check", [PYTHON, "scripts/check_reference.py"]),
            ]
        )

    npm = args.npm or shutil.which("npm")
    if npm is None:
        results.append(Result("Frontend build", "NOT_RUN", "npm is not on PATH; use --npm PATH"))
    else:
        results.append(run("Frontend build", [npm, "run", "build"], cwd=ROOT / "frontend"))

    if args.real_model:
        import os

        env = os.environ.copy()
        env["SHOPLENS_MODEL_PROVIDER"] = "deepseek"
        results.append(run("Real model smoke", [PYTHON, "scripts/smoke_real_model.py"], env=env))
    else:
        results.append(Result("Real model smoke", "NOT_RUN", "opt in with --real-model"))

    for result in results:
        print(f"[{result.status}] {result.name}")
        if result.detail and (result.status != "PASS" or result.name.endswith("pytest")):
            print("  " + result.detail.replace("\n", "\n  "))

    failures = sum(result.status == "FAIL" for result in results)
    pending = sum(result.status == "NOT_RUN" for result in results)
    passed = sum(result.status == "PASS" for result in results)
    print(f"Summary: PASS={passed} FAIL={failures} NOT_RUN={pending}")
    if failures:
        return 1
    blocking_pending = any(
        result.status == "NOT_RUN"
        and result.name != "Real model smoke"
        and not (args.skip_postgres and result.name.startswith("PostgreSQL "))
        for result in results
    )
    if blocking_pending:
        return 2
    if args.real_model and results[-1].status == "NOT_RUN":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
