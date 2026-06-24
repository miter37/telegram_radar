"""Ticker cross-validation: visualize feed mentions vs actual price moves.

For each linked ticker, query:
- feed_ticker_links → when the ticker was mentioned
- market_bars → price around that date (T-3, T, T+3)
- Compute "did the price actually move within ±N days of the mention?"

Outputs a list of (ticker, date, importance, mention_count, price_at, price_after, change_pct).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TickerMentionEvent:
    ticker: str
    ticker_name: Optional[str]
    feed_id: int
    signal_id: int
    date: str
    importance: int
    interest: int
    main_content: str
    channel_name: str
    # price data
    price_at: Optional[float]
    price_before: Optional[float]
    price_after: Optional[float]
    change_pct_before: Optional[float]   # at vs (T-N)
    change_pct_after: Optional[float]    # (T+N) vs at
    volume_at: Optional[int]


def list_linked_tickers(
    conn: sqlite3.Connection,
    *,
    min_mentions: int = 1,
) -> list[dict]:
    """Return all tickers that have at least N feed_ticker_links."""
    rows = conn.execute("""
        SELECT ticker,
               COUNT(*) AS mentions,
               MAX(ftl.created_at) AS last_seen,
               MAX(s.importance_score) AS max_importance
        FROM feed_ticker_links ftl
        JOIN feed_signals s ON s.id = ftl.signal_id
        GROUP BY ticker
        HAVING mentions >= ?
        ORDER BY mentions DESC, last_seen DESC
    """, (min_mentions,)).fetchall()
    out = []
    for r in rows:
        name = conn.execute(
            "SELECT ticker_name FROM feed_ticker_links WHERE ticker = ? LIMIT 1",
            (r["ticker"],),
        ).fetchone()
        out.append({
            "ticker": r["ticker"],
            "ticker_name": name["ticker_name"] if name else None,
            "mentions": r["mentions"],
            "last_seen": r["last_seen"],
            "max_importance": r["max_importance"] or 0,
        })
    return out


def _get_price(
    conn: sqlite3.Connection,
    ticker: str,
    date: str,
) -> Optional[dict]:
    row = conn.execute("""
        SELECT date, open, high, low, close, volume FROM market_bars
        WHERE ticker = ? AND date = ?
    """, (ticker, date)).fetchone()
    return dict(row) if row else None


def _closest_price(
    conn: sqlite3.Connection,
    ticker: str,
    target_date: str,
    *,
    window_days: int = 3,
) -> Optional[dict]:
    """Find the closest market_bars entry within ±window_days of target_date."""
    target = datetime.strptime(target_date, "%Y-%m-%d")
    lo = (target - timedelta(days=window_days)).strftime("%Y-%m-%d")
    hi = (target + timedelta(days=window_days)).strftime("%Y-%m-%d")
    row = conn.execute("""
        SELECT date, open, high, low, close, volume, ABS(JULIANDAY(date) - JULIANDAY(?)) AS dist
        FROM market_bars
        WHERE ticker = ? AND date BETWEEN ? AND ?
        ORDER BY dist ASC
        LIMIT 1
    """, (target_date, ticker, lo, hi)).fetchone()
    return dict(row) if row else None


def list_mentions_for_ticker(
    conn: sqlite3.Connection,
    ticker: str,
    *,
    limit: int = 100,
) -> list[TickerMentionEvent]:
    """List all feed events mentioning the ticker, with price cross-validation."""
    rows = conn.execute("""
        SELECT s.id AS signal_id, s.feed_id, s.date, s.topic, s.main_content,
               s.importance_score, s.interest_score, s.channel_name,
               ftl.ticker_name
        FROM feed_ticker_links ftl
        JOIN feed_signals s ON s.id = ftl.signal_id
        WHERE ftl.ticker = ?
        ORDER BY s.date DESC
        LIMIT ?
    """, (ticker, limit)).fetchall()
    out: list[TickerMentionEvent] = []
    for r in rows:
        date = r["date"]
        at = _get_price(conn, ticker, date)
        before = _closest_price(conn, ticker, (
            datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d"), window_days=2)
        after = _closest_price(conn, ticker, (
            datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d"), window_days=2)
        price_at = at["close"] if at else None
        price_before = before["close"] if before else None
        price_after = after["close"] if after else None
        cpb = ((price_at - price_before) / price_before * 100) if (price_at and price_before) else None
        cpa = ((price_after - price_at) / price_at * 100) if (price_after and price_at) else None
        out.append(TickerMentionEvent(
            ticker=ticker,
            ticker_name=r["ticker_name"],
            feed_id=r["feed_id"],
            signal_id=r["signal_id"],
            date=date,
            importance=r["importance_score"],
            interest=r["interest_score"],
            main_content=r["main_content"],
            channel_name=r["channel_name"],
            price_at=price_at,
            price_before=price_before,
            price_after=price_after,
            change_pct_before=cpb,
            change_pct_after=cpa,
            volume_at=at["volume"] if at else None,
        ))
    return out


def ticker_price_change_summary(
    conn: sqlite3.Connection,
    ticker: str,
    *,
    days: int = 30,
) -> dict:
    """Return a summary of price moves vs mentions in the last N days."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT date, close, volume FROM market_bars
        WHERE ticker = ? AND date >= ?
        ORDER BY date ASC
    """, (ticker, cutoff)).fetchall()
    if len(rows) < 2:
        return {"ticker": ticker, "bars": 0, "first_close": None, "last_close": None,
                "change_pct": None, "mention_count": 0}
    first = rows[0]
    last = rows[-1]
    change = (last["close"] - first["close"]) / first["close"] * 100
    mention_count = conn.execute("""
        SELECT COUNT(*) AS c FROM feed_ticker_links ftl
        JOIN feed_signals s ON s.id = ftl.signal_id
        WHERE ftl.ticker = ? AND s.date >= ?
    """, (ticker, cutoff)).fetchone()["c"]
    return {
        "ticker": ticker,
        "bars": len(rows),
        "first_close": first["close"],
        "last_close": last["close"],
        "change_pct": change,
        "first_date": first["date"],
        "last_date": last["date"],
        "mention_count": mention_count,
    }
