"""User interest profile: weighted interests for interest_score recalc."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class InterestEntry:
    name: str
    group: str = "industry"  # company|industry|person|topic
    weight: float = 1.0


class InterestProfile:
    """Load/save user interests as JSON with weights.

    Used by the LLM worker (passed as user_interests list, names only) and
    by InterestScorer (recomputes interest_score system-side from tags).
    """

    def __init__(self, path: Path):
        self.path = path
        self._entries: list[InterestEntry] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("interests", [])
            if isinstance(entries, list):
                self._entries = [
                    InterestEntry(
                        name=str(e.get("name", "")).strip(),
                        group=str(e.get("group", "industry")),
                        weight=float(e.get("weight", 1.0)),
                    )
                    for e in entries
                    if isinstance(e, dict) and e.get("name")
                ]
        except Exception:
            self._entries = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"interests": [asdict(e) for e in self._entries]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    # ----- public API -----

    def list(self) -> list[InterestEntry]:
        return list(self._entries)

    def names(self) -> list[str]:
        return [e.name for e in self._entries]

    def set(self, entries: Iterable[InterestEntry]) -> None:
        self._entries = list(entries)
        self._save()

    def add(self, entry: InterestEntry) -> None:
        for e in self._entries:
            if e.name == entry.name and e.group == entry.group:
                e.weight = entry.weight
                self._save()
                return
        self._entries.append(entry)
        self._save()

    def remove(self, name: str, group: str | None = None) -> bool:
        before = len(self._entries)
        self._entries = [
            e for e in self._entries
            if not (e.name == name and (group is None or e.group == group))
        ]
        if len(self._entries) < before:
            self._save()
            return True
        return False

    def update(self, name: str, group: str, weight: float) -> bool:
        for e in self._entries:
            if e.name == name and e.group == group:
                e.weight = weight
                self._save()
                return True
        return False
