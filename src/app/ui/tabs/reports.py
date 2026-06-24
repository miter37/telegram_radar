"""일간 리포트 탭: 날짜 리스트 + 본문 뷰 + 즉시 생성 버튼."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.config import load_llm_config, load_user_interests
from core.db import connection, repositories
from core.llm.extractor import LLMExtractor
from core.report import (
    ReportConfig,
    format_report_text,
    generate_daily_report,
    get_daily_report,
    get_daily_report_dates,
    load_report_config,
    save_daily_report,
    send_to_telegram_bot,
)

logger = logging.getLogger(__name__)


class GenerateThread(QThread):
    finished_ok = Signal(dict)
    finished_err = Signal(str)
    progress = Signal(str)

    def __init__(self, date: str, db_path, user_interests: list[str]):
        super().__init__()
        self._date = date
        self._db_path = db_path
        self._interests = user_interests

    def run(self) -> None:
        try:
            asyncio.run(self._amain())
        except Exception as e:
            logger.exception("GenerateThread crashed")
            self.finished_err.emit(f"{e.__class__.__name__}: {e}")

    async def _amain(self) -> None:
        llm_cfg = load_llm_config()
        ex = LLMExtractor(
            base_url=llm_cfg.base_url,
            api_key=llm_cfg.api_key,
            model=llm_cfg.model,
        )
        self.progress.emit("LLM 호출 중…")
        res = await generate_daily_report(
            extractor=ex,
            date=self._date,
            db_path=self._db_path,
            user_interests=self._interests,
        )
        await ex.close()
        if not res.ok:
            self.finished_err.emit(res.error or "unknown")
            return
        conn = connection.get_connection(self._db_path)
        save_daily_report(
            conn,
            date=self._date,
            title=res.title or f"{self._date} 일간 리포트",
            body=res.body_text,
            payload=res.payload or {},
            sent_to_bot=False,
        )
        self.finished_ok.emit({
            "date": self._date,
            "title": res.title,
            "body": res.body_text,
        })


class SendToBotThread(QThread):
    finished_ok = Signal(str)
    finished_err = Signal(str)

    def __init__(self, bot_token: str, chat_id: str, text: str, date: str, db_path):
        super().__init__()
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._text = text
        self._date = date
        self._db_path = db_path

    def run(self) -> None:
        try:
            asyncio.run(self._amain())
        except Exception as e:
            self.finished_err.emit(f"{e.__class__.__name__}: {e}")

    async def _amain(self) -> None:
        ok, info = await send_to_telegram_bot(
            bot_token=self._bot_token,
            chat_id=self._chat_id,
            text=self._text,
        )
        if not ok:
            self.finished_err.emit(info)
            return
        conn = connection.get_connection(self._db_path)
        cur = conn.execute(
            "UPDATE daily_reports SET sent_to_bot=1, bot_chat_id=?, sent_at=? WHERE report_date=?",
            (self._chat_id, datetime.now().astimezone().isoformat(timespec="seconds"),
             self._date),
        )
        conn.commit()
        self.finished_ok.emit(info)


class ReportsTab(QFrame):
    def __init__(self, db_path, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self.setObjectName("reportsTab")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QFrame()
        head.setObjectName("paneHead")
        head.setFixedHeight(40)
        h = QHBoxLayout(head)
        h.setContentsMargins(12, 0, 12, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("일간 리포트")
        title.setObjectName("paneTitle")
        sub = QLabel("Phase 3 B6: LLM 일간 요약 + 텔레그램 봇 전송")
        sub.setObjectName("paneSub")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        h.addLayout(title_box, 1)
        btn_generate = QPushButton("오늘 리포트 생성")
        btn_generate.setProperty("class", "primary")
        btn_generate.setCursor(Qt.PointingHandCursor)
        btn_generate.clicked.connect(self._on_generate_today)
        h.addWidget(btn_generate)
        btn_refresh = QPushButton("새로고침")
        btn_refresh.setProperty("class", "miniBtn")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self.refresh)
        h.addWidget(btn_refresh)
        layout.addWidget(head)

        # Body
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Date list
        self._date_list = QListWidget()
        self._date_list.setStyleSheet(
            "QListWidget { background: #121820; border: 0; border-right: 1px solid #303746; }"
            "QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #202632; color: #cbd5e1; }"
            "QListWidget::item:selected { background: #1f4f82; color: #ffffff; }"
        )
        self._date_list.setFixedWidth(220)
        self._date_list.itemSelectionChanged.connect(self._on_date_changed)
        body.addWidget(self._date_list)

        # Report body
        right = QFrame()
        right.setStyleSheet("background: #0f141b;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        # Action bar
        action_bar = QFrame()
        action_bar.setStyleSheet("background: #151a22; border-bottom: 1px solid #303746;")
        action_bar.setFixedHeight(36)
        ab = QHBoxLayout(action_bar)
        ab.setContentsMargins(10, 4, 10, 4)
        self._meta = QLabel("(리포트 선택)")
        self._meta.setStyleSheet("color: #8f98a8; font-size: 11px;")
        ab.addWidget(self._meta, 1)
        self._btn_send = QPushButton("봇으로 전송")
        self._btn_send.setProperty("class", "miniBtn")
        self._btn_send.setCursor(Qt.PointingHandCursor)
        self._btn_send.clicked.connect(self._on_send_to_bot)
        self._btn_send.setEnabled(False)
        ab.addWidget(self._btn_send)
        rl.addWidget(action_bar)

        # Body
        self._body = QTextEdit()
        self._body.setReadOnly(True)
        self._body.setStyleSheet(
            "QTextEdit { background: #0e131a; color: #d7dde8; "
            "font-family: 'Noto Sans KR', sans-serif; font-size: 13px; "
            "border: 0; padding: 14px; }"
        )
        rl.addWidget(self._body, 1)

        # Status
        self._status = QLabel("")
        self._status.setStyleSheet(
            "color: #697386; font-size: 11px; "
            "font-family: 'Consolas','Liberation Mono',monospace; "
            "padding: 4px 12px; background: #151a22; border-top: 1px solid #303746;"
        )
        rl.addWidget(self._status)

        body.addWidget(right, 1)
        layout.addLayout(body, 1)

        self._current_date: Optional[str] = None
        self._current_body: str = ""
        self.refresh()

    def _conn(self):
        return connection.get_connection(self._db_path)

    def refresh(self) -> None:
        conn = self._conn()
        dates = get_daily_report_dates(conn, limit=60)
        self._date_list.clear()
        if not dates:
            placeholder = QListWidgetItem("(리포트 없음)")
            placeholder.setFlags(Qt.NoItemFlags)
            self._date_list.addItem(placeholder)
            self._body.setPlainText(
                "아직 일간 리포트가 없습니다.\n"
                "[오늘 리포트 생성] 버튼을 눌러 즉시 생성하거나, "
                "설정 탭의 [일간 리포트] 패널에서 자동 생성을 켜세요."
            )
            self._meta.setText("(리포트 선택)")
            self._btn_send.setEnabled(False)
            return
        for d in dates:
            self._date_list.addItem(QListWidgetItem(d))
        self._date_list.setCurrentRow(0)

    def _on_date_changed(self) -> None:
        items = self._date_list.selectedItems()
        if not items:
            return
        date = items[0].text()
        self._render_date(date)

    def _render_date(self, date: str) -> None:
        conn = self._conn()
        r = get_daily_report(conn, date)
        if r is None:
            return
        self._current_date = date
        self._current_body = r["body"]
        # render as markdown-ish plain text with a little styling
        self._body.setPlainText(r["body"])
        sent_mark = "✓ 봇 전송됨" if r["sent_to_bot"] else "로컬만"
        self._meta.setText(
            f"{date}  ·  {r['title']}  ·  {sent_mark}"
        )
        self._btn_send.setEnabled(not r["sent_to_bot"])

    def _on_generate_today(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        self._status.setText(f"{today} 리포트 생성 중…")
        cfg = load_report_config()
        interests = load_user_interests() if cfg.include_user_interests else []
        self._gen_thread = GenerateThread(today, self._db_path, interests)
        self._gen_thread.progress.connect(self._status.setText)
        self._gen_thread.finished_ok.connect(self._on_generate_ok)
        self._gen_thread.finished_err.connect(self._on_generate_err)
        self._gen_thread.start()

    def _on_generate_ok(self, result: dict) -> None:
        self._status.setText(f"생성 완료: {result['date']}")
        self.refresh()
        # select the newly added date
        for i in range(self._date_list.count()):
            if self._date_list.item(i).text() == result["date"]:
                self._date_list.setCurrentRow(i)
                break

    def _on_generate_err(self, err: str) -> None:
        self._status.setText(f"실패: {err}")

    def _on_send_to_bot(self) -> None:
        if not self._current_date or not self._current_body:
            return
        cfg = load_report_config()
        if not cfg.bot_token or not cfg.bot_chat_id:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "봇 미설정",
                "설정 탭의 [일간 리포트] 패널에서 봇 토큰과 chat_id를 먼저 설정하세요."
            )
            return
        self._status.setText("봇 전송 중…")
        self._send_thread = SendToBotThread(
            cfg.bot_token, cfg.bot_chat_id,
            self._current_body, self._current_date, self._db_path,
        )
        self._send_thread.finished_ok.connect(self._on_send_ok)
        self._send_thread.finished_err.connect(self._on_send_err)
        self._send_thread.start()

    def _on_send_ok(self, info: str) -> None:
        self._status.setText(f"봇 전송 완료: {info}")
        self.refresh()

    def _on_send_err(self, err: str) -> None:
        self._status.setText(f"봇 전송 실패: {err}")
