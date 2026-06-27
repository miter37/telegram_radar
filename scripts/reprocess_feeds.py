"""Reprocess feed items through the LLM.

Walks the feed_items table for messages without a corresponding
llm_extractions row, calls LLMExtractor.extract() on each, and writes
feed_signals + llm_extractions + signal_tags. Useful when the LLM call
itself fails for a long time (e.g. local llama.cpp server swapped
models) and old feed_items accumulated without signal rows.

Usage:
    python scripts/reprocess_feeds.py --limit 200
    python scripts/reprocess_feeds.py --since 2026-06-20
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.config import load_llm_config, load_user_interests, DATA_DIR  # noqa: E402
from core.db import connection, repositories  # noqa: E402
from core.llm.extractor import LLMExtractor  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("reprocess_feeds")


async def _process_one(
    extractor: LLMExtractor,
    conn: sqlite3.Connection,
    interests: list[str],
    feed: dict,
) -> tuple[bool, str]:
    """Run extract() + persist feed_signal/llm_extraction. Return (ok, err)."""
    res = await extractor.extract(
        datetime=feed["ts"],
        channel_name=feed["channel_name"],
        message_text=feed["text"],
        message_url=feed["url"],
        user_interests=interests,
    )
    if not res.ok or not res.payload:
        return False, res.error or "empty payload"

    p = res.payload
    # Skip messages with no analyzable topic.
    if not p.get("main_content"):
        return False, "no main_content"

    importance = int(p.get("importance_score") or 0)
    interest = int(p.get("interest_score") or 0)

    try:
        # Clear prior failed extractions for this feed so a successful one
        # doesn't get duplicated on next run.
        conn.execute(
            "DELETE FROM llm_extractions WHERE feed_id = ? AND parsed_ok = 0",
            (feed["id"],),
        )
        llm_ext_id = repositories.insert_llm_extraction(
            conn,
            feed_id=feed["id"],
            prompt_version="reprocess_v1",
            raw_json=res.raw_text or "",
            parsed_ok=True,
            error_message=None,
        )
        sig_id = repositories.insert_feed_signal(
            conn,
            feed_id=feed["id"],
            date=str(feed["ts"])[:10],
            channel_name=feed["channel_name"],
            topic=p.get("topic") or p.get("main_content", "")[:80],
            main_content=p.get("main_content", ""),
            importance_score=importance,
            interest_score=interest,
            should_alert=bool(p.get("should_alert")),
        )
        # Remove stale signal_tags for this signal so we can re-insert cleanly.
        conn.execute("DELETE FROM signal_tags WHERE signal_id = ?", (sig_id,))
        # Insert tags from tag_groups + flat tags. Upsert each canonical tag,
        # then link via signal_tags.
        tag_groups = p.get("tag_groups") or {}
        flat_tags: list[tuple[str, str]] = []  # (canonical_name, tag_group)
        for grp in ("companies", "people", "industries", "products", "themes"):
            for t in tag_groups.get(grp, []) or []:
                t = str(t).strip()
                if t:
                    flat_tags.append((t, grp))
        # Fallback: use flat tags list for anything not covered above.
        covered = {name for name, _ in flat_tags}
        for t in p.get("tags", []) or []:
            t = str(t).strip()
            if t and t not in covered:
                flat_tags.append((t, "general"))
                covered.add(t)
        for canonical_name, grp in flat_tags:
            try:
                canonical_id = repositories.upsert_canonical_tag(
                    conn,
                    canonical_name=canonical_name,
                    tag_group=grp,
                )
                repositories.insert_signal_tag(
                    conn,
                    feed_id=feed["id"],
                    signal_id=sig_id,
                    canonical_tag_id=canonical_id,
                    canonical_name=canonical_name,
                    tag_group=grp,
                )
            except Exception as e:
                log.warning("signal_tag insert failed for %r: %s", canonical_name, e)
        conn.commit()
        return True, ""
    except Exception as e:
        conn.rollback()
        return False, f"db insert failed: {e}"


async def _main(args: argparse.Namespace) -> None:
    llm_cfg = load_llm_config()
    log.info("LLM endpoint: %s model=%s", llm_cfg.base_url, llm_cfg.model)
    interests = load_user_interests()

    db_path = DATA_DIR / "market_radar.sqlite"
    connection.init_db(db_path)
    conn = connection.get_connection(db_path)

    # Fetch feed items that have NO successful llm_extraction yet.
    # Includes items where parsing failed (parsed_ok=0) so we can retry.
    sql = """
        SELECT f.id, f.datetime AS ts, f.channel_name, f.message_text AS text, f.message_url AS url
        FROM feed_items f
        LEFT JOIN llm_extractions e ON e.feed_id = f.id AND e.parsed_ok = 1
        WHERE e.id IS NULL
    """
    params: list = []
    if args.since:
        sql += " AND f.datetime >= ?"
        params.append(args.since)
    sql += " ORDER BY f.datetime DESC LIMIT ?"
    params.append(args.limit)

    cur = conn.execute(sql, params)
    feeds = [dict(row) for row in cur.fetchall()]
    log.info("Found %d feed items to reprocess", len(feeds))
    if not feeds:
        return

    extractor = LLMExtractor(
        base_url=llm_cfg.base_url,
        api_key=llm_cfg.api_key,
        model=llm_cfg.model,
    )
    try:
        model_id = await extractor.resolve_model()
        log.info("Resolved model: %s", model_id)
    except Exception as e:
        log.warning("resolve_model failed (continuing with configured): %s", e)

    # Process with concurrency.
    sem = asyncio.Semaphore(5)

    async def _run(feed: dict) -> tuple[int, bool, str]:
        async with sem:
            ok, err = await _process_one(extractor, conn, interests, feed)
            return feed["id"], ok, err

    t0 = time.monotonic()
    ok_count = 0
    fail_count = 0
    # Iterate oldest-first to keep timeline coherent.
    for feed in reversed(feeds):
        fid, ok, err = await _run(feed)
        if ok:
            ok_count += 1
            log.info("[OK  ] feed_id=%d (running total ok=%d fail=%d)", fid, ok_count, fail_count)
        else:
            fail_count += 1
            log.warning("[FAIL] feed_id=%d err=%s", fid, err)
    elapsed = time.monotonic() - t0
    log.info(
        "Done. ok=%d fail=%d total=%d elapsed=%.1fs (%.1f msgs/s)",
        ok_count, fail_count, len(feeds), elapsed, len(feeds) / max(elapsed, 0.01),
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=500, help="Max feed items to process")
    p.add_argument("--since", type=str, default=None, help="Only process items newer than this ISO date (YYYY-MM-DD)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        sys.exit(130)