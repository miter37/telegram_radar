"""Channel manager dialog: add/remove/toggle channels.

Persists to data/channels.json via ChannelStore.
Resolves @username through Telethon (via a resolver callback the main window provides).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


# Resolver signature: async def resolve(username) -> (channel_id, title) | raises
ChannelResolver = Callable[[str], "asyncio.Future"]


class ChannelManagerDialog(QDialog):
    """Modal dialog for managing Telegram channel subscriptions.

    Emits channels_changed() whenever the user adds/removes/toggles a channel.
    The main window listens and re-registers Telethon handlers.
    """

    channels_changed = Signal()

    def __init__(
        self,
        *,
        store,  # core.models.channel.ChannelStore
        resolver: Optional[ChannelResolver] = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("channelManagerDialog")
        self.setModal(True)
        self.setWindowTitle("채널 관리")
        self.resize(640, 460)

        self._store = store
        self._resolver = resolver

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Head
        head = QFrame()
        head.setProperty("class", "modalHead")
        h = QHBoxLayout(head)
        h.setContentsMargins(12, 8, 8, 8)
        title = QLabel("채널 관리")
        title.setStyleSheet("font-weight: 760;")
        h.addWidget(title, 1)
        btn_close = QPushButton("닫기")
        btn_close.setProperty("class", "miniBtn")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        h.addWidget(btn_close)
        layout.addWidget(head)

        # Body
        body = QFrame()
        body.setProperty("class", "modalBody")
        b = QVBoxLayout(body)
        b.setContentsMargins(12, 12, 12, 12)
        b.setSpacing(10)

        # Add row
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        add_row.addWidget(QLabel("@username"))
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("@channel_username (예: kiwoom_us_toktok)")
        self._edit.returnPressed.connect(self._on_add)
        add_row.addWidget(self._edit, 1)
        btn_add = QPushButton("검증·추가")
        btn_add.setProperty("class", "primary")
        btn_add.setCursor(Qt.PointingHandCursor)
        btn_add.clicked.connect(self._on_add)
        add_row.addWidget(btn_add)
        b.addLayout(add_row)

        # Table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["username", "title", "enabled", ""])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        b.addWidget(self._table, 1)

        # Status hint
        self._hint = QLabel("수집 활성화된 채널만 실시간 피드를 받습니다.")
        self._hint.setStyleSheet(
            "color: #697386; font-size: 11px; "
            "font-family: 'Consolas','Liberation Mono',monospace;"
        )
        b.addWidget(self._hint)

        layout.addWidget(body, 1)

        # Foot
        foot = QFrame()
        foot.setProperty("class", "modalFoot")
        f = QHBoxLayout(foot)
        f.setContentsMargins(12, 8, 12, 8)
        f.addStretch(1)
        btn_done = QPushButton("완료")
        btn_done.setProperty("class", "primary")
        btn_done.setCursor(Qt.PointingHandCursor)
        btn_done.clicked.connect(self.accept)
        f.addWidget(btn_done)
        layout.addWidget(foot)

        self.reload_table()

    def reload_table(self) -> None:
        self._table.setRowCount(0)
        for ch in self._store.list():
            row = self._table.rowCount()
            self._table.insertRow(row)

            self._table.setItem(row, 0, QTableWidgetItem(ch.username))
            self._table.setItem(row, 1, QTableWidgetItem(ch.title or "(해결 안됨)"))

            enabled_checkbox = QCheckBox()
            enabled_checkbox.setChecked(ch.enabled)
            enabled_checkbox.toggled.connect(
                lambda checked, cid=ch.id: self._on_toggle(cid, checked)
            )
            wrap = QWidget()
            w = QHBoxLayout(wrap)
            w.setContentsMargins(0, 0, 0, 0)
            w.addStretch(1)
            w.addWidget(enabled_checkbox)
            w.addStretch(1)
            self._table.setCellWidget(row, 2, wrap)

            btn_del = QPushButton("삭제")
            btn_del.setProperty("class", "miniBtn")
            btn_del.setCursor(Qt.PointingHandCursor)
            btn_del.clicked.connect(lambda _checked=False, cid=ch.id: self._on_remove(cid))
            self._table.setCellWidget(row, 3, btn_del)

    def _on_toggle(self, channel_id: int, enabled: bool) -> None:
        if self._store.set_enabled(channel_id, enabled):
            self.channels_changed.emit()

    def _on_remove(self, channel_id: int) -> None:
        ans = QMessageBox.question(
            self,
            "삭제 확인",
            "이 채널을 목록에서 삭제하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans == QMessageBox.Yes:
            if self._store.remove(channel_id):
                self.reload_table()
                self.channels_changed.emit()

    def _on_add(self) -> None:
        raw = self._edit.text().strip()
        if not raw:
            return
        username = raw if raw.startswith("@") else f"@{raw}"

        existing = self._store.get_by_username(username)
        if existing is not None:
            QMessageBox.information(
                self, "이미 등록됨", f"{username} 은(는) 이미 목록에 있습니다."
            )
            return

        if self._resolver is None:
            # No Telethon resolver available yet (e.g. not logged in).
            # Register with id=0; the collector will resolve when it starts.
            self._store.add(id=0, username=username, title=username, enabled=True)
            self._edit.clear()
            self.reload_table()
            self.channels_changed.emit()
            self._hint.setText(
                f"{username} 추가됨 (텔레그램 미연결 상태 — 첫 연결 시 자동 확인)"
            )
            return

        try:
            channel_id, title = self._resolver(username)
        except Exception as e:
            logger.warning("resolve failed for %s: %s", username, e)
            QMessageBox.warning(
                self,
                "해결 실패",
                f"{username} 채널을 찾을 수 없습니다.\n\n{e}\n\n"
                f"확인 사항:\n"
                f"  • 텔레그램 로그인이 완료되었는지\n"
                f"  • 채널이 public인지 (private 채널은 username으로 추가 불가)\n"
                f"  • @ 기호 없이 username만 입력했는지",
            )
            return

        self._store.add(id=int(channel_id), username=username, title=title, enabled=True)
        self._edit.clear()
        self.reload_table()
        self.channels_changed.emit()
        self._hint.setText(f"{username} 추가됨 — {title}")
