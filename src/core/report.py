"""Daily report: LLM-generated summary of the day's feed_signals + analytics."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .db import repositories
from .llm.extractor import LLMExtractor
from .llm.analysis import (
    collect_feeds_for_target, compute_daily_metrics,
)
from .analytics.flow import get_daily_summary, list_top_tags_today
from .normalize.interest import InterestProfile
from .config import DATA_DIR, load_user_interests

logger = logging.getLogger(__name__)

REPORT_VERSION = "daily_report_v0.1"

DAILY_REPORT_PROMPT = """{SYSTEM}
너는 텔레그램 주식 피드를 분석하여 한국어 일간 리포트를 작성하는 애널리스트다.
어제 하루 동안의 핵심 흐름을 한 페이지 분량으로 요약한다.
반드시 한국어로만 출력한다. JSON만 출력한다.
추론 과정은 짧게 한 줄로 끝내고 바로 JSON을 출력한다.

출력 스키마:
{{
  "title": "YYYY-MM-DD 일간 리포트",
  "highlights": ["핵심 1", "핵심 2", "핵심 3"],
  "topic_summary": [
    {{"topic": "주제", "summary": "한 문장", "importance": 0}}
  ],
  "tag_flow": ["태그1: 변화", "태그2: 변화"],
  "watchlist": ["내일 주시 1", "내일 주시 2"],
  "narrative": "한 문단으로"
}}

{INPUT}
{
  "date": "{{date}}",
  "feeds": {{feeds_json}},
  "topic_summaries": {{topic_summaries_json}},
  "top_tags_today": {{top_tags_json}},
  "user_interests": {{user_interests_json}}
}

{OUTPUT_JSON_SCHEMA}
{{
  "title": "string",
  "highlights": ["string"],
  "topic_summary": [{{"topic": "string", "summary": "string", "importance": 0}}],
  "tag_flow": ["string"],
  "watchlist": ["string"],
  "narrative": "string"
}}
"""


def render_daily_prompt(
    *,
    date: str,
    feeds: list[dict],
    topic_summaries: list[dict],
    top_tags: list[dict],
    user_interests: list[str],
) -> str:
    return (
        DAILY_REPORT_PROMPT
        .replace("{{date}}", date)
        .replace("{{feeds_json}}", json.dumps(feeds, ensure_ascii=False)[:8000])
        .replace("{{topic_summaries_json}}", json.dumps(topic_summaries, ensure_ascii=False)[:3000])
        .replace("{{top_tags_json}}", json.dumps(top_tags, ensure_ascii=False)[:2000])
        .replace("{{user_interests_json}}", json.dumps(user_interests, ensure_ascii=False))
    )


@dataclass
class DailyReportResult:
    ok: bool
    title: Optional[str] = None
    payload: Optional[dict] = None
    body_text: str = ""
    error: Optional[str] = None


def _gather_inputs(
    conn: sqlite3.Connection,
    date: str,
    user_interests: list[str],
) -> dict:
    # feeds of the day (cap at 80 to keep prompt manageable)
    rows = conn.execute("""
        SELECT s.id, s.topic, s.main_content, s.importance_score, s.interest_score,
               s.channel_name
        FROM feed_signals s
        WHERE s.date = ?
        ORDER BY s.importance_score + s.interest_score DESC
        LIMIT 80
    """, (date,)).fetchall()
    feeds: list[dict] = []
    for r in rows:
        tag_rows = conn.execute(
            "SELECT canonical_name FROM signal_tags WHERE signal_id = ?", (r["id"],)
        ).fetchall()
        feeds.append({
            "topic": r["topic"],
            "channel": r["channel_name"],
            "main_content": r["main_content"],
            "importance": r["importance_score"],
            "interest": r["interest_score"],
            "tags": [t["canonical_name"] for t in tag_rows],
        })
    topic_summaries = get_daily_summary(conn, date, top_n=10)
    top_tags = list_top_tags_today(conn, limit=10)
    return {
        "feeds": feeds,
        "topic_summaries": topic_summaries,
        "top_tags": top_tags,
        "user_interests": user_interests,
    }


async def generate_daily_report(
    *,
    extractor: LLMExtractor,
    date: str,
    db_path,
    user_interests: list[str],
) -> DailyReportResult:
    from .db import connection as _conn
    conn = _conn.get_connection(db_path)
    try:
        inputs = _gather_inputs(conn, date, user_interests)
    finally:
        pass
    if not inputs["feeds"]:
        return DailyReportResult(
            ok=False, error=f"{date}에 수집된 신호가 없습니다."
        )
    rendered = render_daily_prompt(
        date=date,
        feeds=inputs["feeds"],
        topic_summaries=inputs["topic_summaries"],
        top_tags=inputs["top_tags"],
        user_interests=inputs["user_interests"],
    )
    sys_msg = rendered.split("{INPUT}")[0].replace("{SYSTEM}", "").strip()
    if "{OUTPUT_JSON_SCHEMA}" in rendered:
        sys_msg = sys_msg + "\n\n" + rendered.split("{OUTPUT_JSON_SCHEMA}")[1].strip()
    user_msg = rendered.split("{INPUT}")[1].split("{OUTPUT_JSON_SCHEMA}")[0].strip()
    try:
        model_id = await extractor.resolve_model()
    except Exception:
        model_id = "auto"
    if extractor._client is None:
        await extractor._ensure_client()
    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.3,
        "top_p": 0.9,
        "max_tokens": 2000,
    }
    try:
        r = await extractor._client.post(
            f"{extractor.base_url}/chat/completions", json=body
        )
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        import re as _re
        text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
        # find JSON
        try:
            payload = json.loads(text)
        except Exception:
            m = _re.search(r"\{.*\}", text, _re.DOTALL)
            if not m:
                return DailyReportResult(ok=False, error="LLM 응답에서 JSON을 찾을 수 없음", body_text=text[:500])
            payload = json.loads(m.group(0))
    except Exception as e:
        logger.exception("generate_daily_report failed")
        return DailyReportResult(ok=False, error=f"{e.__class__.__name__}: {e}")
    body_text = format_report_text(date, payload)
    return DailyReportResult(
        ok=True,
        title=payload.get("title") or f"{date} 일간 리포트",
        payload=payload,
        body_text=body_text,
    )


def format_report_text(date: str, payload: dict) -> str:
    """Render payload dict as readable Korean text (markdown-ish)."""
    out: list[str] = []
    out.append(f"📊 *{payload.get('title', date + ' 일간 리포트')}*")
    out.append("")
    if payload.get("highlights"):
        out.append("🔥 *핵심*")
        for h in payload["highlights"]:
            out.append(f"  • {h}")
        out.append("")
    if payload.get("topic_summary"):
        out.append("📌 *주제별 요약*")
        for t in payload["topic_summary"]:
            imp = t.get("importance", 0)
            bar = "🟥" if imp >= 80 else "🟧" if imp >= 50 else "🟨"
            out.append(f"  {bar} {t.get('topic', '')} — {t.get('summary', '')}")
        out.append("")
    if payload.get("tag_flow"):
        out.append("📈 *태그 흐름*")
        for tf in payload["tag_flow"]:
            out.append(f"  • {tf}")
        out.append("")
    if payload.get("watchlist"):
        out.append("👀 *내일 주시*")
        for w in payload["watchlist"]:
            out.append(f"  • {w}")
        out.append("")
    if payload.get("narrative"):
        out.append(f"💬 _{payload['narrative']}_")
    return "\n".join(out)


# ---------- report config + DB ----------

@dataclass
class ReportConfig:
    enabled: bool = False
    hour: int = 9                # 0-23
    minute: int = 0              # 0-59
    bot_token: str = ""          # Telegram bot token (e.g. "123:ABC")
    bot_chat_id: str = ""        # chat_id to send to
    include_user_interests: bool = True


def load_report_config() -> ReportConfig:
    p = DATA_DIR / "settings" / "report.json"
    if not p.exists():
        return ReportConfig()
    try:
        import json
        d = json.loads(p.read_text(encoding="utf-8"))
        return ReportConfig(
            enabled=bool(d.get("enabled", False)),
            hour=int(d.get("hour", 9)),
            minute=int(d.get("minute", 0)),
            bot_token=str(d.get("bot_token", "")),
            bot_chat_id=str(d.get("bot_chat_id", "")),
            include_user_interests=bool(d.get("include_user_interests", True)),
        )
    except Exception:
        return ReportConfig()


def save_report_config(cfg: ReportConfig) -> None:
    import json
    p = DATA_DIR / "settings" / "report.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "enabled": cfg.enabled,
        "hour": cfg.hour,
        "minute": cfg.minute,
        "bot_token": cfg.bot_token,
        "bot_chat_id": cfg.bot_chat_id,
        "include_user_interests": cfg.include_user_interests,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def save_daily_report(
    conn: sqlite3.Connection,
    *,
    date: str,
    title: str,
    body: str,
    payload: dict,
    sent_to_bot: bool = False,
    bot_chat_id: Optional[str] = None,
) -> int:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    sent_at = now if sent_to_bot else None
    cur = conn.execute("""
        INSERT INTO daily_reports
            (report_date, title, body, payload_json, sent_to_bot, bot_chat_id, sent_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_date) DO UPDATE SET
            title=excluded.title, body=excluded.body, payload_json=excluded.payload_json,
            sent_to_bot=excluded.sent_to_bot, bot_chat_id=excluded.bot_chat_id, sent_at=excluded.sent_at
    """, (
        date, title, body, json.dumps(payload, ensure_ascii=False),
        1 if sent_to_bot else 0, bot_chat_id, sent_at, now,
    ))
    conn.commit()
    return cur.lastrowid


def list_daily_reports(conn: sqlite3.Connection, *, limit: int = 60) -> list[dict]:
    rows = conn.execute("""
        SELECT id, report_date, title, body, sent_to_bot, bot_chat_id, sent_at, created_at
        FROM daily_reports
        ORDER BY report_date DESC
        LIMIT ?
    """, (limit,)).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "date": r["report_date"],
            "title": r["title"] or "",
            "body": r["body"] or "",
            "sent_to_bot": bool(r["sent_to_bot"]),
            "bot_chat_id": r["bot_chat_id"],
            "sent_at": r["sent_at"],
            "created_at": r["created_at"],
        })
    return out


def get_daily_report(conn: sqlite3.Connection, date: str) -> Optional[dict]:
    r = conn.execute("""
        SELECT id, report_date, title, body, sent_to_bot, bot_chat_id, sent_at, created_at
        FROM daily_reports WHERE report_date = ?
    """, (date,)).fetchone()
    if r is None:
        return None
    return {
        "id": r["id"],
        "date": r["report_date"],
        "title": r["title"] or "",
        "body": r["body"] or "",
        "sent_to_bot": bool(r["sent_to_bot"]),
        "bot_chat_id": r["bot_chat_id"],
        "sent_at": r["sent_at"],
        "created_at": r["created_at"],
    }


def get_daily_report_dates(conn: sqlite3.Connection, *, limit: int = 60) -> list[str]:
    rows = conn.execute("""
        SELECT report_date FROM daily_reports
        ORDER BY report_date DESC LIMIT ?
    """, (limit,)).fetchall()
    return [r["report_date"] for r in rows]


async def send_to_telegram_bot(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
) -> tuple[bool, str]:
    """Send a message to a Telegram bot via the Bot API (HTTPS)."""
    import httpx
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            if data.get("ok"):
                return True, "전송 완료"
            return False, f"Telegram API error: {data.get('description', '?')}"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"
