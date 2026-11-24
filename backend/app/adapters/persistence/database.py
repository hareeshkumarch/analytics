import os
from pathlib import Path
import tempfile
from typing import Any
from sqlalchemy import create_engine, inspect, pool, text
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.persistence.orm_models import Base, ConversationRow, WorkspaceRow

LOCK_TIMEOUT_MS = 5_000
STATEMENT_TIMEOUT_MS = 30_000


def create_db_engine(database_url: str | None = None) -> Any:
    url = database_url
    if url is None:
        if os.environ.get("TESTING") == "1":
            url = f"sqlite:///{Path(tempfile.mkdtemp(prefix='ledgerline-test-')) / 'test.db'}"
        else:
            url = os.environ.get("DATABASE_URL")

    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at the PostgreSQL instance, or set TESTING=1 to use a throwaway "
            "SQLite database."
        )
    if url.startswith("sqlite"):
        shared_connection = {"poolclass": pool.StaticPool} if ":memory:" in url or url == "sqlite://" else {}
        engine = create_engine(url, connect_args={"check_same_thread": False}, **shared_connection)
        Base.metadata.create_all(engine)
        if "runs" in inspect(engine).get_table_names():
            columns = {column["name"] for column in inspect(engine).get_columns("runs")}
            if "orchestration_json" not in columns:
                with engine.begin() as connection:
                    connection.execute(
                        text("ALTER TABLE runs ADD COLUMN orchestration_json JSON NOT NULL DEFAULT '{}' ")
                    )
        return engine

    return create_engine(
        url,
        connect_args={"options": f"-c lock_timeout={LOCK_TIMEOUT_MS} -c statement_timeout={STATEMENT_TIMEOUT_MS}"},
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


def create_session_factory(engine: Any = None) -> sessionmaker[Session]:
    eng = engine or create_db_engine()
    return sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)


def ensure_workspace(db: Session, workspace_id: str, name: str = "Default Workspace") -> WorkspaceRow:
    ws = db.get(WorkspaceRow, workspace_id)
    if not ws:
        ws = WorkspaceRow(workspace_id=workspace_id, name=name)
        db.add(ws)
        db.flush()
    return ws


def ensure_conversation(
    db: Session, workspace_id: str, conversation_id: str, title: str = "Default Conversation"
) -> ConversationRow:
    ensure_workspace(db, workspace_id)
    conv = db.get(ConversationRow, conversation_id)
    if not conv:
        conv = ConversationRow(conversation_id=conversation_id, workspace_id=workspace_id, title=title)
        db.add(conv)
        db.flush()
    return conv
