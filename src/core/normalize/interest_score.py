"""Interest scorer: recompute interest_score from canonical_tags + interests."""

from __future__ import annotations

import logging
import sqlite3
from typing import Iterable

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def compute_interest(
    tag_names: Iterable[str],
    interests,  # list[InterestEntry]
) -> int:
    """Return interest_score (0-100) for a feed given its tag names."""
    if not interests:
        return 50  # neutral
    tags_norm = {_norm(t) for t in tag_names}
    if not tags_norm:
        return 50
    # sum weighted hits, normalize against total weight
    total = sum(e.weight for e in interests)
    if total <= 0:
        return 50
    matched = 0.0
    for e in interests:
        n = _norm(e.name)
        if not n:
            continue
        # direct match
        if n in tags_norm:
            matched += e.weight
            continue
        # partial: tag contains interest or vice versa
        for t in tags_norm:
            if n in t or t in n:
                matched += e.weight * 0.5
                break
    raw = matched / total
    # raw 0 → 0, raw 1 → 100
    score = int(round(max(0.0, min(1.0, raw)) * 100))
    return score


def recompute_for_signal(
    conn: sqlite3.Connection,
    signal_id: int,
    interests,
) -> int:
    rows = conn.execute(
        "SELECT canonical_name FROM signal_tags WHERE signal_id = ?", (signal_id,)
    ).fetchall()
    names = [r["canonical_name"] for r in rows]
    score = compute_interest(names, interests)
    conn.execute(
        "UPDATE feed_signals SET interest_score = ? WHERE id = ?",
        (score, signal_id),
    )
    return score


def recompute_all(conn: sqlite3.Connection, interests) -> int:
    rows = conn.execute("SELECT id FROM feed_signals").fetchall()
    n = 0
    for r in rows:
        recompute_for_signal(conn, r["id"], interests)
        n += 1
    conn.commit()
    return n
