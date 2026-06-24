"""Timeline modal: show all signals for a given tag/topic in chronological order.

Used by:
- Double-click on tag cell in live feed
- Double-click on topic in daily topics
- Click "관련 피드" in analysis tab
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.db import connection, repositories
from app.ui.theme import score_color
from app.ui.widgets.raw_feed_modal import RawFeedModal


class TagTimelineDialog(QDialog):
    """Show all signals for a tag/topic in chronological order (oldest first)."""

    def __init__(
        self,
        *,
        target: str,           # tag name or topic substring
        kind: str,             # 'tag' | 'topic'
        db_path,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._db_path = db_path
        self._target = target
        self._kind = kind

        title_text = f"태그 타임라인: {target}" if kind == "tag" else f"주제 타임라인: {target}"
        self.setWindowTitle(title_text)
        self.setModal(True)
        self.resize(880, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Head
        head = QFrame()
        head.setProperty("class", "modalHead")
        h = QHBoxLayout(head)
        h.setContentsMargins(12, 8, 8, 8)
        h.addWidget(QLabel(title_text), 1)
        self._summary = QLabel("불러오는 중…")
        self._summary.setStyleSheet("color: #8f98a8; font-size: 11px;")
        h.addWidget(self._summary)
        btn_close = QPushButton("닫기")
        btn_close.setProperty("class", "miniBtn")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.reject)
        h.addWidget(btn_close)
        layout.addWidget(head)

        # Body
        body = QFrame()
        body.setProperty("class", "modalBody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # filters bar
        filter_bar = QFrame()
        filter_bar.setStyleSheet("background: #0f141b; border-bottom: 1px solid #303746;")
        filter_bar.setFixedHeight(36)
        fb = QHBoxLayout(filter_bar)
        fb.setContentsMargins(10, 4, 10, 4)
        fb.setSpacing(8)
        self._imp_min = None
        self._int_min = None
        fb.addWidget(QLabel("중요도 ≥"))
        from PySide6.QtWidgets import QSpinBox
        self._imp_min = QSpinBox()
        self._imp_min.setRange(0, 100)
        self._imp_min.setValue(0)
        self._imp_min.setSpecialValueText("전체")
        self._imp_min.valueChanged.connect(self._reload)
        fb.addWidget(self._imp_min)
        fb.addWidget(QLabel("관심도 ≥"))
        self._int_min = QSpinBox()
        self._int_min.setRange(0, 100)
        self._int_min.setValue(0)
        self._int_min.setSpecialValueText("전체")
        self._int_min.valueChanged.connect(self._reload)
        fb.addWidget(self._int_min)
        fb.addStretch(1)
        from PySide6.QtWidgets import QPushButton as _PB
        btn_export = _PB("CSV 내보내기")
        btn_export.setProperty("class", "miniBtn")
        btn_export.setCursor(Qt.PointingHandCursor)
        btn_export.clicked.connect(self._export_csv)
        fb.addWidget(btn_export)
        bl.addWidget(filter_bar)

        # table
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["시간", "채널", "주제", "주요내용", "중요", "관심"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self._table.doubleClicked.connect(self._on_double)
        bl.addWidget(self._table, 1)

        layout.addWidget(body, 1)

        # Foot
        foot = QFrame()
        foot.setProperty("class", "modalFoot")
        f = QHBoxLayout(foot)
        f.setContentsMargins(12, 8, 12, 8)
        f.addStretch(1)
        kbd = QLabel("Enter=원문 · Esc=닫기")
        kbd.setStyleSheet(
            "color: #697386; font-family: 'Consolas','Liberation Mono',monospace; font-size: 11px;"
        )
        f.addWidget(kbd)
        layout.addWidget(foot)

        self._rows: list = []
        self._reload()

    def _conn(self):
        return connection.get_connection(self._db_path)

    def _reload(self) -> None:
        conn = self._conn()
        imp_min = self._imp_min.value() or None
        int_min = self._int_min.value() or None
        if self._kind == "tag":
            where = ["st.canonical_name = ?"]
            params: list = [self._target]
        else:
            where = ["s.topic = ?"]
            params = [self._target]
        if imp_min is not None:
            where.append("s.importance_score >= ?")
            params.append(imp_min)
        if int_min is not None:
            where.append("s.interest_score >= ?")
            params.append(int_min)
        where_sql = " AND ".join(where)
        rows = conn.execute(f"""
            SELECT s.*, GROUP_CONCAT(st.canonical_name, '|') AS tag_names
            FROM feed_signals s
            JOIN signal_tags st ON st.signal_id = s.id
            WHERE {where_sql}
            GROUP BY s.id
            ORDER BY s.id ASC
        """, params).fetchall()
        self._rows = rows
        self._summary.setText(f"{len(rows)}건 (시간순)")
        self._table.setRowCount(0)
        for r in rows:
            i = self._table.rowCount()
            self._table.insertRow(i)
            self._table.setItem(i, 0, QTableWidgetItem(r["date"]))
            self._table.setItem(i, 1, QTableWidgetItem(r["channel_name"]))
            self._table.setItem(i, 2, QTableWidgetItem(r["topic"]))
            self._table.setItem(i, 3, QTableWidgetItem(r["main_content"]))
            score_item = QTableWidgetItem(str(r["importance_score"]))
            score_item.setForeground(QColor({"scoreHigh": "#ff8494", "scoreMid": "#eac45c"}.get(
                score_color(r["importance_score"]), "#8bbef8")))
            self._table.setItem(i, 4, score_item)
            int_item = QTableWidgetItem(str(r["interest_score"]))
            int_item.setForeground(QColor({"scoreHigh": "#ff8494", "scoreMid": "#eac45c"}.get(
                score_color(r["interest_score"]), "#8bbef8")))
            self._table.setItem(i, 5, int_item)
            self._table.setRowHeight(i, 26)

    def _on_double(self, index) -> None:
        if index.row() < 0 or index.row() >= len(self._rows):
            return
        r = self._rows[index.row()]
        conn = self._conn()
        feed = repositories.get_feed(conn, r["feed_id"])
        if feed is None:
            return
        tags = [t for t in (r["tag_names"] or "").split("|") if t]
        modal = RawFeedModal(
            feed_id=feed.id,
            datetime=r["date"],
            channel_name=r["channel_name"],
            topic=r["topic"],
            main_content=r["main_content"],
            importance_score=r["importance_score"],
            interest_score=r["interest_score"],
            tags=tags,
            message_text=feed.message_text,
            message_url=feed.message_url,
            parent=self.window() if self.window() else self,
        )
        modal.exec()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            idx = self._table.currentIndex()
            if idx.isValid():
                self._on_double(idx)
                return
        super().keyPressEvent(event)

    def _export_csv(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        import csv
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV 내보내기",
            f"{self._target.replace(' ', '_')}_timeline.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "channel", "topic", "main_content",
                            "importance", "interest", "should_alert", "tags"])
                for r in self._rows:
                    w.writerow([
                        r["date"], r["channel_name"], r["topic"],
                        r["main_content"], r["importance_score"],
                        r["interest_score"], r["should_alert"],
                        r["tag_names"] or "",
                    ])
            self._summary.setText(f"내보내기 완료: {path}")
        except Exception as e:
            self._summary.setText(f"내보내기 실패: {e}")
