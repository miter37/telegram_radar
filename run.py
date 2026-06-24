#!/usr/bin/env python3
"""Run with: python run.py"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ is on the import path
HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
