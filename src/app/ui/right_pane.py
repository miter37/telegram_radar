"""Right pane: live metrics (feeds, signals, LLM success, channels)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class _Metric(QFrame):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "background: #10161f; border: 1px solid #303746; padding: 7px;"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self._v = QLabel("0")
        self._v.setStyleSheet(
            "color: #e5edf8; font-size: 18px; font-weight: 800; "
            "font-family: 'Consolas','Liberation Mono',monospace;"
        )
        self._l = QLabel(label)
        self._l.setStyleSheet("color: #697386; font-size: 11px;")
        v.addWidget(self._v)
        v.addWidget(self._l)

    def set_value(self, text: str) -> None:
        self._v.setText(text)


class RightPane(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet("background: #121820; border-left: 1px solid #303746;")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # --- TODAY ---
        today = QFrame()
        today.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        th = QVBoxLayout(today)
        th.setContentsMargins(8, 6, 8, 8)
        th.setSpacing(6)
        t_title = QLabel("TODAY")
        t_title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        th.addWidget(t_title)
        grid = QHBoxLayout()
        grid.setSpacing(6)
        self._m_feeds = _Metric("수집")
        self._m_signals = _Metric("신호")
        grid.addWidget(self._m_feeds)
        grid.addWidget(self._m_signals)
        grid2 = QHBoxLayout()
        grid2.setSpacing(6)
        self._m_tags = _Metric("태그")
        self._m_channels = _Metric("채널")
        grid2.addWidget(self._m_tags)
        grid2.addWidget(self._m_channels)
        th.addLayout(grid)
        th.addLayout(grid2)
        outer.addWidget(today)

        # --- LLM ---
        llm = QFrame()
        llm.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        lh = QVBoxLayout(llm)
        lh.setContentsMargins(8, 6, 8, 8)
        lh.setSpacing(6)
        l_title = QLabel("LLM")
        l_title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        lh.addWidget(l_title)
        grid3 = QHBoxLayout()
        grid3.setSpacing(6)
        self._m_ok = _Metric("OK")
        self._m_fail = _Metric("FAIL")
        grid3.addWidget(self._m_ok)
        grid3.addWidget(self._m_fail)
        lh.addLayout(grid3)
        self._m_pct = _Metric("성공률(%)")
        lh.addWidget(self._m_pct)
        outer.addWidget(llm)

        # --- Process hint ---
        proc = QFrame()
        proc.setStyleSheet("background: #151b24; border: 1px solid #303746;")
        ph = QVBoxLayout(proc)
        ph.setContentsMargins(8, 6, 8, 8)
        ph.setSpacing(6)
        p_title = QLabel("PROCESS")
        p_title.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746;"
        )
        ph.addWidget(p_title)
        steps = [
            "1 ingest raw feed",
            "2 one-pass LLM JSON",
            "3 schema validation",
            "4 normalize tags",
            "5 store signal",
            "6 update metrics",
        ]
        for s in steps:
            lbl = QLabel(s)
            lbl.setStyleSheet(
                "color: #697386; font-size: 11px; "
                "font-family: 'Consolas','Liberation Mono',monospace;"
            )
            ph.addWidget(lbl)
        outer.addWidget(proc)

        outer.addStretch(1)

    # --- public API ---

    def update_metrics(
        self,
        *,
        feeds: int,
        signals: int,
        llm_ok: int,
        llm_fail: int,
        llm_ok_pct: float,
        tags: int,
    ) -> None:
        self._m_feeds.set_value(f"{feeds:,}")
        self._m_signals.set_value(f"{signals:,}")
        self._m_tags.set_value(f"{tags:,}")
        self._m_ok.set_value(f"{llm_ok:,}")
        self._m_fail.set_value(f"{llm_fail:,}")
        self._m_pct.set_value(f"{llm_ok_pct:.1f}")

    def set_channels(self, n: int) -> None:
        self._m_channels.set_value(f"{n}")
