"""IngestWorker: QThread that runs the Telethon asyncio loop.

Receives a queue of channel ids; subscribes to NewMessage events;
inserts raw feeds into SQLite; emits a signal for LLMWorker to pick up.

Telethon event loop is started with asyncio.run() inside the QThread.
Code/password prompts are forwarded to the GUI thread via a QMetaObject
invoke (blocking call to main thread) — Telethon requires prompt values
to be returned synchronously, so we block this worker thread until the
user clicks OK in the modal dialog.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal
from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from core.config import TelegramConfig
from core.db import connection, repositories
from core.telegram.collector import (
    IngestedMessage,
    make_handler,
    resolve_channel,
)
from core.telegram.session import start_client

logger = logging.getLogger(__name__)


class IngestWorker(QThread):
    """QThread that owns a Telethon client and a SQLite connection."""

    # emitted from worker thread; safely crosses thread boundary via Qt's queued connection
    message_collected = Signal(int, dict)  # feed_id, payload dict for LLM
    status_update = Signal(str)            # human-readable status for toolbar
    error = Signal(str)
    login_required = Signal(str, str)      # (prompt_message, kind) — 'code' or 'password'
    login_success = Signal()
    login_failure = Signal(str)            # error reason
    # channel resolve request/response (carries a request_id so multiple in-flight don't collide)
    channel_resolve_ok = Signal(int, int, str)   # (request_id, channel_id, title)
    channel_resolve_err = Signal(int, str)       # (request_id, error_message)

    def __init__(self, tg_cfg: TelegramConfig, db_path: Path, parent: QObject | None = None):
        super().__init__(parent)
        self._tg = tg_cfg
        self._db_path = db_path
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[TelegramClient] = None
        self._stopped = False
        self._handler_registered = False
        self._handler_lock = threading.Lock()
        # Channel changes pushed from main thread
        self._channel_q: "queue.Queue[set[int]]" = queue.Queue()
        # Prompt request/response (main thread provides the answer)
        self._prompt_q: "queue.Queue[str]" = queue.Queue()
        # Channel resolve requests: {request_id: queue.Queue[(id, title) or Exception]}
        self._resolve_qs: dict[int, "queue.Queue"] = {}
        self._resolve_seq = 0
        self._resolve_lock = threading.Lock()

    # ----- main-thread API -----

    def update_enabled_channels(self, ids: set[int]) -> None:
        self._channel_q.put(ids)

    def validate_channels_sync(
        self,
        ids: set[int],
        timeout: float = 30.0,
    ) -> tuple[set[int], dict[int, str]]:
        """Sync wrapper around validate_channel_ids().

        Posts the coroutine to the worker loop and waits for the result.
        Returns (valid_ids, invalid_id→reason).
        """
        if not ids:
            return set(), {}
        if self._loop is None or not self._loop.is_running():
            return set(ids), {i: "워커 미준비" for i in ids}
        result_q: "queue.Queue" = queue.Queue()
        future = asyncio.run_coroutine_threadsafe(
            self._do_validate(list(ids), result_q),
            self._loop,
        )
        try:
            valid, invalid = result_q.get(timeout=timeout)
            return valid, invalid
        except queue.Empty:
            return set(ids), {i: "validate 타임아웃" for i in ids}

    async def _do_validate(self, ids: list[int], result_q) -> None:
        try:
            valid, invalid = await self.validate_channel_ids(set(ids))
            result_q.put((valid, invalid))
        except Exception as e:
            result_q.put((set(), {i: f"validate 실패: {e}" for i in ids}))

    def provide_prompt_answer(self, text: str) -> None:
        self._prompt_q.put(text)

    def request_channel_resolve(self, username: str, timeout: float = 20.0) -> tuple[int, str]:
        """Ask the worker thread to resolve @username → (channel_id, title).

        Blocks the caller (GUI thread) until the worker responds or times out.
        Raises RuntimeError on failure / timeout.
        """
        with self._resolve_lock:
            self._resolve_seq += 1
            req_id = self._resolve_seq
        q: "queue.Queue" = queue.Queue()
        with self._resolve_lock:
            self._resolve_qs[req_id] = q
        # post to the worker's asyncio loop
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("텔레그램 워커가 아직 준비되지 않았습니다")
        try:
            asyncio.run_coroutine_threadsafe(
                self._do_resolve(req_id, username),
                self._loop,
            )
        except Exception as e:
            with self._resolve_lock:
                self._resolve_qs.pop(req_id, None)
            raise RuntimeError(f"resolve 요청 실패: {e}")
        try:
            result = q.get(timeout=timeout)
        except queue.Empty:
            with self._resolve_lock:
                self._resolve_qs.pop(req_id, None)
            raise RuntimeError("채널 확인 타임아웃")
        with self._resolve_lock:
            self._resolve_qs.pop(req_id, None)
        if isinstance(result, Exception):
            raise result
        return result  # (channel_id, title)

    async def _do_resolve(self, req_id: int, username: str) -> None:
        """Worker-side coroutine: resolve and post result to the requester's queue."""
        try:
            if self._client is None:
                raise RuntimeError("텔레그램이 아직 연결되지 않았습니다")
            entity = await resolve_channel(self._client, username)
            if entity is None:
                raise RuntimeError("채널을 찾을 수 없거나 접근 권한이 없습니다")
            result = (entity.id, entity.title or username)
        except Exception as e:
            result = e
        # deliver to the requester's queue (thread-safe)
        with self._resolve_lock:
            q = self._resolve_qs.get(req_id)
        if q is not None:
            try:
                q.put(result, timeout=2.0)
            except queue.Full:
                pass

    async def validate_channel_ids(self, ids: set[int]) -> tuple[set[int], dict[int, str]]:
        """Validate that ids correspond to actual broadcast channels.

        Returns (valid_ids, invalid_id→reason). Channels that resolve to
        a User (PeerUser) or Chat (small group) are rejected since they
        don't produce the broadcast events we need.
        """
        valid: set[int] = set()
        invalid: dict[int, str] = {}
        if self._client is None:
            return valid, {i: "client not ready" for i in ids}
        from telethon.tl.types import Channel as TLChannel
        for cid in ids:
            try:
                entity = await self._client.get_entity(cid)
            except Exception as e:
                invalid[cid] = f"get_entity failed: {e.__class__.__name__}"
                continue
            if not isinstance(entity, TLChannel):
                kind = type(entity).__name__
                # PeerUser (User) or Chat (legacy small group) — wrong type
                invalid[cid] = (
                    f"{kind} (broadcast 채널이 아님 — public 채널 @username만 추가 가능)"
                )
                continue
            valid.add(cid)
        return valid, invalid

    def stop(self) -> None:
        self._stopped = True
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ----- QThread.run -----

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as e:
            logger.exception("IngestWorker crashed")
            self.error.emit(f"IngestWorker: {e.__class__.__name__}: {e}")

    # ----- async main -----

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._client = TelegramClient(
            str(self._tg.session_path), self._tg.api_id, self._tg.api_hash
        )
        await self._client.connect()

        if not await self._client.is_user_authorized():
            self.status_update.emit("텔레그램 로그인 대기 중…")
            try:
                # start_client calls self._prompt_callback synchronously from
                # the asyncio loop, which then emits login_required signal.
                await start_client(
                    self._client,
                    phone=self._tg.phone,
                    prompt=self._prompt_callback,
                )
            except Exception as e:
                err = f"{e.__class__.__name__}: {e}"
                logger.exception("login failed")
                self.login_failure.emit(err)
                # keep the loop alive but stop collecting
                self._stopped = True
                return
            self.login_success.emit()
            self.status_update.emit("Telegram 인증 완료")

        me = await self._client.get_me()
        my_id = me.id if me else 0
        self.status_update.emit(f"Telegram 연결됨: {me.first_name if me else '?'}")

        # initial handler with empty set; main thread will push the real set
        self._re_register_handler(set(), my_id)
        self.status_update.emit("Telethon 핸들러 대기 중")

        # Phase 2.8: initial backfill for any channels that have never been ingested
        await self._initial_backfill(my_id)

        # wait for stop or channel updates
        while not self._stopped:
            try:
                # drain channel updates
                try:
                    new_ids = self._channel_q.get_nowait()
                except queue.Empty:
                    new_ids = None
                if new_ids is not None:
                    self._re_register_handler(new_ids, my_id)
                    self.status_update.emit(f"수집 채널: {len(new_ids)}개")
                    # Phase 2.8: history backfill for newly added channels
                    await self._backfill_new_channels(new_ids, my_id)

                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                break

        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.debug("client.disconnect failed during shutdown: %s", e)

    async def _backfill_new_channels(self, ids: set[int], my_id: int) -> None:
        """Backfill history for newly added channels (state in channel_q)."""
        from core.telegram.history import (
            fetch_channel_history, load_history_config,
        )
        from core.config import DATA_DIR
        from core.db import connection, repositories
        from telethon.tl.types import Channel as TLChannel

        cfg = load_history_config(DATA_DIR / "settings" / "history.json")
        if cfg.mode == "off":
            return
        for cid in ids:
            if cid == 0:
                continue
            conn = connection.get_connection(self._db_path)
            last = repositories.get_last_seen(conn, cid)
            if last > 0 and cfg.mode == "since_last":
                continue
            try:
                entity = await self._client.get_entity(cid)
                if not isinstance(entity, TLChannel):
                    continue
                self.status_update.emit(
                    f"히스토리 fetch: {entity.title or entity.username or cid}…"
                )
                res = await fetch_channel_history(
                    self._client, entity, self._db_path, cfg, self._on_message_async,
                )
                n = res.get("fetched", 0)
                self.status_update.emit(
                    f"히스토리 {n}건 ({entity.title or entity.username or cid}, mode={cfg.mode})"
                )
            except Exception as e:
                logger.warning("backfill failed for channel %s: %s", cid, e)

    async def _initial_backfill(self, my_id: int) -> None:
        """On startup, fetch history for all channels that have never been ingested.

        Driven by data/channels.json (ChannelStore); only runs once per channel
        per session (idempotent: ingest_state.last_message_id persists).
        """
        from core.telegram.history import (
            fetch_channel_history, load_history_config,
        )
        from core.config import DATA_DIR
        from core.db import connection, repositories
        from telethon.tl.types import Channel as TLChannel
        import json

        cfg = load_history_config(DATA_DIR / "settings" / "history.json")
        if cfg.mode == "off":
            self.status_update.emit("히스토리 모드: off (실시간만)")
            return

        channels_path = DATA_DIR / "channels.json"
        if not channels_path.exists():
            return
        try:
            data = json.loads(channels_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("channels.json parse failed: %s", e)
            return

        for entry in data:
            if not entry.get("enabled", True):
                continue
            cid = int(entry.get("id", 0))
            if cid == 0:
                # channel not yet resolved; skip — main thread will push it via channel_q
                continue
            conn = connection.get_connection(self._db_path)
            last = repositories.get_last_seen(conn, cid)
            if last > 0 and cfg.mode == "since_last":
                # already backfilled once
                continue
            try:
                entity = await self._client.get_entity(cid)
                if not isinstance(entity, TLChannel):
                    continue
                self.status_update.emit(
                    f"시작 백필: {entity.title or entity.username or cid}…"
                )
                res = await fetch_channel_history(
                    self._client, entity, self._db_path, cfg, self._on_message_async,
                )
                n = res.get("fetched", 0)
                self.status_update.emit(
                    f"백필 {n}건 ({entity.title or entity.username or cid})"
                )
            except Exception as e:
                logger.warning("initial backfill failed for %s: %s", cid, e)

    def _re_register_handler(self, ids: set[int], my_id: int) -> None:
        with self._handler_lock:
            if self._handler_registered:
                try:
                    self._client.remove_event_handler(self._handler)
                except Exception as e:
                    logger.debug("remove_event_handler failed: %s", e)
                self._handler_registered = False
            if not ids:
                return
            self._handler = make_handler(
                client_user_id=my_id,
                enabled_channel_ids=ids,
                on_message=self._on_message_async,
            )
            self._client.add_event_handler(self._handler, events.NewMessage)
            self._handler_registered = True

    async def _on_message_async(self, ingested: IngestedMessage) -> None:
        # insert into feed_items, then emit to main thread
        try:
            conn = connection.get_connection(self._db_path)
            feed_id = repositories.insert_feed(
                conn,
                datetime=ingested.datetime,
                channel_name=ingested.channel_name,
                channel_id=ingested.channel_id,
                message_text=ingested.message_text,
                message_url=ingested.message_url,
            )
            if feed_id is None:
                # duplicate (raw_hash UNIQUE)
                return
            ingested.feed_id = feed_id
            self.status_update.emit(
                f"+1 feed: {ingested.channel_name} ({len(ingested.message_text)} chars)"
            )
            self.message_collected.emit(feed_id, {
                "feed_id": feed_id,
                "datetime": ingested.datetime,
                "channel_name": ingested.channel_name,
                "channel_id": ingested.channel_id,
                "message_text": ingested.message_text,
                "message_url": ingested.message_url,
            })
        except Exception as e:
            logger.exception("on_message failed")
            self.error.emit(f"on_message: {e}")

    # ----- prompt bridge (blocks this worker thread until main answers) -----

    def _prompt_callback(self, message: str) -> str:
        """Called by start_client() from the asyncio loop in this worker.

        Telethon's sign-in needs the code/password returned synchronously from
        this callback. Qt forbids opening a modal dialog from a non-GUI thread,
        so we signal the main thread (which shows the dialog) and block here on
        a queue until the main thread posts the user's input via
        provide_prompt_answer().
        """
        logger.info("[login] prompt requested: %r", message)
        # Drain any stale answers from a previous prompt
        try:
            while True:
                self._prompt_q.get_nowait()
        except queue.Empty:
            pass

        is_password = "비밀번호" in message or "password" in message.lower()
        kind = "password" if is_password else "code"
        self._current_prompt = message
        self._login_kind = kind
        # emit (message, kind) directly so the main thread doesn't need to
        # poll worker state (avoids race conditions).
        self.login_required.emit(message, kind)
        logger.info("[login] signal emitted, waiting for user input (kind=%s)", kind)
        try:
            ans = self._prompt_q.get(timeout=300.0)
            logger.info("[login] got user input (len=%d)", len(ans))
            return ans.strip()
        except queue.Empty:
            logger.error("[login] timeout waiting for user input")
            raise RuntimeError("로그인 입력을 받지 못했습니다 (타임아웃)")

    @property
    def current_prompt(self) -> str:
        return getattr(self, "_current_prompt", "Telegram 로그인")

    @property
    def login_kind(self) -> str:
        return getattr(self, "_login_kind", "code")

    @property
    def current_prompt(self) -> str:
        return getattr(self, "_current_prompt", "Telegram 로그인")
