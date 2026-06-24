"""Channel data model and JSON persistence."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class Channel:
    id: int
    username: str
    title: str
    enabled: bool
    added_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Channel":
        return cls(
            id=int(d.get("id", 0)),
            username=str(d.get("username", "")),
            title=str(d.get("title", "")),
            enabled=bool(d.get("enabled", True)),
            added_at=str(d.get("added_at", datetime.now().astimezone().isoformat(timespec="seconds"))),
        )


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ChannelStore:
    """Thread-unsafe (single GUI thread). Atomic file writes."""

    DEFAULT_TEST_CHANNEL = "@kiwoom_us_toktok"

    def __init__(self, path: Path):
        self.path = path
        self._channels: list[Channel] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._channels = [Channel.from_dict(d) for d in data]
        except Exception:
            self._channels = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [c.to_dict() for c in self._channels]
        # atomic write
        fd, tmp = tempfile.mkstemp(
            prefix=".channels.", suffix=".json", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise

    # ----- public API -----

    def list(self) -> list[Channel]:
        return list(self._channels)

    def enabled_list(self) -> list[Channel]:
        return [c for c in self._channels if c.enabled]

    def get_by_username(self, username: str) -> Optional[Channel]:
        u = username.lstrip("@").lower()
        for c in self._channels:
            if c.username.lstrip("@").lower() == u:
                return c
        return None

    def add(self, *, id: int, username: str, title: str, enabled: bool = True) -> Channel:
        existing = self.get_by_username(username)
        if existing is not None:
            existing.id = id
            existing.title = title
            existing.enabled = enabled
            self._save()
            return existing
        ch = Channel(
            id=id,
            username=username if username.startswith("@") else f"@{username}",
            title=title,
            enabled=enabled,
            added_at=_now_iso(),
        )
        self._channels.append(ch)
        self._save()
        return ch

    def remove(self, channel_id: int) -> bool:
        before = len(self._channels)
        self._channels = [c for c in self._channels if c.id != channel_id]
        if len(self._channels) < before:
            self._save()
            return True
        return False

    def set_enabled(self, channel_id: int, enabled: bool) -> bool:
        for c in self._channels:
            if c.id == channel_id:
                c.enabled = enabled
                self._save()
                return True
        return False

    def ensure_default_if_empty(self) -> None:
        """If the store is empty, register the default test channel (unverified id=0)."""
        if self._channels:
            return
        self.add(
            id=0,  # will be resolved on first connect
            username=self.DEFAULT_TEST_CHANNEL,
            title="키움증권 미국주식 톡톡",
            enabled=True,
        )
