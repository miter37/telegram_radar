"""ReportWorker: QThread that triggers daily report generation + bot delivery.

Each minute checks if it's time to generate. Skips if a report for today
already exists.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from core.config import load_llm_config, load_user_interests
from core.llm.extractor import LLMExtractor
from core.report import (
    ReportConfig,
    generate_daily_report,
    get_daily_report,
    load_report_config,
    save_daily_report,
    save_report_config,
    send_to_telegram_bot,
)
from core.db import connection

logger = logging.getLogger(__name__)


class ReportWorker(QThread):
    report_ready = Signal(dict)        # {date, title, body, sent, error}
    status_update = Signal(str)

    def __init__(self, db_path: Path, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._stopped = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._extractor: Optional[LLMExtractor] = None
        self._last_fired_date: Optional[str] = None
        self._last_tick: Optional[datetime] = None

    def stop(self) -> None:
        self._stopped = True
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def run(self) -> None:
        try:
            asyncio.run(self._amain())
        except Exception as e:
            logger.exception("ReportWorker crashed")
            self.status_update.emit(f"ReportWorker: {e.__class__.__name__}: {e}")

    async def _amain(self) -> None:
        self._loop = asyncio.get_running_loop()
        llm_cfg = load_llm_config()
        self._extractor = LLMExtractor(
            base_url=llm_cfg.base_url,
            api_key=llm_cfg.api_key,
            model=llm_cfg.model,
        )
        self.status_update.emit("ReportWorker 시작 (매 분 체크)")
        while not self._stopped:
            try:
                await self._tick()
            except Exception as e:
                logger.exception("tick failed")
                self.status_update.emit(f"리포트 tick 실패: {e}")
            await asyncio.sleep(60.0)
        await self._extractor.close()

    async def _tick(self) -> None:
        """One check + maybe run the daily report."""
        cfg = load_report_config()
        if not cfg.enabled:
            return
        now = datetime.now()
        # Cooldown: don't re-run the same date within 10 minutes
        if self._last_tick and (now - self._last_tick) < timedelta(minutes=10):
            return
        self._last_tick = now
        if now.hour != cfg.hour or now.minute < cfg.minute or now.minute >= cfg.minute + 10:
            return
        # Pick "yesterday" in KST. For simplicity, use today's date — the user
        # usually wants the day's morning summary of what was collected.
        target_date = now.strftime("%Y-%m-%d")
        if self._last_fired_date == target_date:
            return
        # already exists?
        conn = connection.get_connection(self._db_path)
        if get_daily_report(conn, target_date) is not None:
            self._last_fired_date = target_date
            return
        self.status_update.emit(f"일간 리포트 생성 중: {target_date}")
        interests = load_user_interests() if cfg.include_user_interests else []
        result = await generate_daily_report(
            extractor=self._extractor,
            date=target_date,
            db_path=self._db_path,
            user_interests=interests,
        )
        if not result.ok:
            self.status_update.emit(f"리포트 생성 실패: {result.error}")
            self.report_ready.emit({
                "date": target_date, "title": "(실패)",
                "body": "", "sent": False, "error": result.error,
            })
            self._last_fired_date = target_date
            return
        # persist
        save_daily_report(
            conn,
            date=target_date,
            title=result.title or f"{target_date} 일간 리포트",
            body=result.body_text,
            payload=result.payload or {},
            sent_to_bot=False,
        )
        # send to bot
        sent = False
        sent_error = None
        if cfg.bot_token and cfg.bot_chat_id:
            ok, info = await send_to_telegram_bot(
                bot_token=cfg.bot_token,
                chat_id=cfg.bot_chat_id,
                text=result.body_text,
            )
            sent = ok
            if ok:
                save_daily_report(
                    conn,
                    date=target_date,
                    title=result.title or f"{target_date} 일간 리포트",
                    body=result.body_text,
                    payload=result.payload or {},
                    sent_to_bot=True,
                    bot_chat_id=cfg.bot_chat_id,
                )
                self.status_update.emit(f"리포트 봇 전송 완료: {target_date}")
            else:
                sent_error = info
                self.status_update.emit(f"리포트 봇 전송 실패: {info}")
        else:
            self.status_update.emit(
                f"리포트 생성 완료 (봇 미설정): {target_date}"
            )
        self._last_fired_date = target_date
        self.report_ready.emit({
            "date": target_date,
            "title": result.title,
            "body": result.body_text,
            "sent": sent,
            "error": sent_error,
        })
