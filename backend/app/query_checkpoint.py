"""应用凭据下的 PostgreSQL checkpoint；分析查询仍使用独立 reader。"""

from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

from app.config import Settings


@contextmanager
def postgres_checkpointer(settings: Settings, *, setup: bool = False):
    with psycopg.connect(
        settings.dsn("app"),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        connect_timeout=3,
        options="-c search_path=app_state -c statement_timeout=5000",
    ) as conn:
        saver = PostgresSaver(conn)
        if setup:
            saver.setup()
        yield saver
