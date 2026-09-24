"""Alembic environment; credentials always come from ShopLens settings."""

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings


def run_migrations_online() -> None:
    dsn = get_settings().dsn("admin").replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(dsn, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
