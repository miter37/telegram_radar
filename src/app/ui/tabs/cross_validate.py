"""주가 교차검증 탭: 종목별 feed_ticker_links vs market_bars 시각화."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import cross_validate
from core.db import connection

logger = logging.getLogger(__name__)


class PriceChart(QWidget):
    """Inline price chart with feed event markers."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._prices: list[tuple[str, float]] = []
        self._events: list[tuple[str, int]] = []  # (date, importance)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(
        self,
        prices: list[tuple[str, float]],
        events: list[tuple[str, int]],
    ) -> None:
        self._prices = list(prices)
        self._events = list(events)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0e131a"))
        if not self._prices or len(self._prices) < 2:
            p.setPen(QColor("#697386"))
            p.drawText(self.rect(), Qt.AlignCenter, "(데이터 없음)")
            return
        lo = min(p for _, p in self._prices)
        hi = max(p for _, p in self._prices)
        if hi == lo:
            hi = lo + 1
        w = self.width()
        h = self.height()
        margin_l = 8
        margin_r = 8
        margin_t = 20
        margin_b = 20
        cw = w - margin_l - margin_r
        ch = h - margin_t - margin_b
        # x: index-based
        step = cw / max(1, len(self._prices) - 1)
        points = []
        for i, (_, v) in enumerate(self._prices):
            x = margin_l + i * step
            y = margin_t + ch - (v - lo) / (hi - lo) * ch
            points.append((x, y, v))
        # grid
        p.setPen(QPen(QColor("#202632"), 1, Qt.DotLine))
        for frac in (0.25, 0.5, 0.75):
            y = margin_t + ch - frac * ch
            p.drawLine(margin_l, int(y), w - margin_r, int(y))
        # min/max labels
        p.setPen(QColor("#697386"))
        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        p.drawText(margin_l, h - 4, f"{lo:.2f}")
        p.drawText(margin_l, 12, f"{hi:.2f}")
        # line
        p.setPen(QPen(QColor("#4ea1ff"), 2))
        for i in range(len(points) - 1):
            p.drawLine(int(points[i][0]), int(points[i][1]),
                       int(points[i + 1][0]), int(points[i + 1][1]))
        # event markers
        if self._events:
            event_dates = {d: imp for d, imp in self._events}
            for i, (d, v) in enumerate(self._prices):
                if d in event_dates:
                    imp = event_dates[d]
                    x, y = points[i][0], points[i][1]
                    color = QColor("#ff8494") if imp >= 80 else QColor("#eac45c") if imp >= 50 else QColor("#8bbef8")
                    p.setBrush(color)
                    p.setPen(QPen(QColor("#0e131a"), 1))
                    r = 4 if imp < 50 else 6 if imp < 80 else 8
                    p.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)


class CrossValidateTab(QFrame):
    def __init__(self, db_path, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self.setObjectName("crossValidate")

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
        title = QLabel("주가 교차검증")
        title.setObjectName("paneTitle")
        sub = QLabel("feed_ticker_links × market_bars · 피드 언급 vs 실제 주가 변동")
        sub.setObjectName("paneSub")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        h.addLayout(title_box, 1)
        btn_refresh = QPushButton("새로고침")
        btn_refresh.setProperty("class", "miniBtn")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self._load_tickers)
        h.addWidget(btn_refresh)
        layout.addWidget(head)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Ticker list
        self._ticker_list = QListWidget()
        self._ticker_list.setStyleSheet(
            "QListWidget { background: #121820; border: 0; border-right: 1px solid #303746; }"
            "QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #202632; color: #cbd5e1; }"
            "QListWidget::item:selected { background: #1f4f82; color: #ffffff; }"
        )
        self._ticker_list.setFixedWidth(240)
        self._ticker_list.itemSelectionChanged.connect(self._on_ticker_changed)
        body.addWidget(self._ticker_list)

        # Right pane: chart + table
        right = QFrame()
        right.setStyleSheet("background: #0f141b;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        # Summary
        self._summary = QFrame()
        self._summary.setStyleSheet("background: #151a22; border-bottom: 1px solid #303746;")
        self._summary.setFixedHeight(80)
        sl = QHBoxLayout(self._summary)
        sl.setContentsMargins(12, 8, 12, 8)
        self._summary_labels: list[QLabel] = []
        for label in ("ticker", "30d", "first", "last", "change", "mentions"):
            v = QFrame()
            v.setStyleSheet("background: #0e131a; border: 1px solid #303746; padding: 4px 6px;")
            vl = QVBoxLayout(v)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(0)
            v_lbl = QLabel(label)
            v_lbl.setStyleSheet("color: #697386; font-size: 10px;")
            num_lbl = QLabel("-")
            num_lbl.setStyleSheet("color: #e5edf8; font-size: 14px; font-weight: 800; font-family: 'Consolas',monospace;")
            vl.addWidget(v_lbl)
            vl.addWidget(num_lbl)
            sl.addWidget(v, 1)
            self._summary_labels.append(num_lbl)
        rl.addWidget(self._summary)

        # Chart
        self._chart = PriceChart()
        rl.addWidget(self._chart)

        # Events table
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["date", "feed", "importance", "price_at", "Δ before", "Δ after"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        rl.addWidget(self._table, 1)

        # Status
        self._status = QLabel("")
        self._status.setStyleSheet(
            "color: #697386; font-size: 11px; "
            "font-family: 'Consolas','Liberation Mono',monospace; "
            "padding: 4px 12px; background: #151a22; border-top: 1px solid #303746;"
        )
        rl.addWidget(self._status)

        body.addWidget(right, 1)
        layout.addLayout(body, 1)

        self._load_tickers()

    def _conn(self):
        return connection.get_connection(self._db_path)

    def _load_tickers(self) -> None:
        self._ticker_list.clear()
        conn = self._conn()
        try:
            tickers = cross_validate.list_linked_tickers(conn, min_mentions=1)
        except Exception as e:
            self._status.setText(f"종목 로드 실패: {e}")
            return
        if not tickers:
            placeholder = QListWidgetItem("(링크된 종목 없음)")
            placeholder.setFlags(Qt.NoItemFlags)
            self._ticker_list.addItem(placeholder)
            self._status.setText(
                "feed_ticker_links가 비어있음. LLM 추출된 신호에 "
                "ticker 링크가 있어야 표시됩니다."
            )
            return
        for t in tickers:
            label = f"{t['ticker']}"
            if t.get("ticker_name"):
                label += f" ({t['ticker_name']})"
            label += f" · {t['mentions']}건"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, t["ticker"])
            self._ticker_list.addItem(item)
        self._ticker_list.setCurrentRow(0)
        self._status.setText(f"{len(tickers)}개 ticker")

    def _on_ticker_changed(self) -> None:
        items = self._ticker_list.selectedItems()
        if not items:
            return
        ticker = items[0].data(Qt.UserRole)
        if not ticker:
            return
        self._render_ticker(ticker)

    def _render_ticker(self, ticker: str) -> None:
        conn = self._conn()
        # summary
        summary = cross_validate.ticker_price_change_summary(conn, ticker, days=30)
        if summary["bars"] < 2:
            self._status.setText(f"{ticker}: market_bars 데이터 부족. 설정 탭의 주가 패널에서 업데이트하세요.")
            self._summary_labels[0].setText(ticker.split(".")[-1] if "." in ticker else ticker)
            self._summary_labels[1].setText(str(summary.get("bars", 0)))
            self._summary_labels[2].setText("-")
            self._summary_labels[3].setText("-")
            self._summary_labels[4].setText("-")
            self._summary_labels[5].setText(str(summary.get("mention_count", 0)))
            return
        self._summary_labels[0].setText(ticker.split(".")[-1] if "." in ticker else ticker)
        self._summary_labels[1].setText(f"{summary['bars']}봉")
        self._summary_labels[2].setText(f"{summary['first_close']:.2f}")
        self._summary_labels[3].setText(f"{summary['last_close']:.2f}")
        change = summary["change_pct"]
        color = "#ff8494" if change > 0 else "#4ea1ff" if change < 0 else "#697386"
        sign = "+" if change > 0 else ""
        self._summary_labels[4].setText(f"{sign}{change:.2f}%")
        self._summary_labels[4].setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: 800; font-family: 'Consolas',monospace;"
        )
        self._summary_labels[5].setText(f"{summary['mention_count']}건")
        # price series
        rows = conn.execute("""
            SELECT date, close FROM market_bars
            WHERE ticker = ? ORDER BY date ASC
        """, (ticker,)).fetchall()
        prices = [(r["date"], r["close"]) for r in rows]
        # events
        events = cross_validate.list_mentions_for_ticker(conn, ticker, limit=100)
        ev_dates = [(e.date, e.importance) for e in events if e.price_at is not None]
        self._chart.set_data(prices, ev_dates)
        # table
        self._table.setRowCount(0)
        for e in events:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(e.date))
            main = e.main_content[:40] + ("…" if len(e.main_content) > 40 else "")
            self._table.setItem(r, 1, QTableWidgetItem(f"{e.channel_name} · {main}"))
            imp_item = QTableWidgetItem(str(e.importance))
            if e.importance >= 80:
                imp_item.setForeground(QColor("#ff8494"))
            elif e.importance >= 50:
                imp_item.setForeground(QColor("#eac45c"))
            self._table.setItem(r, 2, imp_item)
            self._table.setItem(r, 3, QTableWidgetItem(
                f"{e.price_at:.2f}" if e.price_at is not None else "-"
            ))
            cpb = e.change_pct_before
            cpb_item = QTableWidgetItem(f"{cpb:+.2f}%" if cpb is not None else "-")
            if cpb is not None:
                cpb_item.setForeground(QColor("#ff8494" if cpb > 0 else "#4ea1ff" if cpb < 0 else "#697386"))
            self._table.setItem(r, 4, cpb_item)
            cpa = e.change_pct_after
            cpa_item = QTableWidgetItem(f"{cpa:+.2f}%" if cpa is not None else "-")
            if cpa is not None:
                cpa_item.setForeground(QColor("#ff8494" if cpa > 0 else "#4ea1ff" if cpa < 0 else "#697386"))
            self._table.setItem(r, 5, cpa_item)
            self._table.setRowHeight(r, 24)
        # status
        n_aligned = sum(1 for e in events if e.price_at is not None)
        self._status.setText(
            f"{ticker} · {len(events)}건 feed 언급 중 {n_aligned}건에 가격 데이터 매칭"
        )
