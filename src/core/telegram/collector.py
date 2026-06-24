"""Telethon collector: subscribes to channel messages and forwards them.

Runs in a dedicated asyncio loop inside IngestWorker (QThread).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

from telethon import events
from telethon.tl.custom.message import Message
from telethon.tl.types import Channel as TLChannel

logger = logging.getLogger(__name__)


@dataclass
class IngestedMessage:
    feed_id: int
    datetime: str  # ISO 8601
    channel_name: str
    channel_id: int
    message_text: str
    message_url: Optional[str]


# Callback signature: async def on_message(ingested: IngestedMessage) -> None
OnMessageCallback = Callable[[IngestedMessage], Awaitable[None]]


def make_message_url(channel: TLChannel, message_id: int) -> Optional[str]:
    """Construct https://t.me/{username}/{id} if username exists."""
    uname = getattr(channel, "username", None)
    if uname:
        return f"https://t.me/{uname}/{message_id}"
    return None


def make_handler(
    *,
    client_user_id: int,
    on_message: OnMessageCallback,
    enabled_channel_ids: set[int],
):
    """Create a NewMessage event handler that filters by channel id.

    The set of enabled_channel_ids is captured at handler-creation time.
    Use collector.add_channels / remove_channels to re-register a new handler
    when the user toggles channels.
    """

    async def handler(event: events.NewMessage.Event) -> None:
        try:
            msg: Message = event.message
            if not event.is_channel:
                return
            chat = await event.get_chat()
            if not isinstance(chat, TLChannel):
                return
            if chat.id not in enabled_channel_ids:
                return

            text = msg.message or ""
            if not text.strip():
                return

            # Skip our own messages (avoid loops if the account posts)
            me_id = client_user_id
            if me_id and msg.sender_id == me_id:
                return

            dt = (
                msg.date.astimezone().isoformat(timespec="seconds")
                if msg.date
                else datetime.now().astimezone().isoformat(timespec="seconds")
            )
            url = make_message_url(chat, msg.id)
            ingested = IngestedMessage(
                feed_id=0,  # filled by the worker after DB insert
                datetime=dt,
                channel_name=chat.title or getattr(chat, "username", "") or f"id={chat.id}",
                channel_id=chat.id,
                message_text=text,
                message_url=url,
            )
            await on_message(ingested)
        except Exception:
            logger.exception("handler error for message")

    return handler


async def resolve_channel(client, username: str) -> Optional[TLChannel]:
    """Resolve @username to a Channel entity. Returns None on failure."""
    try:
        uname = username.lstrip("@")
        entity = await client.get_entity(uname)
        if isinstance(entity, TLChannel):
            return entity
        return None
    except Exception as e:
        logger.warning("resolve_channel failed for %s: %s", username, e)
        return None
