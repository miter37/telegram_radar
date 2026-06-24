"""주제 클러스터 탭."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from core.analytics.cluster import list_clusters, recompute_topic_clusters
from core.db import connection


class ClustersTab(QFrame):
    def __init__(self, db_path, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self.setObjectName("clustersTab")

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
        title = QLabel("주제 클러스터")
        title.setObjectName("paneTitle")
        sub = QLabel("유사 피드 묶음 · topic=Jaccard≥0.5 · 중복 제거")
        sub.setObjectName("paneSub")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        h.addLayout(title_box, 1)
        btn_refresh = QPushButton("재클러스터링")
        btn_refresh.setProperty("class", "miniBtn")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self._on_recompute)
        h.addWidget(btn_refresh)
        layout.addWidget(head)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["주제", "피드수", "첫 등장", "최근", "총 중요도", "대표 태그"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        layout.addWidget(self._table, 1)

        self.refresh()

    def _conn(self):
        return connection.get_connection(self._db_path)

    def _on_recompute(self) -> None:
        conn = self._conn()
        try:
            n = recompute_topic_clusters(conn)
            win = self.window()
            if win is not None and hasattr(win, "_status_msg"):
                win._status_msg.setText(f"클러스터 갱신: {n}건 신규")
        except Exception as e:
            win = self.window()
            if win is not None and hasattr(win, "_status_msg"):
                win._status_msg.setText(f"클러스터링 실패: {e}")
        self.refresh()

    def refresh(self) -> None:
        conn = self._conn()
        try:
            rows = list_clusters(conn, limit=200)
        except Exception:
            rows = []
        self._table.setRowCount(0)
        for c in rows:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(c["topic"]))
            self._table.setItem(r, 1, QTableWidgetItem(str(c["feed_count"])))
            self._table.setItem(r, 2, QTableWidgetItem(c["first_seen"]))
            self._table.setItem(r, 3, QTableWidgetItem(c["last_seen"]))
            self._table.setItem(r, 4, QTableWidgetItem(f"{int(c['cluster_score'])}"))
            self._table.setItem(r, 5, QTableWidgetItem(", ".join(c["tags"][:6])))
        for r in range(self._table.rowCount()):
            self._table.setRowHeight(r, 26)
