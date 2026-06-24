"""2-stage daily topic report pipeline.

Stage 1: cluster_daily_topics() — LLM groups N signals of a day into M topics
Stage 2: generate_topic_report() — for each topic, LLM synthesizes a report
Final:    run_daily_topic_pipeline() — runs both, persists to DB + writes MD files
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import DATA_DIR
from .db import connection, repositories
from .db.repositories import TopicCluster
from .llm.engines import Engine, EngineRegistry, chat_completion
from .llm.extractor import LLMExtractor
from .llm.topic_prompts import (
    CLUSTER_PROMPT_VERSION,
    SUMMARY_PROMPT_VERSION,
    render_cluster_prompt,
    render_summary_prompt,
    split_for_chat,
)

logger = logging.getLogger(__name__)


@dataclass
class TopicReportPayload:
    report_date: str
    topic_idx: int
    label: str
    summary: str
    body_md: str
    timeline: list[dict] = field(default_factory=list)
    watchlist: list[str] = field(default_factory=list)
    member_count: int = 0
    avg_importance: float = 0.0
    avg_interest: float = 0.0
    top_signal_ids: list[int] = field(default_factory=list)
    md_path: Optional[str] = None
    prompt_version: str = f"{CLUSTER_PROMPT_VERSION}+{SUMMARY_PROMPT_VERSION}"


# ---------- Data gathering ----------

def gather_daily_signals(
    conn: sqlite3.Connection,
    date: str,
    *,
    limit: int = 200,
) -> list[dict]:
    """Collect all feed_signals for a date with member message text and tags.

    Returns importance-ordered list. Each entry:
    {signal_id, feed_id, time, channel, importance, interest,
     topic, main_content, raw_text, tags}
    """
    rows = conn.execute("""
        SELECT s.id, s.feed_id, s.date, s.channel_name, s.importance_score,
               s.interest_score, s.topic, s.main_content
        FROM feed_signals s
        WHERE s.date = ?
        ORDER BY s.importance_score + s.interest_score DESC, s.id ASC
        LIMIT ?
    """, (date, limit)).fetchall()
    out = []
    for r in rows:
        feed_row = conn.execute(
            "SELECT message_text FROM feed_items WHERE id = ?",
            (r["feed_id"],),
        ).fetchone()
        raw_text = (feed_row["message_text"] if feed_row else "") or ""
        tag_rows = conn.execute(
            "SELECT canonical_name FROM signal_tags WHERE signal_id = ?",
            (r["id"],),
        ).fetchall()
        tags = [t["canonical_name"] for t in tag_rows]
        # extract HH:MM from date
        time_str = (r["date"] or "")[11:16] if len(r["date"]) >= 16 else ""
        out.append({
            "signal_id": r["id"],
            "feed_id": r["feed_id"],
            "time": time_str,
            "channel": r["channel_name"],
            "importance": r["importance_score"],
            "interest": r["interest_score"],
            "topic": r["topic"],
            "main_content": r["main_content"],
            "raw_text": raw_text,
            "tags": tags,
        })
    return out


def _build_cluster_messages(signals: list[dict]) -> list[dict]:
    """Compress signals for the clustering LLM (no raw_text, only preview)."""
    out = []
    for i, s in enumerate(signals, start=1):
        preview = (s["raw_text"] or "")[:200]
        out.append({
            "idx": i,
            "channel": s["channel"],
            "time": s["time"],
            "topic": s["topic"],
            "content": s["main_content"],
            "importance": s["importance"],
            "preview": preview,
        })
    return out


def _build_summary_members(signals: list[dict]) -> list[dict]:
    """For Stage 2: include full raw_text (truncated per item) + meta."""
    out = []
    for s in signals:
        rt = (s["raw_text"] or "")[:1200]
        out.append({
            "signal_id": s["signal_id"],
            "time": s["time"],
            "channel": s["channel"],
            "importance": s["importance"],
            "interest": s["interest"],
            "topic": s["topic"],
            "main_content": s["main_content"],
            "raw_text": rt,
            "tags": s["tags"][:8],
        })
    return out


# ---------- Stage 1 ----------

def _parse_cluster_response(text: str) -> list[dict]:
    """Parse Stage-1 LLM JSON. Returns [{topic_idx, label, member_idx:[]}, ...]"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(0))
    topics = data.get("topics") or []
    out = []
    for t in topics:
        out.append({
            "topic_idx": int(t.get("topic_idx", 0)),
            "label": str(t.get("label", "")).strip()[:12],
            "member_idx": [int(x) for x in (t.get("member_idx") or []) if isinstance(x, (int, float))],
        })
    return out


async def cluster_daily_topics(
    *,
    extractor: LLMExtractor,
    db_path,
    date: str,
    max_topics: int = 12,
) -> tuple[bool, str, list[TopicCluster]]:
    """Run Stage 1. Returns (ok, error_message, clusters_importance_sorted)."""
    conn = connection.get_connection(db_path)
    signals = gather_daily_signals(conn, date)
    if not signals:
        return False, f"{date}에 신호 없음", []

    cluster_messages = _build_cluster_messages(signals)
    rendered = render_cluster_prompt(
        date=date,
        messages=cluster_messages,
        min_topics=3,
        max_topics=max_topics,
    )
    sys_msg, user_msg = split_for_chat(rendered)

    # Use multi-engine router if available
    if extractor._registry is not None and extractor._registry.list_enabled():
        from .llm.engines import chat_completion
        engines = extractor._registry.list_enabled()
        body = {
            "model": "",
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 2000,
        }
        # model name will be set per engine inside router
        res = await chat_completion(
            engines,
            messages=body["messages"],
            temperature=body["temperature"],
            top_p=body["top_p"],
            max_tokens=body["max_tokens"],
        )
        if not res.ok:
            return False, res.error or "all engines failed", []
        text = res.content
    else:
        # legacy single-engine path
        await extractor._ensure_client()
        if extractor._resolved_model is None:
            await extractor.resolve_model()
        body = {
            "model": extractor._resolved_model,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
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
        except Exception as e:
            return False, f"{e.__class__.__name__}: {e}", []

    topics = _parse_cluster_response(text)
    if not topics:
        return False, "Stage 1 응답 파싱 실패 (JSON topics 없음)", []

    # Resolve member_idx → member_signal_ids (importance sorted)
    clusters_out: list[TopicCluster] = []
    for t in topics:
        member_signal_ids = []
        for idx in t["member_idx"]:
            i = idx - 1
            if 0 <= i < len(signals):
                member_signal_ids.append(signals[i]["signal_id"])
        # sort by importance desc
        id_to_imp = {s["signal_id"]: (s["importance"], s["interest"]) for s in signals}
        member_signal_ids.sort(key=lambda sid: id_to_imp.get(sid, (0, 0)), reverse=True)
        if not member_signal_ids:
            continue
        avg_imp = sum(id_to_imp[sid][0] for sid in member_signal_ids) / len(member_signal_ids)
        avg_int = sum(id_to_imp[sid][1] for sid in member_signal_ids) / len(member_signal_ids)
        clusters_out.append(TopicCluster(
            report_date=date,
            topic_idx=t["topic_idx"],
            label=t["label"] or f"주제 {t['topic_idx']}",
            member_signal_ids=member_signal_ids,
            member_count=len(member_signal_ids),
            avg_importance=avg_imp,
            avg_interest=avg_int,
        ))

    # sort clusters by total (avg_imp + avg_int) desc
    clusters_out.sort(key=lambda c: (c.avg_importance + c.avg_interest), reverse=True)
    # renumber topic_idx starting from 1 in importance order
    for i, c in enumerate(clusters_out, start=1):
        c.topic_idx = i

    return True, "", clusters_out


# ---------- Stage 2 ----------

def _parse_summary_response(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {}
        return json.loads(m.group(0))


async def generate_topic_report(
    *,
    extractor: LLMExtractor,
    db_path,
    date: str,
    cluster: TopicCluster,
) -> tuple[bool, str, dict]:
    """Stage 2: synthesize a report for one cluster. Returns (ok, err, payload_dict)."""
    conn = connection.get_connection(db_path)
    signals = gather_daily_signals(conn, date)
    sig_map = {s["signal_id"]: s for s in signals}
    member_signals = [sig_map[sid] for sid in cluster.member_signal_ids if sid in sig_map]
    if not member_signals:
        return False, "cluster에 매핑되는 신호가 없음", {}

    members = _build_summary_members(member_signals)
    rendered = render_summary_prompt(
        date=date,
        label=cluster.label,
        members=members,
    )
    sys_msg, user_msg = split_for_chat(rendered)

    if extractor._registry is not None and extractor._registry.list_enabled():
        from .llm.engines import chat_completion
        engines = extractor._registry.list_enabled()
        res = await chat_completion(
            engines,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            top_p=0.9,
            max_tokens=1500,
        )
        if not res.ok:
            return False, res.error or "all engines failed", {}
        text = res.content
    else:
        await extractor._ensure_client()
        if extractor._resolved_model is None:
            await extractor.resolve_model()
        body = {
            "model": extractor._resolved_model,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.3,
            "top_p": 0.9,
            "max_tokens": 1500,
        }
        try:
            r = await extractor._client.post(
                f"{extractor.base_url}/chat/completions", json=body
            )
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"]
        except Exception as e:
            return False, f"{e.__class__.__name__}: {e}", {}

    payload = _parse_summary_response(text)
    if not payload or "summary" not in payload:
        return False, "Stage 2 응답 파싱 실패 (summary 없음)", {}
    return True, "", payload


# ---------- MD writer ----------

def _md_safe_filename(label: str, idx: int) -> str:
    safe = re.sub(r"[^\w가-힣\s-]", "", label or "").strip()
    safe = re.sub(r"\s+", "-", safe)
    if not safe:
        safe = f"topic-{idx}"
    return f"{idx:02d}-{safe}.md"


def write_topic_report_md(
    *,
    report_date: str,
    report: dict,
    signals_lookup: dict[int, dict],
    md_dir: Optional[Path] = None,
) -> Path:
    """Write one MD file. Returns the file path."""
    md_dir = md_dir or (DATA_DIR / "reports" / report_date)
    md_dir.mkdir(parents=True, exist_ok=True)
    fname = _md_safe_filename(report["label"], report["topic_idx"])
    path = md_dir / fname

    # fetch raw_text for member signals
    body_lines: list[str] = []
    body_lines.append(f"---")
    body_lines.append(f"date: {report_date}")
    body_lines.append(f"topic: {report['label']}")
    body_lines.append(f"topic_idx: {report['topic_idx']}")
    body_lines.append(f"signal_count: {report['member_count']}")
    body_lines.append(f"avg_importance: {report['avg_importance']:.1f}")
    body_lines.append(f"avg_interest: {report['avg_interest']:.1f}")
    body_lines.append(f"prompt_version: {report['prompt_version']}")
    body_lines.append(f"generated_at: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    body_lines.append("---")
    body_lines.append("")
    body_lines.append(f"# {report['topic_idx']}. {report['label']}")
    body_lines.append("")
    body_lines.append("## 요약")
    body_lines.append("")
    body_lines.append(report["summary"])
    body_lines.append("")
    body_lines.append("## 상세 분석")
    body_lines.append("")
    body_lines.append(report["body_md"])
    body_lines.append("")
    if report.get("timeline"):
        body_lines.append("## 타임라인")
        body_lines.append("")
        for t in report["timeline"]:
            tm = t.get("time", "")
            note = t.get("note", "")
            body_lines.append(f"- **{tm}** — {note}")
        body_lines.append("")
    if report.get("watchlist"):
        body_lines.append("## 내일 주시")
        body_lines.append("")
        for w in report["watchlist"]:
            body_lines.append(f"- {w}")
        body_lines.append("")
    body_lines.append("---")
    body_lines.append("")
    body_lines.append("## 관련 원문 발췌")
    body_lines.append("")

    top_ids = report.get("top_signal_ids") or []
    if not top_ids:
        # fall back to all members sorted by importance
        top_ids = sorted(
            report.get("member_signal_ids") or [],
            key=lambda sid: (
                signals_lookup.get(sid, {}).get("importance", 0),
                signals_lookup.get(sid, {}).get("interest", 0),
            ),
            reverse=True,
        )
    for sid in top_ids:
        s = signals_lookup.get(sid)
        if s is None:
            continue
        body_lines.append(
            f"### [{s['importance']}] {s['time']} · {s['channel']} · {s['topic']}"
        )
        body_lines.append("")
        excerpt = (s.get("raw_text") or s.get("main_content") or "")[:280]
        # quote block
        for line in excerpt.splitlines() or [""]:
            body_lines.append(f"> {line}")
        body_lines.append("")
        body_lines.append(f"- [app://feed/{s['feed_id']}]  ← 클릭 시 앱 내 원문 팝업")
        body_lines.append("")

    path.write_text("\n".join(body_lines), encoding="utf-8")
    return path


def write_daily_index_md(
    *,
    report_date: str,
    topics: list[dict],
    md_dir: Optional[Path] = None,
) -> Path:
    md_dir = md_dir or (DATA_DIR / "reports" / report_date)
    md_dir.mkdir(parents=True, exist_ok=True)
    path = md_dir / "index.md"
    lines: list[str] = []
    lines.append(f"# {report_date} 일자 주제 리포트")
    lines.append("")
    lines.append(f"- 생성: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    lines.append(f"- 주제 수: {len(topics)}")
    lines.append("")
    lines.append("| # | 주제 | 신호 | 평균 점수 | MD |")
    lines.append("|---|---|---|---|---|")
    for t in topics:
        fname = _md_safe_filename(t["label"], t["topic_idx"])
        score = f"imp {t['avg_importance']:.0f} / int {t['avg_interest']:.0f}"
        lines.append(
            f"| {t['topic_idx']} | {t['label']} | {t['member_count']} | {score} | [{fname}]({fname}) |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------- Pipeline ----------

async def run_daily_topic_pipeline(
    *,
    extractor: LLMExtractor,
    db_path,
    date: str,
    max_topics: int = 12,
    progress=None,  # optional callable(str)
) -> dict:
    """Run Stage 1 + Stage 2 for a single date, persist, write MDs.

    Returns dict with: {ok, error, clusters: [...], reports: [...]}
    """
    if progress:
        try:
            progress(f"Stage 1: 클러스터링 중 ({date})")
        except Exception:
            pass

    ok, err, clusters = await cluster_daily_topics(
        extractor=extractor, db_path=db_path, date=date, max_topics=max_topics,
    )
    if not ok:
        return {"ok": False, "error": err, "clusters": [], "reports": []}

    # Persist Stage 1
    conn = connection.get_connection(db_path)
    repositories.clear_topic_clusters(conn, date)
    for c in clusters:
        repositories.upsert_topic_cluster(
            conn,
            report_date=date,
            topic_idx=c.topic_idx,
            label=c.label,
            member_signal_ids=c.member_signal_ids,
            avg_importance=c.avg_importance,
            avg_interest=c.avg_interest,
        )
    # Also clear old reports
    repositories.clear_topic_reports(conn, date)

    # Build lookup for MD writing
    signals = gather_daily_signals(conn, date)
    sig_map = {s["signal_id"]: s for s in signals}

    # Stage 2 per cluster
    reports: list[dict] = []
    md_dir = DATA_DIR / "reports" / date
    for c in clusters:
        if progress:
            try:
                progress(f"Stage 2: {c.topic_idx}/{len(clusters)} — {c.label} 종합 중")
            except Exception:
                pass
        ok2, err2, payload = await generate_topic_report(
            extractor=extractor, db_path=db_path, date=date, cluster=c,
        )
        if not ok2:
            logger.warning("Stage 2 failed for %s: %s", c.label, err2)
            continue
        # member_signal_ids ordered by importance for top_signal_ids
        top_ids = c.member_signal_ids  # already importance-sorted
        report = TopicReportPayload(
            report_date=date,
            topic_idx=c.topic_idx,
            label=c.label,
            summary=payload.get("summary", "").strip(),
            body_md=payload.get("body_md", "").strip(),
            timeline=payload.get("timeline") or [],
            watchlist=payload.get("watchlist") or [],
            member_count=c.member_count,
            avg_importance=c.avg_importance,
            avg_interest=c.avg_interest,
            top_signal_ids=top_ids,
            prompt_version=f"{CLUSTER_PROMPT_VERSION}+{SUMMARY_PROMPT_VERSION}",
        )
        # write MD
        report_dict = {
            "report_date": report.report_date,
            "topic_idx": report.topic_idx,
            "label": report.label,
            "summary": report.summary,
            "body_md": report.body_md,
            "timeline": report.timeline,
            "watchlist": report.watchlist,
            "member_count": report.member_count,
            "avg_importance": report.avg_importance,
            "avg_interest": report.avg_interest,
            "top_signal_ids": report.top_signal_ids,
            "member_signal_ids": c.member_signal_ids,
            "prompt_version": report.prompt_version,
        }
        md_path = write_topic_report_md(
            report_date=date,
            report=report_dict,
            signals_lookup=sig_map,
            md_dir=md_dir,
        )
        report.md_path = str(md_path)
        # persist
        repositories.upsert_topic_report(
            conn,
            report_date=date,
            topic_idx=report.topic_idx,
            label=report.label,
            summary=report.summary,
            body_md=report.body_md,
            timeline=report.timeline,
            watchlist=report.watchlist,
            member_count=report.member_count,
            avg_importance=report.avg_importance,
            avg_interest=report.avg_interest,
            top_signal_ids=report.top_signal_ids,
            md_path=report.md_path,
            prompt_version=report.prompt_version,
        )
        reports.append({
            "topic_idx": report.topic_idx,
            "label": report.label,
            "summary": report.summary,
            "body_md": report.body_md,
            "timeline": report.timeline,
            "watchlist": report.watchlist,
            "member_count": report.member_count,
            "avg_importance": report.avg_importance,
            "avg_interest": report.avg_interest,
            "top_signal_ids": report.top_signal_ids,
            "md_path": report.md_path,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })

    # index.md
    if reports:
        write_daily_index_md(
            report_date=date,
            topics=reports,
            md_dir=md_dir,
        )

    if progress:
        try:
            progress(f"완료: {len(reports)}개 주제 리포트 생성")
        except Exception:
            pass

    return {
        "ok": True,
        "error": None,
        "clusters": [
            {
                "topic_idx": c.topic_idx,
                "label": c.label,
                "member_count": c.member_count,
                "avg_importance": c.avg_importance,
                "avg_interest": c.avg_interest,
            }
            for c in clusters
        ],
        "reports": reports,
    }


def reports_dir_for(date: str) -> Path:
    return DATA_DIR / "reports" / date
