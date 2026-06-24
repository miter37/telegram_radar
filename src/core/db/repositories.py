"""CRUD repositories for the 5 Phase 0 tables.

Pure functions over a sqlite3.Connection; no business logic.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


def raw_hash(text: str) -> str:
    """sha256 of normalized text (cross-channel dedupe)."""
    norm = " ".join((text or "").split()).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- feed_items ----------

@dataclass
class FeedItem:
    id: int
    datetime: str
    channel_name: str
    channel_id: int
    message_text: str
    message_url: Optional[str]
    raw_hash: str
    collected_at: str


def insert_feed(
    conn: sqlite3.Connection,
    *,
    datetime: str,
    channel_name: str,
    channel_id: int,
    message_text: str,
    message_url: Optional[str],
) -> Optional[int]:
    """Insert a feed_item. Returns id, or None if duplicate (raw_hash UNIQUE)."""
    h = raw_hash(message_text)
    try:
        cur = conn.execute(
            """
            INSERT INTO feed_items
                (datetime, channel_name, channel_id, message_text, message_url, raw_hash, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (datetime, channel_name, channel_id, message_text, message_url, h, now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def get_feed(conn: sqlite3.Connection, feed_id: int) -> Optional[FeedItem]:
    row = conn.execute(
        "SELECT * FROM feed_items WHERE id = ?", (feed_id,)
    ).fetchone()
    if row is None:
        return None
    return FeedItem(
        id=row["id"],
        datetime=row["datetime"],
        channel_name=row["channel_name"],
        channel_id=row["channel_id"],
        message_text=row["message_text"],
        message_url=row["message_url"],
        raw_hash=row["raw_hash"],
        collected_at=row["collected_at"],
    )


# ---------- llm_extractions ----------

def insert_llm_extraction(
    conn: sqlite3.Connection,
    *,
    feed_id: int,
    prompt_version: str,
    raw_json: str,
    parsed_ok: bool,
    error_message: Optional[str] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO llm_extractions
            (feed_id, prompt_version, raw_json, parsed_ok, error_message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (feed_id, prompt_version, raw_json, 1 if parsed_ok else 0, error_message, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


# ---------- feed_signals ----------

@dataclass
class FeedSignal:
    id: int
    feed_id: int
    date: str
    channel_name: str
    topic: str
    main_content: str
    importance_score: int
    interest_score: int
    should_alert: bool
    created_at: str
    tags: list[str] = field(default_factory=list)


def insert_feed_signal(
    conn: sqlite3.Connection,
    *,
    feed_id: int,
    date: str,
    channel_name: str,
    topic: str,
    main_content: str,
    importance_score: int,
    interest_score: int,
    should_alert: bool,
) -> int:
    cur = conn.execute(
        """
        INSERT OR REPLACE INTO feed_signals
            (feed_id, date, channel_name, topic, main_content,
             importance_score, interest_score, should_alert, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feed_id,
            date,
            channel_name,
            topic,
            main_content,
            importance_score,
            interest_score,
            1 if should_alert else 0,
            now_iso(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_signals(
    conn: sqlite3.Connection,
    *,
    limit: int = 500,
    offset: int = 0,
    sort: str = "id_desc",
    importance_min: int | None = None,
    interest_min: int | None = None,
    channel: str | None = None,
    topic_substr: str | None = None,
    should_alert: bool | None = None,
    text_search: str | None = None,
) -> list[FeedSignal]:
    """List recent signals with aggregated tags and filters.

    sort: id_desc | id_asc | importance_desc | interest_desc | channel | topic
    """
    where = []
    params: list = []
    if importance_min is not None:
        where.append("s.importance_score >= ?")
        params.append(importance_min)
    if interest_min is not None:
        where.append("s.interest_score >= ?")
        params.append(interest_min)
    if channel:
        where.append("s.channel_name = ?")
        params.append(channel)
    if topic_substr:
        where.append("s.topic LIKE ?")
        params.append(f"%{topic_substr}%")
    if should_alert is not None:
        where.append("s.should_alert = ?")
        params.append(1 if should_alert else 0)
    if text_search:
        where.append(
            "(s.topic LIKE ? OR s.main_content LIKE ? OR s.channel_name LIKE ? OR s.id IN "
            "(SELECT signal_id FROM signal_tags WHERE canonical_name LIKE ?))"
        )
        ts = f"%{text_search}%"
        params.extend([ts, ts, ts, ts])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order_map = {
        "id_desc": "s.id DESC",
        "id_asc": "s.id ASC",
        "importance_desc": "s.importance_score DESC, s.id DESC",
        "interest_desc": "s.interest_score DESC, s.id DESC",
        "channel": "s.channel_name ASC, s.id DESC",
        "topic": "s.topic ASC, s.id DESC",
    }
    order_sql = order_map.get(sort, order_map["id_desc"])

    sql = f"""
        SELECT s.*, GROUP_CONCAT(st.canonical_name, '|') AS tag_names
        FROM feed_signals s
        LEFT JOIN signal_tags st ON st.signal_id = s.id
        {where_sql}
        GROUP BY s.id
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    out: list[FeedSignal] = []
    for row in rows:
        tags = [t for t in (row["tag_names"] or "").split("|") if t]
        out.append(
            FeedSignal(
                id=row["id"],
                feed_id=row["feed_id"],
                date=row["date"],
                channel_name=row["channel_name"],
                topic=row["topic"],
                main_content=row["main_content"],
                importance_score=row["importance_score"],
                interest_score=row["interest_score"],
                should_alert=bool(row["should_alert"]),
                created_at=row["created_at"],
                tags=tags,
            )
        )
    return out


# ---------- canonical_tags ----------

def upsert_canonical_tag(
    conn: sqlite3.Connection,
    *,
    canonical_name: str,
    tag_group: str,
    aliases: Optional[list[str]] = None,
) -> int:
    """Insert or no-op. Returns existing or new id."""
    row = conn.execute(
        "SELECT id FROM canonical_tags WHERE canonical_name = ?", (canonical_name,)
    ).fetchone()
    if row is not None:
        return row["id"]
    aliases_json = json.dumps(aliases or [], ensure_ascii=False)
    cur = conn.execute(
        """
        INSERT INTO canonical_tags (canonical_name, tag_group, aliases, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (canonical_name, tag_group, aliases_json, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


# ---------- signal_tags ----------

def insert_signal_tag(
    conn: sqlite3.Connection,
    *,
    feed_id: int,
    signal_id: int,
    canonical_tag_id: int,
    canonical_name: str,
    tag_group: str,
    confidence: Optional[float] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO signal_tags
            (feed_id, signal_id, canonical_tag_id, canonical_name, tag_group,
             normalize_confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feed_id,
            signal_id,
            canonical_tag_id,
            canonical_name,
            tag_group,
            confidence,
            now_iso(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def distinct_channels(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT channel_name FROM feed_signals
        ORDER BY channel_name
    """).fetchall()
    return [r["channel_name"] for r in rows]


def fts_search_feed_text(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 100,
) -> list[int]:
    """Return feed_ids matching FTS5 query.

    Supports:
    - simple term: "HBM" → rows containing HBM
    - multiple terms (AND): "HBM SK하이닉스" → both
    - prefix: "HBM*" → starts with HBM
    - phrase: '"HBM 테스트"' → exact phrase
    """
    if not query.strip():
        return []
    q = query.strip()
    # escape double quotes for phrase
    rows = conn.execute(
        "SELECT rowid FROM feed_items_fts WHERE message_text MATCH ? LIMIT ?",
        (q, limit),
    ).fetchall()
    return [r["rowid"] for r in rows]


def fts_search_signals(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 200,
) -> list[int]:
    """Return feed_signals.id whose source feed matches FTS5 query.

    Used by 'raw text search' to find signals whose original Telegram message
    matches the query.
    """
    if not query.strip():
        return []
    feed_ids = fts_search_feed_text(conn, query, limit=limit)
    if not feed_ids:
        return []
    placeholders = ",".join("?" * len(feed_ids))
    rows = conn.execute(
        f"SELECT id FROM feed_signals WHERE feed_id IN ({placeholders})",
        feed_ids,
    ).fetchall()
    return [r["id"] for r in rows]


def fts_count(conn: sqlite3.Connection) -> int:
    """Number of rows in the FTS5 index (== feed_items row count)."""
    row = conn.execute("SELECT COUNT(*) AS c FROM feed_items_fts").fetchone()
    return int(row["c"]) if row else 0


# ---------- ingest state (per-channel last seen message) ----------

def get_last_seen(
    conn: sqlite3.Connection,
    channel_id: int,
) -> int:
    """Return the last_message_id we have ingested for this channel.

    Returns 0 if never ingested.
    """
    row = conn.execute(
        "SELECT last_message_id FROM ingest_state WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    return int(row["last_message_id"]) if row else 0


def set_last_seen(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    channel_username: Optional[str],
    last_message_id: int,
    total_fetched: int = 0,
) -> None:
    """Upsert the last seen message id for a channel."""
    conn.execute("""
        INSERT INTO ingest_state
            (channel_id, channel_username, last_message_id, last_fetched_at, total_fetched)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            channel_username = COALESCE(excluded.channel_username, channel_username),
            last_message_id = MAX(last_message_id, excluded.last_message_id),
            last_fetched_at = excluded.last_fetched_at,
            total_fetched = total_fetched + excluded.total_fetched
    """, (
        channel_id,
        channel_username,
        last_message_id,
        now_iso(),
        total_fetched,
    ))
    conn.commit()


def list_ingest_state(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT channel_id, channel_username, last_message_id, last_fetched_at, total_fetched
        FROM ingest_state
        ORDER BY last_fetched_at DESC
    """).fetchall()
    return [dict(r) for r in rows]


def reset_ingest_state(conn: sqlite3.Connection, channel_id: Optional[int] = None) -> int:
    """Reset last_message_id to 0 so next start re-fetches all history.

    If channel_id is None, reset all.
    Returns number of rows reset.
    """
    if channel_id is None:
        cur = conn.execute("DELETE FROM ingest_state")
        return cur.rowcount
    cur = conn.execute(
        "DELETE FROM ingest_state WHERE channel_id = ?", (channel_id,)
    )
    return cur.rowcount


# ---------- daily topic clusters (Stage 1) ----------

@dataclass
class TopicCluster:
    report_date: str
    topic_idx: int
    label: str
    member_signal_ids: list[int]
    member_count: int
    avg_importance: float
    avg_interest: float


def upsert_topic_cluster(
    conn: sqlite3.Connection,
    *,
    report_date: str,
    topic_idx: int,
    label: str,
    member_signal_ids: list[int],
    avg_importance: float,
    avg_interest: float,
) -> int:
    """Insert or replace a Stage-1 cluster row."""
    ids_csv = ",".join(str(i) for i in member_signal_ids)
    conn.execute("""
        INSERT INTO daily_topic_clusters
            (report_date, topic_idx, label, member_signal_ids, member_count,
             avg_importance, avg_interest, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_date, topic_idx) DO UPDATE SET
            label=excluded.label,
            member_signal_ids=excluded.member_signal_ids,
            member_count=excluded.member_count,
            avg_importance=excluded.avg_importance,
            avg_interest=excluded.avg_interest
    """, (
        report_date, topic_idx, label, ids_csv, len(member_signal_ids),
        avg_importance, avg_interest, now_iso(),
    ))
    conn.commit()
    return conn.execute(
        "SELECT id FROM daily_topic_clusters WHERE report_date=? AND topic_idx=?",
        (report_date, topic_idx),
    ).fetchone()["id"]


def clear_topic_clusters(conn: sqlite3.Connection, report_date: str) -> int:
    cur = conn.execute(
        "DELETE FROM daily_topic_clusters WHERE report_date = ?", (report_date,)
    )
    conn.commit()
    return cur.rowcount


def list_topic_clusters(conn: sqlite3.Connection, report_date: str) -> list[TopicCluster]:
    rows = conn.execute("""
        SELECT report_date, topic_idx, label, member_signal_ids, member_count,
               avg_importance, avg_interest
        FROM daily_topic_clusters
        WHERE report_date = ?
        ORDER BY topic_idx ASC
    """, (report_date,)).fetchall()
    out = []
    for r in rows:
        ids = [int(x) for x in (r["member_signal_ids"] or "").split(",") if x]
        out.append(TopicCluster(
            report_date=r["report_date"],
            topic_idx=r["topic_idx"],
            label=r["label"],
            member_signal_ids=ids,
            member_count=r["member_count"],
            avg_importance=r["avg_importance"] or 0.0,
            avg_interest=r["avg_interest"] or 0.0,
        ))
    return out


# ---------- daily topic reports (Stage 2) ----------

def upsert_topic_report(
    conn: sqlite3.Connection,
    *,
    report_date: str,
    topic_idx: int,
    label: str,
    summary: str,
    body_md: str,
    timeline: list[dict],
    watchlist: list[str],
    member_count: int,
    avg_importance: float,
    avg_interest: float,
    top_signal_ids: list[int],
    md_path: Optional[str],
    prompt_version: str,
) -> int:
    conn.execute("""
        INSERT INTO daily_topic_reports
            (report_date, topic_idx, label, summary, body_md, timeline_json,
             watchlist_json, member_count, avg_importance, avg_interest,
             top_signal_ids, md_path, prompt_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_date, topic_idx) DO UPDATE SET
            label=excluded.label, summary=excluded.summary, body_md=excluded.body_md,
            timeline_json=excluded.timeline_json, watchlist_json=excluded.watchlist_json,
            member_count=excluded.member_count, avg_importance=excluded.avg_importance,
            avg_interest=excluded.avg_interest, top_signal_ids=excluded.top_signal_ids,
            md_path=excluded.md_path, prompt_version=excluded.prompt_version,
            created_at=excluded.created_at
    """, (
        report_date, topic_idx, label, summary, body_md,
        json.dumps(timeline, ensure_ascii=False),
        json.dumps(watchlist, ensure_ascii=False),
        member_count, avg_importance, avg_interest,
        ",".join(str(i) for i in top_signal_ids),
        md_path, prompt_version, now_iso(),
    ))
    conn.commit()
    return conn.execute(
        "SELECT id FROM daily_topic_reports WHERE report_date=? AND topic_idx=?",
        (report_date, topic_idx),
    ).fetchone()["id"]


def clear_topic_reports(conn: sqlite3.Connection, report_date: str) -> int:
    cur = conn.execute(
        "DELETE FROM daily_topic_reports WHERE report_date = ?", (report_date,)
    )
    conn.commit()
    return cur.rowcount


def list_topic_reports(conn: sqlite3.Connection, report_date: str) -> list[dict]:
    rows = conn.execute("""
        SELECT report_date, topic_idx, label, summary, body_md, timeline_json,
               watchlist_json, member_count, avg_importance, avg_interest,
               top_signal_ids, md_path, prompt_version, created_at
        FROM daily_topic_reports
        WHERE report_date = ?
        ORDER BY topic_idx ASC
    """, (report_date,)).fetchall()
    out = []
    for r in rows:
        try:
            timeline = json.loads(r["timeline_json"] or "[]")
        except Exception:
            timeline = []
        try:
            watchlist = json.loads(r["watchlist_json"] or "[]")
        except Exception:
            watchlist = []
        top_ids = [int(x) for x in (r["top_signal_ids"] or "").split(",") if x]
        out.append({
            "report_date": r["report_date"],
            "topic_idx": r["topic_idx"],
            "label": r["label"],
            "summary": r["summary"],
            "body_md": r["body_md"],
            "timeline": timeline,
            "watchlist": watchlist,
            "member_count": r["member_count"],
            "avg_importance": r["avg_importance"] or 0.0,
            "avg_interest": r["avg_interest"] or 0.0,
            "top_signal_ids": top_ids,
            "md_path": r["md_path"],
            "prompt_version": r["prompt_version"],
            "created_at": r["created_at"],
        })
    return out


def list_topic_report_dates(conn: sqlite3.Connection, *, limit: int = 60) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT report_date FROM daily_topic_reports
        ORDER BY report_date DESC LIMIT ?
    """, (limit,)).fetchall()
    return [r["report_date"] for r in rows]


# ---------- aggregate stats ----------

def stats(conn: sqlite3.Connection) -> dict:
    """Quick stats for status bar."""
    out = {}
    for label, table in [
        ("feeds", "feed_items"),
        ("signals", "feed_signals"),
        ("llm_ok", "llm_extractions"),
        ("llm_fail", "llm_extractions"),
        ("tags", "canonical_tags"),
    ]:
        pass
    out["feeds"] = conn.execute("SELECT COUNT(*) c FROM feed_items").fetchone()["c"]
    out["signals"] = conn.execute("SELECT COUNT(*) c FROM feed_signals").fetchone()["c"]
    out["llm_ok"] = conn.execute(
        "SELECT COUNT(*) c FROM llm_extractions WHERE parsed_ok=1"
    ).fetchone()["c"]
    out["llm_fail"] = conn.execute(
        "SELECT COUNT(*) c FROM llm_extractions WHERE parsed_ok=0"
    ).fetchone()["c"]
    out["tags"] = conn.execute("SELECT COUNT(*) c FROM canonical_tags").fetchone()["c"]
    total = out["llm_ok"] + out["llm_fail"]
    out["llm_ok_pct"] = (out["llm_ok"] / total * 100.0) if total > 0 else 0.0
    return out
