"""Raw feed modal: shows the original Telegram message + LLM-stored values.

Triggered by Enter on a selected row in the live feed table.
Closed by Esc or the close button.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class RawFeedModal(QDialog):
    """Modal dialog showing a single feed's raw text + structured output."""

    def __init__(
        self,
        *,
        feed_id: int,
        datetime: str,
        channel_name: str,
        topic: str,
        main_content: str,
        importance_score: int,
        interest_score: int,
        tags: list[str],
        message_text: str,
        message_url: Optional[str],
        prompt_version: Optional[str] = None,
        llm_raw: Optional[str] = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("rawFeedModal")
        self.setModal(True)
        self.setWindowTitle("원문 피드")
        self.resize(820, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Head
        head = QFrame()
        head.setProperty("class", "modalHead")
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(12, 8, 8, 8)
        title = QLabel(
            f"{datetime}  ·  {channel_name}  ·  {topic}"
        )
        title.setStyleSheet("font-weight: 760;")
        head_layout.addWidget(title, 1)
        btn_close = QPushButton("닫기")
        btn_close.setProperty("class", "miniBtn")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.reject)
        head_layout.addWidget(btn_close)
        layout.addWidget(head)

        # Body
        body = QFrame()
        body.setProperty("class", "modalBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(8)

        body_layout.addWidget(self._section_label("원문"))
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(message_text)
        text.setMinimumHeight(180)
        body_layout.addWidget(text)

        if message_url:
            url_row = QHBoxLayout()
            url_row.setSpacing(6)
            url_row.addWidget(self._section_label("메시지 링크"))
            url_lbl = QLabel(f'<a href="{message_url}">{message_url}</a>')
            url_lbl.setTextFormat(Qt.RichText)
            url_lbl.setOpenExternalLinks(True)
            url_lbl.setStyleSheet("color: #4ea1ff;")
            url_row.addWidget(url_lbl, 1)
            body_layout.addLayout(url_row)

        body_layout.addWidget(self._section_label("LLM 저장값"))
        meta_lines = [
            f"주제: {topic}",
            f"주요내용: {main_content}",
            f"태그: {', '.join(tags) if tags else '(없음)'}",
            f"중요도: {importance_score}   ·   관심도: {interest_score}",
        ]
        if prompt_version:
            meta_lines.append(f"프롬프트: {prompt_version}")
        meta = QLabel("\n".join(meta_lines))
        meta.setStyleSheet(
            "background: #0f141b; border: 1px solid #303746; padding: 8px; "
            "color: #c7d0df; font-family: 'Consolas','Liberation Mono',monospace; font-size: 12px;"
        )
        meta.setWordWrap(True)
        body_layout.addWidget(meta)

        if llm_raw:
            body_layout.addWidget(self._section_label("LLM 원본 응답"))
            raw = QTextEdit()
            raw.setReadOnly(True)
            raw.setPlainText(llm_raw[:8000])
            raw.setMaximumHeight(160)
            body_layout.addWidget(raw)

        body_layout.addStretch(1)
        layout.addWidget(body, 1)

        # Foot
        foot = QFrame()
        foot.setProperty("class", "modalFoot")
        foot_layout = QHBoxLayout(foot)
        foot_layout.setContentsMargins(12, 8, 12, 8)
        kbd = QLabel(
            f"feed_items.id={feed_id}  ·  Esc 닫기  ·  Ctrl+C 본문 복사"
        )
        kbd.setStyleSheet(
            "color: #697386; font-family: 'Consolas','Liberation Mono',monospace; font-size: 11px;"
        )
        foot_layout.addWidget(kbd, 1)
        btn_copy = QPushButton("원문 복사")
        btn_copy.setProperty("class", "miniBtn")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(message_text))
        foot_layout.addWidget(btn_copy)
        layout.addWidget(foot)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #8f98a8; font-size: 11px; "
            "font-family: 'Consolas','Liberation Mono',monospace;"
        )
        return lbl

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
