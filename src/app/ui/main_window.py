"""Main window: 3-column layout (left nav | main pane | right pane)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import DATA_DIR
from core.db import connection, repositories
from core.models.channel import ChannelStore
from app.workers.ingest_worker import IngestWorker
from app.workers.llm_worker import LLMWorker
from app.workers.report_worker import ReportWorker
from app.ui.tabs.live_feed import LiveFeedTab
from app.ui.tabs.flow_dashboard import FlowDashboardTab
from app.ui.tabs.daily_topics import DailyTopicsTab
from app.ui.tabs.analysis import AnalysisTab
from app.ui.tabs.clusters import ClustersTab
from app.ui.tabs.prompt import PromptTab
from app.ui.tabs.reports import ReportsTab
from app.ui.tabs.cross_validate import CrossValidateTab
from app.ui.tabs.settings import SettingsTab
from app.ui.tabs.placeholder_tab import PlaceholderTab
from app.ui.widgets.channel_manager import ChannelManagerDialog
from app.ui.widgets.left_nav import LeftNav
from app.ui.theme import THEMES, THEME_LABELS, save_theme_pref

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, db_path: Path, theme_name: str = "dark"):
        super().__init__()
        self._db_path = db_path
        self._theme_name = theme_name
        self.setWindowTitle("Market Radar Desktop")
        self.resize(1480, 880)
        self.setMinimumSize(1100, 640)

        # Channel store (auto-register default test channel)
        self._channel_store = ChannelStore(DATA_DIR / "channels.json")
        self._channel_store.ensure_default_if_empty()

        self._login_dialog_open = False

        self._build_ui()
        self._start_workers()
        self._wire_signals()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        # Central
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        root.addWidget(self._build_titlebar())

        # Toolbar
        root.addWidget(self._build_toolbar())

        # 3-column body
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_nav = LeftNav()
        body.addWidget(self._left_nav)

        # Main pane (stacked tabs)
        self._main_stack = QStackedWidget()
        self._live_tab = LiveFeedTab(self._db_path)
        self._flow_tab = FlowDashboardTab(self._db_path)
        self._daily_tab = DailyTopicsTab(self._db_path)
        self._analysis_tab = AnalysisTab(self._db_path)
        self._clusters_tab = ClustersTab(self._db_path)
        self._prompt_tab = PromptTab(self._db_path)
        self._settings_tab = SettingsTab(self._db_path)
        from app.ui.tabs.reports import ReportsTab
        self._reports_tab = ReportsTab(self._db_path)
        self._cross_tab = CrossValidateTab(self._db_path)
        self._main_stack.addWidget(self._live_tab)              # 0 live
        self._main_stack.addWidget(self._flow_tab)               # 1
        self._main_stack.addWidget(self._daily_tab)              # 2
        self._main_stack.addWidget(self._analysis_tab)           # 3
        self._main_stack.addWidget(self._clusters_tab)           # 4
        self._main_stack.addWidget(self._prompt_tab)             # 5
        self._main_stack.addWidget(self._reports_tab)            # 6
        self._main_stack.addWidget(self._cross_tab)              # 7
        self._main_stack.addWidget(self._settings_tab)           # 8
        body.addWidget(self._main_stack, 1)

        # Right pane (today metrics)
        body.addWidget(self._build_rightpane())

        root.addLayout(body, 1)

        # Status bar
        self.setStatusBar(self._build_statusbar())

    def _build_titlebar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(32)
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 12, 0)
        title = QLabel("Market Radar Desktop")
        title.setObjectName("appTitle")
        h.addWidget(title, 1)
        version = QLabel("v0.1.0")
        version.setObjectName("appVersion")
        h.addWidget(version)
        return bar

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(42)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 10, 0)
        h.setSpacing(12)

        brand = QLabel("MARKET RADAR")
        brand.setObjectName("brand")
        h.addWidget(brand)

        self._statusline = QLabel("초기화 중…")
        self._statusline.setObjectName("statusLine")
        h.addWidget(self._statusline, 1)

        # Theme toggle
        self._theme_btn = QPushButton(THEME_LABELS.get(self._theme_name, "🌙 다크"))
        self._theme_btn.setProperty("class", "miniBtn")
        self._theme_btn.setCursor(Qt.PointingHandCursor)
        self._theme_btn.setFixedWidth(90)
        self._theme_btn.clicked.connect(self._on_toggle_theme)
        h.addWidget(self._theme_btn)

        return bar

    def _on_toggle_theme(self) -> None:
        new = "light" if self._theme_name == "dark" else "dark"
        self._theme_name = new
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(THEMES[new])
        self._theme_btn.setText(THEME_LABELS.get(new, "🌙 다크"))
        try:
            save_theme_pref(new)
        except Exception:
            pass

    def _build_rightpane(self) -> QFrame:
        from .right_pane import RightPane
        self._right_pane = RightPane()
        self._right_pane.setFixedWidth(310)
        return self._right_pane

    def _build_statusbar(self) -> QStatusBar:
        sb = QStatusBar()
        self._status_msg = QLabel("준비")
        sb.addPermanentWidget(self._status_msg, 1)
        return sb

    # ---------- wiring ----------

    def _wire_signals(self) -> None:
        self._left_nav.tab_changed.connect(self._on_tab_changed)
        self._left_nav.open_channel_manager.connect(self._open_channel_manager)

        self._ingest.message_collected.connect(self._on_message_collected)
        self._ingest.status_update.connect(self._set_statusline)
        self._ingest.error.connect(self._on_error)
        self._ingest.login_required.connect(self._on_login_required)
        self._ingest.login_success.connect(self._on_login_success)
        self._ingest.login_failure.connect(self._on_login_failure)

        self._llm.signal_ready.connect(self._on_signal_ready)
        self._llm.alert_ready.connect(self._on_alert)
        self._llm.status_update.connect(self._set_statusline)
        self._llm.error.connect(self._on_error)
        self._llm.stats_update.connect(self._on_stats_update)

    def _start_workers(self) -> None:
        from core.config import load_telegram_config, load_llm_config, load_user_interests
        from core.llm.engines import EngineRegistry
        tg = load_telegram_config()
        llm = load_llm_config()

        self._engine_registry = EngineRegistry()

        self._ingest = IngestWorker(tg, self._db_path)
        self._ingest.start()

        self._llm = LLMWorker(
            llm,
            self._db_path,
            user_interests_provider=lambda: load_user_interests(),
        )
        self._llm.start()

        # Phase 3 B6: daily report worker
        self._report_worker = ReportWorker(self._db_path)
        self._report_worker.status_update.connect(self._set_statusline)
        self._report_worker.report_ready.connect(self._on_report_ready)
        self._report_worker.start()

        # Inject engine registry into the llm/report workers after they
        # have constructed their extractors. We schedule a small delay
        # so the async-loop inside each worker has time to construct
        # the extractor.
        QTimer.singleShot(1500, self._inject_engine_registry)

        # Push initial enabled channel set
        ids = {c.id for c in self._channel_store.enabled_list() if c.id}
        self._ingest.update_enabled_channels(ids)
        QTimer.singleShot(500, self._initial_stats)

    def _inject_engine_registry(self) -> None:
        if self._llm._extractor is not None:
            self._llm._extractor.set_registry(self._engine_registry)
        if self._report_worker._extractor is not None:
            self._report_worker._extractor.set_registry(self._engine_registry)

    def _on_report_ready(self, payload: dict) -> None:
        date = payload.get("date", "")
        sent = payload.get("sent", False)
        err = payload.get("error")
        if err:
            self._status_msg.setText(f"리포트 {date} 실패: {err}")
        else:
            self._status_msg.setText(
                f"리포트 {date} {'봇 전송' if sent else '로컬 저장'} 완료"
            )
        # refresh the reports tab if it exists
        if hasattr(self, "_reports_tab") and self._reports_tab is not None:
            try:
                self._reports_tab.refresh()
            except Exception:
                pass

    def _initial_stats(self) -> None:
        try:
            conn = connection.get_connection(self._db_path)
            self._on_stats_update(repositories.stats(conn))
        except Exception:
            pass

    # ---------- slots ----------

    def _on_tab_changed(self, tab_id: str) -> None:
        mapping = {
            "live": 0, "flow": 1, "daily": 2, "analysis": 3,
            "cluster": 4, "prompt": 5, "reports": 6, "cross": 7, "settings": 8,
        }
        idx = mapping.get(tab_id, 0)
        self._main_stack.setCurrentIndex(idx)

    def _open_channel_manager(self) -> None:
        dlg = ChannelManagerDialog(
            store=self._channel_store,
            resolver=self._resolve_channel_sync,
            parent=self,
        )
        dlg.channels_changed.connect(self._on_channels_changed)
        dlg.exec()

    def _on_channels_changed(self) -> None:
        ids = {c.id for c in self._channel_store.enabled_list() if c.id}
        self._ingest.update_enabled_channels(ids)
        # update right pane channel count
        self._right_pane.set_channels(len(self._channel_store.enabled_list()))

    def _on_message_collected(self, feed_id: int, payload: dict) -> None:
        # hand off to LLM worker
        self._llm.enqueue(payload)

    def _on_signal_ready(self, sig) -> None:
        self._live_tab.prepend_signal(sig)

    def _on_alert(self, payload: dict) -> None:
        """Phase 2.3: show a system notification for alerts."""
        sig = payload.get("signal")
        reason = payload.get("reason", "")
        if sig is None:
            return
        # statusline (always)
        self._status_msg.setText(
            f"🚨 ALERT [{sig.channel_name}] {sig.topic} · imp={sig.importance_score} int={sig.interest_score}"
        )
        # bell sound (if available) + status bar flash
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.beep()
        except Exception:
            pass
        # system tray notification (optional)
        try:
            from PySide6.QtCore import QSystemTrayIcon
            from PySide6.QtGui import QIcon
            tray = getattr(self, "_tray", None)
            if tray is None:
                # create ad-hoc without icon — shows generic OS notification
                tray = QSystemTrayIcon(self.windowIcon() or QIcon(), self)
                tray.setToolTip("Market Radar")
                self._tray = tray
            if not tray.isVisible():
                tray.show()
            tray.showMessage(
                f"[S] {sig.topic}",
                f"{sig.channel_name} · {sig.main_content}\n중요 {sig.importance_score} / 관심 {sig.interest_score}",
                QSystemTrayIcon.Critical,
                5000,
            )
        except Exception as e:
            logger.debug("tray notification skipped: %s", e)

    def _on_stats_update(self, stats: dict) -> None:
        self._right_pane.update_metrics(
            feeds=stats.get("feeds", 0),
            signals=stats.get("signals", 0),
            llm_ok=stats.get("llm_ok", 0),
            llm_fail=stats.get("llm_fail", 0),
            llm_ok_pct=stats.get("llm_ok_pct", 0.0),
            tags=stats.get("tags", 0),
        )
        n_channels = len(self._channel_store.enabled_list())
        self._right_pane.set_channels(n_channels)
        self._status_msg.setText(
            f"LIVE · {stats.get('feeds', 0)} feeds · {stats.get('signals', 0)} signals · "
            f"LLM OK {stats.get('llm_ok_pct', 0.0):.1f}% · {n_channels} channels"
        )

    def _set_statusline(self, text: str) -> None:
        self._statusline.setText(text)

    def _on_error(self, msg: str) -> None:
        logger.error(msg)
        self._status_msg.setText(f"오류: {msg}")

    def _on_login_required(self, message: str, kind: str) -> None:
        """Show a modal QInputDialog for the login code or 2FA password.

        Re-entry guard: if a dialog is already open, ignore the new request
        (this can happen with 2FA — only the password prompt should reach us
        after a successful code sign-in).
        """
        if self._login_dialog_open:
            logger.info("login dialog already open, skipping duplicate request")
            return
        self._login_dialog_open = True
        try:
            if kind == "password":
                title = "Telegram 2차 인증"
                label = message or "2차 인증 비밀번호 입력:"
                echo = QLineEdit.Password
            else:
                title = "Telegram 로그인 코드"
                label = (
                    f"{message}\n\n"
                    f"텔레그램 앱 또는 SMS로 전송된 5자리 인증 코드를 입력하세요."
                )
                echo = QLineEdit.Normal
            self._status_msg.setText(
                "로그인 대기 중 — 텔레그램에서 보낸 코드/비밀번호를 입력하세요"
            )
            text, ok = QInputDialog.getText(
                self, title, label, echo=echo
            )
            if not ok:
                self._ingest.provide_prompt_answer("")
                return
            self._ingest.provide_prompt_answer(text or "")
        finally:
            self._login_dialog_open = False

    def _on_login_success(self) -> None:
        self._status_msg.setText("Telegram 로그인 성공")
        QMessageBox.information(
            self, "Telegram 로그인",
            "로그인에 성공했습니다. 채널 수집을 시작합니다."
        )

    def _on_login_failure(self, reason: str) -> None:
        self._status_msg.setText(f"Telegram 로그인 실패: {reason}")
        QMessageBox.critical(
            self, "Telegram 로그인 실패",
            f"텔레그램 로그인에 실패했습니다.\n\n{reason}\n\n"
            f"확인 사항:\n"
            f"  • 5자리 코드가 정확한지 (오타, 만료)\n"
            f"  • 2차 인증이 켜져 있으면 비밀번호 입력 필요\n"
            f"  • .env의 TG_API_ID / TG_API_HASH 가 정확한지\n"
            f"  • 세션 파일을 삭제 후 재시도: rm data/market_radar.session"
        )

    def _resolve_channel_sync(self, username: str) -> tuple[int, str]:
        """Delegate channel resolution to the IngestWorker's asyncio loop.

        Telethon's client is bound to a specific event loop, so the GUI thread
        cannot call it directly. The worker exposes request_channel_resolve
        which posts a coroutine to its own loop and waits for the result.
        """
        return self._ingest.request_channel_resolve(username, timeout=20.0)

    # ---------- close handling ----------

    def closeEvent(self, event) -> None:
        try:
            self._ingest.stop()
            self._llm.stop()
            if hasattr(self, "_report_worker"):
                self._report_worker.stop()
            self._ingest.wait(3000)
            self._llm.wait(3000)
            if hasattr(self, "_report_worker"):
                self._report_worker.wait(2000)
        except Exception:
            pass
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_F5:
            self._live_tab.refresh()
            return
        super().keyPressEvent(event)
