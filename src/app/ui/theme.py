"""QSS themes (dark + light) for the app.

Differences from preview.html:
- Qt doesn't have CSS grid. We use QWidget layouts in code.
- We don't use the @import style — pure QSS string.
- Colors, fonts, paddings are direct ports of the preview.
"""

# Dark theme — preview.html palette
DARK_QSS = """
* {
    font-family: "Noto Sans KR", "Segoe UI", "Liberation Sans", Arial, sans-serif;
    font-size: 13px;
    color: #d7dde8;
}

QMainWindow, QWidget#central {
    background: #0a0d12;
}

QWidget {
    background: #11161e;
    color: #d7dde8;
}

/* ---------- Title bar / brand ---------- */
QFrame#titleBar {
    background: #10151d;
    border-bottom: 1px solid #303746;
}
QLabel#appTitle {
    color: #c8d1e0;
    font-size: 12px;
    font-weight: 650;
}
QLabel#appVersion {
    color: #697386;
    font-family: "Consolas", "Liberation Mono", monospace;
    font-size: 11px;
}
QLabel#brand {
    font-weight: 800;
    color: #e9eef8;
    letter-spacing: -0.02em;
    font-size: 14px;
}
QLabel#statusLine {
    color: #8f98a8;
    font-family: "Consolas", "Liberation Mono", monospace;
    font-size: 11px;
}

/* ---------- Toolbar ---------- */
QFrame#toolbar {
    background: #151a22;
    border-bottom: 1px solid #303746;
}

/* ---------- Left nav ---------- */
QFrame#leftNav {
    background: #11161e;
    border-right: 1px solid #303746;
}
QPushButton#channelManagerBtn {
    background: #1c2532;
    border: 1px solid #3a4352;
    color: #dbe4f2;
    padding: 6px 10px;
    text-align: left;
    font-size: 12px;
}
QPushButton#channelManagerBtn:hover { background: #293244; }

QLabel#navSection {
    color: #687386;
    font-size: 11px;
    font-family: "Consolas", "Liberation Mono", monospace;
    padding: 8px 10px 4px 10px;
}
QPushButton.navItem {
    background: transparent;
    border: 1px solid transparent;
    color: #aeb8c8;
    text-align: left;
    padding: 6px 10px;
    font-size: 12px;
    border-radius: 0;
}
QPushButton.navItem:hover {
    background: #1b222e;
    border-color: #303746;
}
QPushButton.navItem:checked {
    background: #1f2937;
    color: #e5edf8;
    border-color: #3d4657;
}

/* ---------- Main pane ---------- */
QFrame#mainPane {
    background: #0f141b;
}
QFrame#paneHead {
    background: #151a22;
    border-bottom: 1px solid #303746;
}
QLabel#paneTitle {
    font-weight: 760;
    color: #e5edf8;
    font-size: 14px;
}
QLabel#paneSub {
    color: #697386;
    font-size: 11px;
    font-family: "Consolas", "Liberation Mono", monospace;
}
QLabel#paneKbd {
    color: #697386;
    font-size: 11px;
    font-family: "Consolas", "Liberation Mono", monospace;
}

/* ---------- Status bar ---------- */
QStatusBar {
    background: #0f141b;
    color: #8f98a8;
    border-top: 1px solid #303746;
    font-family: "Consolas", "Liberation Mono", monospace;
    font-size: 11px;
}

/* ---------- Tables (live feed) ---------- */
QTableView {
    background: #0f141b;
    alternate-background-color: #121923;
    gridline-color: #303746;
    border: 1px solid #303746;
    selection-background-color: #1f4f82;
    selection-color: #ffffff;
}
QTableView::item {
    padding: 4px 6px;
    color: #b7c0cf;
}
QHeaderView::section {
    background: #222936;
    color: #d4dce8;
    padding: 5px 7px;
    border: 0;
    border-right: 1px solid #303746;
    border-bottom: 1px solid #303746;
    font-weight: 760;
    font-size: 12px;
}
QTableCornerButton::section {
    background: #222936;
    border: 0;
}

/* ---------- Tag chips inside a cell (rendered as text with color) ---------- */
QLabel.tagChip {
    background: #1d2a3b;
    border: 1px solid #334762;
    color: #c7dcf8;
    font-family: "Consolas", "Liberation Mono", monospace;
    font-size: 11px;
    padding: 1px 5px;
}

/* ---------- Score colors ---------- */
QLabel.scoreHigh { color: #ff8494; font-family: "Consolas", "Liberation Mono", monospace; font-weight: 800; }
QLabel.scoreMid  { color: #eac45c; font-family: "Consolas", "Liberation Mono", monospace; font-weight: 800; }
QLabel.scoreLow  { color: #8bbef8; font-family: "Consolas", "Liberation Mono", monospace; font-weight: 800; }

/* ---------- Inputs / buttons ---------- */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {
    background: #0f141b;
    color: #d7dde8;
    border: 1px solid #353d4c;
    padding: 4px 8px;
    selection-background-color: #1f4f82;
    selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: #4ea1ff;
}

QPushButton {
    background: #202735;
    border: 1px solid #3b4658;
    color: #dbe4f2;
    padding: 5px 10px;
    font-size: 12px;
}
QPushButton:hover { background: #293244; }
QPushButton:pressed { background: #1a202b; }
QPushButton:disabled { color: #555e6f; background: #181d26; border-color: #2a313c; }
QPushButton.primary {
    background: #1f4f82;
    border-color: #4c78a8;
    color: #ffffff;
}
QPushButton.primary:hover { background: #2662a0; }

QPushButton.miniBtn {
    background: #1c2532;
    color: #cbd5e1;
    border: 1px solid #3a4352;
    padding: 2px 6px;
    font-size: 11px;
}
QPushButton.miniBtn:hover { background: #293244; }

/* ---------- Modal dialog ---------- */
QDialog {
    background: #11161e;
    border: 1px solid #4b5565;
}
QFrame.modalHead {
    background: #1a202b;
    border-bottom: 1px solid #303746;
}
QFrame.modalFoot {
    background: #151b24;
    border-top: 1px solid #303746;
}
QWidget.modalBody { background: #11161e; }

QListWidget {
    background: #10161f;
    border: 1px solid #303746;
}
QListWidget::item {
    padding: 5px 7px;
    border-bottom: 1px solid #202632;
}
QListWidget::item:selected { background: #1f4f82; color: #ffffff; }

QCheckBox { color: #d7dde8; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid #3b4658;
    background: #0f141b;
}
QCheckBox::indicator:checked { background: #1f4f82; border-color: #4c78a8; }

QScrollBar:vertical {
    background: #0a0d12;
    width: 12px;
    border: 0;
}
QScrollBar::handle:vertical {
    background: #2a313c;
    min-height: 30px;
    border: 1px solid #3a313c;
}
QScrollBar::handle:vertical:hover { background: #3a4352; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: #0a0d12;
    height: 12px;
    border: 0;
}
QScrollBar::handle:horizontal {
    background: #2a313c;
    min-width: 30px;
    border: 1px solid #3a313c;
}
QScrollBar::handle:horizontal:hover { background: #3a4352; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ---------- Placeholder tabs ---------- */
QFrame.placeholder {
    background: #0f141b;
}
QLabel.placeholderLabel {
    color: #8f98a8;
    font-size: 18px;
    font-weight: 760;
}
QLabel.placeholderSubLabel {
    color: #697386;
    font-size: 12px;
    font-family: "Consolas", "Liberation Mono", monospace;
}
"""


# Light theme — bright, low-contrast, daylight-friendly
LIGHT_QSS = """
* {
    font-family: "Noto Sans KR", "Segoe UI", "Liberation Sans", Arial, sans-serif;
    font-size: 13px;
    color: #1a1f2c;
}

QMainWindow, QWidget#central { background: #f4f5f8; }
QWidget { background: #ffffff; color: #1a1f2c; }

QFrame#titleBar { background: #e8ebf1; border-bottom: 1px solid #c8cdd6; }
QLabel#appTitle { color: #1a1f2c; font-size: 12px; font-weight: 650; }
QLabel#appVersion { color: #6b7280; font-family: "Consolas", monospace; font-size: 11px; }
QLabel#brand { font-weight: 800; color: #111827; font-size: 14px; }
QLabel#statusLine { color: #4b5563; font-family: "Consolas", monospace; font-size: 11px; }

QFrame#toolbar { background: #f0f2f7; border-bottom: 1px solid #c8cdd6; }

QFrame#leftNav { background: #f4f5f8; border-right: 1px solid #c8cdd6; }
QPushButton#channelManagerBtn {
    background: #ffffff; border: 1px solid #c8cdd6; color: #1a1f2c;
    padding: 6px 10px; text-align: left; font-size: 12px;
}
QPushButton#channelManagerBtn:hover { background: #e0e7ef; }

QLabel#navSection {
    color: #6b7280; font-size: 11px; font-family: "Consolas", monospace;
    padding: 8px 10px 4px 10px;
}
QPushButton.navItem {
    background: transparent; border: 1px solid transparent; color: #4b5563;
    text-align: left; padding: 6px 10px; font-size: 12px;
}
QPushButton.navItem:hover { background: #e5e9f0; border-color: #c8cdd6; }
QPushButton.navItem:checked {
    background: #dbeafe; color: #1e3a8a; border-color: #93c5fd;
}

QFrame#mainPane { background: #ffffff; }
QFrame#paneHead { background: #f0f2f7; border-bottom: 1px solid #c8cdd6; }
QLabel#paneTitle { font-weight: 760; color: #111827; font-size: 14px; }
QLabel#paneSub { color: #6b7280; font-size: 11px; font-family: "Consolas", monospace; }
QLabel#paneKbd { color: #6b7280; font-size: 11px; font-family: "Consolas", monospace; }

QStatusBar {
    background: #f0f2f7; color: #4b5563; border-top: 1px solid #c8cdd6;
    font-family: "Consolas", monospace; font-size: 11px;
}

QTableView {
    background: #ffffff; alternate-background-color: #f9fafb;
    gridline-color: #e5e7eb; border: 1px solid #c8cdd6;
    selection-background-color: #2563eb; selection-color: #ffffff;
}
QTableView::item { padding: 4px 6px; color: #1f2937; }
QHeaderView::section {
    background: #e5e7eb; color: #111827; padding: 5px 7px;
    border: 0; border-right: 1px solid #c8cdd6; border-bottom: 1px solid #c8cdd6;
    font-weight: 760; font-size: 12px;
}
QTableCornerButton::section { background: #e5e7eb; border: 0; }

QLabel.scoreHigh { color: #b91c1c; font-family: "Consolas", monospace; font-weight: 800; }
QLabel.scoreMid  { color: #b45309; font-family: "Consolas", monospace; font-weight: 800; }
QLabel.scoreLow  { color: #1d4ed8; font-family: "Consolas", monospace; font-weight: 800; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QDateEdit {
    background: #ffffff; color: #1a1f2c; border: 1px solid #c8cdd6;
    padding: 4px 8px; selection-background-color: #2563eb; selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QDateEdit:focus {
    border-color: #2563eb;
}

QPushButton {
    background: #f3f4f6; border: 1px solid #c8cdd6; color: #1a1f2c;
    padding: 5px 10px; font-size: 12px;
}
QPushButton:hover { background: #e5e7eb; }
QPushButton:pressed { background: #d1d5db; }
QPushButton:disabled { color: #9ca3af; background: #f3f4f6; border-color: #e5e7eb; }
QPushButton.primary { background: #2563eb; border-color: #1d4ed8; color: #ffffff; }
QPushButton.primary:hover { background: #1d4ed8; }
QPushButton.miniBtn {
    background: #ffffff; color: #1f2937; border: 1px solid #c8cdd6;
    padding: 2px 6px; font-size: 11px;
}
QPushButton.miniBtn:hover { background: #e5e7eb; }

QDialog { background: #ffffff; border: 1px solid #c8cdd6; }
QFrame.modalHead { background: #f0f2f7; border-bottom: 1px solid #c8cdd6; }
QFrame.modalFoot { background: #f9fafb; border-top: 1px solid #c8cdd6; }
QWidget.modalBody { background: #ffffff; }

QListWidget {
    background: #ffffff; border: 1px solid #c8cdd6;
}
QListWidget::item {
    padding: 5px 7px; border-bottom: 1px solid #f0f2f7; color: #1f2937;
}
QListWidget::item:selected { background: #dbeafe; color: #1e3a8a; }

QCheckBox { color: #1a1f2c; }
QCheckBox::indicator {
    width: 14px; height: 14px; border: 1px solid #c8cdd6; background: #ffffff;
}
QCheckBox::indicator:checked { background: #2563eb; border-color: #1d4ed8; }

QScrollBar:vertical { background: #f0f2f7; width: 12px; border: 0; }
QScrollBar::handle:vertical { background: #c8cdd6; min-height: 30px; border: 1px solid #9ca3af; }
QScrollBar::handle:vertical:hover { background: #9ca3af; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #f0f2f7; height: 12px; border: 0; }
QScrollBar::handle:horizontal { background: #c8cdd6; min-width: 30px; border: 1px solid #9ca3af; }
QScrollBar::handle:horizontal:hover { background: #9ca3af; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QFrame.placeholder { background: #ffffff; }
QLabel.placeholderLabel { color: #4b5563; font-size: 18px; font-weight: 760; }
QLabel.placeholderSubLabel { color: #6b7280; font-size: 12px; font-family: "Consolas", monospace; }
"""


def score_color(value: int) -> str:
    """Return the QSS class for a 0-100 score."""
    if value >= 80:
        return "scoreHigh"
    if value >= 50:
        return "scoreMid"
    return "scoreLow"


# Theme persistence
THEMES = {
    "dark": DARK_QSS,
    "light": LIGHT_QSS,
}

THEME_LABELS = {
    "dark": "🌙 다크",
    "light": "☀ 라이트",
}


def load_theme_pref() -> str:
    """Load saved theme preference, defaulting to dark."""
    from core.config import DATA_DIR
    p = DATA_DIR / "settings" / "theme.txt"
    if p.exists():
        try:
            t = p.read_text(encoding="utf-8").strip()
            if t in THEMES:
                return t
        except Exception:
            pass
    return "dark"


def save_theme_pref(name: str) -> None:
    from core.config import DATA_DIR
    p = DATA_DIR / "settings" / "theme.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(name, encoding="utf-8")
