"""Reprocess failed / unanalyzed feeds through LLM."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Iterable, Optional

from .db import connection, repositories
from .llm.extractor import LLMExtractor
from .llm.prompts import CURRENT_VERSION
from .normalize.tags import TagNormalizer

logger = logging.getLogger(__name__)


def list_failed_feed_ids(conn: sqlite3.Connection, *, limit: int = 500) -> list[int]:
    """Return feed_ids whose latest extraction attempt failed (parsed_ok=0)."""
    rows = conn.execute("""
        SELECT feed_id FROM llm_extractions
        WHERE parsed_ok = 0
        GROUP BY feed_id
        ORDER BY MAX(id) DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [r["feed_id"] for r in rows]


def list_pending_feed_ids(conn: sqlite3.Connection, *, limit: int = 500) -> list[int]:
    """Return feed_ids that have no feed_signals yet."""
    rows = conn.execute("""
        SELECT f.id FROM feed_items f
        LEFT JOIN feed_signals s ON s.feed_id = f.id
        WHERE s.id IS NULL
        ORDER BY f.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [r["feed_id"] if "feed_id" in rows else r["id"] for r in rows]


async def reprocess_feeds(
    *,
    feed_ids: list[int],
    db_path,
    llm_cfg,
    interests_provider,
    on_signal=None,  # callable(FeedSignal)
) -> dict:
    """Re-run LLM extraction for the given feed_ids.

    Returns {ok: int, fail: int, skipped: int}.
    """
    extractor = LLMExtractor(
        base_url=llm_cfg.base_url,
        api_key=llm_cfg.api_key,
        model=llm_cfg.model,
    )
    normalizer: Optional[TagNormalizer] = None
    ok = 0
    fail = 0
    skipped = 0
    try:
        for fid in feed_ids:
            conn = connection.get_connection(db_path)
            row = conn.execute(
                "SELECT * FROM feed_items WHERE id = ?", (fid,)
            ).fetchone()
            if row is None:
                skipped += 1
                continue
            # delete prior signal for this feed (if any) so we get a fresh one
            conn.execute("DELETE FROM feed_signals WHERE feed_id = ?", (fid,))
            interests = interests_provider() or []
            res = await extractor.extract(
                datetime=row["datetime"],
                channel_name=row["channel_name"],
                message_text=row["message_text"],
                message_url=row["message_url"],
                user_interests=interests,
            )
            repositories.insert_llm_extraction(
                conn,
                feed_id=fid,
                prompt_version=res.prompt_version,
                raw_json=res.raw_text or json.dumps({"error": res.error}, ensure_ascii=False),
                parsed_ok=res.ok,
                error_message=None if res.ok else res.error,
            )
            if not res.ok or not res.payload:
                fail += 1
                continue
            p = res.payload
            importance = int(p.get("importance_score", 0))
            interest = int(p.get("interest_score", 0))
            sig_id = repositories.insert_feed_signal(
                conn,
                feed_id=fid,
                date=row["datetime"][:10],
                channel_name=row["channel_name"],
                topic=p.get("topic", ""),
                main_content=p.get("main_content", ""),
                importance_score=importance,
                interest_score=interest,
                should_alert=bool(p.get("should_alert", False)),
            )
            # re-tag
            if normalizer is None:
                normalizer = TagNormalizer(conn)
            groups = p.get("tag_groups", {}) or {}
            raw_tags: list[tuple[str, str]] = []
            for grp in ("companies", "people", "industries"):
                target = grp.rstrip("s")
                for t in groups.get(grp, []) or []:
                    if isinstance(t, str) and t.strip():
                        raw_tags.append((t.strip(), target))
            for t in p.get("tags", []) or []:
                if isinstance(t, str) and t.strip():
                    raw_tags.append((t.strip(), "industry"))
            seen = set()
            for name, hint in raw_tags:
                try:
                    norm = normalizer.normalize(name, group_hint=hint)
                except Exception:
                    continue
                key = (norm.canonical_id, norm.group)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    repositories.insert_signal_tag(
                        conn,
                        feed_id=fid,
                        signal_id=sig_id,
                        canonical_tag_id=norm.canonical_id,
                        canonical_name=norm.canonical_name,
                        tag_group=norm.group,
                        confidence=norm.confidence,
                    )
                except Exception:
                    pass
            from .db.repositories import FeedSignal
            sig = FeedSignal(
                id=sig_id,
                feed_id=fid,
                date=row["datetime"][:10],
                channel_name=row["channel_name"],
                topic=p.get("topic", ""),
                main_content=p.get("main_content", ""),
                importance_score=importance,
                interest_score=interest,
                should_alert=bool(p.get("should_alert", False)),
                created_at=row["collected_at"],
                tags=[n for n, _ in raw_tags],
            )
            if on_signal:
                try:
                    on_signal(sig)
                except Exception:
                    pass
            ok += 1
    finally:
        await extractor.close()
    return {"ok": ok, "fail": fail, "skipped": skipped}
