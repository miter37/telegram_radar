"""Analytics: tag flow metrics, daily topic aggregation, velocity/acceleration."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

from ..db import connection, repositories

logger = logging.getLogger(__name__)


@dataclass
class TagFlowRow:
    tag_id: int
    canonical_name: str
    date: str
    feed_count: int
    avg_importance: float
    avg_interest: float
    velocity: float
    acceleration: float


@dataclass
class HeatCell:
    tag: str
    date: str
    value: float   # 0..100 heat
    change: float  # velocity


def aggregate_daily_signals(conn: sqlite3.Connection) -> list[tuple[str, int, float, float, set[str]]]:
    """Return per-date aggregates from feed_signals: (date, count, avg_imp, avg_int, tag_set).

    Used as the source of truth for tag_flow_metrics and daily_topics.
    """
    rows = conn.execute("""
        SELECT s.date,
               s.id AS signal_id,
               s.importance_score,
               s.interest_score,
               s.topic,
               s.main_content
        FROM feed_signals s
        ORDER BY s.date DESC
    """).fetchall()
    by_date: dict[str, list] = {}
    for r in rows:
        d = r["date"]
        by_date.setdefault(d, []).append(r)
    out = []
    for d, items in by_date.items():
        n = len(items)
        avg_imp = sum(x["importance_score"] for x in items) / max(n, 1)
        avg_int = sum(x["interest_score"] for x in items) / max(n, 1)
        # collect tags via signal_tags join
        ids = tuple(x["signal_id"] for x in items)
        placeholders = ",".join("?" * len(ids))
        tag_rows = conn.execute(
            f"SELECT DISTINCT canonical_name FROM signal_tags WHERE signal_id IN ({placeholders})",
            ids,
        ).fetchall() if ids else []
        tag_set = {t["canonical_name"] for t in tag_rows}
        out.append((d, n, avg_imp, avg_int, tag_set))
    return out


def recompute_tag_flow_metrics(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
) -> int:
    """Aggregate feed_signals per (date, tag) → tag_flow_metrics.

    Velocity = today - moving_avg_7d
    Acceleration = velocity_today - velocity_yesterday
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Wipe recent window and recompute
    conn.execute("DELETE FROM tag_flow_metrics WHERE date >= ?", (cutoff,))

    # collect per (date, canonical_name)
    rows = conn.execute("""
        SELECT s.date AS date,
               st.canonical_name AS tag,
               st.canonical_tag_id AS tag_id,
               COUNT(*) AS cnt,
               AVG(s.importance_score) AS avg_imp,
               AVG(s.interest_score) AS avg_int
        FROM feed_signals s
        JOIN signal_tags st ON st.signal_id = s.id
        WHERE s.date >= ?
        GROUP BY s.date, st.canonical_name
        ORDER BY s.date DESC
    """, (cutoff,)).fetchall()

    # also pre-fetch tag_ids
    tag_id_map: dict[str, int] = {}
    for r in rows:
        tag_id_map[r["tag"]] = r["tag_id"]

    # group by tag for velocity calc
    by_tag: dict[str, list[dict]] = {}
    for r in rows:
        by_tag.setdefault(r["tag"], []).append({
            "date": r["date"],
            "cnt": r["cnt"],
            "avg_imp": r["avg_imp"] or 0.0,
            "avg_int": r["avg_int"] or 0.0,
        })

    inserted = 0
    for tag, history in by_tag.items():
        history.sort(key=lambda x: x["date"])
        # 7-day MA for velocity
        for i, h in enumerate(history):
            window = history[max(0, i - 6):i + 1]
            ma = sum(w["cnt"] for w in window) / len(window)
            velocity = h["cnt"] - ma
            if i == 0:
                acceleration = 0.0
            else:
                # previous day's velocity (computed inline)
                prev_window = history[max(0, i - 7):i]
                if prev_window:
                    prev_ma = sum(w["cnt"] for w in prev_window) / len(prev_window)
                    prev_velocity = history[i - 1]["cnt"] - prev_ma
                else:
                    prev_velocity = 0.0
                acceleration = velocity - prev_velocity
            conn.execute("""
                INSERT INTO tag_flow_metrics
                    (tag_id, date, feed_count, avg_importance, avg_interest, velocity, acceleration, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tag_id_map.get(tag, 0),
                h["date"],
                h["cnt"],
                h["avg_imp"],
                h["avg_int"],
                velocity,
                acceleration,
                datetime.now().astimezone().isoformat(timespec="seconds"),
            ))
            inserted += 1
    conn.commit()
    return inserted


def get_heatmap(
    conn: sqlite3.Connection,
    *,
    days: int = 7,
    top_n: int = 12,
) -> tuple[list[str], list[str], list[HeatCell]]:
    """Build (date_columns, tag_rows, cells) for the heatmap.

    - top_n tags by total feed_count in the window
    - date columns = last `days` dates (oldest→newest left to right)
    - cell.value = avg(avg_importance, avg_interest) clipped 0..100
    """
    dates = conn.execute("""
        SELECT DISTINCT date FROM tag_flow_metrics
        ORDER BY date DESC LIMIT ?
    """, (days,)).fetchall()
    date_cols = [d["date"] for d in dates][::-1]  # oldest first

    # pick top N tags
    top_tags = conn.execute("""
        SELECT tag_id, SUM(feed_count) AS total
        FROM tag_flow_metrics
        GROUP BY tag_id
        ORDER BY total DESC
        LIMIT ?
    """, (top_n,)).fetchall()
    tag_ids = [t["tag_id"] for t in top_tags]
    if not tag_ids:
        return [], [], []
    name_rows = conn.execute(
        f"SELECT id, canonical_name FROM canonical_tags WHERE id IN ({','.join('?' * len(tag_ids))})",
        tag_ids,
    ).fetchall()
    name_map = {r["id"]: r["canonical_name"] for r in name_rows}
    tag_rows = [name_map.get(tid, f"#{tid}") for tid in tag_ids]

    cells: list[HeatCell] = []
    for tag_id in tag_ids:
        tag = name_map.get(tag_id, f"#{tag_id}")
        # newest velocity for change indicator
        newest = conn.execute("""
            SELECT velocity FROM tag_flow_metrics
            WHERE tag_id = ? ORDER BY date DESC LIMIT 1
        """, (tag_id,)).fetchone()
        change = newest["velocity"] if newest else 0.0
        for d in date_cols:
            r = conn.execute("""
                SELECT avg_importance, avg_interest, feed_count, velocity
                FROM tag_flow_metrics
                WHERE tag_id = ? AND date = ?
            """, (tag_id, d)).fetchone()
            if r is None:
                cells.append(HeatCell(tag=tag, date=d, value=0.0, change=change))
            else:
                v = (r["avg_importance"] + r["avg_interest"]) / 2.0
                cells.append(HeatCell(tag=tag, date=d, value=v, change=change))
    return date_cols, tag_rows, cells


def list_top_tags_today(
    conn: sqlite3.Connection,
    *,
    limit: int = 5,
) -> list[dict]:
    """Top tags by today's feed_count, with avg importance/interest."""
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT t.canonical_name AS tag,
               SUM(tm.feed_count) AS cnt,
               AVG(tm.avg_importance) AS avg_imp,
               AVG(tm.avg_interest) AS avg_int
        FROM tag_flow_metrics tm
        JOIN canonical_tags t ON t.id = tm.tag_id
        WHERE tm.date = ?
        GROUP BY t.canonical_name
        ORDER BY cnt DESC
        LIMIT ?
    """, (today, limit)).fetchall()
    return [
        {
            "tag": r["tag"],
            "count": r["cnt"],
            "avg_importance": r["avg_imp"] or 0.0,
            "avg_interest": r["avg_int"] or 0.0,
        }
        for r in rows
    ]


def list_recent_dates(
    conn: sqlite3.Connection,
    *,
    limit: int = 30,
) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT date FROM feed_signals
        ORDER BY date DESC LIMIT ?
    """, (limit,)).fetchall()
    return [r["date"] for r in rows]


def get_daily_summary(
    conn: sqlite3.Connection,
    date: str,
    *,
    top_n: int = 5,
) -> list[dict]:
    """Per-date topic summary: grouped by topic, ranked by total_score."""
    rows = conn.execute("""
        SELECT s.topic,
               s.main_content,
               s.importance_score,
               s.interest_score,
               s.id AS signal_id
        FROM feed_signals s
        WHERE s.date = ?
        ORDER BY s.importance_score + s.interest_score DESC
    """, (date,)).fetchall()
    by_topic: dict[str, dict] = {}
    for r in rows:
        t = r["topic"] or "(unknown)"
        b = by_topic.setdefault(t, {
            "topic": t,
            "main_content": r["main_content"],
            "total_score": 0.0,
            "feed_count": 0,
            "avg_importance": 0.0,
            "avg_interest": 0.0,
            "representative_feed_ids": [],
            "tags": set(),
        })
        b["feed_count"] += 1
        b["total_score"] += r["importance_score"] + r["interest_score"]
        b["avg_importance"] += r["importance_score"]
        b["avg_interest"] += r["interest_score"]
        b["representative_feed_ids"].append(r["signal_id"])
        # tags
        tag_rows = conn.execute(
            "SELECT canonical_name FROM signal_tags WHERE signal_id = ?", (r["signal_id"],)
        ).fetchall()
        for tr in tag_rows:
            b["tags"].add(tr["canonical_name"])
    out = []
    for t, b in by_topic.items():
        if b["feed_count"]:
            b["avg_importance"] /= b["feed_count"]
            b["avg_interest"] /= b["feed_count"]
        b["tags"] = sorted(b["tags"])
        out.append(b)
    out.sort(key=lambda x: x["total_score"], reverse=True)
    return out[:top_n]


def store_daily_topics(
    conn: sqlite3.Connection,
    date: str,
    *,
    top_n: int = 5,
) -> int:
    """Compute and write daily_topics for the given date.

    Creates a topic_cluster on the fly keyed by topic name (simplified).
    """
    summaries = get_daily_summary(conn, date, top_n=top_n)
    # ensure one cluster per topic
    for s in summaries:
        existing = conn.execute(
            "SELECT id FROM topic_clusters WHERE topic = ?", (s["topic"],)
        ).fetchone()
        if existing is None:
            cur = conn.execute("""
                INSERT INTO topic_clusters
                    (topic, canonical_tags, first_seen_at, last_seen_at, feed_count, cluster_score, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                s["topic"],
                ",".join(s["tags"]),
                date, date,
                s["feed_count"],
                s["total_score"],
                datetime.now().astimezone().isoformat(timespec="seconds"),
                datetime.now().astimezone().isoformat(timespec="seconds"),
            ))
            cluster_id = cur.lastrowid
        else:
            cluster_id = existing["id"]
            conn.execute("""
                UPDATE topic_clusters
                SET last_seen_at = ?, feed_count = feed_count + ?, cluster_score = cluster_score + ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                date, s["feed_count"], s["total_score"],
                datetime.now().astimezone().isoformat(timespec="seconds"),
                cluster_id,
            ))
        # daily_topics: replace if exists
        rep_ids = ",".join(str(x) for x in s["representative_feed_ids"])
        conn.execute("DELETE FROM daily_topics WHERE date = ? AND topic_cluster_id = ?", (date, cluster_id))
        conn.execute("""
            INSERT INTO daily_topics
                (date, topic_cluster_id, daily_rank, summary, representative_feed_ids, total_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            date, cluster_id, 0,
            s["main_content"],
            rep_ids,
            s["total_score"],
            datetime.now().astimezone().isoformat(timespec="seconds"),
        ))
    conn.commit()
    return len(summaries)


def list_daily_topics(
    conn: sqlite3.Connection,
    date: str,
) -> list[dict]:
    rows = conn.execute("""
        SELECT dt.id, dt.daily_rank, dt.summary, dt.representative_feed_ids, dt.total_score,
               tc.topic, tc.canonical_tags
        FROM daily_topics dt
        JOIN topic_clusters tc ON tc.id = dt.topic_cluster_id
        WHERE dt.date = ?
        ORDER BY dt.total_score DESC
    """, (date,)).fetchall()
    out = []
    for r in rows:
        tags = [t for t in (r["canonical_tags"] or "").split(",") if t]
        rep_ids = [int(x) for x in (r["representative_feed_ids"] or "").split(",") if x]
        out.append({
            "id": r["id"],
            "rank": r["daily_rank"],
            "summary": r["summary"],
            "feed_ids": rep_ids,
            "total_score": r["total_score"],
            "topic": r["topic"],
            "tags": tags,
        })
    return out


def list_distinct_topics(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[str]:
    rows = conn.execute("""
        SELECT topic, COUNT(*) AS cnt FROM feed_signals
        GROUP BY topic ORDER BY cnt DESC LIMIT ?
    """, (limit,)).fetchall()
    return [r["topic"] for r in rows]
