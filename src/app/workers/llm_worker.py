"""LLMWorker: QThread that processes feed items through the LLM.

Queue-based: IngestWorker emits message_collected → main thread
calls LLMWorker.enqueue(). LLMWorker pulls from the queue, calls the
OpenAI-compatible endpoint, and writes feed_signals + signal_tags + tags.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from core.config import LLMConfig
from core.db import connection, repositories
from core.db.repositories import FeedSignal
from core.llm.extractor import LLMExtractor
from core.normalize.tags import TagNormalizer

logger = logging.getLogger(__name__)


class LLMWorker(QThread):
    signal_ready = Signal(object)   # FeedSignal
    alert_ready = Signal(dict)      # {signal, reason}
    status_update = Signal(str)
    error = Signal(str)
    stats_update = Signal(dict)     # {feeds, signals, llm_ok, llm_fail, ...}

    def __init__(
        self,
        llm_cfg: LLMConfig,
        db_path: Path,
        user_interests_provider,  # callable returning list[str]
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._llm_cfg = llm_cfg
        self._db_path = db_path
        self._interests_provider = user_interests_provider
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._stopped = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._extractor: Optional[LLMExtractor] = None
        self._normalizer: Optional[TagNormalizer] = None

    def enqueue(self, payload: dict) -> None:
        self._q.put(payload)

    def enqueue_batch(self, payloads: list[dict]) -> None:
        """Enqueue multiple items; worker will process as micro-batch.

        Each payload in the list is the same dict format as enqueue() expects.
        """
        for p in payloads:
            self._q.put(p)

    def stop(self) -> None:
        self._stopped = True
        self._q.put(None)  # wake the loop
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as e:
            logger.exception("LLMWorker crashed")
            self.error.emit(f"LLMWorker: {e.__class__.__name__}: {e}")

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._extractor = LLMExtractor(
            base_url=self._llm_cfg.base_url,
            api_key=self._llm_cfg.api_key,
            model=self._llm_cfg.model,
        )
        # try to resolve model early; failure is non-fatal
        try:
            model_id = await self._extractor.resolve_model()
            self.status_update.emit(f"LLM 모델: {model_id}")
        except Exception as e:
            self.status_update.emit(f"LLM 모델 자동 확인 실패: {e}")
        self._emit_stats()

        # Phase 2.6: micro-batch (drain queue, process concurrently with semaphore)
        batch_concurrency = 4
        sem = asyncio.Semaphore(batch_concurrency)

        async def _one(item):
            async with sem:
                try:
                    await self._process(item)
                except Exception as e:
                    logger.exception("process failed")
                    self.error.emit(f"LLM process: {e}")

        while not self._stopped:
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                continue
            # drain additional items within 50ms window
            batch = [item]
            try:
                while len(batch) < 10:
                    extra = self._q.get_nowait()
                    if extra is None:
                        break
                    batch.append(extra)
            except queue.Empty:
                pass
            await asyncio.gather(*[_one(it) for it in batch])
            self._emit_stats()

        await self._extractor.close()

    async def _process(self, item: dict) -> None:
        feed_id: int = item["feed_id"]
        datetime_str: str = item["datetime"]
        channel_name: str = item["channel_name"]
        message_text: str = item["message_text"]
        message_url: Optional[str] = item.get("message_url")
        interests = self._interests_provider() or []

        # Parse datetime for date column
        date_str = datetime_str[:10]

        result = await self._extractor.extract(
            datetime=datetime_str,
            channel_name=channel_name,
            message_text=message_text,
            message_url=message_url,
            user_interests=interests,
        )

        # Always log the extraction attempt
        conn = connection.get_connection(self._db_path)
        repositories.insert_llm_extraction(
            conn,
            feed_id=feed_id,
            prompt_version=result.prompt_version,
            raw_json=result.raw_text or json.dumps({"error": result.error}, ensure_ascii=False),
            parsed_ok=result.ok,
            error_message=None if result.ok else result.error,
        )

        if not result.ok or result.payload is None:
            self.status_update.emit(
                f"LLM 실패: feed#{feed_id} ({result.error})"
            )
            return

        p = result.payload
        importance = int(p.get("importance_score", 0))
        interest = int(p.get("interest_score", 0))
        topic_str = p.get("topic", "")
        main_content = p.get("main_content", "")

        # Tags: build raw list (will be normalized)
        groups = p.get("tag_groups", {}) or {}
        raw_tags: list[tuple[str, str]] = []
        for grp in ("companies", "people", "industries"):
            target = grp.rstrip("s")
            for t in groups.get(grp, []) or []:
                if isinstance(t, str) and t.strip():
                    raw_tags.append((t.strip(), target))
        for t in p.get("tags", []) or []:
            if isinstance(t, str) and t.strip():
                raw_tags.append((t.strip(), "industry"))

        if self._normalizer is None:
            self._normalizer = TagNormalizer(conn)

        normalized_tags: list = []
        seen = set()
        for name, grp_hint in raw_tags:
            try:
                norm = self._normalizer.normalize(name, group_hint=grp_hint)
            except Exception as e:
                logger.warning("normalize failed for %r: %s", name, e)
                continue
            key = (norm.canonical_id, norm.group)
            if key in seen:
                continue
            seen.add(key)
            normalized_tags.append(norm)

        # Phase 2.3: alert decision (importance/interest criteria + cooldown)
        from core.analytics.alert import (
            decide, last_alert_for_topic, load_criteria,
        )
        from core.config import DATA_DIR
        criteria = load_criteria(DATA_DIR / "settings" / "alerts.json")
        last_at = last_alert_for_topic(conn, topic_str)
        decision = decide(
            importance=importance,
            interest=interest,
            topic=topic_str,
            channel=channel_name,
            criteria=criteria,
            last_alert_at=last_at,
        )
        should_alert = decision.should_alert or bool(p.get("should_alert", False))
        alert_reason = decision.reason

        sig_id = repositories.insert_feed_signal(
            conn,
            feed_id=feed_id,
            date=date_str,
            channel_name=channel_name,
            topic=topic_str,
            main_content=main_content,
            importance_score=importance,
            interest_score=interest,
            should_alert=should_alert,
        )

        for norm in normalized_tags:
            try:
                repositories.insert_signal_tag(
                    conn,
                    feed_id=feed_id,
                    signal_id=sig_id,
                    canonical_tag_id=norm.canonical_id,
                    canonical_name=norm.canonical_name,
                    tag_group=norm.group,
                    confidence=norm.confidence,
                )
            except Exception as e:
                logger.warning("tag insert failed: %s", e)

        sig = FeedSignal(
            id=sig_id,
            feed_id=feed_id,
            date=date_str,
            channel_name=channel_name,
            topic=topic_str,
            main_content=main_content,
            importance_score=importance,
            interest_score=interest,
            should_alert=should_alert,
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            tags=[n.canonical_name for n in normalized_tags],
        )
        self.signal_ready.emit(sig)
        if should_alert:
            self.alert_ready.emit({
                "signal": sig,
                "reason": alert_reason,
            })
            self.status_update.emit(
                f"🚨 ALERT: {channel_name} · {topic_str} (imp={importance} int={interest})"
            )
        else:
            self.status_update.emit(
                f"LLM OK: {channel_name} · {topic_str} · imp={importance}"
            )

        # Phase 1.7: auto-update daily_topics for this signal's date
        try:
            from core.analytics.flow import store_daily_topics
            store_daily_topics(conn, date_str, top_n=5)
        except Exception as e:
            logger.debug("daily_topics update skipped: %s", e)

        # Phase 3.5: auto-link to tickers based on tag names
        try:
            from core.ticker_link import link_signal_tickers
            n_links = link_signal_tickers(conn, sig_id)
            if n_links:
                self.status_update.emit(f"🔗 ticker link: {n_links}개")
        except Exception as e:
            logger.debug("ticker link skipped: %s", e)

    def _emit_stats(self) -> None:
        try:
            conn = connection.get_connection(self._db_path)
            self.stats_update.emit(repositories.stats(conn))
        except Exception as e:
            logger.debug("stats emit failed: %s", e)
