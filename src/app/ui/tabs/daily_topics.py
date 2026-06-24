"""일자별 주제 리포트 탭 (완전 재작성).

Layout:
- 좌측: 날짜 리스트
- 상단: 컨트롤 (실행 / 재실행 / 슬라이더 / MD 폴더)
- 우측: 선택 날짜의 주제 카드 리스트
  - 카드: 제목 + 요약 + 펼침/접힘
  - 펼친 상태: 본문 + 타임라인 + watchlist + 메시지 참조 카드
  - 메시지 카드: [원문 보기] → RawFeedModal (app://feed/ID 클릭)
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.config import DATA_DIR, load_llm_config, load_user_interests
from core.db import connection, repositories
from core.llm.extractor import LLMExtractor
from core.topic_report import (
    gather_daily_signals,
    reports_dir_for,
    run_daily_topic_pipeline,
)
from app.ui.widgets.raw_feed_modal import RawFeedModal

logger = logging.getLogger(__name__)


class PipelineThread(QThread):
    finished_ok = Signal(dict)
    finished_err = Signal(str)
    progress = Signal(str)

    def __init__(self, date: str, db_path, max_topics: int = 12):
        super().__init__()
        self._date = date
        self._db_path = db_path
        self._max_topics = max_topics

    def run(self) -> None:
        try:
            asyncio.run(self._amain())
        except Exception as e:
            logger.exception("PipelineThread crashed")
            self.finished_err.emit(f"{e.__class__.__name__}: {e}")

    async def _amain(self) -> None:
        from core.llm.engines import EngineRegistry
        llm_cfg = load_llm_config()
        ex = LLMExtractor(
            base_url=llm_cfg.base_url,
            api_key=llm_cfg.api_key,
            model=llm_cfg.model,
        )
        registry = EngineRegistry()
        ex.set_registry(registry)
        self.progress.emit("LLM 준비 중…")
        res = await run_daily_topic_pipeline(
            extractor=ex,
            db_path=self._db_path,
            date=self._date,
            max_topics=self._max_topics,
            progress=lambda msg: self.progress.emit(msg),
        )
        await ex.close()
        if not res["ok"]:
            self.finished_err.emit(res.get("error") or "unknown")
            return
        self.finished_ok.emit(res)


class TopicReportCard(QFrame):
    """One topic report card (collapsible)."""

    raw_clicked = Signal(int)  # feed_id

    def __init__(
        self,
        report: dict,
        signals_lookup: dict[int, dict],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._report = report
        self._signals = signals_lookup
        self._expanded = False

        self.setStyleSheet(
            "QFrame { background: #141a23; border: 1px solid #303746; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # header (clickable to expand)
        head = QFrame()
        head.setCursor(Qt.PointingHandCursor)
        head.setStyleSheet(
            "QFrame { background: #1a202b; border-bottom: 1px solid #303746; }"
        )
        head.setFixedHeight(40)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 0, 10, 0)
        self._title_lbl = QLabel(
            f"{report['topic_idx']}. {report['label']}"
        )
        self._title_lbl.setStyleSheet(
            "color: #e5edf8; font-weight: 760; font-size: 14px;"
        )
        hl.addWidget(self._title_lbl, 1)
        avg_imp = report.get("avg_importance", 0)
        avg_int = report.get("avg_interest", 0)
        score_color = (
            "#ff8494" if avg_imp >= 80 else
            "#eac45c" if avg_imp >= 50 else "#8bbef8"
        )
        score_lbl = QLabel(
            f"imp {avg_imp:.0f} / int {avg_int:.0f}"
        )
        score_lbl.setStyleSheet(
            f"color: {score_color}; font-family: 'Consolas','Liberation Mono',monospace; "
            f"font-size: 12px; font-weight: 800;"
        )
        hl.addWidget(score_lbl)
        self._toggle_lbl = QLabel("▼ 펼치기")
        self._toggle_lbl.setStyleSheet(
            "color: #4ea1ff; font-size: 12px; padding-left: 12px;"
        )
        hl.addWidget(self._toggle_lbl)
        layout.addWidget(head)
        # Make head clickable
        head.mouseReleaseEvent = lambda ev: self._toggle()

        # body (initially hidden)
        self._body = QFrame()
        self._body.setVisible(False)
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(10)

        # summary
        sum_lbl = QLabel("요약")
        sum_lbl.setStyleSheet(
            "color: #c7dcf8; font-size: 11px; "
            "font-family: 'Consolas','Liberation Mono',monospace;"
        )
        bl.addWidget(sum_lbl)
        sum_text = QLabel(report.get("summary", ""))
        sum_text.setStyleSheet("color: #d3dbe8; font-size: 13px; line-height: 1.5;")
        sum_text.setWordWrap(True)
        bl.addWidget(sum_text)

        # body_md
        if report.get("body_md"):
            bl.addSpacing(4)
            body_lbl = QLabel("상세 분석")
            body_lbl.setStyleSheet(
                "color: #c7dcf8; font-size: 11px; "
                "font-family: 'Consolas','Liberation Mono',monospace;"
            )
            bl.addWidget(body_lbl)
            body_text = QLabel(report["body_md"])
            body_text.setStyleSheet("color: #b8c1d0; font-size: 12px; line-height: 1.5;")
            body_text.setWordWrap(True)
            bl.addWidget(body_text)

        # timeline
        if report.get("timeline"):
            bl.addSpacing(4)
            tl_lbl = QLabel("타임라인")
            tl_lbl.setStyleSheet(
                "color: #c7dcf8; font-size: 11px; "
                "font-family: 'Consolas','Liberation Mono',monospace;"
            )
            bl.addWidget(tl_lbl)
            for t in report["timeline"]:
                tm = t.get("time", "")
                note = t.get("note", "")
                line = QLabel(f"<b style='color:#b9d8ff'>{tm}</b> — {note}")
                line.setStyleSheet("color: #b8c1d0; font-size: 12px;")
                line.setWordWrap(True)
                bl.addWidget(line)

        # watchlist
        if report.get("watchlist"):
            bl.addSpacing(4)
            wl_lbl = QLabel("내일 주시")
            wl_lbl.setStyleSheet(
                "color: #c7dcf8; font-size: 11px; "
                "font-family: 'Consolas','Liberation Mono',monospace;"
            )
            bl.addWidget(wl_lbl)
            for w in report["watchlist"]:
                line = QLabel(f"☐ {w}")
                line.setStyleSheet("color: #eac45c; font-size: 12px;")
                line.setWordWrap(True)
                bl.addWidget(line)

        # message refs
        bl.addSpacing(6)
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("background: #303746; max-height: 1px; border: 0;")
        bl.addWidget(div)

        top_ids = report.get("top_signal_ids") or []
        ref_lbl = QLabel(
            f"관련 원문 ({len(top_ids)}건, 중요도순)"
        )
        ref_lbl.setStyleSheet(
            "color: #c7dcf8; font-size: 11px; "
            "font-family: 'Consolas','Liberation Mono',monospace;"
        )
        bl.addWidget(ref_lbl)

        for sid in top_ids:
            s = self._signals.get(sid)
            if s is None:
                continue
            bl.addWidget(self._make_message_card(s))

        # MD path
        if report.get("md_path"):
            bl.addSpacing(4)
            md_row = QHBoxLayout()
            md_lbl = QLabel(f"MD: {report['md_path']}")
            md_lbl.setStyleSheet(
                "color: #697386; font-size: 11px; "
                "font-family: 'Consolas','Liberation Mono',monospace;"
            )
            md_row.addWidget(md_lbl, 1)
            btn_open = QPushButton("MD 폴더 열기")
            btn_open.setProperty("class", "miniBtn")
            btn_open.setCursor(Qt.PointingHandCursor)
            btn_open.clicked.connect(self._open_md_dir)
            md_row.addWidget(btn_open)
            bl.addLayout(md_row)

        layout.addWidget(self._body)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._toggle_lbl.setText("▲ 접기" if self._expanded else "▼ 펼치기")

    def _make_message_card(self, s: dict) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: #0e131a; border: 1px solid #303746; }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(4)
        # header row
        hr = QHBoxLayout()
        imp_color = (
            "#ff8494" if s["importance"] >= 80 else
            "#eac45c" if s["importance"] >= 50 else "#8bbef8"
        )
        score_lbl = QLabel(f"[{s['importance']}]")
        score_lbl.setStyleSheet(
            f"color: {imp_color}; font-family: 'Consolas','Liberation Mono',monospace; "
            f"font-weight: 800; font-size: 12px;"
        )
        hr.addWidget(score_lbl)
        meta = QLabel(f"{s['time']} · {s['channel']} · {s['topic']}")
        meta.setStyleSheet("color: #c7dcf8; font-size: 12px;")
        hr.addWidget(meta, 1)
        btn_raw = QPushButton("원문 보기")
        btn_raw.setProperty("class", "miniBtn")
        btn_raw.setCursor(Qt.PointingHandCursor)
        btn_raw.clicked.connect(lambda _checked=False, fid=s["feed_id"]: self.raw_clicked.emit(fid))
        hr.addWidget(btn_raw)
        v.addLayout(hr)
        # excerpt
        excerpt = (s.get("raw_text") or s.get("main_content") or "")[:200]
        if len(s.get("raw_text") or "") > 200:
            excerpt += "…"
        ex_lbl = QLabel(excerpt)
        ex_lbl.setStyleSheet("color: #b8c1d0; font-size: 12px;")
        ex_lbl.setWordWrap(True)
        v.addWidget(ex_lbl)
        return card

    def _open_md_dir(self) -> None:
        if not self._report.get("md_path"):
            return
        p = Path(self._report["md_path"]).parent
        if not p.exists():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


class DailyTopicsTab(QFrame):
    def __init__(self, db_path, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self.setObjectName("dailyTopics")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Head
        head = QFrame()
        head.setObjectName("paneHead")
        head.setFixedHeight(40)
        h = QHBoxLayout(head)
        h.setContentsMargins(12, 0, 12, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("일자별 주제 리포트")
        title.setObjectName("paneTitle")
        sub = QLabel("2-stage LLM: 클러스터링 → 종합. MD 저장. 클릭으로 원문.")
        sub.setObjectName("paneSub")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        h.addLayout(title_box, 1)
        layout.addWidget(head)

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

        # Right pane
        right = QFrame()
        right.setStyleSheet("background: #0f141b;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        # control bar
        ctrl = QFrame()
        ctrl.setStyleSheet("background: #151a22; border-bottom: 1px solid #303746;")
        ctrl.setFixedHeight(48)
        cl = QHBoxLayout(ctrl)
        cl.setContentsMargins(10, 6, 10, 6)
        cl.setSpacing(8)
        btn_run = QPushButton("⚡ 2-stage LLM 실행")
        btn_run.setProperty("class", "primary")
        btn_run.setCursor(Qt.PointingHandCursor)
        btn_run.clicked.connect(self._on_run)
        cl.addWidget(btn_run)
        btn_rerun = QPushButton("🔄 재실행")
        btn_rerun.setProperty("class", "miniBtn")
        btn_rerun.setCursor(Qt.PointingHandCursor)
        btn_rerun.clicked.connect(self._on_rerun)
        cl.addWidget(btn_rerun)
        cl.addSpacing(8)
        cl.addWidget(QLabel("주제 수"))
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(5, 20)
        self._slider.setValue(12)
        self._slider.setFixedWidth(120)
        cl.addWidget(self._slider)
        self._slider_label = QLabel("12")
        self._slider_label.setStyleSheet(
            "color: #c7dcf8; font-family: 'Consolas','Liberation Mono',monospace; font-size: 12px;"
        )
        self._slider_label.setFixedWidth(24)
        self._slider.valueChanged.connect(
            lambda v: self._slider_label.setText(str(v))
        )
        cl.addWidget(self._slider_label)
        cl.addStretch(1)
        rl.addWidget(ctrl)

        # card area (scroll)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._card_host = QFrame()
        self._card_host.setStyleSheet("background: #0f141b;")
        self._card_layout = QVBoxLayout(self._card_host)
        self._card_layout.setContentsMargins(10, 10, 10, 10)
        self._card_layout.setSpacing(8)
        self._card_layout.addStretch(1)
        scroll.setWidget(self._card_host)
        rl.addWidget(scroll, 1)

        # status
        self._status = QLabel("날짜를 선택하세요")
        self._status.setStyleSheet(
            "color: #697386; font-size: 11px; "
            "font-family: 'Consolas','Liberation Mono',monospace; "
            "padding: 4px 12px; background: #151a22; border-top: 1px solid #303746;"
        )
        rl.addWidget(self._status)

        body.addWidget(right, 1)
        layout.addLayout(body, 1)

        self._current_date: Optional[str] = None
        self._pipeline_thread: Optional[PipelineThread] = None
        self.refresh()

    def _conn(self):
        return connection.get_connection(self._db_path)

    def refresh(self) -> None:
        """Reload date list from DB."""
        conn = self._conn()
        dates = repositories.list_topic_report_dates(conn, limit=60)
        prev = self._current_date
        self._date_list.blockSignals(True)
        self._date_list.clear()
        if not dates:
            placeholder = QListWidgetItem("(생성된 리포트 없음)")
            placeholder.setFlags(Qt.NoItemFlags)
            self._date_list.addItem(placeholder)
            self._status.setText(
                "오른쪽 [⚡ 2-stage LLM 실행] 버튼으로 첫 리포트를 생성하세요"
            )
        else:
            for d in dates:
                self._date_list.addItem(QListWidgetItem(d))
        self._date_list.blockSignals(False)
        if prev and prev in dates:
            # restore selection
            for i in range(self._date_list.count()):
                if self._date_list.item(i).text() == prev:
                    self._date_list.setCurrentRow(i)
                    break
        elif dates:
            self._date_list.setCurrentRow(0)
        else:
            self._clear_cards()

    def _on_date_changed(self) -> None:
        items = self._date_list.selectedItems()
        if not items:
            return
        date = items[0].text()
        self._current_date = date
        self._render_date(date)

    def _clear_cards(self) -> None:
        while self._card_layout.count():
            item = self._card_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._card_layout.addStretch(1)

    def _render_date(self, date: str) -> None:
        self._clear_cards()
        conn = self._conn()
        reports = repositories.list_topic_reports(conn, date)
        if not reports:
            self._status.setText(
                f"{date} — 리포트 없음. [⚡ 2-stage LLM 실행] 또는 [🔄 재실행] 버튼을 눌러 생성."
            )
            return
        # build signals lookup
        signals = gather_daily_signals(conn, date)
        sig_map = {s["signal_id"]: s for s in signals}
        for r in reports:
            card = TopicReportCard(r, sig_map, self)
            card.raw_clicked.connect(self._on_raw_clicked)
            self._card_layout.addWidget(card)
        self._card_layout.addStretch(1)
        self._status.setText(
            f"{date} — {len(reports)}개 주제 리포트"
        )

    def _on_raw_clicked(self, feed_id: int) -> None:
        conn = self._conn()
        feed = repositories.get_feed(conn, feed_id)
        if feed is None:
            return
        # find matching signal for additional context
        sig = None
        for s in repositories.list_signals(conn, limit=500):
            if s.feed_id == feed_id:
                sig = s
                break
        modal = RawFeedModal(
            feed_id=feed.id,
            datetime=feed.datetime,
            channel_name=feed.channel_name,
            topic=sig.topic if sig else "",
            main_content=sig.main_content if sig else "",
            importance_score=sig.importance_score if sig else 0,
            interest_score=sig.interest_score if sig else 0,
            tags=sig.tags if sig else [],
            message_text=feed.message_text,
            message_url=feed.message_url,
            parent=self.window() if self.window() else self,
        )
        modal.exec()

    def _on_run(self) -> None:
        """Run pipeline for the currently selected date (or today if none)."""
        date = self._current_date
        if not date:
            # no selection → use today
            date = datetime.now().strftime("%Y-%m-%d")
        self._start_pipeline(date, force=False)

    def _on_rerun(self) -> None:
        date = self._current_date
        if not date:
            self._status.setText("재실행할 날짜를 먼저 선택하세요")
            return
        from PySide6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "재실행 확인",
            f"{date} 일자의 2-stage LLM 파이프라인을 다시 실행합니다.\n"
            f"기존 MD 파일과 DB row는 덮어쓰여집니다.\n\n계속하시겠습니까?",
        )
        if ans != QMessageBox.Yes:
            return
        self._start_pipeline(date, force=True)

    def _start_pipeline(self, date: str, force: bool) -> None:
        if self._pipeline_thread is not None and self._pipeline_thread.isRunning():
            return
        self._status.setText(f"{date} 파이프라인 시작…")
        self._pipeline_thread = PipelineThread(
            date=date, db_path=self._db_path, max_topics=self._slider.value()
        )
        self._pipeline_thread.progress.connect(self._status.setText)
        self._pipeline_thread.finished_ok.connect(
            lambda res: self._on_pipeline_ok(date, res)
        )
        self._pipeline_thread.finished_err.connect(self._on_pipeline_err)
        self._pipeline_thread.start()

    def _on_pipeline_ok(self, date: str, res: dict) -> None:
        self._status.setText(
            f"{date} 완료: {len(res['reports'])}개 주제 리포트 (MD: "
            f"{reports_dir_for(date)})"
        )
        # select the new date
        self.refresh()
        for i in range(self._date_list.count()):
            if self._date_list.item(i).text() == date:
                self._date_list.blockSignals(False)
                self._date_list.setCurrentRow(i)
                break

    def _on_pipeline_err(self, err: str) -> None:
        self._status.setText(f"실패: {err}")
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "파이프라인 실패", err)
