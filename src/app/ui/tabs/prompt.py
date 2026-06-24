"""LLM 프롬프트 관리 탭: 버전/스키마/실패율/실패 샘플."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.db import connection
from core.llm.prompts import CURRENT_VERSION, list_prompt_files


class PromptTab(QFrame):
    def __init__(self, db_path, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self.setObjectName("promptTab")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QFrame()
        head.setObjectName("paneHead")
        head.setFixedHeight(40)
        h = QHBoxLayout(head)
        h.setContentsMargins(12, 0, 12, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("LLM 프롬프트")
        title.setObjectName("paneTitle")
        sub = QLabel(f"현재 버전: {CURRENT_VERSION} · 실패율 / 스키마 / 변경 이력")
        sub.setObjectName("paneSub")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        h.addLayout(title_box, 1)
        layout.addWidget(head)

        body = QHBoxLayout()
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(10)

        # === Left: stats + versions ===
        left = QFrame()
        left.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 6, 8, 8)
        lv.setSpacing(6)
        t = QLabel("프롬프트 버전")
        t.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        lv.addWidget(t)
        self._version_table = QTableWidget(0, 3)
        self._version_table.setHorizontalHeaderLabels(["버전", "건수", "성공률"])
        self._version_table.verticalHeader().setVisible(False)
        self._version_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._version_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._version_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._version_table.setEditTriggers(QTableWidget.NoEditTriggers)
        lv.addWidget(self._version_table, 1)

        t2 = QLabel("실패 샘플 (최근 10건)")
        t2.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        lv.addWidget(t2)
        self._fail_table = QTableWidget(0, 2)
        self._fail_table.setHorizontalHeaderLabels(["feed_id", "에러"])
        self._fail_table.verticalHeader().setVisible(False)
        self._fail_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._fail_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._fail_table.setEditTriggers(QTableWidget.NoEditTriggers)
        lv.addWidget(self._fail_table, 1)

        body.addWidget(left, 1)

        # === Right: prompt viewer ===
        right = QFrame()
        right.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 6, 8, 8)
        rv.setSpacing(6)
        t3 = QLabel("프롬프트 본문")
        t3.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        rv.addWidget(t3)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setStyleSheet(
            "QPlainTextEdit { background: #0e131a; color: #d7dde8; "
            "font-family: 'Consolas','Liberation Mono',monospace; font-size: 12px; "
            "border: 1px solid #303746; }"
        )
        rv.addWidget(self._text, 1)
        body.addWidget(right, 1)
        layout.addLayout(body, 1)

        self._load_versions()
        self._load_failures()
        self._load_prompt()

    def _conn(self):
        return connection.get_connection(self._db_path)

    def _load_versions(self) -> None:
        conn = self._conn()
        rows = conn.execute("""
            SELECT prompt_version,
                   COUNT(*) AS total,
                   SUM(parsed_ok) AS ok
            FROM llm_extractions
            GROUP BY prompt_version
            ORDER BY prompt_version DESC
        """).fetchall()
        self._version_table.setRowCount(0)
        for r in rows:
            row = self._version_table.rowCount()
            self._version_table.insertRow(row)
            ok = r["ok"] or 0
            total = r["total"]
            pct = (ok / total * 100.0) if total else 0.0
            self._version_table.setItem(row, 0, QTableWidgetItem(r["prompt_version"]))
            self._version_table.setItem(row, 1, QTableWidgetItem(str(total)))
            pct_item = QTableWidgetItem(f"{pct:.1f}%")
            color = "#36c275" if pct >= 90 else "#e4b341" if pct >= 70 else "#ef596f"
            from PySide6.QtGui import QColor
            pct_item.setForeground(QColor(color))
            self._version_table.setItem(row, 2, pct_item)

    def _load_failures(self) -> None:
        conn = self._conn()
        rows = conn.execute("""
            SELECT feed_id, error_message, prompt_version, created_at
            FROM llm_extractions
            WHERE parsed_ok = 0
            ORDER BY id DESC
            LIMIT 10
        """).fetchall()
        self._fail_table.setRowCount(0)
        for r in rows:
            row = self._fail_table.rowCount()
            self._fail_table.insertRow(row)
            self._fail_table.setItem(row, 0, QTableWidgetItem(f"#{r['feed_id']}"))
            err = r["error_message"] or "(no message)"
            if len(err) > 200:
                err = err[:200] + "…"
            self._fail_table.setItem(row, 1, QTableWidgetItem(err))

    def _load_prompt(self) -> None:
        from core.llm.prompts import get_prompt
        try:
            text = get_prompt(CURRENT_VERSION)
        except FileNotFoundError:
            text = "(프롬프트 파일 없음)"
        self._text.setPlainText(text)
