"""Auto-link feeds to tickers based on canonical tag names.

Strategy:
- Korean companies: known aliases map to 6-digit KRX codes
- Foreign companies: known aliases map to US tickers
- Numeric tag (6 digits) → assume KRX
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Optional

from .db import repositories
from .market import normalize_ticker

logger = logging.getLogger(__name__)


# alias -> (ticker, ticker_name)
TICKER_ALIASES: dict[str, tuple[str, str]] = {
    # KRX
    "삼성전자": ("005930", "삼성전자"),
    "삼성전기": ("009150", "삼성전기"),
    "sk하이닉스": ("000660", "SK하이닉스"),
    "sk hynix": ("000660", "SK하이닉스"),
    "skc": ("011790", "SKC"),
    "엔비디아": ("NVDA", "엔비디아"),
    "nvidia": ("NVDA", "엔비디아"),
    "tsmc": ("TSM", "TSMC"),
    "lg에너지솔루션": ("373220", "LG에너지솔루션"),
    "lg chem": ("051910", "LG화학"),
    "lg화학": ("051910", "LG화학"),
    "posco": ("005490", "POSCO홀딩스"),
    "포스코": ("005490", "POSCO홀딩스"),
    "현대차": ("005380", "현대차"),
    "기아": ("000270", "기아"),
    "네이버": ("035420", "NAVER"),
    "카카오": ("035720", "카카오"),
    "isc": ("095340", "ISC"),
    "티에스이": ("131290", "티에스이"),
    "리노공업": ("058470", "리노공업"),
    "솔루스첨단소재": ("336370", "솔루스첨단소재"),
    "솔루스": ("336370", "솔루스첨단소재"),
    "ls electric": ("010120", "LS ELECTRIC"),
    "ls 일렉트릭": ("010120", "LS ELECTRIC"),
    # US
    "apple": ("AAPL", "Apple"),
    "microsoft": ("MSFT", "Microsoft"),
    "google": ("GOOGL", "Alphabet"),
    "alphabet": ("GOOGL", "Alphabet"),
    "meta": ("META", "Meta"),
    "amazon": ("AMZN", "Amazon"),
    "tesla": ("TSLA", "Tesla"),
    "amd": ("AMD", "AMD"),
    "intel": ("INTC", "Intel"),
    "broadcom": ("AVGO", "Broadcom"),
    "taiwan semi": ("TSM", "TSMC"),
    "asml": ("ASML", "ASML"),
}


def detect_tickers(tag_names: list[str]) -> list[tuple[str, str]]:
    """Return [(ticker, name)] for any matching tag."""
    out: list[tuple[str, str]] = []
    seen = set()
    for tag in tag_names:
        if not tag:
            continue
        t_norm = tag.strip().lower()
        # exact alias hit
        if t_norm in TICKER_ALIASES:
            tk, tn = TICKER_ALIASES[t_norm]
            key = normalize_ticker(tk)
            if key not in seen:
                out.append((key, tn))
                seen.add(key)
            continue
        # bare 6-digit → KRX
        if re.fullmatch(r"\d{6}", tag.strip()):
            key = normalize_ticker(tag.strip())
            if key not in seen:
                out.append((key, tag.strip()))
                seen.add(key)
    return out


def link_signal_tickers(conn: sqlite3.Connection, signal_id: int) -> int:
    """Given a signal_id, find canonical tags → tickers, write feed_ticker_links.

    Returns number of links created (0 if none).
    """
    rows = conn.execute(
        "SELECT canonical_name FROM signal_tags WHERE signal_id = ?", (signal_id,)
    ).fetchall()
    tag_names = [r["canonical_name"] for r in rows]
    matches = detect_tickers(tag_names)
    if not matches:
        return 0
    sig = conn.execute(
        "SELECT feed_id FROM feed_signals WHERE id = ?", (signal_id,)
    ).fetchone()
    if sig is None:
        return 0
    feed_id = sig["feed_id"]
    n = 0
    for tk, tn in matches:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO feed_ticker_links
                    (feed_id, signal_id, ticker, ticker_name, confidence, source, created_at)
                VALUES (?, ?, ?, ?, 1.0, 'tag_match', ?)
            """, (feed_id, signal_id, tk, tn, datetime.now().astimezone().isoformat(timespec="seconds")))
            n += 1
        except Exception as e:
            logger.debug("link failed: %s", e)
    conn.commit()
    return n


def list_tickers_for_signal(conn: sqlite3.Connection, signal_id: int) -> list[dict]:
    rows = conn.execute("""
        SELECT ticker, ticker_name, confidence, source
        FROM feed_ticker_links
        WHERE signal_id = ?
        ORDER BY confidence DESC
    """, (signal_id,)).fetchall()
    return [dict(r) for r in rows]


def list_signals_for_ticker(conn: sqlite3.Connection, ticker: str) -> list[int]:
    rows = conn.execute("""
        SELECT signal_id FROM feed_ticker_links
        WHERE ticker = ?
        ORDER BY signal_id DESC
    """, (ticker,)).fetchall()
    return [r["signal_id"] for r in rows]
