"""Left navigation buttons: channel manager + tab items."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LeftNav(QFrame):
    """Vertical nav. Emits tab_changed(tab_id) and open_channel_manager()."""

    tab_changed = Signal(str)
    open_channel_manager = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("leftNav")
        self.setFixedWidth(178)

        self._tab_buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Channel manager button
        self._btn_channels = QPushButton("⚙ 채널 관리")
        self._btn_channels.setObjectName("channelManagerBtn")
        self._btn_channels.setCursor(Qt.PointingHandCursor)
        self._btn_channels.clicked.connect(self.open_channel_manager)
        layout.addWidget(self._btn_channels)

        layout.addSpacing(8)

        # Sections: FEED / ANALYSIS / SYSTEM
        self._add_section(layout, "FEED")
        self._add_tab(layout, "live", "● 실시간 DB 피드")
        self._add_tab(layout, "flow", "○ 흐름 대시보드")
        self._add_tab(layout, "daily", "○ 일자별 주제")

        self._add_section(layout, "ANALYSIS")
        self._add_tab(layout, "analysis", "○ 태그/주제 분석")
        self._add_tab(layout, "cluster", "○ 주제 클러스터")

        self._add_section(layout, "SYSTEM")
        self._add_tab(layout, "prompt", "○ LLM 프롬프트")
        self._add_tab(layout, "reports", "○ 일간 리포트")
        self._add_tab(layout, "cross", "○ 주가 교차검증")
        self._add_tab(layout, "settings", "○ 설정/태그사전")

        layout.addStretch(1)

        # Default selection
        self._select("live")

    def _add_section(self, layout: QVBoxLayout, text: str) -> None:
        lbl = QLabel(text)
        lbl.setObjectName("navSection")
        layout.addWidget(lbl)

    def _add_tab(self, layout: QVBoxLayout, tab_id: str, label: str) -> None:
        btn = QPushButton(label)
        btn.setProperty("class", "navItem")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _checked=False, t=tab_id: self._on_tab_clicked(t))
        self._group.addButton(btn)
        self._tab_buttons[tab_id] = btn
        layout.addWidget(btn)

    def _on_tab_clicked(self, tab_id: str) -> None:
        self._select(tab_id)
        self.tab_changed.emit(tab_id)

    def _select(self, tab_id: str) -> None:
        btn = self._tab_buttons.get(tab_id)
        if btn is not None:
            btn.setChecked(True)

    def set_active(self, tab_id: str) -> None:
        self._select(tab_id)
