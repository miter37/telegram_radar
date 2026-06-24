"""Narrative shift detection: identify when the conversation pivots.

Approach: compare the recent (last 24h) tag distribution and dominant topics
against the prior (24-72h) baseline. A 'shift' is a tag/topic that emerged
or spiked in the recent window after being absent or quiet before.

Outputs:
- new_topics: topics first seen in the last 24h
- rising_tags: tags whose velocity is in the top 20% in the last 24h but
  were below median in the prior 48h
- fading_tags: tags that were hot in the prior window but absent or low now
- topic_drift: list of (old_topic, new_topic) pairs that overlap in tag set
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class NarrativeShift:
    new_topics: list[dict]
    rising_tags: list[dict]
    fading_tags: list[dict]
    drift_pairs: list[tuple[str, str]]
    generated_at: str


def _period_split(
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime, datetime]:
    if now is None:
        now = datetime.now()
    recent_start = now - timedelta(hours=24)
    baseline_start = now - timedelta(hours=72)
    return recent_start, baseline_start, now


def detect_shifts(
    conn: sqlite3.Connection,
    *,
    now: Optional[datetime] = None,
) -> NarrativeShift:
    recent_start, baseline_start, now = _period_split(now)
    now_iso = now.isoformat(timespec="seconds")
    recent_iso = recent_start.isoformat(timespec="seconds")
    baseline_iso = baseline_start.isoformat(timespec="seconds")

    # gather topics
    recent_topics = conn.execute("""
        SELECT topic, COUNT(*) AS cnt, MAX(id) AS max_id
        FROM feed_signals
        WHERE created_at >= ?
        GROUP BY topic
        ORDER BY cnt DESC
    """, (recent_iso,)).fetchall()

    baseline_topics = conn.execute("""
        SELECT topic, COUNT(*) AS cnt
        FROM feed_signals
        WHERE created_at >= ? AND created_at < ?
        GROUP BY topic
    """, (baseline_iso, recent_iso)).fetchall()
    baseline_topic_count = {r["topic"]: r["cnt"] for r in baseline_topics}

    # new topics: in recent, not in baseline
    new_topics = [
        {"topic": r["topic"], "count": r["cnt"]}
        for r in recent_topics
        if r["topic"] not in baseline_topic_count
    ]

    # gather tags
    recent_tag_rows = conn.execute("""
        SELECT st.canonical_name AS tag, COUNT(*) AS cnt
        FROM signal_tags st
        JOIN feed_signals s ON s.id = st.signal_id
        WHERE s.created_at >= ?
        GROUP BY st.canonical_name
        ORDER BY cnt DESC
    """, (recent_iso,)).fetchall()
    recent_tag_count = {r["tag"]: r["cnt"] for r in recent_tag_rows}

    baseline_tag_rows = conn.execute("""
        SELECT st.canonical_name AS tag, COUNT(*) AS cnt
        FROM signal_tags st
        JOIN feed_signals s ON s.id = st.signal_id
        WHERE s.created_at >= ? AND s.created_at < ?
        GROUP BY st.canonical_name
    """, (baseline_iso, recent_iso)).fetchall()
    baseline_tag_count = {r["tag"]: r["cnt"] for r in baseline_tag_rows}

    # rising tags: top by velocity
    rising_tags: list[dict] = []
    fading_tags: list[dict] = []
    if recent_tag_count:
        median_recent = sorted(recent_tag_count.values())[len(recent_tag_count) // 2]
    else:
        median_recent = 0
    if baseline_tag_count:
        median_baseline = sorted(baseline_tag_count.values())[len(baseline_tag_count) // 2]
    else:
        median_baseline = 0

    all_tags = set(recent_tag_count) | set(baseline_tag_count)
    for tag in all_tags:
        r_cnt = recent_tag_count.get(tag, 0)
        b_cnt = baseline_tag_count.get(tag, 0)
        if r_cnt >= max(3, median_recent * 2) and b_cnt <= median_baseline:
            rising_tags.append({"tag": tag, "recent": r_cnt, "baseline": b_cnt})
        if b_cnt >= max(3, median_baseline * 2) and r_cnt <= median_recent:
            fading_tags.append({"tag": tag, "recent": r_cnt, "baseline": b_cnt})

    rising_tags.sort(key=lambda x: x["recent"], reverse=True)
    fading_tags.sort(key=lambda x: x["baseline"], reverse=True)

    # topic drift: pairs of topics sharing many tags
    topic_tag_rows = conn.execute("""
        SELECT s.topic, st.canonical_name AS tag
        FROM feed_signals s
        JOIN signal_tags st ON st.signal_id = s.id
        WHERE s.created_at >= ?
    """, (baseline_iso,)).fetchall()
    topic_tags: dict[str, set[str]] = defaultdict(set)
    for r in topic_tag_rows:
        topic_tags[r["topic"]].add(r["tag"])
    topics = list(topic_tags.keys())
    drift_pairs: list[tuple[str, str]] = []
    for i, t1 in enumerate(topics):
        for t2 in topics[i + 1: i + 5]:  # cap pairs to avoid blowup
            if t1 == t2:
                continue
            shared = topic_tags[t1] & topic_tags[t2]
            if len(shared) >= 2 and len(shared) / max(1, len(topic_tags[t1] | topic_tags[t2])) >= 0.4:
                drift_pairs.append((t1, t2))
        if len(drift_pairs) >= 5:
            break

    return NarrativeShift(
        new_topics=new_topics[:10],
        rising_tags=rising_tags[:10],
        fading_tags=fading_tags[:10],
        drift_pairs=drift_pairs[:5],
        generated_at=now_iso,
    )
