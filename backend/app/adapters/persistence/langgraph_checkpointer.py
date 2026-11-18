"""PostgreSQL-backed LangGraph checkpoints for durable orchestration runs."""

from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Any, Iterator

from sqlalchemy.engine import make_url


@contextmanager
def open_langgraph_checkpointer(database_url: str | None = None) -> Iterator[Any | None]:
    """Open a Postgres saver when the application is using PostgreSQL.

    SQLite remains useful for local and unit-test runs, but its in-memory database is not a
    durable checkpoint backend. The saver owns its connection pool for the duration of the run.
    """
    configured_url = database_url or os.environ.get("DATABASE_URL")
    if not configured_url:
        yield None
        return

    url = make_url(configured_url)
    if url.get_backend_name() != "postgresql":
        yield None
        return

    # LangGraph's Postgres serializer can process application state made from JSON-compatible
    # values and the built-in LangChain message types while rejecting unknown msgpack classes.
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    from langgraph.checkpoint.postgres import PostgresSaver

    conn_string = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with PostgresSaver.from_conn_string(conn_string) as checkpointer:
        checkpointer.setup()
        yield checkpointer
