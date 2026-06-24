"""History backfill: fetch past messages from Telegram channels.

Modes (controlled by HISTORY_MODE in config / settings):
- "off":            no history fetch (live only)
- "since_last":     fetch only messages with id > last_seen (default)
- "since_date":     fetch messages newer than HISTORY_DAYS ago
- "all":            fetch last HISTORY_FETCH_LIMIT messages per channel
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional

from telethon import TelegramClient
from telethon.tl.custom.message import Message
from telethon.tl.types import Channel as TLChannel

from ..db import connection, repositories
from .collector import IngestedMessage, make_message_url

logger = logging.getLogger(__name__)


@dataclass
class HistoryConfig:
    mode: str = "since_date"   # off | since_last | since_date | all
    fetch_limit: int = 500     # max messages per channel
    days: int = 7              # for since_date (default: last 7 days)


def load_history_config(path) -> HistoryConfig:
    """Load history config from a JSON file (or defaults)."""
    if not path.exists():
        return HistoryConfig()  # default: since_date mode, 7 days, 500 limit
    try:
        import json
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return HistoryConfig(
            mode=data.get("mode", "since_date"),
            fetch_limit=int(data.get("fetch_limit", 500)),
            days=int(data.get("days", 7)),
        )
    except Exception as e:
        logger.warning("history config load failed: %s", e)
        return HistoryConfig()


def save_history_config(path, cfg: HistoryConfig) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({
            "mode": cfg.mode,
            "fetch_limit": cfg.fetch_limit,
            "days": cfg.days,
        }, f, ensure_ascii=False, indent=2)


async def iter_history(
    client: TelegramClient,
    channel: TLChannel,
    *,
    min_id: int = 0,
    offset_date: Optional[datetime] = None,
    limit: int = 200,
) -> AsyncIterator[Message]:
    """Iterate messages in chronological order (oldest first → newest last).

    Telethon's iter_messages returns newest first; we reverse at the end
    so the consumer processes them in order.
    """
    collected: list[Message] = []
    async for msg in client.iter_messages(
        channel,
        limit=limit,
        min_id=min_id,
        offset_date=offset_date,
        reverse=True,  # oldest first
    ):
        if not msg.message or not msg.message.strip():
            continue
        collected.append(msg)
    for msg in collected:
        yield msg


async def fetch_channel_history(
    client: TelegramClient,
    channel: TLChannel,
    db_path,
    cfg: HistoryConfig,
    on_message,  # async callable(IngestedMessage)
) -> dict:
    """Fetch history for one channel according to config.

    Returns {fetched: int, skipped: int, last_id: int, mode: str}.
    """
    conn = connection.get_connection(db_path)
    last_seen = repositories.get_last_seen(conn, channel.id)
    inserted = 0
    skipped = 0
    last_id = last_seen

    if cfg.mode == "off":
        return {"fetched": 0, "skipped": 0, "last_id": last_id, "mode": "off"}

    min_id: int = 0
    offset_date: Optional[datetime] = None
    if cfg.mode == "since_last" and last_seen > 0:
        min_id = last_seen
    elif cfg.mode == "since_date":
        offset_date = datetime.now() - timedelta(days=cfg.days)
    # "all" → no filter, just limit

    try:
        async for msg in iter_history(
            client, channel,
            min_id=min_id,
            offset_date=offset_date,
            limit=cfg.fetch_limit,
        ):
            text = msg.message or ""
            if not text.strip():
                continue
            dt = (
                msg.date.astimezone().isoformat(timespec="seconds")
                if msg.date
                else datetime.now().astimezone().isoformat(timespec="seconds")
            )
            url = make_message_url(channel, msg.id)
            ingested = IngestedMessage(
                feed_id=0,
                datetime=dt,
                channel_name=channel.title or getattr(channel, "username", "") or f"id={channel.id}",
                channel_id=channel.id,
                message_text=text,
                message_url=url,
            )
            try:
                await on_message(ingested)
                inserted += 1
                if msg.id > last_id:
                    last_id = msg.id
            except Exception as e:
                logger.warning("on_message failed for history msg %s: %s", msg.id, e)
                skipped += 1
    except Exception as e:
        logger.exception("history fetch failed for %s", channel.username or channel.id)
        return {"fetched": inserted, "skipped": skipped, "last_id": last_id,
                "mode": cfg.mode, "error": f"{e.__class__.__name__}: {e}"}

    # persist last_seen
    if inserted > 0:
        conn2 = connection.get_connection(db_path)
        repositories.set_last_seen(
            conn2,
            channel_id=channel.id,
            channel_username=getattr(channel, "username", None),
            last_message_id=last_id,
            total_fetched=inserted,
        )

    return {
        "fetched": inserted,
        "skipped": skipped,
        "last_id": last_id,
        "mode": cfg.mode,
    }
