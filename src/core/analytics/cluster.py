"""Topic clustering: simple keyword-based grouping of similar feeds.

For Phase 2.1 we use a pragmatic approach:
- Group by topic name (case-insensitive) when LLM gives a stable topic.
- Optionally merge topics with high tag overlap (Jaccard >= 0.5).
- Update topic_clusters with first/last seen, feed_count, cluster_score.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Iterable

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)


def _tokens(s: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(s or "") if len(t) > 1}


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def recompute_topic_clusters(
    conn: sqlite3.Connection,
    *,
    jaccard_threshold: float = 0.5,
) -> int:
    """Group feed_signals by topic + tag overlap; update topic_clusters.

    Strategy:
    1. Pull all feed_signals with their tags.
    2. For each row, build (topic, tags). If topic exists already, add to it.
       Else find existing cluster with same/overlapping topic and tags; if
       Jaccard(tags) >= threshold or topic matches, merge; else create new.
    3. Update feed_count, first_seen_at, last_seen_at, cluster_score.
    """
    rows = conn.execute("""
        SELECT s.id AS signal_id, s.date, s.topic, s.importance_score, s.interest_score
        FROM feed_signals s
        ORDER BY s.date ASC
    """).fetchall()

    if not rows:
        return 0

    tag_rows = conn.execute("""
        SELECT signal_id, canonical_name, tag_group FROM signal_tags
    """).fetchall()
    sig_tags: dict[int, list[str]] = defaultdict(list)
    for r in tag_rows:
        sig_tags[r["signal_id"]].append(r["canonical_name"])

    clusters: list[dict] = []  # {id, topic, tag_set, first_seen, last_seen, count, score, members}
    next_id_hint = conn.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 AS n FROM topic_clusters"
    ).fetchone()["n"]

    def find_match(topic: str, tags: list[str]):
        t_norm = (topic or "").strip()
        for c in clusters:
            if c["topic"] == t_norm and t_norm:
                return c
            if jaccard(tags, c["tag_set"]) >= jaccard_threshold:
                return c
        return None

    for r in rows:
        sig_id = r["signal_id"]
        tags = sig_tags.get(sig_id, [])
        match = find_match(r["topic"], tags)
        if match is None:
            c = {
                "id_hint": next_id_hint,
                "topic": (r["topic"] or "(unknown)").strip(),
                "tag_set": set(tags),
                "first_seen": r["date"],
                "last_seen": r["date"],
                "count": 1,
                "score": float(r["importance_score"] + r["interest_score"]),
                "members": [sig_id],
            }
            clusters.append(c)
            next_id_hint += 1
        else:
            match["tag_set"].update(tags)
            match["count"] += 1
            match["score"] += float(r["importance_score"] + r["interest_score"])
            if r["date"] < match["first_seen"]:
                match["first_seen"] = r["date"]
            if r["date"] > match["last_seen"]:
                match["last_seen"] = r["date"]
            match["members"].append(sig_id)

    # Now persist
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    # Try to match against existing DB clusters by topic name
    existing = conn.execute("SELECT id, topic FROM topic_clusters").fetchall()
    by_topic: dict[str, int] = {r["topic"]: r["id"] for r in existing}

    inserted = 0
    for c in clusters:
        if c["topic"] in by_topic:
            cid = by_topic[c["topic"]]
            conn.execute("""
                UPDATE topic_clusters
                SET canonical_tags = ?, first_seen_at = ?, last_seen_at = ?,
                    feed_count = ?, cluster_score = ?, updated_at = ?
                WHERE id = ?
            """, (
                ",".join(sorted(c["tag_set"])),
                c["first_seen"], c["last_seen"],
                c["count"], c["score"], now, cid,
            ))
        else:
            cur = conn.execute("""
                INSERT INTO topic_clusters
                    (topic, canonical_tags, first_seen_at, last_seen_at, feed_count, cluster_score, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                c["topic"],
                ",".join(sorted(c["tag_set"])),
                c["first_seen"], c["last_seen"],
                c["count"], c["score"], now, now,
            ))
            by_topic[c["topic"]] = cur.lastrowid
            inserted += 1
    conn.commit()
    return inserted


def list_clusters(conn: sqlite3.Connection, *, limit: int = 100) -> list[dict]:
    rows = conn.execute("""
        SELECT id, topic, canonical_tags, first_seen_at, last_seen_at, feed_count, cluster_score
        FROM topic_clusters
        ORDER BY last_seen_at DESC, cluster_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    out = []
    for r in rows:
        tags = [t for t in (r["canonical_tags"] or "").split(",") if t]
        out.append({
            "id": r["id"],
            "topic": r["topic"],
            "tags": tags,
            "first_seen": r["first_seen_at"],
            "last_seen": r["last_seen_at"],
            "feed_count": r["feed_count"],
            "cluster_score": r["cluster_score"],
        })
    return out
