"""Schema SQL as a string constant."""

from pathlib import Path

_SCHEMA_FILE = Path(__file__).parent / "schema.sql"
SCHEMA_SQL = _SCHEMA_FILE.read_text(encoding="utf-8")
