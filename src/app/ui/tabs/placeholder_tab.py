"""Placeholder tab used for non-Phase-0 tabs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class PlaceholderTab(QFrame):
    def __init__(self, title: str, sub: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("class", "placeholder")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)

        label = QLabel(title)
        label.setProperty("class", "placeholderLabel")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

        sub_label = QLabel(sub or "Phase 1~2에서 구현")
        sub_label.setProperty("class", "placeholderSubLabel")
        sub_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(sub_label)
