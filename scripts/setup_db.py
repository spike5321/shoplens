"""Create isolated database roles, run Alembic, and grant minimum access."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values, set_key
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.config import get_settings  # noqa: E402


def ensure_roles() -> None:
    settings = get_settings()
    env_path = ROOT / ".env"
    current = dotenv_values(env_path) if env_path.exists() else {}
    credentials = {}
    for role, key, configured in (
        (settings.pg_app_user, "SHOPLENS_PG_APP_PASSWORD", settings.pg_app_password),
        (settings.pg_reader_user, "SHOPLENS_PG_READER_PASSWORD", settings.pg_reader_password),
    ):
        password = configured.get_secret_value()
        if not password and not env_path.exists():
            raise RuntimeError("Provide app/reader passwords through environment or a local .env")
        credentials[role] = (key, password or secrets.token_urlsafe(24))
    with psycopg.connect(settings.dsn("admin"), autocommit=True) as conn:
        for role, (_, password) in credentials.items():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            if exists:
                conn.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
            else:
                conn.execute(
                    sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
    for _, (key, password) in credentials.items():
        if env_path.exists() and current.get(key) != password:
            set_key(str(env_path), key, password, quote_mode="never")


def grant_access() -> None:
    settings = get_settings()
    with psycopg.connect(settings.dsn("admin")) as conn:
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        for role in (settings.pg_app_user, settings.pg_reader_user):
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(settings.pg_db), sql.Identifier(role)
                )
            )
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA analytics TO {}").format(
                sql.Identifier(settings.pg_reader_user)
            )
        )
        conn.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO {}").format(
                sql.Identifier(settings.pg_reader_user)
            )
        )
        conn.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA app_state TO {}").format(
                sql.Identifier(settings.pg_app_user)
            )
        )
        conn.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON app_state.sessions, app_state.runs, app_state.events TO {}"
            ).format(sql.Identifier(settings.pg_app_user))
        )


def main() -> None:
    ensure_roles()
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    grant_access()
    print("Database roles, migration, and grants ready. Credentials are not logged.")


if __name__ == "__main__":
    main()
