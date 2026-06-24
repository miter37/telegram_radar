"""Alert engine: importance>=X AND interest>=Y with cooldown per topic cluster."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AlertDecision:
    should_alert: bool
    reason: str
    criteria: dict


def load_criteria(path: Path) -> dict:
    """Load alert criteria from settings/alerts.json."""
    if not path.exists():
        return {
            "importance_min": 80,
            "interest_min": 70,
            "cooldown_minutes": 30,
            "exclude_channels": [],
        }
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "importance_min": 80,
            "interest_min": 70,
            "cooldown_minutes": 30,
            "exclude_channels": [],
        }


def save_criteria(path: Path, criteria: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(criteria, f, ensure_ascii=False, indent=2)


def decide(
    *,
    importance: int,
    interest: int,
    topic: str,
    channel: str,
    criteria: dict,
    last_alert_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> AlertDecision:
    if now is None:
        now = datetime.now()
    if channel in (criteria.get("exclude_channels") or []):
        return AlertDecision(False, "제외 채널", criteria)
    if importance < criteria.get("importance_min", 80):
        return AlertDecision(False, f"importance {importance} < {criteria['importance_min']}", criteria)
    if interest < criteria.get("interest_min", 70):
        return AlertDecision(False, f"interest {interest} < {criteria['interest_min']}", criteria)
    cooldown = criteria.get("cooldown_minutes", 30)
    if last_alert_at is not None:
        delta = (now - last_alert_at).total_seconds() / 60.0
        if delta < cooldown:
            return AlertDecision(False, f"cooldown ({delta:.0f}m < {cooldown}m)", criteria)
    return AlertDecision(True, "criteria met", criteria)


def last_alert_for_topic(conn: sqlite3.Connection, topic: str) -> Optional[datetime]:
    """Return the created_at of the most recent should_alert=1 signal with this topic."""
    row = conn.execute("""
        SELECT created_at FROM feed_signals
        WHERE topic = ? AND should_alert = 1
        ORDER BY id DESC LIMIT 1
    """, (topic,)).fetchone()
    if row is None:
        return None
    try:
        return datetime.fromisoformat(row["created_at"])
    except Exception:
        return None
