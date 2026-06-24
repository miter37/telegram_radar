"""LLM-powered topic analysis: collect feeds for a target, call LLM, return result."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .extractor import LLMExtractor
from .analysis_prompts import render_analysis_prompt

logger = logging.getLogger(__name__)

ANALYSIS_VERSION = "topic_analysis_v0.1"


@dataclass
class TopicAnalysis:
    ok: bool
    payload: Optional[dict] = None
    error: Optional[str] = None
    raw: str = ""


def collect_feeds_for_target(
    conn: sqlite3.Connection,
    target: str,
    *,
    days: int = 7,
    limit: int = 50,
) -> list[dict]:
    """Find feed_signals whose topic or tag matches the target string."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    pat = f"%{target}%"
    rows = conn.execute("""
        SELECT s.id, s.date, s.topic, s.main_content, s.importance_score, s.interest_score, s.feed_id
        FROM feed_signals s
        LEFT JOIN signal_tags st ON st.signal_id = s.id
        WHERE s.date >= ?
          AND (s.topic LIKE ? OR st.canonical_name LIKE ?)
        GROUP BY s.id
        ORDER BY s.date DESC
        LIMIT ?
    """, (since, pat, pat, limit)).fetchall()
    out = []
    for r in rows:
        tag_rows = conn.execute(
            "SELECT canonical_name FROM signal_tags WHERE signal_id = ?", (r["id"],)
        ).fetchall()
        out.append({
            "date": r["date"],
            "topic": r["topic"],
            "main_content": r["main_content"],
            "importance_score": r["importance_score"],
            "interest_score": r["interest_score"],
            "tags": [t["canonical_name"] for t in tag_rows],
        })
    return out


def compute_daily_metrics(
    conn: sqlite3.Connection,
    target: str,
    *,
    days: int = 7,
) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    pat = f"%{target}%"
    rows = conn.execute("""
        SELECT s.date,
               COUNT(*) AS feed_count,
               AVG(s.importance_score) AS avg_importance,
               AVG(s.interest_score) AS avg_interest
        FROM feed_signals s
        LEFT JOIN signal_tags st ON st.signal_id = s.id
        WHERE s.date >= ? AND (s.topic LIKE ? OR st.canonical_name LIKE ?)
        GROUP BY s.date
        ORDER BY s.date
    """, (since, pat, pat)).fetchall()
    return [
        {
            "date": r["date"],
            "feed_count": r["feed_count"],
            "avg_importance": r["avg_importance"] or 0.0,
            "avg_interest": r["avg_interest"] or 0.0,
        }
        for r in rows
    ]


async def run_topic_analysis(
    *,
    extractor: LLMExtractor,
    target: str,
    period: str,
    feeds: list[dict],
    daily_metrics: list[dict],
    user_interests: list[str],
) -> TopicAnalysis:
    rendered = render_analysis_prompt(
        target=target,
        period=period,
        feeds_json=json.dumps(feeds, ensure_ascii=False)[:6000],
        daily_metrics_json=json.dumps(daily_metrics, ensure_ascii=False)[:2000],
        user_interests_json=json.dumps(user_interests, ensure_ascii=False),
    )
    # crude split
    sys = rendered.split("{INPUT}")[0].replace("{SYSTEM}", "").strip()
    user = rendered.split("{INPUT}")[1].split("{OUTPUT_JSON_SCHEMA}")[0].strip()
    try:
        model_id = await extractor.resolve_model()
    except Exception:
        model_id = "auto"
    try:
        # direct httpx via shared client
        if extractor._client is None:
            await extractor._ensure_client()
        body = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "top_p": 0.9,
            "max_tokens": 1500,
        }
        r = await extractor._client.post(
            f"{extractor.base_url}/chat/completions", json=body
        )
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        # strip think
        import re as _re
        text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
        # find JSON
        try:
            payload = json.loads(text)
        except Exception:
            m = _re.search(r"\{.*\}", text, _re.DOTALL)
            if m:
                payload = json.loads(m.group(0))
            else:
                return TopicAnalysis(ok=False, error="no JSON in response", raw=text[:500])
        return TopicAnalysis(ok=True, payload=payload, raw=text)
    except Exception as e:
        logger.exception("analysis failed")
        return TopicAnalysis(ok=False, error=f"{e.__class__.__name__}: {e}")
