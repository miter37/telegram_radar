"""Live DB feed tab: virtualized table of recent feed_signals with filters/sort."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.db import connection, repositories
from core.db.repositories import FeedSignal
from app.ui.theme import score_color
from app.ui.widgets.raw_feed_modal import RawFeedModal
from app.ui.widgets.tag_timeline import TagTimelineDialog

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


class LiveFeedModel(QAbstractTableModel):
    """Backs the live feed table with filter/sort parameters."""

    HEADERS = ["시간", "채널", "주제", "주요내용", "태그", "중요", "관심", ""]

    def __init__(self, db_path):
        super().__init__()
        self._db_path = db_path
        self._rows: list[FeedSignal] = []
        self._oldest_id: Optional[int] = None
        self._has_more_older = True
        # filter / sort state
        self._sort = "id_desc"
        self._importance_min: Optional[int] = None
        self._interest_min: Optional[int] = None
        self._channel: Optional[str] = None
        self._topic_substr: Optional[str] = None
        self._should_alert: Optional[bool] = None
        self._text_search: Optional[str] = None
        self._fts_signal_ids: Optional[set[int]] = None
        self._load_initial()

    def _conn(self):
        return connection.get_connection(self._db_path)

    def set_filters(
        self,
        *,
        sort: Optional[str] = None,
        importance_min: Optional[int] = None,
        interest_min: Optional[int] = None,
        channel: Optional[str] = None,
        topic_substr: Optional[str] = None,
        should_alert: Optional[bool] = None,
        text_search: Optional[str] = None,
        fts_signal_ids: Optional[set[int]] = None,
    ) -> None:
        if sort is not None:
            self._sort = sort
        self._importance_min = importance_min
        self._interest_min = interest_min
        self._channel = channel
        self._topic_substr = topic_substr
        self._should_alert = should_alert
        self._text_search = text_search
        self._fts_signal_ids = fts_signal_ids
        self._load_initial()

    def _load_initial(self) -> None:
        conn = self._conn()
        # FTS5 mode: if we have fts_signal_ids, limit by them
        if self._fts_signal_ids is not None:
            if not self._fts_signal_ids:
                self.beginResetModel()
                self._rows = []
                self._oldest_id = None
                self._has_more_older = False
                self.endResetModel()
                return
            ids = sorted(self._fts_signal_ids, reverse=True)
            placeholders = ",".join("?" * len(ids))
            extra_where = [f"s.id IN ({placeholders})", f"({','.join('?' * len(ids))})".replace("?,?", "?,")]
            # simpler: use IN clause directly
            sql = f"""
                SELECT s.*, GROUP_CONCAT(st.canonical_name, '|') AS tag_names
                FROM feed_signals s
                LEFT JOIN signal_tags st ON st.signal_id = s.id
                WHERE s.id IN ({placeholders})
                GROUP BY s.id
                ORDER BY s.id DESC
                LIMIT ?
            """
            params = ids + [BATCH_SIZE]
            rows = conn.execute(sql, params).fetchall()
            out = []
            for r in rows:
                out.append(FeedSignal(
                    id=r["id"], feed_id=r["feed_id"], date=r["date"],
                    channel_name=r["channel_name"], topic=r["topic"],
                    main_content=r["main_content"],
                    importance_score=r["importance_score"],
                    interest_score=r["interest_score"],
                    should_alert=bool(r["should_alert"]),
                    created_at=r["created_at"],
                    tags=[t for t in (r["tag_names"] or "").split("|") if t],
                ))
            self.beginResetModel()
            self._rows = out
            self._oldest_id = out[-1].id if out else None
            self._has_more_older = False  # FTS results don't paginate
            self.endResetModel()
            return
        rows = repositories.list_signals(
            conn, limit=BATCH_SIZE, offset=0, sort=self._sort,
            importance_min=self._importance_min,
            interest_min=self._interest_min,
            channel=self._channel,
            topic_substr=self._topic_substr,
            should_alert=self._should_alert,
            text_search=self._text_search,
        )
        self.beginResetModel()
        self._rows = rows
        if rows:
            if self._sort in ("id_desc", "importance_desc", "interest_desc"):
                self._oldest_id = rows[-1].id
            else:
                self._oldest_id = rows[0].id
        else:
            self._oldest_id = None
        self._has_more_older = len(rows) >= BATCH_SIZE
        self.endResetModel()

    def refresh(self) -> None:
        self._load_initial()

    def load_older(self) -> None:
        if not self._has_more_older or self._oldest_id is None:
            return
        conn = self._conn()
        if self._sort in ("id_desc", "importance_desc", "interest_desc"):
            sql = """
                SELECT s.*, GROUP_CONCAT(st.canonical_name, '|') AS tag_names
                FROM feed_signals s
                LEFT JOIN signal_tags st ON st.signal_id = s.id
                WHERE s.id < ?
                GROUP BY s.id
                ORDER BY {order}
                LIMIT ?
            """.format(order=repositories.list_signals.__defaults__ and "s.id DESC" or "s.id DESC")
        else:
            sql = """
                SELECT s.*, GROUP_CONCAT(st.canonical_name, '|') AS tag_names
                FROM feed_signals s
                LEFT JOIN signal_tags st ON st.signal_id = s.id
                WHERE s.id > ?
                GROUP BY s.id
                ORDER BY s.id ASC
                LIMIT ?
            """
        raw = conn.execute(sql, (self._oldest_id, BATCH_SIZE)).fetchall()
        if not raw:
            self._has_more_older = False
            return
        new_rows = [
            FeedSignal(
                id=r["id"], feed_id=r["feed_id"], date=r["date"],
                channel_name=r["channel_name"], topic=r["topic"],
                main_content=r["main_content"],
                importance_score=r["importance_score"],
                interest_score=r["interest_score"],
                should_alert=bool(r["should_alert"]),
                created_at=r["created_at"],
                tags=[t for t in (r["tag_names"] or "").split("|") if t],
            )
            for r in raw
        ]
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(new_rows) - 1)
        self._rows.extend(new_rows)
        if self._sort in ("id_desc", "importance_desc", "interest_desc"):
            self._oldest_id = new_rows[-1].id
        else:
            self._oldest_id = new_rows[-1].id  # last loaded (largest id in asc)
        if len(new_rows) < BATCH_SIZE:
            self._has_more_older = False
        self.endInsertRows()

    def prepend(self, sig: FeedSignal) -> None:
        # Only auto-prepend if filters accept this signal
        if self._importance_min is not None and sig.importance_score < self._importance_min:
            return
        if self._interest_min is not None and sig.interest_score < self._interest_min:
            return
        if self._channel and sig.channel_name != self._channel:
            return
        if self._topic_substr and self._topic_substr.lower() not in (sig.topic or "").lower():
            return
        if self._should_alert is not None and sig.should_alert != self._should_alert:
            return
        if self._text_search:
            ts = self._text_search.lower()
            hay = " ".join([sig.topic, sig.main_content, sig.channel_name] + sig.tags).lower()
            if ts not in hay:
                return
        if self._fts_signal_ids is not None and sig.id not in self._fts_signal_ids:
            return
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._rows.insert(0, sig)
        self.endInsertRows()
        if self._oldest_id is None:
            self._oldest_id = sig.id

    # ---- QAbstractTableModel API ----

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            if col == 0:
                return row.date
            if col == 1:
                return row.channel_name
            if col == 2:
                return row.topic
            if col == 3:
                return row.main_content
            if col == 4:
                return " ".join(f"#{t}" for t in row.tags)
            if col == 5:
                return str(row.importance_score)
            if col == 6:
                return str(row.interest_score)
            if col == 7:
                return "보기"
        if role == Qt.ForegroundRole:
            if col == 5:
                return QColor({"scoreHigh": "#ff8494", "scoreMid": "#eac45c"}.get(
                    score_color(row.importance_score), "#8bbef8"))
            if col == 6:
                return QColor({"scoreHigh": "#ff8494", "scoreMid": "#eac45c"}.get(
                    score_color(row.interest_score), "#8bbef8"))
        if role == Qt.TextAlignmentRole:
            if col in (5, 6):
                return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def signal_at(self, row: int) -> Optional[FeedSignal]:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


class LiveFeedTab(QFrame):
    """The main tab showing feed_signals in a virtualized table."""

    raw_modal_requested = Signal(int)  # feed_id

    def __init__(self, db_path, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self._model = LiveFeedModel(db_path)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Pane head
        head = QFrame()
        head.setObjectName("paneHead")
        head.setFixedHeight(40)
        h = QHBoxLayout(head)
        h.setContentsMargins(12, 0, 12, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("실시간 DB 피드")
        title.setObjectName("paneTitle")
        sub = QLabel("feed_signals · F5 새로고침 · Enter=원문 · Ctrl+F 검색")
        sub.setObjectName("paneSub")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        h.addLayout(title_box, 1)
        kbd = QLabel("F5 refresh · Enter open · Ctrl+F search")
        kbd.setObjectName("paneKbd")
        h.addWidget(kbd)
        layout.addWidget(head)

        # Filter bar
        self._build_filter_bar(layout)

        # Table
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._table.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._table.doubleClicked.connect(self._on_double_clicked)
        self._table.setFocusPolicy(Qt.StrongFocus)
        layout.addWidget(self._table, 1)

        self._refresh_channel_combo()

    def _build_filter_bar(self, parent_layout: QVBoxLayout) -> None:
        bar = QFrame()
        bar.setFixedHeight(40)
        bar.setStyleSheet("background: #0f141b; border-bottom: 1px solid #303746;")
        b = QHBoxLayout(bar)
        b.setContentsMargins(10, 4, 10, 4)
        b.setSpacing(6)

        # search
        self._search = QLineEdit()
        self._search.setPlaceholderText("검색: HBM, SK하이닉스, \"HBM 테스트\", HBM*  (Ctrl+F)")
        self._search.setClearButtonEnabled(True)
        self._search.setMaximumWidth(320)
        self._search.textChanged.connect(self._on_search)
        b.addWidget(self._search)

        # search mode
        b.addWidget(QLabel("모드:"))
        self._search_mode = QComboBox()
        self._search_mode.addItems(["FTS5 (전체 원문)", "LIKE (메타데이터)"])
        self._search_mode.setToolTip(
            "FTS5: 원문 텍스트 전문 검색 (정확/빠름)\n"
            "LIKE: 주제/태그/채널/내용에 포함된 단어"
        )
        self._search_mode.currentIndexChanged.connect(self._on_filter_changed)
        b.addWidget(self._search_mode)

        # channel combo
        b.addWidget(QLabel("채널:"))
        self._channel_combo = QComboBox()
        self._channel_combo.setMinimumWidth(120)
        self._channel_combo.addItem("전체", None)
        self._channel_combo.currentIndexChanged.connect(self._on_filter_changed)
        b.addWidget(self._channel_combo)

        # importance min
        b.addWidget(QLabel("중요도 ≥"))
        self._imp_min = QSpinBox()
        self._imp_min.setRange(0, 100)
        self._imp_min.setValue(0)
        self._imp_min.setSpecialValueText("전체")
        self._imp_min.valueChanged.connect(self._on_filter_changed)
        b.addWidget(self._imp_min)

        # interest min
        b.addWidget(QLabel("관심도 ≥"))
        self._int_min = QSpinBox()
        self._int_min.setRange(0, 100)
        self._int_min.setValue(0)
        self._int_min.setSpecialValueText("전체")
        self._int_min.valueChanged.connect(self._on_filter_changed)
        b.addWidget(self._int_min)

        # alert toggle
        self._alert_only = QPushButton("S/A만")
        self._alert_only.setCheckable(True)
        self._alert_only.setProperty("class", "miniBtn")
        self._alert_only.toggled.connect(self._on_filter_changed)
        b.addWidget(self._alert_only)

        # sort
        b.addSpacing(10)
        b.addWidget(QLabel("정렬:"))
        self._sort_combo = QComboBox()
        self._sort_combo.addItems([
            "최신순", "오래된순", "중요도순", "관심도순", "채널순", "주제순"
        ])
        self._sort_combo.currentIndexChanged.connect(self._on_filter_changed)
        b.addWidget(self._sort_combo)

        b.addStretch(1)
        btn_reset = QPushButton("초기화")
        btn_reset.setProperty("class", "miniBtn")
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.clicked.connect(self._reset_filters)
        b.addWidget(btn_reset)

        parent_layout.addWidget(bar)

    def _refresh_channel_combo(self) -> None:
        prev = self._channel_combo.currentData()
        self._channel_combo.blockSignals(True)
        self._channel_combo.clear()
        self._channel_combo.addItem("전체", None)
        try:
            conn = connection.get_connection(self._db_path)
            for ch in repositories.distinct_channels(conn):
                self._channel_combo.addItem(ch, ch)
        except Exception:
            pass
        # restore previous selection
        if prev is not None:
            idx = self._channel_combo.findData(prev)
            if idx >= 0:
                self._channel_combo.setCurrentIndex(idx)
        self._channel_combo.blockSignals(False)

    def _current_filters(self) -> dict:
        sort_map = {
            0: "id_desc", 1: "id_asc", 2: "importance_desc", 3: "interest_desc",
            4: "channel", 5: "topic",
        }
        text = self._search.text().strip()
        if not text:
            text_search = None
            fts_signal_ids = None
        elif self._search_mode.currentIndex() == 0:
            # FTS5 mode: pass None for text_search (LIKE), pre-compute signal_ids
            text_search = None
            try:
                from core.db import connection, repositories
                conn = connection.get_connection(self._db_path)
                ids = repositories.fts_search_signals(conn, text, limit=2000)
                fts_signal_ids = set(ids) if ids else None
            except Exception:
                fts_signal_ids = None
        else:
            text_search = text
            fts_signal_ids = None
        return {
            "sort": sort_map.get(self._sort_combo.currentIndex(), "id_desc"),
            "importance_min": self._imp_min.value() or None,
            "interest_min": self._int_min.value() or None,
            "channel": self._channel_combo.currentData(),
            "topic_substr": None,
            "should_alert": True if self._alert_only.isChecked() else None,
            "text_search": text_search,
            "fts_signal_ids": fts_signal_ids,
        }

    def _on_search(self, text: str) -> None:
        self._model.set_filters(**self._current_filters())

    def _on_filter_changed(self) -> None:
        self._model.set_filters(**self._current_filters())

    def _reset_filters(self) -> None:
        self._search.clear()
        self._imp_min.setValue(0)
        self._int_min.setValue(0)
        self._alert_only.setChecked(False)
        self._sort_combo.setCurrentIndex(0)
        self._channel_combo.setCurrentIndex(0)
        self._model.set_filters(**self._current_filters())

    def _on_scroll(self, value: int) -> None:
        vbar = self._table.verticalScrollBar()
        if value >= vbar.maximum() - 4:
            self._model.load_older()

    def _on_double_clicked(self, index) -> None:
        sig = self._model.signal_at(index.row())
        if sig is None:
            return
        # col 4 = tags column → open tag timeline for first tag
        if index.column() == 4 and sig.tags:
            tag = sig.tags[0]
            dlg = TagTimelineDialog(
                target=tag, kind="tag",
                db_path=self._db_path,
                parent=self.window() if self.window() else self,
            )
            dlg.exec()
            return
        self._show_modal(sig)

    def _show_modal(self, sig: FeedSignal) -> None:
        conn = connection.get_connection(self._db_path)
        feed = repositories.get_feed(conn, sig.feed_id)
        if feed is None:
            logger.warning("feed_items missing for feed_id=%s", sig.feed_id)
            return
        modal = RawFeedModal(
            feed_id=feed.id,
            datetime=sig.date,
            channel_name=sig.channel_name,
            topic=sig.topic,
            main_content=sig.main_content,
            importance_score=sig.importance_score,
            interest_score=sig.interest_score,
            tags=sig.tags,
            message_text=feed.message_text,
            message_url=feed.message_url,
            parent=self.window() if self.window() else self,
        )
        modal.exec()
        self.raw_modal_requested.emit(feed.id)

    # ---- public API ----

    def prepend_signal(self, sig: FeedSignal) -> None:
        self._model.prepend(sig)
        self._table.scrollToTop()
        self._refresh_channel_combo()

    def refresh(self) -> None:
        self._model.refresh()
        self._refresh_channel_combo()

    def signal_at_row(self, row: int) -> Optional[FeedSignal]:
        return self._model.signal_at(row)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            idx = self._table.currentIndex()
            if idx.isValid():
                sig = self._model.signal_at(idx.row())
                if sig is not None:
                    self._show_modal(sig)
                    return
        elif event.key() == Qt.Key_F5:
            self.refresh()
            return
        elif event.key() == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            self._search.setFocus()
            self._search.selectAll()
            return
        super().keyPressEvent(event)
