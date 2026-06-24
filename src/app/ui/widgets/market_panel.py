"""Market panel: ticker snapshot + price chart for cross-validating feeds.

Pure Qt drawing (no matplotlib). Shows:
- Snapshot box: last price, change %, volume
- ASCII-style sparkline (block characters) for recent close prices
- Per-day table (date, close, change %, volume)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import market

logger = logging.getLogger(__name__)


class Sparkline(QWidget):
    """Tiny inline price chart drawn with QPainter."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._values: list[float] = []
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_values(self, values: list[float]) -> None:
        self._values = list(values)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0e131a"))
        if not self._values or len(self._values) < 2:
            p.setPen(QColor("#697386"))
            p.drawText(self.rect(), Qt.AlignCenter, "(데이터 없음)")
            return
        lo = min(self._values)
        hi = max(self._values)
        if hi == lo:
            hi = lo + 1
        w = self.width()
        h = self.height()
        margin = 6
        step = (w - 2 * margin) / max(1, len(self._values) - 1)
        points = []
        for i, v in enumerate(self._values):
            x = margin + i * step
            y = h - margin - (v - lo) / (hi - lo) * (h - 2 * margin)
            points.append((x, y))
        # grid
        p.setPen(QPen(QColor("#202632"), 1, Qt.DotLine))
        for frac in (0.25, 0.5, 0.75):
            y = h - margin - frac * (h - 2 * margin)
            p.drawLine(margin, int(y), w - margin, int(y))
        # line
        p.setPen(QPen(QColor("#4ea1ff"), 2))
        for i in range(len(points) - 1):
            p.drawLine(int(points[i][0]), int(points[i][1]),
                       int(points[i + 1][0]), int(points[i + 1][1]))
        # last dot
        lx, ly = points[-1]
        p.setBrush(QColor("#4ea1ff"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(int(lx - 3), int(ly - 3), 6, 6)
        # min/max labels
        p.setPen(QColor("#697386"))
        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        p.drawText(margin, h - 1, f"{lo:.2f}")
        p.drawText(margin, 12, f"{hi:.2f}")


class MarketPanel(QFrame):
    def __init__(self, db_path, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self.setStyleSheet("background: #151b24; border: 1px solid #303746;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        title = QLabel("주가 / 거래량 (Phase 3.5)")
        title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        layout.addWidget(title)

        # input row
        row = QHBoxLayout()
        row.addWidget(QLabel("ticker"))
        self._ticker_edit = QLineEdit()
        self._ticker_edit.setPlaceholderText("예: 005930, AAPL, TSLA")
        self._ticker_edit.returnPressed.connect(self._on_load)
        row.addWidget(self._ticker_edit, 1)
        btn_load = QPushButton("불러오기")
        btn_load.setProperty("class", "primary")
        btn_load.setCursor(Qt.PointingHandCursor)
        btn_load.clicked.connect(self._on_load)
        row.addWidget(btn_load)
        btn_update = QPushButton("yfinance 업데이트")
        btn_update.setProperty("class", "miniBtn")
        btn_update.setCursor(Qt.PointingHandCursor)
        btn_update.clicked.connect(self._on_update)
        row.addWidget(btn_update)
        layout.addLayout(row)

        # snapshot
        self._snapshot = QLabel("(ticker 입력 후 불러오기)")
        self._snapshot.setStyleSheet(
            "color: #b8c1d0; font-size: 12px; "
            "background: #0e131a; border: 1px solid #303746; padding: 8px;"
        )
        self._snapshot.setWordWrap(True)
        layout.addWidget(self._snapshot)

        # sparkline
        self._spark = Sparkline()
        layout.addWidget(self._spark)

        # bars table
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["date", "close", "change%", "volume", "open"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.setMaximumHeight(180)
        layout.addWidget(self._table)
        v = QLabel("피드와 교차검증: 이 ticker가 언급된 피드는 라이브 피드 탭의 "
                   "검색에 '티커'로 매핑하면 같이 보입니다. (수동)")
        v.setStyleSheet("color: #697386; font-size: 11px;")
        v.setWordWrap(True)
        layout.addWidget(v)
        layout.addStretch(1)

    def _conn(self):
        from core.db import connection
        return connection.get_connection(self._db_path)

    def _on_load(self) -> None:
        from core.db import connection
        ticker = self._ticker_edit.text().strip()
        if not ticker:
            return
        conn = self._conn()
        market.ensure_market_schema(conn)
        bars = market.get_bars(conn, ticker, days=60)
        if not bars:
            self._snapshot.setText(
                f"{market.normalize_ticker(ticker)}: 캐시 없음. "
                f"[yfinance 업데이트] 버튼을 눌러 데이터를 받아오세요."
            )
            return
        self._render(ticker, bars)

    def _on_update(self) -> None:
        from core.db import connection
        ticker = self._ticker_edit.text().strip()
        if not ticker:
            return
        conn = self._conn()
        market.ensure_market_schema(conn)
        try:
            n = market.update_ticker(conn, ticker, period="3mo")
            if n == 0:
                self._snapshot.setText(f"{market.normalize_ticker(ticker)}: yfinance에서 데이터 없음")
                return
            bars = market.get_bars(conn, ticker, days=60)
            self._render(ticker, bars)
            self._snapshot.setText(self._snapshot.text() + f"  ·  {n}행 업데이트됨")
        except Exception as e:
            self._snapshot.setText(f"업데이트 실패: {e}")

    def _render(self, ticker: str, bars: list[dict]) -> None:
        sym = market.normalize_ticker(ticker)
        if not bars:
            return
        # snapshot
        last = bars[-1]
        prev = bars[-2] if len(bars) >= 2 else None
        change_pct = None
        if prev and prev["close"]:
            change_pct = (last["close"] - prev["close"]) / prev["close"] * 100
        color = "#8bbef8"
        sign = ""
        if change_pct is not None:
            if change_pct > 0:
                color = "#ef596f"
                sign = "▲"
            elif change_pct < 0:
                color = "#4ea1ff"
                sign = "▼"
        self._snapshot.setStyleSheet(
            f"color: #b8c1d0; font-size: 13px; "
            f"background: #0e131a; border: 1px solid {color}; padding: 8px;"
        )
        snap_text = (
            f"<b style='color:#e5edf8'>{sym}</b> · "
            f"<span style='color:{color}'>{last['close']:.2f}</span> "
        )
        if change_pct is not None:
            snap_text += f"<span style='color:{color}'>{sign} {change_pct:+.2f}%</span> "
        snap_text += f"· 거래량 {last['volume']:,}  · {last['date']}"
        if len(bars) >= 2:
            lo_30 = min(b["low"] for b in bars[-30:] if b.get("low"))
            hi_30 = max(b["high"] for b in bars[-30:] if b.get("high"))
            snap_text += f"  · 30일 {lo_30:.2f} ~ {hi_30:.2f}"
        self._snapshot.setText(snap_text)
        # sparkline
        self._spark.set_values([b["close"] for b in bars if b.get("close") is not None])
        # table
        self._table.setRowCount(0)
        prev_close = None
        for b in reversed(bars):
            r = self._table.rowCount()
            self._table.insertRow(r)
            cp = ""
            if prev_close is not None and b["close"] and prev_close:
                cpct = (b["close"] - prev_close) / prev_close * 100
                cp = f"{cpct:+.2f}"
            self._table.setItem(r, 0, QTableWidgetItem(str(b.get("date", ""))))
            self._table.setItem(r, 1, QTableWidgetItem(f"{b.get('close', 0):.2f}"))
            cp_item = QTableWidgetItem(cp)
            if cp.startswith("+"):
                cp_item.setForeground(QColor("#ef596f"))
            elif cp.startswith("-"):
                cp_item.setForeground(QColor("#4ea1ff"))
            self._table.setItem(r, 2, cp_item)
            self._table.setItem(r, 3, QTableWidgetItem(f"{b.get('volume', 0):,}"))
            self._table.setItem(r, 4, QTableWidgetItem(f"{b.get('open', 0):.2f}"))
            self._table.setRowHeight(r, 20)
            prev_close = b["close"]
