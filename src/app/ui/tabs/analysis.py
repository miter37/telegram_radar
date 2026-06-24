"""태그/주제 분석 탭: 입력 폼 + 분석 실행 + 결과 표시."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.config import load_llm_config, load_user_interests
from core.db import connection
from core.llm.analysis import (
    collect_feeds_for_target,
    compute_daily_metrics,
    run_topic_analysis,
)
from core.llm.extractor import LLMExtractor

logger = logging.getLogger(__name__)


class AnalysisRunner(QThread):
    finished_ok = Signal(dict)
    finished_err = Signal(str)
    progress = Signal(str)

    def __init__(self, *, target: str, period: str, scope: str, db_path):
        super().__init__()
        self._target = target
        self._period = period
        self._scope = scope
        self._db_path = db_path
        self._extractor: Optional[LLMExtractor] = None

    def run(self) -> None:
        try:
            asyncio.run(self._amain())
        except Exception as e:
            logger.exception("AnalysisRunner crashed")
            self.finished_err.emit(f"{e.__class__.__name__}: {e}")

    async def _amain(self) -> None:
        days_map = {"최근 7일": 7, "최근 14일": 14, "최근 30일": 30}
        days = days_map.get(self._period, 7)

        self.progress.emit("관련 피드 수집 중…")
        conn = connection.get_connection(self._db_path)
        feeds = collect_feeds_for_target(conn, self._target, days=days, limit=80)
        if not feeds:
            self.finished_err.emit(f"'{self._target}' 관련 피드가 없습니다.")
            return
        self.progress.emit(f"피드 {len(feeds)}건 수집됨. 일자별 메트릭 계산 중…")
        daily = compute_daily_metrics(conn, self._target, days=days)

        llm_cfg = load_llm_config()
        self._extractor = LLMExtractor(
            base_url=llm_cfg.base_url,
            api_key=llm_cfg.api_key,
            model=llm_cfg.model,
        )

        self.progress.emit("LLM 분석 호출 중…")
        interests = load_user_interests()
        res = await run_topic_analysis(
            extractor=self._extractor,
            target=self._target,
            period=self._period,
            feeds=feeds,
            daily_metrics=daily,
            user_interests=interests,
        )
        await self._extractor.close()

        if not res.ok:
            self.finished_err.emit(res.error or "unknown")
            return
        self.finished_ok.emit({"payload": res.payload, "raw": res.raw})


class AnalysisTab(QFrame):
    def __init__(self, db_path, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self.setObjectName("analysisTab")

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
        title = QLabel("태그/주제 분석")
        title.setObjectName("paneTitle")
        sub = QLabel("on-demand LLM 분석 · 5줄 요약 / 타임라인 / 변화 / 확인 필요")
        sub.setObjectName("paneSub")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        h.addLayout(title_box, 1)
        layout.addWidget(head)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Left: form
        form = QFrame()
        form.setStyleSheet("background: #121820; border-right: 1px solid #303746;")
        form.setFixedWidth(280)
        fl = QVBoxLayout(form)
        fl.setContentsMargins(12, 12, 12, 12)
        fl.setSpacing(8)

        fl.addWidget(QLabel("분석 대상 (주제/태그)"))
        self._target = QLineEdit()
        self._target.setPlaceholderText("예: HBM, SK하이닉스, 유리기판")
        fl.addWidget(self._target)

        fl.addWidget(QLabel("기간"))
        self._period = QComboBox()
        self._period.addItems(["최근 7일", "최근 14일", "최근 30일"])
        fl.addWidget(self._period)

        fl.addWidget(QLabel("피드 범위"))
        self._scope = QComboBox()
        self._scope.addItems(["내 관심분야 중심", "전체 피드 기준", "상위 중요도 피드만"])
        fl.addWidget(self._scope)

        self._run_btn = QPushButton("분석 실행")
        self._run_btn.setProperty("class", "primary")
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.clicked.connect(self._on_run)
        fl.addWidget(self._run_btn)

        self._progress = QLabel("대기")
        self._progress.setStyleSheet(
            "color: #697386; font-size: 11px; "
            "font-family: 'Consolas','Liberation Mono',monospace;"
        )
        fl.addWidget(self._progress)

        fl.addStretch(1)

        hint = QLabel(
            "수집된 feed_signals에서 대상과 관련된 피드만 모아 LLM에 분석을 요청합니다.\n"
            "LLM 응답은 JSON으로 파싱되어 화면에 표시됩니다."
        )
        hint.setStyleSheet("color: #697386; font-size: 11px;")
        hint.setWordWrap(True)
        fl.addWidget(hint)

        body.addWidget(form)

        # Right: output
        right = QFrame()
        right.setStyleSheet("background: #0f141b;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._output = QFrame()
        self._output.setStyleSheet("background: #0f141b;")
        self._output_layout = QVBoxLayout(self._output)
        self._output_layout.setContentsMargins(12, 12, 12, 12)
        self._output_layout.setSpacing(8)
        self._output_layout.addStretch(1)
        scroll.setWidget(self._output)
        rl.addWidget(scroll)

        body.addWidget(right, 1)
        layout.addLayout(body, 1)

        self._runner: Optional[AnalysisRunner] = None
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        self._clear_output()
        ph = QLabel("좌측 폼을 채우고 [분석 실행]을 눌러주세요.")
        ph.setStyleSheet("color: #697386; font-size: 13px; padding: 20px;")
        ph.setAlignment(Qt.AlignCenter)
        self._output_layout.addWidget(ph)
        self._output_layout.addStretch(1)

    def _clear_output(self) -> None:
        while self._output_layout.count():
            item = self._output_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _on_run(self) -> None:
        target = self._target.text().strip()
        if not target:
            return
        if self._runner is not None and self._runner.isRunning():
            return
        self._run_btn.setEnabled(False)
        self._clear_output()
        loading = QLabel("분석 중…")
        loading.setStyleSheet("color: #4ea1ff; font-size: 13px; padding: 20px;")
        loading.setAlignment(Qt.AlignCenter)
        self._output_layout.addWidget(loading)
        self._progress.setText("분석 시작…")
        self._runner = AnalysisRunner(
            target=target,
            period=self._period.currentText(),
            scope=self._scope.currentText(),
            db_path=self._db_path,
        )
        self._runner.progress.connect(self._progress.setText)
        self._runner.finished_ok.connect(self._on_ok)
        self._runner.finished_err.connect(self._on_err)
        self._runner.start()

    def _on_ok(self, result: dict) -> None:
        self._run_btn.setEnabled(True)
        self._progress.setText("완료")
        self._clear_output()
        payload = result.get("payload") or {}
        self._render_payload(payload)

    def _on_err(self, msg: str) -> None:
        self._run_btn.setEnabled(True)
        self._progress.setText("실패")
        self._clear_output()
        err = QLabel(f"분석 실패: {msg}")
        err.setStyleSheet("color: #ef596f; font-size: 13px; padding: 20px;")
        err.setWordWrap(True)
        self._output_layout.addWidget(err)
        self._output_layout.addStretch(1)

    def _render_payload(self, p: dict) -> None:
        # Summary card
        if p.get("summary"):
            card = self._make_card("5줄 요약")
            for line in p["summary"]:
                lbl = QLabel(f"• {line}")
                lbl.setStyleSheet("color: #d3dbe8; font-size: 13px;")
                lbl.setWordWrap(True)
                card.layout().addWidget(lbl)
            self._output_layout.addWidget(card)

        # Timeline
        if p.get("timeline"):
            card = self._make_card("날짜별 흐름")
            for t in p["timeline"]:
                row = QFrame()
                row.setStyleSheet("background: transparent;")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                d = QLabel(t.get("date", ""))
                d.setStyleSheet(
                    "color: #b9d8ff; font-family: 'Consolas','Liberation Mono',monospace; "
                    "font-size: 12px; min-width: 80px;"
                )
                n = QLabel(t.get("note", ""))
                n.setStyleSheet("color: #b8c1d0; font-size: 12px;")
                n.setWordWrap(True)
                rl.addWidget(d)
                rl.addWidget(n, 1)
                card.layout().addWidget(row)
            self._output_layout.addWidget(card)

        if p.get("trend_change"):
            card = self._make_card("최근 변화")
            lbl = QLabel(p["trend_change"])
            lbl.setStyleSheet("color: #d3dbe8; font-size: 12px;")
            lbl.setWordWrap(True)
            card.layout().addWidget(lbl)
            self._output_layout.addWidget(card)

        if p.get("importance_reasons"):
            card = self._make_card("중요도가 올라간 이유")
            for r in p["importance_reasons"]:
                lbl = QLabel(f"• {r}")
                lbl.setStyleSheet("color: #d3dbe8; font-size: 12px;")
                lbl.setWordWrap(True)
                card.layout().addWidget(lbl)
            self._output_layout.addWidget(card)

        if p.get("watchlist"):
            card = self._make_card("확인 필요")
            for w in p["watchlist"]:
                lbl = QLabel(f"☐ {w}")
                lbl.setStyleSheet("color: #eac45c; font-size: 12px;")
                lbl.setWordWrap(True)
                card.layout().addWidget(lbl)
            self._output_layout.addWidget(card)

        if p.get("uncertainties"):
            card = self._make_card("불확실한 부분")
            for u in p["uncertainties"]:
                lbl = QLabel(f"⚠ {u}")
                lbl.setStyleSheet("color: #ef596f; font-size: 12px;")
                lbl.setWordWrap(True)
                card.layout().addWidget(lbl)
            self._output_layout.addWidget(card)

        if p.get("interest_for_user"):
            card = self._make_card("관심 분야 연관성")
            lbl = QLabel(p["interest_for_user"])
            lbl.setStyleSheet("color: #4ea1ff; font-size: 12px;")
            lbl.setWordWrap(True)
            card.layout().addWidget(lbl)
            self._output_layout.addWidget(card)

        self._output_layout.addStretch(1)

    def _make_card(self, title: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: #141a23; border: 1px solid #303746; }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)
        head = QLabel(title)
        head.setStyleSheet(
            "color: #dce4f1; font-weight: 760; font-size: 12px; "
            "background: #1a202b; padding: 4px 8px; border-bottom: 1px solid #303746; margin: -8px -10px 4px -10px;"
        )
        v.addWidget(head)
        return card
