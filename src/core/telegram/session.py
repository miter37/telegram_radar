"""Telegram session management with interactive login dialog callbacks.

The login itself runs in the Telethon client thread (inside IngestWorker),
but code/password prompts are forwarded to the GUI thread via a callback.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

logger = logging.getLogger(__name__)


PromptCallback = Callable[[str], str]
# callback signature: callback(prompt_message) -> user_input
# e.g. "Enter code: " -> "12345"
# e.g. "2FA password: " -> "secret"


async def start_client(
    client: TelegramClient,
    *,
    phone: str,
    prompt: PromptCallback,
) -> None:
    """Connect and authenticate. Uses prompt() for code / 2FA password.

    If a valid session file exists, this returns immediately after connect.
    """
    if not client.is_connected():
        await client.connect()

    if await client.is_user_authorized():
        logger.info("Telegram session already authorized")
        return

    logger.info("Sending code request to %s", phone)
    sent = await client.send_code_request(phone)
    code = prompt(f"Telegram 로그인 코드 ({phone}) 입력:").strip()
    if not code:
        raise RuntimeError("로그인 코드가 비어있습니다")

    try:
        # Telethon 1.43 signature: sign_in(phone, code, *, password, bot_token, phone_code_hash)
        # The phone_code_hash is carried via the SentCode object from send_code_request.
        await client.sign_in(
            phone=phone,
            code=code,
            phone_code_hash=sent.phone_code_hash,
        )
    except SessionPasswordNeededError:
        pwd = prompt("2차 인증 비밀번호 입력:").strip()
        if not pwd:
            raise RuntimeError("2차 인증 비밀번호가 비어있습니다")
        await client.sign_in(password=pwd)

    if not await client.is_user_authorized():
        raise RuntimeError("Telegram 인증 실패")
    logger.info("Telegram authenticated successfully")


def make_client(api_id: int, api_hash: str, session_path: Path) -> TelegramClient:
    """Construct a TelegramClient. session_path can be a .session file or name."""
    return TelegramClient(str(session_path), api_id, api_hash)
