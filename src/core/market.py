"""Market data: yfinance-based price/volume fetcher with SQLite cache.

Two modes:
1. On-demand: load_market_panel(ticker) returns recent bars for a chart
2. Snapshot: ticker_snapshot(ticker) returns latest price + change

Caching: per (ticker, date) bar data is stored in market_bars table for fast
re-display. Use update_ticker to refresh from yfinance.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TickerSnapshot:
    ticker: str
    name: Optional[str]
    currency: Optional[str]
    last_close: Optional[float]
    prev_close: Optional[float]
    change_pct: Optional[float]
    volume: Optional[int]
    as_of: str
    error: Optional[str] = None


# Korean exchange suffixes for yfinance
KRX_SUFFIX = ".KS"   # KOSPI
KOSDAQ_SUFFIX = ".KQ"


def normalize_ticker(ticker: str) -> str:
    """Add .KS suffix to bare Korean tickers (6-digit numbers).

    yfinance uses 005930.KS for Samsung, 035720.KS for Kakao, etc.
    US tickers are passed through.
    """
    t = (ticker or "").strip()
    if not t:
        return t
    if "." in t:
        return t.upper()
    if t.isdigit() and len(t) == 6:
        return f"{t}{KRX_SUFFIX}"
    return t.upper()


def ticker_snapshot(ticker: str) -> TickerSnapshot:
    """Fetch latest snapshot via yfinance (no caching)."""
    sym = normalize_ticker(ticker)
    try:
        import yfinance as yf
        t = yf.Ticker(sym)
        info = {}
        try:
            info = t.fast_info or {}
        except Exception:
            pass
        name = info.get("shortName") or info.get("longName") if isinstance(info, dict) else None
        currency = info.get("currency") if isinstance(info, dict) else None
        last_price = info.get("last_price") if isinstance(info, dict) else None
        prev_close = info.get("previous_close") if isinstance(info, dict) else None
        volume = info.get("last_volume") if isinstance(info, dict) else None
        change = None
        if last_price is not None and prev_close:
            change = (last_price - prev_close) / prev_close * 100
        return TickerSnapshot(
            ticker=sym,
            name=name,
            currency=currency,
            last_close=last_price,
            prev_close=prev_close,
            change_pct=change,
            volume=volume,
            as_of=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as e:
        return TickerSnapshot(
            ticker=sym, name=None, currency=None,
            last_close=None, prev_close=None, change_pct=None,
            volume=None, as_of=datetime.now().isoformat(timespec="seconds"),
            error=f"{e.__class__.__name__}: {e}",
        )


def fetch_history(
    ticker: str,
    *,
    period: str = "3mo",
    interval: str = "1d",
):
    """Return a list of (date, open, high, low, close, volume) tuples.

    Empty list on failure.
    """
    sym = normalize_ticker(ticker)
    try:
        import yfinance as yf
        t = yf.Ticker(sym)
        df = t.history(period=period, interval=interval, auto_adjust=False)
        if df is None or df.empty:
            return []
        rows = []
        for idx, row in df.iterrows():
            d = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
            rows.append((
                d,
                float(row.get("Open") or 0.0),
                float(row.get("High") or 0.0),
                float(row.get("Low") or 0.0),
                float(row.get("Close") or 0.0),
                int(row.get("Volume") or 0),
            ))
        return rows
    except Exception as e:
        logger.warning("yfinance history failed for %s: %s", sym, e)
        return []


# ---------- DB schema (market_bars) ----------

def ensure_market_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            fetched_at TEXT NOT NULL,
            UNIQUE(ticker, date)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_bars_ticker_date "
        "ON market_bars(ticker, date)"
    )
    conn.commit()


def upsert_bars(
    conn: sqlite3.Connection,
    ticker: str,
    bars: list[tuple],
) -> int:
    ensure_market_schema(conn)
    n = 0
    now = datetime.now().isoformat(timespec="seconds")
    sym = normalize_ticker(ticker)
    for d, o, h, l, c, v in bars:
        conn.execute("""
            INSERT INTO market_bars
                (ticker, date, open, high, low, close, volume, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume,
                fetched_at=excluded.fetched_at
        """, (sym, d, o, h, l, c, v, now))
        n += 1
    conn.commit()
    return n


def get_bars(
    conn: sqlite3.Connection,
    ticker: str,
    *,
    days: int = 30,
) -> list[dict]:
    ensure_market_schema(conn)
    sym = normalize_ticker(ticker)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume
        FROM market_bars
        WHERE ticker = ? AND date >= ?
        ORDER BY date ASC
    """, (sym, cutoff)).fetchall()
    return [dict(r) for r in rows]


def update_ticker(conn: sqlite3.Connection, ticker: str, *, period: str = "3mo") -> int:
    """Fetch from yfinance and persist to DB. Returns number of rows stored."""
    bars = fetch_history(ticker, period=period, interval="1d")
    if not bars:
        return 0
    return upsert_bars(conn, ticker, bars)


def list_known_tickers(conn: sqlite3.Connection) -> list[str]:
    ensure_market_schema(conn)
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM market_bars ORDER BY ticker"
    ).fetchall()
    return [r["ticker"] for r in rows]
