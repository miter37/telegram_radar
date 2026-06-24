"""설정/태그사전 탭: 관심분야 가중치, 태그 사전 alias, 알림 기준."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config import DATA_DIR
from core.db import connection
from core.normalize.interest import InterestEntry, InterestProfile
from core.normalize.interest_score import recompute_all
from core.normalize.tags import TagNormalizer

logger = logging.getLogger(__name__)


class SettingsTab(QFrame):
    def __init__(self, db_path, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self.setObjectName("settingsTab")

        self._profile = InterestProfile(DATA_DIR / "user_interests.json")
        self._normalizer: Optional[TagNormalizer] = None

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
        title = QLabel("설정 / 태그사전")
        title.setObjectName("paneTitle")
        sub = QLabel("관심분야 가중치 · 태그 alias · 알림 기준 · interest_score 재계산")
        sub.setObjectName("paneSub")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        h.addLayout(title_box, 1)
        layout.addWidget(head)

        # Body: 3-column
        body = QHBoxLayout()
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(10)

        # === Left: 관심분야 ===
        left = self._build_interests_panel()
        body.addWidget(left, 1)

        # === Center: 태그사전 ===
        center = self._build_tags_panel()
        body.addWidget(center, 1)

        # === Right: 알림 기준 ===
        right = self._build_alert_panel()
        body.addWidget(right, 1)

        layout.addLayout(body, 1)

        # Second row: 히스토리 백필
        body2 = QHBoxLayout()
        body2.setContentsMargins(10, 0, 10, 10)
        body2.setSpacing(10)
        hist = self._build_history_panel()
        body2.addWidget(hist, 1)
        exp = self._build_export_panel()
        body2.addWidget(exp, 1)
        from app.ui.widgets.market_panel import MarketPanel
        market_panel = MarketPanel(self._db_path)
        body2.addWidget(market_panel, 1)

        # Third row: 일간 리포트
        body3 = QHBoxLayout()
        body3.setContentsMargins(10, 0, 10, 10)
        body3.setSpacing(10)
        engines_panel = self._build_engines_panel()
        body3.addWidget(engines_panel, 1)
        report_panel = self._build_report_panel()
        body3.addWidget(report_panel, 1)
        layout.addLayout(body3)
        # info panel (ingest_state snapshot)
        from core.db import repositories
        try:
            state_rows = repositories.list_ingest_state(self._conn())
        except Exception:
            state_rows = []
        info = QFrame()
        info.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        il = QVBoxLayout(info)
        il.setContentsMargins(8, 6, 8, 8)
        il.setSpacing(4)
        ti = QLabel("ingest_state")
        ti.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        il.addWidget(ti)
        if not state_rows:
            il.addWidget(QLabel("(없음)"))
        else:
            from PySide6.QtWidgets import QPlainTextEdit
            txt = QPlainTextEdit()
            txt.setReadOnly(True)
            txt.setMaximumHeight(140)
            txt.setStyleSheet(
                "QPlainTextEdit { background: #0e131a; color: #d7dde8; "
                "font-family: 'Consolas','Liberation Mono',monospace; font-size: 11px; "
                "border: 1px solid #303746; }"
            )
            lines = [
                f"@{r['channel_username'] or '?'}  last_id={r['last_message_id']}  "
                f"total={r['total_fetched']}  {r['last_fetched_at']}"
                for r in state_rows
            ]
            txt.setPlainText("\n".join(lines))
            il.addWidget(txt)
        il.addStretch(1)
        body2.addWidget(info, 1)
        layout.addLayout(body2)

        # bottom: action buttons
        bottom = QFrame()
        bottom.setStyleSheet("background: #151a22; border-top: 1px solid #303746;")
        bottom.setFixedHeight(46)
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(8)
        bl.addStretch(1)
        btn_reproc = QPushButton("실패 피드 재처리")
        btn_reproc.setProperty("class", "miniBtn")
        btn_reproc.setCursor(Qt.PointingHandCursor)
        btn_reproc.clicked.connect(self._on_reprocess_failed)
        bl.addWidget(btn_reproc)
        btn_recompute = QPushButton("interest_score 재계산")
        btn_recompute.setProperty("class", "primary")
        btn_recompute.setCursor(Qt.PointingHandCursor)
        btn_recompute.clicked.connect(self._on_recompute_interest)
        bl.addWidget(btn_recompute)
        layout.addWidget(bottom)

        self._load_interests_table()
        self._load_tags_table()

    def _conn(self):
        return connection.get_connection(self._db_path)

    # ---------- Interests ----------

    def _build_interests_panel(self) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(6)
        title = QLabel("관심분야")
        title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        v.addWidget(title)

        # Add row
        add_row = QHBoxLayout()
        add_row.setSpacing(4)
        self._ie_name = QLineEdit()
        self._ie_name.setPlaceholderText("이름 (예: SK하이닉스, HBM, 유리기판)")
        self._ie_name.setMaximumWidth(160)
        add_row.addWidget(self._ie_name, 1)
        self._ie_group = QComboBox()
        self._ie_group.addItems(["industry", "company", "person", "topic"])
        add_row.addWidget(self._ie_group)
        self._ie_weight = QDoubleSpinBox()
        self._ie_weight.setRange(0.1, 5.0)
        self._ie_weight.setSingleStep(0.1)
        self._ie_weight.setValue(1.0)
        self._ie_weight.setMaximumWidth(60)
        add_row.addWidget(self._ie_weight)
        btn_add = QPushButton("추가")
        btn_add.setProperty("class", "miniBtn")
        btn_add.setCursor(Qt.PointingHandCursor)
        btn_add.clicked.connect(self._on_add_interest)
        add_row.addWidget(btn_add)
        v.addLayout(add_row)

        self._interest_table = QTableWidget(0, 3)
        self._interest_table.setHorizontalHeaderLabels(["이름", "그룹", "가중치"])
        self._interest_table.verticalHeader().setVisible(False)
        self._interest_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._interest_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._interest_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._interest_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._interest_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self._interest_table, 1)
        btn_del = QPushButton("선택 삭제")
        btn_del.setProperty("class", "miniBtn")
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(self._on_del_interest)
        v.addWidget(btn_del, alignment=Qt.AlignRight)
        return panel

    def _load_interests_table(self) -> None:
        self._interest_table.setRowCount(0)
        for e in self._profile.list():
            row = self._interest_table.rowCount()
            self._interest_table.insertRow(row)
            self._interest_table.setItem(row, 0, QTableWidgetItem(e.name))
            self._interest_table.setItem(row, 1, QTableWidgetItem(e.group))
            self._interest_table.setItem(row, 2, QTableWidgetItem(f"{e.weight:.1f}"))

    def _on_add_interest(self) -> None:
        name = self._ie_name.text().strip()
        if not name:
            return
        entry = InterestEntry(
            name=name,
            group=self._ie_group.currentText(),
            weight=self._ie_weight.value(),
        )
        self._profile.add(entry)
        self._ie_name.clear()
        self._load_interests_table()

    def _on_del_interest(self) -> None:
        rows = self._interest_table.selectionModel().selectedRows()
        for idx in rows:
            name = self._interest_table.item(idx.row(), 0).text()
            group = self._interest_table.item(idx.row(), 1).text()
            self._profile.remove(name, group)
        self._load_interests_table()

    # ---------- Tag dictionary ----------

    def _build_tags_panel(self) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(6)
        title = QLabel("태그 사전 (canonical_tags)")
        title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        v.addWidget(title)

        # Search
        sr = QHBoxLayout()
        self._tag_search = QLineEdit()
        self._tag_search.setPlaceholderText("태그 검색…")
        self._tag_search.textChanged.connect(self._on_tag_search)
        sr.addWidget(self._tag_search)
        v.addLayout(sr)

        self._tag_table = QTableWidget(0, 3)
        self._tag_table.setHorizontalHeaderLabels(["canonical", "group", "aliases"])
        self._tag_table.verticalHeader().setVisible(False)
        self._tag_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tag_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._tag_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._tag_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._tag_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self._tag_table, 1)

        # Action row
        ar = QHBoxLayout()
        btn_alias = QPushButton("alias 추가")
        btn_alias.setProperty("class", "miniBtn")
        btn_alias.setCursor(Qt.PointingHandCursor)
        btn_alias.clicked.connect(self._on_add_alias)
        ar.addWidget(btn_alias)
        btn_merge = QPushButton("병합")
        btn_merge.setProperty("class", "miniBtn")
        btn_merge.setCursor(Qt.PointingHandCursor)
        btn_merge.clicked.connect(self._on_merge_tag)
        ar.addWidget(btn_merge)
        btn_del = QPushButton("삭제")
        btn_del.setProperty("class", "miniBtn")
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(self._on_delete_tag)
        ar.addWidget(btn_del)
        v.addLayout(ar)

        btn_seed = QPushButton("기본 alias 시드")
        btn_seed.setProperty("class", "miniBtn")
        btn_seed.setCursor(Qt.PointingHandCursor)
        btn_seed.clicked.connect(self._on_seed_defaults)
        v.addWidget(btn_seed)
        return panel

    def _load_tags_table(self) -> None:
        self._tag_table.setRowCount(0)
        conn = self._conn()
        rows = conn.execute("""
            SELECT t.canonical_name AS canonical_name,
                   t.tag_group AS tag_group,
                   t.aliases AS aliases,
                   COUNT(st.id) AS usage
            FROM canonical_tags t
            LEFT JOIN signal_tags st ON st.canonical_tag_id = t.id
            GROUP BY t.id
            ORDER BY usage DESC, t.canonical_name ASC
            LIMIT 200
        """).fetchall()
        for r in rows:
            row = self._tag_table.rowCount()
            self._tag_table.insertRow(row)
            self._tag_table.setItem(row, 0, QTableWidgetItem(r["canonical_name"]))
            self._tag_table.setItem(row, 1, QTableWidgetItem(r["tag_group"]))
            aliases = r["aliases"] or ""
            try:
                import json
                aliases = ", ".join(json.loads(aliases))
            except Exception:
                pass
            self._tag_table.setItem(row, 2, QTableWidgetItem(aliases))

    def _on_tag_search(self, text: str) -> None:
        text = text.strip().lower()
        for r in range(self._tag_table.rowCount()):
            visible = True
            if text:
                items = [
                    self._tag_table.item(r, c).text().lower()
                    for c in range(3) if self._tag_table.item(r, c)
                ]
                visible = any(text in s for s in items)
            self._tag_table.setRowHidden(r, not visible)

    def _on_add_alias(self) -> None:
        rows = self._tag_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "선택", "태그를 먼저 선택하세요")
            return
        canon = self._tag_table.item(rows[0].row(), 0).text()
        alias, ok = QInputDialog.getText(self, "alias 추가", f"'{canon}'에 추가할 alias:")
        if not ok or not alias.strip():
            return
        if self._normalizer is None:
            self._normalizer = TagNormalizer(self._conn())
        ok2 = self._normalizer.add_alias(canon, alias.strip())
        if ok2:
            self._load_tags_table()

    def _on_seed_defaults(self) -> None:
        from core.normalize.tags import BUILTIN_ALIASES
        if self._normalizer is None:
            self._normalizer = TagNormalizer(self._conn())
        n = 0
        for alias, (canon, group) in BUILTIN_ALIASES.items():
            if self._normalizer.add_alias(canon, alias):
                n += 1
        QMessageBox.information(self, "시드 완료", f"{n}개 alias 추가됨")
        self._load_tags_table()

    def _on_merge_tag(self) -> None:
        rows = self._tag_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "선택", "원본 태그를 먼저 선택하세요")
            return
        src = self._tag_table.item(rows[0].row(), 0).text()
        target, ok = QInputDialog.getText(
            self, "태그 병합",
            f"'{src}'의 모든 signal_tag를 어느 canonical로 옮길까요?",
            text=src,
        )
        if not ok or not target.strip():
            return
        target = target.strip()
        conn = self._conn()
        # ensure target exists
        target_id = repositories.upsert_canonical_tag(
            conn, canonical_name=target, tag_group="industry"
        )
        # move signal_tags
        cur = conn.execute(
            "UPDATE signal_tags SET canonical_tag_id = ?, canonical_name = ? "
            "WHERE canonical_name = ?",
            (target_id, target, src),
        )
        moved = cur.rowcount
        # delete source canonical_tags if not used
        conn.execute(
            "DELETE FROM canonical_tags WHERE canonical_name = ? AND id NOT IN "
            "(SELECT DISTINCT canonical_tag_id FROM signal_tags)",
            (src,),
        )
        conn.commit()
        QMessageBox.information(self, "병합 완료", f"{moved}건의 signal_tag가 '{target}'로 이동됨")
        self._load_tags_table()

    def _on_delete_tag(self) -> None:
        rows = self._tag_table.selectionModel().selectedRows()
        if not rows:
            return
        name = self._tag_table.item(rows[0].row(), 0).text()
        ans = QMessageBox.question(
            self, "삭제 확인",
            f"'{name}' canonical_tag을 삭제하시겠습니까?\n"
            f"(연결된 signal_tag가 있으면 삭제되지 않습니다)",
        )
        if ans != QMessageBox.Yes:
            return
        conn = self._conn()
        try:
            conn.execute(
                "DELETE FROM canonical_tags WHERE canonical_name = ? AND id NOT IN "
                "(SELECT DISTINCT canonical_tag_id FROM signal_tags)",
                (name,),
            )
            conn.commit()
        except Exception as e:
            QMessageBox.warning(self, "오류", str(e))
        self._load_tags_table()

    # ---------- Alert criteria ----------

    def _build_alert_panel(self) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(8)
        title = QLabel("알림 기준")
        title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        v.addWidget(title)

        from core.analytics.alert import load_criteria, save_criteria
        self._criteria_path = DATA_DIR / "settings" / "alerts.json"
        self._criteria = load_criteria(self._criteria_path)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("importance ≥"))
        self._imp_min = QSpinBox()
        self._imp_min.setRange(0, 100)
        self._imp_min.setValue(self._criteria.get("importance_min", 80))
        row1.addWidget(self._imp_min)
        row1.addStretch(1)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("interest ≥"))
        self._int_min = QSpinBox()
        self._int_min.setRange(0, 100)
        self._int_min.setValue(self._criteria.get("interest_min", 70))
        row2.addWidget(self._int_min)
        row2.addStretch(1)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("동일 주제 cooldown (분)"))
        self._cooldown = QSpinBox()
        self._cooldown.setRange(0, 240)
        self._cooldown.setValue(self._criteria.get("cooldown_minutes", 30))
        row3.addWidget(self._cooldown)
        row3.addStretch(1)
        v.addLayout(row3)

        v.addWidget(QLabel("제외 채널 (쉼표 구분)"))
        self._exclude = QLineEdit()
        excl = self._criteria.get("exclude_channels", [])
        self._exclude.setText(", ".join(excl))
        self._exclude.setPlaceholderText("예: 스팸채널, 노이즈채널")
        v.addWidget(self._exclude)

        btn_save = QPushButton("저장")
        btn_save.setProperty("class", "primary")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._save_criteria)
        v.addWidget(btn_save)

        info = QLabel(
            "조건을 만족하는 신호는 should_alert=1로 표시되고, "
            "동일 topic_cluster의 마지막 알림 후 cooldown이 지나야 다시 알림.\n"
            "저장 후 즉시 다음 피드부터 적용됨."
        )
        info.setStyleSheet("color: #697386; font-size: 11px;")
        info.setWordWrap(True)
        v.addWidget(info)
        v.addStretch(1)
        return panel

    def _save_criteria(self) -> None:
        from core.analytics.alert import save_criteria
        excl = [s.strip() for s in self._exclude.text().split(",") if s.strip()]
        criteria = {
            "importance_min": self._imp_min.value(),
            "interest_min": self._int_min.value(),
            "cooldown_minutes": self._cooldown.value(),
            "exclude_channels": excl,
        }
        try:
            save_criteria(self._criteria_path, criteria)
            QMessageBox.information(self, "저장", "알림 기준 저장됨")
        except Exception as e:
            QMessageBox.warning(self, "오류", str(e))

    def _build_history_panel(self) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(8)
        title = QLabel("히스토리 백필 (Phase 2.8)")
        title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        v.addWidget(title)

        from core.telegram.history import load_history_config, save_history_config, HistoryConfig
        from core.config import DATA_DIR
        self._hist_path = DATA_DIR / "settings" / "history.json"
        self._hist_cfg = load_history_config(self._hist_path)

        v.addWidget(QLabel("모드"))
        self._hist_mode = QComboBox()
        self._hist_mode.addItems([
            "off (실시간만)",
            "since_last (마지막 이후)",
            "since_date (N일 이내)",
            "all (최근 N개)",
        ])
        mode_idx = {"off": 0, "since_last": 1, "since_date": 2, "all": 3}.get(
            self._hist_cfg.mode, 2
        )
        self._hist_mode.setCurrentIndex(mode_idx)
        v.addWidget(self._hist_mode)

        # days / limit
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("days / limit"))
        self._hist_days = QSpinBox()
        self._hist_days.setRange(1, 365)
        self._hist_days.setValue(self._hist_cfg.days)
        row1.addWidget(self._hist_days)
        self._hist_limit = QSpinBox()
        self._hist_limit.setRange(10, 5000)
        self._hist_limit.setSingleStep(50)
        self._hist_limit.setValue(self._hist_cfg.fetch_limit)
        row1.addWidget(self._hist_limit)
        row1.addStretch(1)
        v.addLayout(row1)

        btn_save = QPushButton("저장")
        btn_save.setProperty("class", "primary")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._save_history_cfg)
        v.addWidget(btn_save)

        btn_reset = QPushButton("ingest_state 초기화 (전체 재수신)")
        btn_reset.setProperty("class", "miniBtn")
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.clicked.connect(self._on_reset_ingest_state)
        v.addWidget(btn_reset)

        info = QLabel(
            "앱 시작 시 또는 새 채널 추가 시 자동으로 이전 메시지를 fetch합니다.\n"
            "since_last: 마지막으로 받은 메시지 이후만\n"
            "since_date: days 이내 메시지\n"
            "all: 채널당 limit건\n"
            "off: 실시간 메시지만 수신"
        )
        info.setStyleSheet("color: #697386; font-size: 11px;")
        info.setWordWrap(True)
        v.addWidget(info)
        v.addStretch(1)
        return panel

    def _save_history_cfg(self) -> None:
        from core.telegram.history import save_history_config, HistoryConfig
        mode_map = {0: "off", 1: "since_last", 2: "since_date", 3: "all"}
        cfg = HistoryConfig(
            mode=mode_map.get(self._hist_mode.currentIndex(), "since_last"),
            fetch_limit=self._hist_limit.value(),
            days=self._hist_days.value(),
        )
        try:
            save_history_config(self._hist_path, cfg)
            QMessageBox.information(self, "저장", "히스토리 설정 저장됨. 재시작 시 적용.")
        except Exception as e:
            QMessageBox.warning(self, "오류", str(e))

    def _on_reset_ingest_state(self) -> None:
        ans = QMessageBox.question(
            self, "초기화 확인",
            "ingest_state를 초기화합니다.\n"
            "다음 앱 시작 시 모든 채널의 최근 메시지를 다시 받게 됩니다.\n"
            "(mode=since_last / all인 경우)\n\n"
            "계속하시겠습니까?",
        )
        if ans != QMessageBox.Yes:
            return
        conn = self._conn()
        try:
            from core.db import repositories
            n = repositories.reset_ingest_state(conn)
            QMessageBox.information(self, "완료", f"{n}개 채널 ingest_state 초기화됨")
        except Exception as e:
            QMessageBox.warning(self, "오류", str(e))

    def _build_export_panel(self) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(6)
        title = QLabel("Export (Phase 3.3)")
        title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        v.addWidget(title)

        # filters
        from PySide6.QtWidgets import QDateEdit
        from PySide6.QtCore import QDate

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("from"))
        self._exp_from = QDateEdit()
        self._exp_from.setCalendarPopup(True)
        self._exp_from.setDate(QDate.currentDate().addDays(-7))
        self._exp_from.setDisplayFormat("yyyy-MM-dd")
        row1.addWidget(self._exp_from)
        row1.addWidget(QLabel("to"))
        self._exp_to = QDateEdit()
        self._exp_to.setCalendarPopup(True)
        self._exp_to.setDate(QDate.currentDate())
        self._exp_to.setDisplayFormat("yyyy-MM-dd")
        row1.addWidget(self._exp_to)
        row1.addStretch(1)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("채널"))
        self._exp_channel = QLineEdit()
        self._exp_channel.setPlaceholderText("(전체)")
        row2.addWidget(self._exp_channel, 1)
        row2.addWidget(QLabel("태그"))
        self._exp_tag = QLineEdit()
        self._exp_tag.setPlaceholderText("(전체)")
        row2.addWidget(self._exp_tag, 1)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("주제 포함"))
        self._exp_topic = QLineEdit()
        self._exp_topic.setPlaceholderText("(전체)")
        row3.addWidget(self._exp_topic, 1)
        row3.addStretch(1)
        v.addLayout(row3)

        # buttons
        br = QHBoxLayout()
        btn_csv = QPushButton("CSV")
        btn_csv.setProperty("class", "primary")
        btn_csv.setCursor(Qt.PointingHandCursor)
        btn_csv.clicked.connect(self._on_export_csv)
        br.addWidget(btn_csv)
        btn_md = QPushButton("Markdown")
        btn_md.setProperty("class", "primary")
        btn_md.setCursor(Qt.PointingHandCursor)
        btn_md.clicked.connect(self._on_export_md)
        br.addWidget(btn_md)
        btn_html = QPushButton("HTML")
        btn_html.setProperty("class", "primary")
        btn_html.setCursor(Qt.PointingHandCursor)
        btn_html.clicked.connect(self._on_export_html)
        br.addWidget(btn_html)
        v.addLayout(br)

        self._exp_status = QLabel("필터 설정 후 버튼 클릭")
        self._exp_status.setStyleSheet(
            "color: #697386; font-size: 11px; "
            "font-family: 'Consolas','Liberation Mono',monospace;"
        )
        self._exp_status.setWordWrap(True)
        v.addWidget(self._exp_status)
        v.addStretch(1)
        return panel

    def _collect_export_filters(self):
        from core.export import ExportFilters
        imp_min = 0
        int_min = 0
        # import inline to avoid cycle
        from PySide6.QtCore import QDate
        f = ExportFilters(
            date_from=self._exp_from.date().toString("yyyy-MM-dd") if self._exp_from.date() != QDate() else None,
            date_to=self._exp_to.date().toString("yyyy-MM-dd") if self._exp_to.date() != QDate() else None,
            importance_min=imp_min or None,
            interest_min=int_min or None,
            channel=self._exp_channel.text().strip() or None,
            topic_substr=self._exp_topic.text().strip() or None,
            tag=self._exp_tag.text().strip() or None,
        )
        return f

    def _on_export_csv(self) -> None:
        from core import export as exp_mod
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV 내보내기", "market_radar_export.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            n = exp_mod.export_csv(self._conn(), Path(path), self._collect_export_filters())
            self._exp_status.setText(f"CSV {n}건 저장: {path}")
        except Exception as e:
            self._exp_status.setText(f"실패: {e}")

    def _on_export_md(self) -> None:
        from core import export as exp_mod
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Markdown 내보내기", "market_radar_export.md", "Markdown (*.md)"
        )
        if not path:
            return
        try:
            n = exp_mod.export_markdown(self._conn(), Path(path), self._collect_export_filters())
            self._exp_status.setText(f"Markdown {n}건 저장: {path}")
        except Exception as e:
            self._exp_status.setText(f"실패: {e}")

    def _on_export_html(self) -> None:
        from core import export as exp_mod
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "HTML 내보내기", "market_radar_export.html", "HTML (*.html)"
        )
        if not path:
            return
        try:
            n = exp_mod.export_html(self._conn(), Path(path), self._collect_export_filters())
            self._exp_status.setText(f"HTML {n}건 저장: {path}")
        except Exception as e:
            self._exp_status.setText(f"실패: {e}")

    def _build_engines_panel(self) -> QFrame:
        from core.llm.engines import (
            Engine, EngineRegistry, PROVIDER_PRESETS, probe_engine,
        )
        from PySide6.QtWidgets import QComboBox as _CB, QInputDialog
        from PySide6.QtCore import QThread, Signal

        panel = QFrame()
        panel.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(6)
        title = QLabel("LLM 엔진 (다중 등록 + 폴백)")
        title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        v.addWidget(title)

        self._engine_registry = EngineRegistry()
        self._engines_table = QTableWidget(0, 5)
        self._engines_table.setHorizontalHeaderLabels(
            ["우선순위", "이름", "provider", "model", "상태"]
        )
        self._engines_table.verticalHeader().setVisible(False)
        self._engines_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._engines_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._engines_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._engines_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._engines_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._engines_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        v.addWidget(self._engines_table, 1)

        # action row
        ar = QHBoxLayout()
        ar.setSpacing(4)
        btn_add = QPushButton("엔진 추가")
        btn_add.setProperty("class", "primary")
        btn_add.setCursor(Qt.PointingHandCursor)
        btn_add.clicked.connect(self._on_add_engine)
        ar.addWidget(btn_add)
        btn_edit = QPushButton("편집")
        btn_edit.setProperty("class", "miniBtn")
        btn_edit.setCursor(Qt.PointingHandCursor)
        btn_edit.clicked.connect(self._on_edit_engine)
        ar.addWidget(btn_edit)
        btn_del = QPushButton("삭제")
        btn_del.setProperty("class", "miniBtn")
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(self._on_delete_engine)
        ar.addWidget(btn_del)
        btn_up = QPushButton("▲ 우선↑")
        btn_up.setProperty("class", "miniBtn")
        btn_up.setCursor(Qt.PointingHandCursor)
        btn_up.clicked.connect(self._on_priority_up)
        ar.addWidget(btn_up)
        btn_down = QPushButton("▼ 우선↓")
        btn_down.setProperty("class", "miniBtn")
        btn_down.setCursor(Qt.PointingHandCursor)
        btn_down.clicked.connect(self._on_priority_down)
        ar.addWidget(btn_down)
        btn_probe = QPushButton("헬스 체크")
        btn_probe.setProperty("class", "miniBtn")
        btn_probe.setCursor(Qt.PointingHandCursor)
        btn_probe.clicked.connect(self._on_probe_engines)
        ar.addWidget(btn_probe)
        v.addLayout(ar)

        info = QLabel(
            "우선순위 1=primary, 2=fallback 1, 3=fallback 2 …\n"
            "미등록 시 env(TG_LLM_*) 단일 엔진 사용. 헬스 체크로 /v1/models 응답 확인."
        )
        info.setStyleSheet("color: #697386; font-size: 11px;")
        info.setWordWrap(True)
        v.addWidget(info)
        v.addStretch(1)

        self._load_engines_table()
        return panel

    def _load_engines_table(self) -> None:
        from core.llm.engines import EngineRegistry
        if not hasattr(self, "_engine_registry"):
            self._engine_registry = EngineRegistry()
        engines = self._engine_registry.list()
        engines.sort(key=lambda e: (e.priority, e.name))
        self._engines_table.setRowCount(0)
        for e in engines:
            r = self._engines_table.rowCount()
            self._engines_table.insertRow(r)
            self._engines_table.setItem(r, 0, QTableWidgetItem(str(e.priority)))
            self._engines_table.setItem(r, 1, QTableWidgetItem(e.name))
            self._engines_table.setItem(r, 2, QTableWidgetItem(e.provider))
            self._engines_table.setItem(r, 3, QTableWidgetItem(e.model))
            status = "✓" if e.enabled else "—"
            if e.last_error:
                status = "✗"
            self._engines_table.setItem(r, 4, QTableWidgetItem(status))

    def _selected_engine_id(self) -> Optional[str]:
        rows = self._engines_table.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        engines = sorted(self._engine_registry.list(), key=lambda e: (e.priority, e.name))
        if 0 <= idx < len(engines):
            return engines[idx].id
        return None

    def _on_add_engine(self) -> None:
        from core.llm.engines import Engine, EngineRegistry, PROVIDER_PRESETS
        # provider 선택
        providers = list(PROVIDER_PRESETS.keys())
        labels = [f"{p} — {PROVIDER_PRESETS[p]['label']}" for p in providers]
        prov, ok = QInputDialog.getItem(self, "엔진 추가", "provider:", labels, 0, False)
        if not ok:
            return
        provider = providers[labels.index(prov)]
        # 이름
        name, ok = QInputDialog.getText(self, "엔진 추가", "이름:", text=provider)
        if not ok or not name.strip():
            return
        # base_url
        default_url = PROVIDER_PRESETS[provider]["base_url"]
        base_url, ok = QInputDialog.getText(
            self, "엔진 추가", "base_url:", text=default_url
        )
        if not ok:
            return
        # Codex OAuth: api_key 불필요, use_codex_oauth=True
        use_codex_oauth = False
        api_key = ""
        if provider == "openai_codex":
            from PySide6.QtWidgets import QMessageBox
            ans = QMessageBox.question(
                self, "Codex OAuth",
                f"~/.codex/auth.json의 OAuth 토큰을 사용하시겠습니까?\n"
                f"(예 — codex CLI 로그인 상태)",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ans == QMessageBox.Yes:
                use_codex_oauth = True
            else:
                api_key, ok = QInputDialog.getText(
                    self, "Codex API 키", "OpenAI API 키 (sk-...):", text=""
                )
                if not ok:
                    return
        else:
            api_key, ok = QInputDialog.getText(
                self, "엔진 추가",
                f"api_key ({PROVIDER_PRESETS[provider]['api_key_hint']}):",
                text="",
            )
            if not ok:
                return
        # model
        model, ok = QInputDialog.getText(
            self, "엔진 추가", "model 이름 (모르면 비워두세요):", text=""
        )
        if not ok:
            return
        priority = self._engine_registry.next_priority()
        eng = Engine(
            id="",
            name=name.strip(),
            provider=provider,
            base_url=base_url.strip(),
            api_key=api_key.strip(),
            model=model.strip(),
            priority=priority,
            enabled=True,
            use_codex_oauth=use_codex_oauth,
        )
        self._engine_registry.add(eng)
        self._load_engines_table()
        msg = f"엔진 '{name}' 추가됨 (우선순위 {priority})"
        if use_codex_oauth:
            msg += "\nCodex OAuth 자동 사용 — auth.json 갱신 시 자동 반영"
        QMessageBox.information(self, "추가됨", msg)

    def _on_edit_engine(self) -> None:
        from core.llm.engines import Engine, PROVIDER_PRESETS
        eid = self._selected_engine_id()
        if not eid:
            return
        eng = self._engine_registry.get(eid)
        if eng is None:
            return
        # Build full editor dialog
        dlg = QDialog(self)
        dlg.setWindowTitle(f"엔진 편집 — {eng.name}")
        dlg.setModal(True)
        dlg.resize(560, 460)
        dlg.setStyleSheet(
            "QDialog { background: #11161e; border: 1px solid #4b5565; }"
        )
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # head
        head = QFrame()
        head.setProperty("class", "modalHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 8, 8, 8)
        t = QLabel("엔진 편집")
        t.setStyleSheet("font-weight: 760;")
        hl.addWidget(t, 1)
        layout.addWidget(head)

        # body
        body = QFrame()
        body.setProperty("class", "modalBody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(8)

        # name
        bl.addWidget(QLabel("이름"))
        edit_name = QLineEdit(eng.name)
        edit_name.setPlaceholderText("표시용 이름 (예: 로컬 llama)")
        bl.addWidget(edit_name)

        # provider (read-only selector for now)
        bl.addWidget(QLabel("provider"))
        edit_provider = QComboBox()
        providers = list(PROVIDER_PRESETS.keys())
        for p in providers:
            edit_provider.addItem(f"{p} — {PROVIDER_PRESETS[p]['label']}", p)
        idx = providers.index(eng.provider) if eng.provider in providers else 0
        edit_provider.setCurrentIndex(idx)
        edit_provider.setToolTip(
            "provider 변경 시 base_url/api_key는 새 provider의 기본값으로 덮어쓸 수 있음.\n"
            "변경하지 않으려면 그대로 두세요."
        )
        bl.addWidget(edit_provider)

        # base_url
        bl.addWidget(QLabel("base_url"))
        edit_base = QLineEdit(eng.base_url)
        edit_base.setPlaceholderText("예: http://127.0.0.1:18085/v1")
        bl.addWidget(edit_base)

        # api_key (masked) + Codex OAuth toggle
        bl.addWidget(QLabel("api_key (Codex OAuth 사용 시 비워도 됨)"))
        edit_key = QLineEdit(eng.api_key)
        edit_key.setEchoMode(QLineEdit.Password)
        edit_key.setPlaceholderText(
            f"힌트: {PROVIDER_PRESETS.get(eng.provider, {}).get('api_key_hint', '비워두면 not-needed')}"
        )
        bl.addWidget(edit_key)

        # Codex OAuth toggle
        edit_codex = QCheckBox("Codex OAuth 사용 (~/.codex/auth.json 자동)")
        edit_codex.setChecked(eng.use_codex_oauth)
        bl.addWidget(edit_codex)

        # model
        bl.addWidget(QLabel("model 이름 (비우면 /v1/models 첫 항목 자동)"))
        edit_model = QLineEdit(eng.model)
        edit_model.setPlaceholderText("예: gpt-4o, glm-4.5, llama-3.1-70b")
        bl.addWidget(edit_model)

        # timeout
        row_t = QHBoxLayout()
        row_t.addWidget(QLabel("timeout (초)"))
        edit_timeout = QSpinBox()
        edit_timeout.setRange(5, 600)
        edit_timeout.setValue(int(eng.timeout))
        row_t.addWidget(edit_timeout)
        row_t.addStretch(1)
        bl.addLayout(row_t)

        # extra_headers
        bl.addWidget(QLabel("extra headers (JSON, 선택)"))
        edit_headers = QLineEdit(eng.extra_headers)
        edit_headers.setPlaceholderText('예: {"X-Org": "myteam"}')
        bl.addWidget(edit_headers)

        # enabled
        edit_enabled = QCheckBox("활성화")
        edit_enabled.setChecked(eng.enabled)
        bl.addWidget(edit_enabled)

        # priority
        row_p = QHBoxLayout()
        row_p.addWidget(QLabel("우선순위"))
        edit_priority = QSpinBox()
        edit_priority.setRange(1, 99)
        edit_priority.setValue(eng.priority)
        row_p.addWidget(edit_priority)
        row_p.addStretch(1)
        bl.addLayout(row_p)

        # error/info
        if eng.last_error:
            err_lbl = QLabel(f"최근 오류: {eng.last_error}")
            err_lbl.setStyleSheet("color: #ef596f; font-size: 11px;")
            err_lbl.setWordWrap(True)
            bl.addWidget(err_lbl)
        if eng.last_ok_at:
            ok_lbl = QLabel(f"최근 성공: {eng.last_ok_at}")
            ok_lbl.setStyleSheet(
                "color: #36c275; font-size: 11px; "
                "font-family: 'Consolas','Liberation Mono',monospace;"
            )
            bl.addWidget(ok_lbl)

        bl.addStretch(1)
        layout.addWidget(body, 1)

        # foot
        foot = QFrame()
        foot.setProperty("class", "modalFoot")
        f = QHBoxLayout(foot)
        f.setContentsMargins(12, 8, 12, 8)
        f.addStretch(1)
        btn_cancel = QPushButton("취소")
        btn_cancel.setProperty("class", "miniBtn")
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.clicked.connect(dlg.reject)
        f.addWidget(btn_cancel)
        btn_save = QPushButton("저장")
        btn_save.setProperty("class", "primary")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(dlg.accept)
        f.addWidget(btn_save)
        layout.addWidget(foot)

        if dlg.exec() != QDialog.Accepted:
            return

        # Apply changes
        eng.name = edit_name.text().strip() or eng.name
        new_provider = edit_provider.currentData()
        if new_provider != eng.provider:
            eng.provider = new_provider
            # update base_url hint if it's currently empty or matches the old preset
            preset = PROVIDER_PRESETS.get(new_provider, {})
            if not edit_base.text().strip() or edit_base.text().strip() == PROVIDER_PRESETS.get(eng.provider, {}).get("base_url", ""):
                eng.base_url = preset.get("base_url", eng.base_url)
        eng.base_url = edit_base.text().strip() or eng.base_url
        # api_key: keep current if unchanged (empty input = keep); only update if user typed something
        new_key = edit_key.text()
        if new_key or eng.use_codex_oauth:
            # user typed a key — but if use_codex_oauth is on, ignore typed key
            if not edit_codex.isChecked():
                eng.api_key = new_key
        eng.use_codex_oauth = edit_codex.isChecked()
        if eng.use_codex_oauth:
            eng.api_key = ""  # always clear when OAuth is on
        eng.model = edit_model.text().strip()
        eng.timeout = float(edit_timeout.value())
        eng.extra_headers = edit_headers.text().strip()
        eng.enabled = edit_enabled.isChecked()
        eng.priority = edit_priority.value()
        # validate extra_headers JSON
        if eng.extra_headers:
            try:
                json.loads(eng.extra_headers)
            except Exception as e:
                QMessageBox.warning(self, "저장 실패", f"extra_headers JSON 파싱 실패: {e}")
                return
        self._engine_registry.update(eng)
        self._load_engines_table()
        QMessageBox.information(
            self, "저장",
            f"엔진 '{eng.name}' 업데이트됨.\n"
            f"provider={eng.provider}, model={eng.model or '(auto)'}, base_url={eng.base_url}"
        )

    def _on_delete_engine(self) -> None:
        eid = self._selected_engine_id()
        if not eid:
            return
        eng = self._engine_registry.get(eid)
        if eng is None:
            return
        ans = QMessageBox.question(
            self, "삭제", f"'{eng.name}' 엔진을 삭제하시겠습니까?"
        )
        if ans == QMessageBox.Yes:
            self._engine_registry.remove(eid)
            self._load_engines_table()

    def _on_priority_up(self) -> None:
        eid = self._selected_engine_id()
        if not eid:
            return
        eng = self._engine_registry.get(eid)
        if eng is None or eng.priority <= 1:
            return
        eng.priority -= 1
        self._engine_registry.update(eng)
        # also bump the one that took the slot up
        for other in self._engine_registry.list():
            if other.id != eng.id and other.priority == eng.priority:
                other.priority += 1
                self._engine_registry.update(other)
        self._load_engines_table()

    def _on_priority_down(self) -> None:
        eid = self._selected_engine_id()
        if not eid:
            return
        eng = self._engine_registry.get(eid)
        if eng is None:
            return
        eng.priority += 1
        self._engine_registry.update(eng)
        for other in self._engine_registry.list():
            if other.id != eng.id and other.priority == eng.priority:
                other.priority -= 1
                self._engine_registry.update(other)
        self._load_engines_table()

    def _on_probe_engines(self) -> None:
        from core.llm.engines import probe_engine
        from PySide6.QtCore import QThread
        engines = self._engine_registry.list_enabled()
        if not engines:
            QMessageBox.information(self, "헬스 체크", "활성화된 엔진 없음")
            return
        # run in background thread
        class ProbeThread(QThread):
            results = []

            def run(self_inner):
                import asyncio
                async def go():
                    out = []
                    for e in engines:
                        ok, msg = await probe_engine(e)
                        out.append((e.name, ok, msg))
                    self_inner.results = out
                asyncio.run(go())

        self._probe_thread = ProbeThread()
        results_holder = []

        def show():
            for name, ok, msg in self._probe_thread.results:
                self._engine_registry.mark_ok(name) if ok else None
            self._load_engines_table()
            lines = [f"  {'✓' if ok else '✗'} {n} — {m}" for n, ok, m in self._probe_thread.results]
            QMessageBox.information(
                self, "헬스 체크 결과", "\n".join(lines) or "결과 없음"
            )

        self._probe_thread.finished.connect(show)
        self._probe_thread.start()

    def _build_report_panel(self) -> QFrame:
        from core.report import (
            ReportConfig, load_report_config, save_report_config,
        )
        from PySide6.QtWidgets import QTimeEdit
        from PySide6.QtCore import QTime

        panel = QFrame()
        panel.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(6)
        title = QLabel("일간 리포트 (Phase 3 B6)")
        title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        v.addWidget(title)

        cfg = load_report_config()
        self._report_cfg = cfg

        # enabled
        from PySide6.QtWidgets import QCheckBox
        self._rep_enabled = QCheckBox("자동 생성 활성화")
        self._rep_enabled.setChecked(cfg.enabled)
        v.addWidget(self._rep_enabled)

        # time
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("생성 시각"))
        self._rep_time = QTimeEdit()
        self._rep_time.setDisplayFormat("HH:mm")
        self._rep_time.setTime(QTime(cfg.hour, cfg.minute))
        row1.addWidget(self._rep_time)
        row1.addStretch(1)
        v.addLayout(row1)

        # bot
        v.addWidget(QLabel("봇 토큰 (BotFather @BotFather)"))
        self._rep_token = QLineEdit()
        self._rep_token.setEchoMode(QLineEdit.Password)
        self._rep_token.setText(cfg.bot_token)
        self._rep_token.setPlaceholderText("예: 123456:ABC-DEF…")
        v.addWidget(self._rep_token)

        v.addWidget(QLabel("수신 chat_id (자신 또는 그룹)"))
        self._rep_chat = QLineEdit()
        self._rep_chat.setText(cfg.bot_chat_id)
        self._rep_chat.setPlaceholderText("예: 123456789 또는 @channelname")
        v.addWidget(self._rep_chat)

        self._rep_interests = QCheckBox("관심분야 요약 포함")
        self._rep_interests.setChecked(cfg.include_user_interests)
        v.addWidget(self._rep_interests)

        btn_save = QPushButton("저장")
        btn_save.setProperty("class", "primary")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._save_report_cfg)
        v.addWidget(btn_save)

        info = QLabel(
            "활성화 시 매일 설정 시각에 어제 신호/태그 흐름/관심분야를 "
            "요약한 LLM 리포트가 생성되어 봇으로 전송되고, "
            "[일간 리포트] 탭에 누적됩니다."
        )
        info.setStyleSheet("color: #697386; font-size: 11px;")
        info.setWordWrap(True)
        v.addWidget(info)
        v.addStretch(1)
        return panel

    def _save_report_cfg(self) -> None:
        from core.report import save_report_config, ReportConfig
        t = self._rep_time.time()
        cfg = ReportConfig(
            enabled=self._rep_enabled.isChecked(),
            hour=t.hour(),
            minute=t.minute(),
            bot_token=self._rep_token.text().strip(),
            bot_chat_id=self._rep_chat.text().strip(),
            include_user_interests=self._rep_interests.isChecked(),
        )
        try:
            save_report_config(cfg)
            QMessageBox.information(self, "저장", "리포트 설정 저장됨")
        except Exception as e:
            QMessageBox.warning(self, "오류", str(e))
        panel = QFrame()
        panel.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(8)
        title = QLabel("알림 기준")
        title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        v.addWidget(title)

        # load existing criteria
        from core.analytics.alert import load_criteria, save_criteria
        self._criteria_path = DATA_DIR / "settings" / "alerts.json"
        self._criteria = load_criteria(self._criteria_path)

        # importance
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("importance ≥"))
        self._imp_min = QSpinBox()
        self._imp_min.setRange(0, 100)
        self._imp_min.setValue(self._criteria.get("importance_min", 80))
        row1.addWidget(self._imp_min)
        row1.addStretch(1)
        v.addLayout(row1)

        # interest
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("interest ≥"))
        self._int_min = QSpinBox()
        self._int_min.setRange(0, 100)
        self._int_min.setValue(self._criteria.get("interest_min", 70))
        row2.addWidget(self._int_min)
        row2.addStretch(1)
        v.addLayout(row2)

        # cooldown
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("동일 주제 cooldown (분)"))
        self._cooldown = QSpinBox()
        self._cooldown.setRange(0, 240)
        self._cooldown.setValue(self._criteria.get("cooldown_minutes", 30))
        row3.addWidget(self._cooldown)
        row3.addStretch(1)
        v.addLayout(row3)

        # exclude channels
        v.addWidget(QLabel("제외 채널 (쉼표 구분)"))
        self._exclude = QLineEdit()
        excl = self._criteria.get("exclude_channels", [])
        self._exclude.setText(", ".join(excl))
        self._exclude.setPlaceholderText("예: 스팸채널, 노이즈채널")
        v.addWidget(self._exclude)

        # save button
        btn_save = QPushButton("저장")
        btn_save.setProperty("class", "primary")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._save_criteria)
        v.addWidget(btn_save)

        # info label
        info = QLabel(
            "조건을 만족하는 신호는 should_alert=1로 표시되고, "
            "동일 topic_cluster의 마지막 알림 후 cooldown이 지나야 다시 알림.\n"
            "저장 후 즉시 다음 피드부터 적용됨."
        )
        info.setStyleSheet("color: #697386; font-size: 11px;")
        info.setWordWrap(True)
        v.addWidget(info)
        v.addStretch(1)
        return panel

    def _save_criteria(self) -> None:
        from core.analytics.alert import save_criteria
        excl = [s.strip() for s in self._exclude.text().split(",") if s.strip()]
        criteria = {
            "importance_min": self._imp_min.value(),
            "interest_min": self._int_min.value(),
            "cooldown_minutes": self._cooldown.value(),
            "exclude_channels": excl,
        }
        try:
            save_criteria(self._criteria_path, criteria)
            QMessageBox.information(self, "저장", "알림 기준 저장됨")
        except Exception as e:
            QMessageBox.warning(self, "오류", str(e))

    def _on_recompute_interest(self) -> None:
        conn = self._conn()
        try:
            n = recompute_all(conn, self._profile.list())
            QMessageBox.information(
                self, "재계산 완료",
                f"interest_score {n}건 재계산됨.\n"
                f"라이브 피드를 새로고침(F5)해서 확인하세요.",
            )
        except Exception as e:
            logger.exception("recompute failed")
            QMessageBox.warning(self, "오류", f"재계산 실패: {e}")

    def _on_reprocess_failed(self) -> None:
        """Phase 2.7: re-run LLM on all feeds that previously failed."""
        from core.db import connection as _conn
        from core.reprocess import list_failed_feed_ids
        from core.config import load_llm_config, load_user_interests
        from core.reprocess import reprocess_feeds

        conn = self._conn()
        fids = list_failed_feed_ids(conn, limit=200)
        if not fids:
            QMessageBox.information(self, "재처리", "실패한 피드 없음")
            return
        ans = QMessageBox.question(
            self, "재처리 확인",
            f"실패한 {len(fids)}건의 피드를 다시 LLM으로 처리합니다.\n"
            f"기존 feed_signal은 삭제되고 새로 만들어집니다.\n"
            f"계속하시겠습니까?",
        )
        if ans != QMessageBox.Yes:
            return
        # run in a separate thread (QThread)
        from PySide6.QtCore import QThread, Signal

        class ReprocessThread(QThread):
            done = Signal(dict)

            def __init__(self, fids, db_path):
                super().__init__()
                self._fids = fids
                self._db_path = db_path

            def run(self):
                import asyncio
                from core.config import load_llm_config, load_user_interests
                llm_cfg = load_llm_config()
                res = asyncio.run(reprocess_feeds(
                    feed_ids=self._fids,
                    db_path=self._db_path,
                    llm_cfg=llm_cfg,
                    interests_provider=load_user_interests,
                ))
                self.done.emit(res)

        self._reproc_thread = ReprocessThread(fids, self._db_path)

        def on_done(res):
            QMessageBox.information(
                self, "재처리 완료",
                f"성공 {res['ok']}건 / 실패 {res['fail']}건 / 건너뜀 {res['skipped']}건"
            )
            self._load_tags_table()

        self._reproc_thread.done.connect(on_done)
        self._reproc_thread.start()
