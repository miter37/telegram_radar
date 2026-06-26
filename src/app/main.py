"""Entry point: bootstrap QApplication and show the main window."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui.theme import load_theme_pref, THEMES
from core.config import DATA_DIR
from core.db import connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = DATA_DIR / "market_radar.sqlite"
    connection.init_db(db_path)

    app = QApplication(sys.argv)
    app.setApplicationName("Market Radar Desktop")
    theme_name = load_theme_pref()
    app.setStyleSheet(THEMES[theme_name])

    win = MainWindow(db_path, theme_name=theme_name)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
