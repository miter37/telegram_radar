"""Export: feed_signals + signal_tags to CSV / Markdown / HTML."""

from __future__ import annotations

import csv
import html
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExportFilters:
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    importance_min: Optional[int] = None
    interest_min: Optional[int] = None
    channel: Optional[str] = None
    topic_substr: Optional[str] = None
    tag: Optional[str] = None


def _build_query(filters: ExportFilters) -> tuple[str, list]:
    where = []
    params: list = []
    if filters.date_from:
        where.append("s.date >= ?")
        params.append(filters.date_from)
    if filters.date_to:
        where.append("s.date <= ?")
        params.append(filters.date_to)
    if filters.importance_min is not None:
        where.append("s.importance_score >= ?")
        params.append(filters.importance_min)
    if filters.interest_min is not None:
        where.append("s.interest_score >= ?")
        params.append(filters.interest_min)
    if filters.channel:
        where.append("s.channel_name = ?")
        params.append(filters.channel)
    if filters.topic_substr:
        where.append("s.topic LIKE ?")
        params.append(f"%{filters.topic_substr}%")
    if filters.tag:
        where.append(
            "s.id IN (SELECT signal_id FROM signal_tags WHERE canonical_name = ?)"
        )
        params.append(filters.tag)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT s.*, f.message_text, f.message_url,
               GROUP_CONCAT(st.canonical_name, '|') AS tag_names
        FROM feed_signals s
        LEFT JOIN feed_items f ON f.id = s.feed_id
        LEFT JOIN signal_tags st ON st.signal_id = s.id
        {where_sql}
        GROUP BY s.id
        ORDER BY s.id ASC
    """
    return sql, params


def _fetch(conn: sqlite3.Connection, filters: ExportFilters) -> list[dict]:
    sql, params = _build_query(filters)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "date": r["date"],
            "channel_name": r["channel_name"],
            "topic": r["topic"],
            "main_content": r["main_content"],
            "importance_score": r["importance_score"],
            "interest_score": r["interest_score"],
            "should_alert": bool(r["should_alert"]),
            "created_at": r["created_at"],
            "message_text": r["message_text"] or "",
            "message_url": r["message_url"] or "",
            "tags": [t for t in (r["tag_names"] or "").split("|") if t],
        })
    return out


def export_csv(
    conn: sqlite3.Connection,
    path: Path,
    filters: ExportFilters,
) -> int:
    rows = _fetch(conn, filters)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "id", "date", "channel", "topic", "main_content",
            "importance", "interest", "should_alert", "tags",
            "message_text", "message_url",
        ])
        for r in rows:
            w.writerow([
                r["id"], r["date"], r["channel_name"], r["topic"],
                r["main_content"], r["importance_score"], r["interest_score"],
                "Y" if r["should_alert"] else "",
                " ".join(f"#{t}" for t in r["tags"]),
                r["message_text"], r["message_url"],
            ])
    return len(rows)


def export_markdown(
    conn: sqlite3.Connection,
    path: Path,
    filters: ExportFilters,
) -> int:
    rows = _fetch(conn, filters)
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    lines: list[str] = []
    lines.append("# Market Radar Export")
    lines.append("")
    lines.append(f"- 생성 시각: {datetime.now().isoformat(timespec='seconds')}")
    if filters.date_from or filters.date_to:
        lines.append(f"- 기간: {filters.date_from or '...'} ~ {filters.date_to or '...'}")
    if filters.channel:
        lines.append(f"- 채널: {filters.channel}")
    if filters.tag:
        lines.append(f"- 태그: {filters.tag}")
    if filters.importance_min is not None:
        lines.append(f"- 중요도 ≥ {filters.importance_min}")
    if filters.interest_min is not None:
        lines.append(f"- 관심도 ≥ {filters.interest_min}")
    lines.append(f"- 총 {len(rows)}건")
    lines.append("")
    for date in sorted(by_date):
        lines.append(f"## {date}")
        lines.append("")
        for r in by_date[date]:
            tags = " ".join(f"`#{t}`" for t in r["tags"])
            alert = "🚨 " if r["should_alert"] else ""
            lines.append(f"### {alert}{r['topic']}")
            lines.append(f"- 채널: {r['channel_name']}")
            lines.append(f"- 주요내용: {r['main_content']}")
            lines.append(f"- 중요도 {r['importance_score']} / 관심도 {r['interest_score']}")
            if tags:
                lines.append(f"- 태그: {tags}")
            if r["message_url"]:
                lines.append(f"- 원문: <{r['message_url']}>")
            lines.append("")
            if r["message_text"]:
                # wrap raw text in blockquote
                qt = "\n".join(f"> {line}" for line in r["message_text"].splitlines())
                lines.append(qt)
                lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def export_html(
    conn: sqlite3.Connection,
    path: Path,
    filters: ExportFilters,
) -> int:
    rows = _fetch(conn, filters)
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    parts: list[str] = []
    parts.append(
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<title>Market Radar Export</title>"
        "<style>"
        "body{background:#0a0d12;color:#d7dde8;font-family:'Segoe UI',sans-serif;"
        "max-width:960px;margin:0 auto;padding:24px;font-size:14px;line-height:1.6}"
        "h1{color:#e5edf8}h2{color:#c2dcff;border-bottom:1px solid #303746;"
        "padding-bottom:6px;margin-top:32px}"
        "h3{color:#e5edf8;margin-bottom:4px}"
        ".meta{color:#697386;font-size:12px;font-family:Consolas,monospace;margin-bottom:6px}"
        ".tag{display:inline-block;background:#1d2a3b;border:1px solid #334762;"
        "color:#c7dcf8;font-size:11px;padding:1px 5px;margin:1px;"
        "font-family:Consolas,monospace}"
        ".alert{color:#ff8494;font-weight:800}"
        ".score{font-family:Consolas,monospace;font-weight:800}"
        ".high{color:#ff8494}.mid{color:#eac45c}.low{color:#8bbef8}"
        ".card{background:#151a23;border:1px solid #303746;padding:14px;"
        "margin-bottom:14px}"
        ".raw{background:#0e131a;border:1px solid #303746;padding:8px;margin-top:6px;"
        "white-space:pre-wrap;font-family:Consolas,monospace;font-size:12px;"
        "color:#b8c1d0}"
        "</style></head><body>"
    )
    parts.append("<h1>Market Radar Export</h1>")
    parts.append(f"<p class='meta'>생성 {datetime.now().isoformat(timespec='seconds')} · "
                 f"{len(rows)}건</p>")
    for date in sorted(by_date):
        parts.append(f"<h2>{html.escape(date)}</h2>")
        for r in by_date[date]:
            alert_cls = "alert" if r["should_alert"] else ""
            tags_html = " ".join(
                f"<span class='tag'>{html.escape(t)}</span>" for t in r["tags"]
            )
            imp_cls = "high" if r["importance_score"] >= 80 else "mid" if r["importance_score"] >= 50 else "low"
            int_cls = "high" if r["interest_score"] >= 70 else "mid" if r["interest_score"] >= 50 else "low"
            parts.append("<div class='card'>")
            parts.append(
                f"<h3 class='{alert_cls}'>{html.escape(r['topic'])}</h3>"
                f"<div class='meta'>채널: {html.escape(r['channel_name'])}</div>"
                f"<div>{html.escape(r['main_content'])}</div>"
                f"<div class='meta'>"
                f"중요도 <span class='score {imp_cls}'>{r['importance_score']}</span> · "
                f"관심도 <span class='score {int_cls}'>{r['interest_score']}</span>"
                f"</div>"
                f"<div>{tags_html}</div>"
            )
            if r["message_url"]:
                parts.append(
                    f"<div class='meta'><a href='{html.escape(r['message_url'])}' "
                    f"style='color:#4ea1ff'>원문 열기</a></div>"
                )
            if r["message_text"]:
                parts.append(
                    f"<div class='raw'>{html.escape(r['message_text'])}</div>"
                )
            parts.append("</div>")
    parts.append("</body></html>")
    path.write_text("".join(parts), encoding="utf-8")
    return len(rows)
