"""흐름 대시보드 탭: 태그별 일자 heatmap + 변화 해석."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.analytics.flow import (
    HeatCell,
    get_heatmap,
    list_top_tags_today,
    recompute_tag_flow_metrics,
)
from core.analytics.narrative import detect_shifts
from core.db import connection


def _heat_color(value: float) -> QColor:
    """Map 0-100 to a heat color (preview.html heat-table palette)."""
    if value < 20:
        return QColor("#111820")
    if value < 40:
        return QColor("#142235")
    if value < 60:
        return QColor("#1d3652")
    if value < 80:
        return QColor("#51421d")
    return QColor("#5a2631")


def _heat_fg(value: float) -> QColor:
    if value < 20:
        return QColor("#6c7688")
    if value < 40:
        return QColor("#a7ccff")
    if value < 60:
        return QColor("#c2dcff")
    if value < 80:
        return QColor("#f3d77a")
    return QColor("#ff9cac")


def _change_label(change: float) -> str:
    if change >= 3.0:
        return "▲ 강함"
    if change >= 1.0:
        return "▲ 상승"
    if change > -1.0:
        return "→ 유지"
    return "▽ 하락"


class FlowDashboardTab(QFrame):
    def __init__(self, db_path, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self.setObjectName("flowDashboard")

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
        title = QLabel("흐름 대시보드")
        title.setObjectName("paneTitle")
        sub = QLabel("tag_flow_metrics · 일자별 가중 중요도 · velocity/acceleration")
        sub.setObjectName("paneSub")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        h.addLayout(title_box, 1)
        self._range_label = QLabel("최근 7일")
        self._range_label.setObjectName("paneKbd")
        h.addWidget(self._range_label)
        btn_refresh = QPushButton("재계산")
        btn_refresh.setProperty("class", "miniBtn")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self._on_recompute)
        h.addWidget(btn_refresh)
        layout.addWidget(head)

        # Body: heatmap + right pane
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Heatmap scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        heat = QFrame()
        heat.setStyleSheet("background: #0f141b;")
        heat_layout = QVBoxLayout(heat)
        heat_layout.setContentsMargins(10, 10, 10, 10)
        heat_layout.setSpacing(8)

        self._table = QTableWidget(0, 0)
        self._table.setStyleSheet(
            "QTableView { background: #0f141b; gridline-color: #303746; border: 0; }"
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setShowGrid(False)
        self._table.setMinimumWidth(700)
        heat_layout.addWidget(self._table)
        heat_layout.addStretch(1)
        scroll.setWidget(heat)
        body.addWidget(scroll, 1)

        # Right pane
        right = QFrame()
        right.setStyleSheet("background: #121820; border-left: 1px solid #303746;")
        right.setFixedWidth(320)
        r = QVBoxLayout(right)
        r.setContentsMargins(10, 10, 10, 10)
        r.setSpacing(8)

        # TODAY top tags
        today_panel = QFrame()
        today_panel.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        tp = QVBoxLayout(today_panel)
        tp.setContentsMargins(8, 6, 8, 8)
        tp.setSpacing(4)
        t_title = QLabel("오늘 상위 태그")
        t_title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        tp.addWidget(t_title)
        self._today_box = QVBoxLayout()
        self._today_box.setSpacing(4)
        tp.addLayout(self._today_box)
        r.addWidget(today_panel)

        # 변화 해석
        interp_panel = QFrame()
        interp_panel.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        ip = QVBoxLayout(interp_panel)
        ip.setContentsMargins(8, 6, 8, 8)
        ip.setSpacing(6)
        i_title = QLabel("내러티브 변화 감지 (3.2)")
        i_title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        ip.addWidget(i_title)
        self._interp_label = QLabel("재계산 버튼을 눌러 갱신하세요.")
        self._interp_label.setWordWrap(True)
        self._interp_label.setStyleSheet("color: #b8c1d0; font-size: 12px;")
        ip.addWidget(self._interp_label)
        r.addWidget(interp_panel)
        r.addStretch(1)

        body.addWidget(right)
        layout.addLayout(body, 1)

        self.refresh()

    def _conn(self):
        return connection.get_connection(self._db_path)

    def _on_recompute(self) -> None:
        conn = self._conn()
        try:
            n = recompute_tag_flow_metrics(conn, days=30)
            self._interp_label.setText(f"tag_flow_metrics {n}행 갱신됨")
        except Exception as e:
            self._interp_label.setText(f"재계산 실패: {e}")
        # narrative detection
        try:
            shifts = detect_shifts(conn)
            self._show_narrative(shifts)
        except Exception as e:
            self._interp_label.setText(f"내러티브 감지 실패: {e}")
        self.refresh()

    def _show_narrative(self, shifts) -> None:
        lines: list[str] = []
        if shifts.new_topics:
            lines.append("🆕 새 주제:")
            for t in shifts.new_topics[:5]:
                lines.append(f"  • {t['topic']} ({t['count']}건)")
        if shifts.rising_tags:
            lines.append("📈 급상승 태그:")
            for t in shifts.rising_tags[:5]:
                lines.append(f"  • {t['tag']}  ({t['baseline']} → {t['recent']})")
        if shifts.fading_tags:
            lines.append("📉 약화 태그:")
            for t in shifts.fading_tags[:5]:
                lines.append(f"  • {t['tag']}  ({t['baseline']} → {t['recent']})")
        if shifts.drift_pairs:
            lines.append("🔀 주제 드리프트:")
            for a, b in shifts.drift_pairs:
                lines.append(f"  • {a} ↔ {b}")
        if not lines:
            self._interp_label.setText("최근 24h 데이터 부족. 메시지가 더 쌓이면 자동 표시됩니다.")
            return
        self._interp_label.setText("\n".join(lines))

    def refresh(self) -> None:
        conn = self._conn()
        try:
            date_cols, tag_rows, cells = get_heatmap(conn, days=7, top_n=12)
        except Exception as e:
            self._interp_label.setText(f"집계 실패: {e}")
            return

        # ensure tag_flow_metrics has data
        if not cells:
            try:
                recompute_tag_flow_metrics(conn, days=30)
                date_cols, tag_rows, cells = get_heatmap(conn, days=7, top_n=12)
            except Exception:
                pass

        # Build table
        cols = 1 + len(date_cols) + 1  # tag | dates... | 변화
        self._table.clear()
        self._table.setColumnCount(cols)
        headers = ["태그"] + date_cols + ["변화"]
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(tag_rows))

        # column widths
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for i in range(1, cols - 1):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(cols - 1, QHeaderView.ResizeToContents)

        cell_map: dict[tuple[str, str], HeatCell] = {(c.tag, c.date): c for c in cells}
        for r_idx, tag in enumerate(tag_rows):
            self._table.setItem(r_idx, 0, QTableWidgetItem(tag))
            for c_idx, d in enumerate(date_cols, start=1):
                cell = cell_map.get((tag, d))
                v = cell.value if cell else 0.0
                item = QTableWidgetItem(f"{v:.0f}")
                bg = _heat_color(v)
                fg = _heat_fg(v)
                item.setBackground(bg)
                item.setForeground(fg)
                self._table.setItem(r_idx, c_idx, item)
            # change column
            sample_cell = next((c for c in cells if c.tag == tag), None)
            change = sample_cell.change if sample_cell else 0.0
            chg_item = QTableWidgetItem(_change_label(change))
            chg_item.setForeground(_heat_fg(min(100, abs(change) * 25)))
            self._table.setItem(r_idx, cols - 1, chg_item)

        self._table.setRowHeight(0, 28) if self._table.rowCount() == 0 else None
        for r in range(self._table.rowCount()):
            self._table.setRowHeight(r, 28)

        # Today top tags
        self._refresh_today()

        # Interpretation
        self._refresh_interp(tag_rows, cells)

    def _refresh_today(self) -> None:
        # clear
        while self._today_box.count():
            item = self._today_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        conn = self._conn()
        top = list_top_tags_today(conn, limit=5)
        if not top:
            lbl = QLabel("오늘 데이터 없음")
            lbl.setStyleSheet("color: #697386; font-size: 12px;")
            self._today_box.addWidget(lbl)
            return
        for i, t in enumerate(top, 1):
            row = QFrame()
            row.setStyleSheet("background: #10161f; border: 1px solid #303746; padding: 4px 6px;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            no_lbl = QLabel(f"{i:02d}")
            no_lbl.setStyleSheet(
                "color: #697386; font-family: 'Consolas','Liberation Mono',monospace; font-size: 11px;"
            )
            no_lbl.setFixedWidth(20)
            rl.addWidget(no_lbl)
            name_lbl = QLabel(t["tag"])
            name_lbl.setStyleSheet("color: #d7dde8; font-size: 12px;")
            rl.addWidget(name_lbl, 1)
            cnt_lbl = QLabel(f"{t['count']}")
            cnt_lbl.setStyleSheet(
                "color: #4ea1ff; font-family: 'Consolas','Liberation Mono',monospace; "
                "font-size: 12px; font-weight: 800;"
            )
            rl.addWidget(cnt_lbl)
            self._today_box.addWidget(row)

    def _refresh_interp(self, tag_rows, cells) -> None:
        if not tag_rows or not cells:
            self._interp_label.setText("아직 충분한 데이터가 없습니다. 메시지가 더 쌓이면 자동으로 표시됩니다.")
            return
        # group by tag → list of values
        from collections import defaultdict
        series: dict[str, list[float]] = defaultdict(list)
        for c in cells:
            series[c.tag].append(c.value)
        notes: list[str] = []
        for tag, vals in series.items():
            if len(vals) < 2:
                continue
            delta = vals[-1] - vals[0]
            if delta >= 15:
                notes.append(f"{tag}: 최근 일주일 +{delta:.0f} 상승 (강한 모멘텀).")
            elif delta >= 5:
                notes.append(f"{tag}: 완만한 상승세 (+{delta:.0f}).")
            elif delta <= -15:
                notes.append(f"{tag}: 최근 일주일 {delta:.0f} 하락 (관심 축소).")
        if not notes:
            self._interp_label.setText("전반적으로 안정적인 흐름입니다.")
        else:
            self._interp_label.setText("\n\n".join(notes[:5]))
