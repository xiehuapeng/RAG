from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def _column_names(engine: Engine, table_name: str) -> set[str]:
    inspector = inspect(engine)
    return {column["name"] for column in inspector.get_columns(table_name)}


def run_startup_migrations(engine: Engine) -> None:
    session_columns = _column_names(engine, "qa_session")
    chunk_columns = _column_names(engine, "kb_chunk")
    feedback_columns = _column_names(engine, "qa_feedback")

    with engine.begin() as conn:
        if "summary_json" not in session_columns:
            conn.execute(text("ALTER TABLE qa_session ADD COLUMN summary_json TEXT"))

        if "chroma_id" not in chunk_columns:
            conn.execute(text("ALTER TABLE kb_chunk ADD COLUMN chroma_id TEXT"))
            conn.execute(text("UPDATE kb_chunk SET chroma_id = 'legacy-' || id WHERE chroma_id IS NULL"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_kb_chunk_chroma_id ON kb_chunk (chroma_id)"))

        if "comment" not in feedback_columns:
            conn.execute(text("ALTER TABLE qa_feedback ADD COLUMN comment TEXT"))
