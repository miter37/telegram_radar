"""SQLite connection management with WAL mode and per-thread connections."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .schema import SCHEMA_SQL

_local = threading.local()


def _make_connection(db_path: Path) -> sqlite3.Connection:
    """Create a connection with WAL + foreign keys + Row factory.

    check_same_thread=False because get_connection() uses thread-local
    storage: each thread (UI, ingest, llm, report) gets its own connection.
    The thread-local pattern guarantees no concurrent access from two
    threads on the same connection.
    """
    conn = sqlite3.connect(
        str(db_path),
        detect_types=sqlite3.PARSE_DECLTYPES,
        timeout=10.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get a per-thread connection. Each thread (main + workers) gets its own."""
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "db_path", None) != str(db_path):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        conn = _make_connection(db_path)
        _local.conn = conn
        _local.db_path = str(db_path)
    return conn


def init_db(db_path: Path) -> None:
    """Initialize database with schema (idempotent)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _make_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
