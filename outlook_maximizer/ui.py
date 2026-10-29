"""Two translucent windows and a native tray; backends stay off the UI thread."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
import queue

from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu,
    QMessageBox, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from .ai import AIError, AnalysisCancelled
from .ai_setup_dialog import AiSetupDialog
from .calendar_api import CalendarError, CalendarEventDeleted, GoogleCalendarClient
from .calendar_receipts import CalendarReceipts
from .controller import SelectionState
from .demo import demo_data
from .event_dialog import EventEditor
from .event_editor import get_timezone
from .floating_panel import FloatingPanel
from .i18n import tr, translate_message
from .models import Analysis, CalendarEvent, MonitorNotice, ValidationError
from .model_catalog import load_antigravity_models, load_codex_models
from .outlook_monitor import OutlookMonitor
from .settings import Settings, discover_antigravity
from .settings_dialog import SettingsDialog
from .window_focus import (
    ForegroundWindow, foreground_window, is_native_window_visible, is_window_above,
    raise_without_activation,
)


def event_label(event: CalendarEvent, language: str = "en") -> str:
    if not event.start or not event.end:
        return tr("日期或時間待確認", language)
    try:
        if event.all_day:
            last = (date.fromisoformat(event.end) - timedelta(days=1)).isoformat()
            return f"{event.start} — {last} · {tr('全天', language)}"
        zone = get_timezone(event.timezone)
        start = datetime.fromisoformat(event.start.replace("Z", "+00:00")).astimezone(zone)
        end = datetime.fromisoformat(event.end.replace("Z", "+00:00")).astimezone(zone)
        return f"{start:%Y/%m/%d %H:%M} — {end.strftime('%H:%M' if start.date() == end.date() else '%m/%d %H:%M')}\n{event.timezone}"
    except (ValueError, ValidationError):
        return tr("請編輯並確認日期與時區。", language)


def _ai_identity(settings: Settings) -> tuple[str, ...]:
    if settings.ai_provider == "antigravity":
        return (settings.ai_provider, settings.antigravity_path, settings.antigravity_model,
                settings.antigravity_effort, settings.timezone, settings.about_me, settings.ui_language)
    if settings.ai_provider == "claude":
        return (settings.ai_provider, settings.claude_path, settings.claude_model,
                settings.claude_effort, settings.timezone, settings.about_me, settings.ui_language)
    return (settings.ai_provider, settings.codex_path, settings.model,
            settings.codex_effort, settings.timezone, settings.about_me, settings.ui_language)


def _panel_heights(settings: Settings, screen_height: int) -> tuple[int, int]:
    summary, reply = settings.summary_height, settings.reply_height
    room = max(200, screen_height - 8)
    if summary + reply > room:
        summary = max(100, min(summary, round(room * summary / (summary + reply))))
        reply = max(100, room - summary)
    return summary, reply


def _label(text: str, *, muted: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setObjectName("mailMuted" if muted else "mailText")
    return label


def _body() -> tuple[QWidget, QVBoxLayout]:
    widget = QWidget()
    widget.setObjectName("mailBody")
    widget.setStyleSheet("QWidget#mailBody {background: transparent;}")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(15)
    return widget, layout


def _line() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setObjectName("mailDivider")
    return line


def _button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setMinimumHeight(34)
    button.setObjectName("mailAction")
    return button


def _tray_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#4777BB"))
    painter.setFont(QFont("Segoe UI Symbol", 24))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "✧")
    painter.end()
    return QIcon(pixmap)


class CompanionApp:
    def __init__(self, qt_app: QApplication, data_dir: Path, *, demo: bool = False, tray: bool = True, animations: bool = True):
        self.qt_app, self.data_dir = qt_app, data_dir
        qt_app.setQuitOnLastWindowClosed(False)
        self._first_run = not demo and not (data_dir / "settings.json").exists()
        self.settings = Settings.load(data_dir)
        self.language = self.settings.ui_language
        self._t = lambda text: tr(text, self.language)
        self.calendar = self._make_calendar()
        self.calendar_connected = self.calendar.is_connected
        self.credentials_configured = self.calendar.credentials_configured
        self.state = SelectionState()
        self.messages: queue.Queue = queue.Queue()
        self.ai_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mail-ai")
        self.io_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="calendar")
        self._job_serial = {"summary": 0, "reply": 0}
        self._model_catalog_serial = 0
        self.monitor: OutlookMonitor | None = None
        self.monitor_session = 0
        self.running = self.closed = self.io_busy = False
        self.demo = demo
        self.panels_visible = True
        self._foreground = ForegroundWindow()
        self._last_external_foreground = ForegroundWindow()
        self.connection_status = (self._t("展示模式 · 使用範例信件") if demo else
                                  self._t("首次使用：請先設定 AI，或從系統匣開始監控。") if self._first_run else
                                  self._t("正在準備連線…"))
        self.settings_window: SettingsDialog | None = None
        self.ai_setup_window: AiSetupDialog | None = None
        self.edited_events: dict[int, CalendarEvent] = {}
        self.calendar_receipts = CalendarReceipts(data_dir)
        self.calendar_buttons: list[tuple[QPushButton, CalendarEvent]] = []
        self.repeat_buttons: dict[QPushButton, QPushButton] = {}
        self.copy_button: QPushButton | None = None
        self._positioning_panels = False
        summary_height, reply_height = _panel_heights(self.settings, qt_app.primaryScreen().availableGeometry().height())
        typography = dict(font_family=self.settings.font_family, font_size=self.settings.font_size,
                          font_bold=self.settings.font_bold, font_italic=self.settings.font_italic,
                          language=self.language)
        self.summary_panel = FloatingPanel("summary", width=self.settings.panel_width, expanded_height=summary_height, opacity=self.settings.floating_opacity, background_color=self.settings.background_color, topmost=self.settings.always_on_top, animations=animations, **typography)
        self.reply_panel = FloatingPanel("reply", width=self.settings.panel_width, expanded_height=reply_height, opacity=self.settings.floating_opacity, background_color=self.settings.background_color, topmost=self.settings.always_on_top, animations=animations, **typography)
        self.summary_panel.header_clicked.connect(self._summary_header)
        self.reply_panel.header_clicked.connect(self._reply_header)
        self.summary_panel.pin_requested.connect(self._pin_windows)
        self.reply_panel.pin_requested.connect(self._pin_windows)
        self.summary_panel.resized.connect(self._position_reply)
        self.summary_panel.moved.connect(self._position_reply)
        self.reply_panel.resized.connect(self._position_reply)
        self.reply_panel.moved.connect(self._move_group_from_reply)
        self._build_tray(tray)
        for panel in (self.summary_panel, self.reply_panel):
            panel.context_requested.connect(self.menu.popup)
        self._place_initial()
        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self._run_auto)
        self.queue_timer = QTimer()
        self.queue_timer.timeout.connect(self._drain)
        self.queue_timer.start(80)
        self._foreground_timer = QTimer()
        self._foreground_timer.setInterval(150)
        self._foreground_timer.timeout.connect(self._check_foreground)
        self._foreground_timer.start()
        if demo:
            self.show_demo()
        elif self._first_run:
            QTimer.singleShot(180, self._show_first_run)
        else:
            QTimer.singleShot(180, self.start_monitor)

    def _show_first_run(self) -> None:
        if self.closed:
            return
        self.open_settings()
        self.open_ai_setup(first_run=True)
        self._first_run = False

    def _make_calendar(self) -> GoogleCalendarClient:
        return GoogleCalendarClient(self.data_dir, calendar_id=self.settings.calendar_id,
                                    ui_language=self.settings.ui_language)

    def _build_tray(self, enabled: bool) -> None:
        self._tray_first_click_in_front: bool | None = None
        self._tray_click_timer = QTimer()
        self._tray_click_timer.setSingleShot(True)
        self._tray_click_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._tray_click_timer.timeout.connect(self._show_from_tray)
        self._tray_double_click_timer = QTimer()
        self._tray_double_click_timer.setSingleShot(True)
        self._tray_double_click_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.menu = QMenu()
        self.menu.setStyleSheet("QMenu {background:#F9FBFE; color:#28384C; border:1px solid #DDE5EF; padding:6px;} QMenu::item {padding:8px 24px 8px 14px;} QMenu::item:selected {background:#E8F0FC;}")
        self.show_action = self.menu.addAction(self._t("顯示浮窗"), self.show_panels)
        self.hide_action = self.menu.addAction(self._t("隱藏浮窗"), self.hide_panels)
        self.retry_action = self.menu.addAction(self._t("重新產生摘要"), self.retry_analysis)
        self.monitor_action = self.menu.addAction(self._t("開始監控"), self.toggle_monitor)
        self.menu.addSeparator()
        self.settings_action = self.menu.addAction(self._t("設定"), self.open_settings)
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction(self._t("結束"), self.close)
        self.tray = QSystemTrayIcon(_tray_icon())
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray_available = enabled and QSystemTrayIcon.isSystemTrayAvailable()
        if self.tray_available:
            self.tray.show()
        self._update_tray()

    def _tray_activated(self, reason) -> None:
        if self.closed:
            return
        interval = max(1, QApplication.doubleClickInterval())
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            # Windows also sends single-click releases around a double-click.
            self._tray_double_click_timer.start(interval)
            self.toggle_panels(exposed=self._tray_first_click_in_front)
        elif reason == QSystemTrayIcon.ActivationReason.Trigger and not self._tray_double_click_timer.isActive():
            if not self._tray_click_timer.isActive():
                self._tray_first_click_in_front = self.panels_visible and self._panels_in_front()
            self._tray_click_timer.start(interval)
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            self._tray_click_timer.stop()
            self._tray_first_click_in_front = None

    def _show_from_tray(self) -> None:
        self.show_panels()

    def _update_tray(self) -> None:
        self.hide_action.setEnabled(self.panels_visible)
        self.retry_action.setEnabled(self.panels_visible)
        self.monitor_action.setText(self._t("暫停監控") if self.running else self._t("開始監控"))
        self.tray.setToolTip(f"{self._t('Outlook 摘要助手')}\n{self.connection_status}")
        if self.settings_window and not sip.isdeleted(self.settings_window):
            self.settings_window.connection_label.setText(self.connection_status)

    def _place_initial(self) -> None:
        area = self.qt_app.primaryScreen().availableGeometry()
        self.summary_panel.move(area.right() - self.settings.panel_width - 27, area.top() + 65)
        self._position_reply()

    def _position_reply(self) -> None:
        if self._positioning_panels or self.closed:
            return
        self._positioning_panels = True
        try:
            screen = self.qt_app.screenAt(self.summary_panel.geometry().center()) or self.summary_panel.screen()
            area = screen.availableGeometry()
            heights = _panel_heights(self.settings, area.height())
            for panel, expanded_height in zip((self.summary_panel, self.reply_panel), heights):
                panel.set_expanded_height(expanded_height)
            width = max(self.summary_panel.width(), self.reply_panel.width())
            height = self.summary_panel.height() + self.reply_panel.height() + 8
            x = max(area.left(), min(self.summary_panel.x(), area.right() - width + 1))
            y = max(area.top(), min(self.summary_panel.y(), area.bottom() - height + 1))
            self.summary_panel.move(x, y)
            self.reply_panel.move(x, y + self.summary_panel.height() + 8)
        finally:
            self._positioning_panels = False

    def _move_group_from_reply(self) -> None:
        if self._positioning_panels or self.closed:
            return
        self._positioning_panels = True
        try:
            self.summary_panel.move(self.reply_panel.x(), self.reply_panel.y() - self.summary_panel.height() - 8)
        finally:
            self._positioning_panels = False
        self._position_reply()

    def _check_foreground(self) -> None:
        if self.closed:
            return
        current = foreground_window()
        # Tray/menu focus is transient; retain the application underneath it.
        if current.kind in {"shell", "unknown"}:
            return
        changed = current != self._foreground
        self._foreground = current
        if current.kind in {"outlook", "other"}:
            self._last_external_foreground = current
        if not self.panels_visible:
            return
        # Outlook can re-stack its existing HWND without changing keyboard focus.
        restore_outlook = current.kind == "outlook" and (changed or not self._panels_in_front())
        restore_pinned = self.settings.always_on_top and (changed or not self._panels_in_front())
        if restore_outlook or restore_pinned:
            self._present()

    def _panels_in_front(self) -> bool:
        panels = (self.summary_panel, self.reply_panel)
        if not all(panel.isVisible() and not panel.isMinimized() for panel in panels):
            return False
        if QApplication.platformName() == "windows" and any(
            is_native_window_visible(int(panel.winId())) is False for panel in panels
        ):
            return False
        current = foreground_window()
        if current.kind in {"shell", "unknown", "companion"}:
            current = self._foreground
        if current.kind == "companion":
            current = self._last_external_foreground
        if current.kind in {"shell", "unknown", "companion"} or not current.hwnd:
            return True
        if QApplication.platformName() == "windows" and is_native_window_visible(current.hwnd) is False:
            return True
        return all(is_window_above(int(panel.winId()), current.hwnd) for panel in panels)

    def _present(self, *, force: bool = False, bring_forward: bool = False) -> None:
        if self.closed:
            return
        visible = self.panels_visible and (self.state.current is not None or force)
        current = foreground_window()
        can_show = bring_forward or self.settings.always_on_top or current.kind in {"outlook", "companion"}
        for panel in (self.summary_panel, self.reply_panel):
            if not visible:
                panel.hide()
            elif can_show and (not panel.isVisible() or panel.isMinimized()):
                panel.showNormal()
        self._position_reply()
        if visible and (bring_forward or self.settings.always_on_top or current.kind == "outlook"):
            for panel in (self.summary_panel, self.reply_panel):
                if panel.isVisible() and not raise_without_activation(int(panel.winId())):
                    if QApplication.platformName() != "offscreen":
                        panel.raise_()

    def toggle_panels(self, *, exposed: bool | None = None) -> None:
        if self.panels_visible and (self._panels_in_front() if exposed is None else exposed):
            self.hide_panels()
        else:
            self.show_panels()

    def show_panels(self) -> None:
        if self.closed:
            return
        self._tray_click_timer.stop()
        self._tray_first_click_in_front = None
        was_hidden = not self.panels_visible
        self.panels_visible = True
        self._present(force=True, bring_forward=True)
        self._update_tray()
        if was_hidden:
            self._schedule_auto()

    def hide_panels(self) -> None:
        if self.closed:
            return
        self._tray_click_timer.stop()
        self._tray_first_click_in_front = None
        self.debounce_timer.stop()
        self.panels_visible = False
        self.state.cancel_pending()
        for panel in (self.summary_panel, self.reply_panel):
            if panel.state == "loading":
                panel.show_idle()
        self._present()
        self._update_tray()

    def _schedule_auto(self) -> None:
        if (self.panels_visible and (self.running or self.demo) and self.state.current
                and (self.settings.auto_summarize or self.settings.auto_reply)):
            self.debounce_timer.start(self.settings.debounce_ms)

    def show_demo(self) -> None:
        mail, analysis = demo_data(self.settings.ui_language)
        self._demo_analysis, self._demo_reply = replace(analysis, reply_draft=""), analysis.reply_draft
        generation = self.state.select(mail)
        self.state.accept(generation, self._demo_analysis)
        self._render_analysis(self._demo_analysis)
        self.reply_panel.show_idle()
        if self.settings.auto_reply:
            self.state.accept_reply(generation, self._demo_reply)
            self._render_reply()
        self._present()

    def start_monitor(self) -> None:
        if self.running or self.closed:
            return
        self.demo = False
        self.running = True
        self._clear_selection()
        self.connection_status = self._t("正在連接傳統版 Outlook…")
        self.monitor_session += 1
        session = self.monitor_session
        self.monitor = OutlookMonitor(lambda notice: self.messages.put(("monitor", session, notice)))
        self.monitor.start()
        self._update_tray()

    def toggle_monitor(self) -> None:
        if not self.running:
            self.start_monitor()
            return
        self.running = False
        self.monitor_session += 1
        if self.monitor:
            self.monitor.stop(timeout=.2)
            self.monitor = None
        self.state.cancel.set()
        self.state.reply_cancel.set()
        self.debounce_timer.stop()
        self.connection_status = self._t("監控已暫停")
        if self.summary_panel.state == "loading":
            self.summary_panel.show_idle()
        if self.reply_panel.state == "loading":
            self.reply_panel.show_idle()
        self._update_tray()

    def _clear_selection(self) -> None:
        self.debounce_timer.stop()
        self.state.select(None)
        self.edited_events.clear()
        self.calendar_buttons.clear()
        self.repeat_buttons.clear()
        self.copy_button = None
        self.summary_panel.show_idle()
        self.reply_panel.show_idle()
        self._present()

    def _receive_mail(self, mail) -> None:
        if not self.running or (self.state.current and self.state.current.analysis_key == mail.analysis_key):
            return
        self.debounce_timer.stop()
        self.state.select(mail)
        self.edited_events.clear()
        self.calendar_buttons.clear()
        self.repeat_buttons.clear()
        self.copy_button = None
        self.summary_panel.show_idle()
        self.reply_panel.show_idle()
        if self.settings.auto_summarize and self.state.analysis:
            self._render_analysis(self.state.analysis)
        if self.settings.auto_reply and self.state.reply_received:
            self._render_reply()
        self._present()
        self._schedule_auto()

    def _run_auto(self) -> None:
        if self.closed or not self.panels_visible or not self.running or not self.state.current:
            return
        if self.settings.auto_summarize and self.state.analysis is None:
            self.request_summary()
        if self.settings.auto_reply and not self.state.reply_received:
            self.request_reply()

    def _summary_header(self) -> None:
        if self.summary_panel.state == "loading":
            return
        if self.summary_panel.state == "ready":
            self.summary_panel.collapse()
        else:
            self.request_summary()

    def _reply_header(self) -> None:
        if self.reply_panel.state == "loading":
            return
        if self.reply_panel.state == "ready":
            self.reply_panel.collapse()
        else:
            self.request_reply()

    def request_summary(self, *, force: bool = False) -> None:
        if self.closed or not self.panels_visible:
            return
        if not self.state.current:
            if self.monitor:
                self.monitor.request_refresh()
            return
        if not force and self.summary_panel.state == "loading":
            return
        if self.state.analysis and not force:
            self._render_analysis(self.state.analysis)
            self._present()
            return
        if force:
            self.state.restart_summary()
            self.edited_events.clear()
        self._submit_job("summary")

    def request_reply(self, *, force: bool = False) -> None:
        if self.closed or not self.panels_visible:
            return
        if not self.state.current:
            return
        if not force and self.reply_panel.state == "loading":
            return
        if self.state.reply_received and not force:
            self._render_reply()
            self._present()
            return
        if force:
            self.state.restart_reply()
        self._submit_job("reply")

    def retry_analysis(self) -> None:
        self.request_summary(force=True)

    def _submit_job(self, kind: str) -> None:
        if not self.state.current or self.closed or not self.panels_visible:
            return
        self._job_serial[kind] += 1
        serial, generation, mail = self._job_serial[kind], self.state.generation, self.state.current
        cancel = self.state.cancel if kind == "summary" else self.state.reply_cancel
        if cancel.is_set():
            cancel = self.state.restart_summary() if kind == "summary" else self.state.restart_reply()
        panel = self.summary_panel if kind == "summary" else self.reply_panel
        panel.show_loading(self._t("正在閱讀這封信…") if kind == "summary" else self._t("正在整理回覆…"))
        self._present()
        message_kind = "analysis" if kind == "summary" else "reply"
        if self.demo:
            result = self._demo_analysis if kind == "summary" else self._demo_reply
            QTimer.singleShot(1300, lambda: self.messages.put((message_kind, generation, serial, result, cancel)))
            return
        settings = replace(self.settings)
        def run():
            try:
                if cancel.is_set():
                    return
                from .ai import create_summarizer
                provider = create_summarizer(settings)
                result = provider.analyze(mail, cancel, include_reply=False) if kind == "summary" else provider.generate_reply(mail, cancel)
                self.messages.put((message_kind, generation, serial, result, cancel))
            except AnalysisCancelled:
                pass
            except (AIError, OSError) as exc:
                self.messages.put(("ai_error", kind, generation, serial, str(exc), cancel))
        self.ai_pool.submit(run)

    def _render_analysis(self, analysis: Analysis) -> None:
        widget, layout = _body()
        for item in analysis.summary:
            layout.addWidget(_label(f"•  {item}"))
        if analysis.action_items:
            layout.addSpacing(7)
            layout.addWidget(_label(self._t("需要你處理"), muted=True))
            for item in analysis.action_items:
                layout.addWidget(_label(f"□  {item}"))
        self.calendar_buttons = []
        self.repeat_buttons = {}
        if analysis.events:
            layout.addSpacing(3)
            divider = _line()
            divider.setObjectName("calendarDivider")
            layout.addWidget(divider)
            layout.addSpacing(3)
        for index, original in enumerate(analysis.events):
            event = self.edited_events.get(index, original)
            if index:
                layout.addWidget(_line())
            row = QHBoxLayout()
            row.addWidget(_label(event.title), 1)
            edit = QPushButton(self._t("編輯"))
            edit.setFlat(True)
            edit.setObjectName("eventEdit")
            edit.clicked.connect(lambda _checked=False, i=index: self.edit_event(i))
            row.addWidget(edit)
            layout.addLayout(row)
            layout.addWidget(_label(event_label(event, self.language) + (f"\n{event.location}" if event.location else ""), muted=True))
            button = _button(self._t("加入行事曆"))
            button.clicked.connect(lambda _checked=False, i=index: self.insert_event(i))
            repeat = _button(self._t("再次加入"))
            repeat.setObjectName("repeatCalendar")
            repeat.setAccessibleName(self._t("再次加入這筆行程"))
            repeat.setToolTip(self._t("刻意新增另一筆相同行程；失敗重試不會重複新增"))
            repeat.clicked.connect(lambda _checked=False, i=index: self.insert_event(i, repeat=True))
            actions = QHBoxLayout()
            actions.setSpacing(8)
            actions.addWidget(button, 1)
            actions.addWidget(repeat)
            layout.addLayout(actions)
            self.calendar_buttons.append((button, event))
            self.repeat_buttons[button] = repeat
        self.summary_panel.show_content(widget)
        self._refresh_calendar_buttons()

    def _render_reply(self) -> None:
        widget, layout = _body()
        layout.addWidget(_label(self.state.reply_draft or self._t("這封信沒有需要回覆的內容。")))
        layout.addWidget(_line())
        self.copy_button = _button(self._t("複製回覆"))
        self.copy_button.setEnabled(bool(self.state.reply_draft))
        self.copy_button.clicked.connect(self.copy_reply)
        layout.addWidget(self.copy_button)
        self.reply_panel.show_content(widget)

    def copy_reply(self) -> None:
        if self.state.reply_draft:
            self.qt_app.clipboard().setText(self.state.reply_draft)
            self._flash_reply()

    def _flash_reply(self) -> None:
        button = self.copy_button
        if not button or sip.isdeleted(button):
            return
        button.setText(self._t("已複製"))
        QTimer.singleShot(1800, lambda: button.setText(self._t("複製回覆")) if not self.closed and not sip.isdeleted(button) else None)

    def _event(self, index: int) -> CalendarEvent | None:
        if not self.state.analysis or not 0 <= index < len(self.state.analysis.events):
            return None
        return self.edited_events.get(index, self.state.analysis.events[index])

    def _refresh_calendar_buttons(self) -> None:
        for button, event in self.calendar_buttons:
            if sip.isdeleted(button):
                continue
            enabled = not self.demo and not self.io_busy
            text = self._t("展示行程 · 不寫入") if self.demo else self._t("加入行事曆")
            added = bool(self.state.current and self.calendar_receipts.is_added(
                self.settings.calendar_id, self.state.current.key, event))
            if added:
                text, enabled = self._t("已加入行事曆"), False
            elif self.io_busy:
                text = self._t("Google 處理中…")
            elif not self.demo and not self.calendar_connected:
                text = self._t("連接 Google")
            elif event.needs_review:
                text = self._t("確認行程")
            button.setText(text)
            button.setEnabled(enabled)
            repeat = self.repeat_buttons.get(button)
            if repeat and not sip.isdeleted(repeat):
                repeat.setVisible(added)
                repeat.setEnabled(added and not self.demo and not self.io_busy)

    def edit_event(self, index: int = 0) -> None:
        event = self._event(index)
        if not event:
            return
        generation = self.state.generation
        serial, analysis = self._job_serial["summary"], self.state.analysis
        def save(updated):
            if (generation == self.state.generation
                    and serial == self._job_serial["summary"]
                    and analysis is self.state.analysis
                    and self._event(index) is event):
                self.edited_events[index] = updated
                events = list(analysis.events)
                events[index] = updated
                self.state.accept(generation, replace(analysis, events=tuple(events)))
                self._render_analysis(self.state.analysis)
        previous = getattr(self, "event_editor", None)
        if previous and not sip.isdeleted(previous):
            previous.close()
        self.event_editor = EventEditor(event, save, language=self.settings.ui_language, parent=self.summary_panel)
        self.event_editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.event_editor.show()

    def insert_event(self, index: int = 0, *, repeat: bool = False) -> None:
        event = self._event(index)
        if not event or not self.state.current or self.demo or self.io_busy:
            return
        added = self.calendar_receipts.is_added(self.settings.calendar_id, self.state.current.key, event)
        if added and not repeat or repeat and not added:
            return
        if not self.calendar_connected:
            self.open_settings()
            return
        if event.needs_review:
            self.edit_event(index)
            return
        try:
            event.validate(for_insert=True)
        except ValidationError as exc:
            QMessageBox.warning(self._dialog_parent(), self._t("請確認行程"), translate_message(str(exc), self.language))
            return
        client, source, receipts = self.calendar, self.state.current.key, self.calendar_receipts
        self.io_busy = True
        self._refresh_calendar_buttons()
        def run():
            try:
                occurrence = receipts.begin(client.calendar_id, source, event, repeat=repeat)
                try:
                    result = client.create_event(event, source, occurrence_key=occurrence)
                except CalendarEventDeleted:
                    # Only a confirmed deleted ID permits replacement. Persist the
                    # next identity before POST so an uncertain retry keeps it.
                    receipts.complete(client.calendar_id, source, event, occurrence)
                    occurrence = receipts.begin(client.calendar_id, source, event, repeat=True)
                    result = client.create_event(event, source, occurrence_key=occurrence)
                receipts.complete(client.calendar_id, source, event, occurrence)
                self.messages.put(("calendar_done", (client.calendar_id, source, event.start, event.end), result))
            except (CalendarError, ValidationError, OSError) as exc:
                self.messages.put(("calendar_error", str(exc)))
        self.io_pool.submit(run)

    def _handle_monitor_notice(self, session: int, notice: MonitorNotice) -> None:
        if not self.running or session != self.monitor_session:
            return
        if notice.kind == "mail" and notice.mail:
            self._receive_mail(notice.mail)
        elif notice.kind == "selection_cleared":
            self._clear_selection()
        elif notice.kind in {"status", "error", "new_mail"}:
            self.connection_status = translate_message(notice.message, self.language)
            if notice.kind == "error":
                self.summary_panel.show_error(self.connection_status)
                self._present(force=True)
            self._update_tray()

    def _drain(self) -> None:
        if self.closed:
            return
        for _ in range(100):
            try:
                kind, *data = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "monitor":
                self._handle_monitor_notice(*data)
            elif kind in {"analysis", "reply"}:
                if not self.panels_visible:
                    continue
                generation, serial, result, cancelled = data
                job = "summary" if kind == "analysis" else "reply"
                if cancelled.is_set() or serial != self._job_serial[job]:
                    continue
                if kind == "analysis" and self.state.accept(generation, result):
                    self._render_analysis(result)
                elif kind == "reply" and self.state.accept_reply(generation, result):
                    self._render_reply()
            elif kind == "ai_error":
                if not self.panels_visible:
                    continue
                job, generation, serial, error, cancelled = data
                if generation == self.state.generation and serial == self._job_serial[job] and not cancelled.is_set():
                    (self.summary_panel if job == "summary" else self.reply_panel).show_error(translate_message(error, self.language))
            elif kind == "calendar_done":
                _key, _result = data
                self.io_busy = False
                self._update_settings_status(self._t("行程已加入 Google Calendar。"))
                self._refresh_calendar_buttons()
            elif kind in {"calendar_error", "auth_error"}:
                self.io_busy = False
                self.calendar_connected = self.calendar.is_connected
                self._update_settings_status(data[0])
                self._refresh_calendar_buttons()
                QMessageBox.warning(self._dialog_parent(), "Google Calendar", translate_message(data[0], self.language))
            elif kind == "auth_status":
                self._update_settings_status(data[0])
            elif kind == "auth_done":
                self.io_busy = False
                self.calendar_connected = self.calendar.is_connected
                self._update_settings_status(self._t("Google 已連接，可以加入行程。"))
                self._refresh_calendar_buttons()
            elif kind == "model_catalog":
                window, serial, catalogs = data
                if (self.settings_window is window and not sip.isdeleted(window)
                        and serial == self._model_catalog_serial):
                    for provider, options in catalogs.items():
                        window.set_models(provider, options)
                    window.reload_models_button.setEnabled(True)

    def _dialog_parent(self):
        if self.settings_window and not sip.isdeleted(self.settings_window):
            return self.settings_window
        return self.summary_panel

    def open_settings(self) -> None:
        setup = getattr(self, "ai_setup_window", None)
        if setup and not sip.isdeleted(setup) and setup.login_in_progress:
            if self.tray_available:
                self.tray.showMessage(self._t("AI 助理登入中"), self._t("請先完成並關閉登入視窗，再回到設定。"),
                                      QSystemTrayIcon.MessageIcon.Information, 5000)
            return
        if self.settings_window and not sip.isdeleted(self.settings_window):
            self.settings_window.show()
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            return
        window = SettingsDialog(self.settings, calendar_connected=self.calendar_connected, credentials_configured=self.credentials_configured, connection_status=self.connection_status, language=self.language)
        self.settings_window = window
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.settings_saved.connect(self._save_settings)
        window.choose_credentials_requested.connect(self._choose_credentials)
        window.connect_requested.connect(self._connect_google)
        window.disconnect_requested.connect(self._disconnect_google)
        window.cancel_auth_requested.connect(self.calendar.cancel_auth)
        window.models_requested.connect(self._load_models)
        window.ai_setup_requested.connect(self.open_ai_setup)
        status = self._t("Google 處理中…") if self.io_busy else (
            self._t("Google Calendar 已連線。") if self.calendar_connected else (
                self._t("OAuth JSON 已載入，請登入 Google。") if self.credentials_configured else self._t("尚未連接 Google Calendar。")
            )
        )
        self._update_settings_status(status)
        window.show()
        window.activateWindow()
        if not self._first_run:
            self._load_models()

    def open_ai_setup(self, *, first_run: bool = False) -> None:
        parent = self.settings_window
        if self.closed or not parent or sip.isdeleted(parent):
            return
        if self.ai_setup_window and not sip.isdeleted(self.ai_setup_window):
            self.ai_setup_window.show()
            self.ai_setup_window.raise_()
            self.ai_setup_window.activateWindow()
            return
        paths = {"codex": parent.codex_edit.text().strip().strip('"'),
                 "antigravity": parent.antigravity_edit.text().strip().strip('"'),
                 "claude": parent.claude_edit.text().strip().strip('"')}
        window = AiSetupDialog(paths, parent.provider_combo.currentData(), language=self.language, first_run=first_run, parent=parent)
        self.ai_setup_window = window
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.path_found.connect(parent.set_cli_path)
        if first_run:
            def apply_first_run_choice(_result):
                if self.closed or sip.isdeleted(parent):
                    return
                selected = [name for name, check in window.provider_checks.items() if check.isChecked()]
                if len(selected) == 1:
                    status = window._statuses.get(selected[0])
                    if status and status.installed:
                        parent.provider_combo.setCurrentIndex(parent.provider_combo.findData(selected[0]))
                elif len(selected) > 1:
                    parent.require_provider_selection()
            window.finished.connect(apply_first_run_choice)
        else:
            window.finished.connect(lambda _result: self._load_models())
        window.show()
        window.activateWindow()

    def _load_models(self) -> None:
        window = self.settings_window
        if not window or sip.isdeleted(window) or self.closed:
            return
        self._model_catalog_serial += 1
        serial = self._model_catalog_serial
        executable = window.antigravity_edit.text().strip().strip('"') or discover_antigravity()
        window.reload_models_button.setEnabled(False)
        window.models_status.setText(self._t("正在載入本機 CLI 模型清單…"))
        def load():
            catalogs = {"codex": load_codex_models(), "antigravity": load_antigravity_models(executable)}
            self.messages.put(("model_catalog", window, serial, catalogs))
        self.io_pool.submit(load)

    def _save_settings(self, updated: Settings) -> None:
        if self.io_busy:
            self._update_settings_status(self._t("請先完成或取消 Google 登入，再儲存設定。"))
            return
        try:
            self._apply_settings(updated)
            if self.settings_window and not sip.isdeleted(self.settings_window):
                self.settings_window.accept()
        except (CalendarError, OSError, ValueError) as exc:
            if self.settings_window and not sip.isdeleted(self.settings_window):
                self.settings_window.error_label.setText(translate_message(str(exc), self.language))

    def _pin_windows(self, pinned: bool) -> None:
        if self.closed or self.settings.always_on_top == pinned:
            return
        try:
            self._apply_settings(replace(self.settings, always_on_top=pinned))
            if self.settings_window and not sip.isdeleted(self.settings_window):
                self.settings_window.topmost_check.setChecked(pinned)
        except (CalendarError, OSError, ValueError) as exc:
            for panel in (self.summary_panel, self.reply_panel):
                panel.header.pin_button.setChecked(self.settings.always_on_top)
            QMessageBox.warning(self._dialog_parent(), self._t("無法儲存釘選設定"), translate_message(str(exc), self.language))

    def _apply_settings(self, updated: Settings) -> None:
        client = GoogleCalendarClient(self.data_dir, calendar_id=updated.calendar_id, ui_language=updated.ui_language)
        updated.save(self.data_dir)
        previous = self.settings
        source_changed = _ai_identity(previous) != _ai_identity(updated)
        self.settings, self.calendar = updated, client
        self.calendar_connected = client.is_connected
        for kind, panel, height in (("summary", self.summary_panel, updated.summary_height), ("reply", self.reply_panel, updated.reply_height)):
            panel.configure_style(width=updated.panel_width, expanded_height=height, opacity=updated.floating_opacity, background_color=updated.background_color, topmost=updated.always_on_top,
                                  font_family=updated.font_family, font_size=updated.font_size,
                                  font_bold=updated.font_bold, font_italic=updated.font_italic)
        if source_changed:
            self.debounce_timer.stop()
            self.state.clear_results()
            for kind in self._job_serial:
                self._job_serial[kind] += 1
            self.edited_events.clear()
            self.calendar_buttons.clear()
            self.repeat_buttons.clear()
            self.copy_button = None
            self.summary_panel.show_idle()
            self.reply_panel.show_idle()
        elif not updated.auto_reply and previous.auto_reply:
            self.state.restart_reply()
            self._job_serial["reply"] += 1
            self.reply_panel.show_idle()
        can_generate = self.panels_visible and (self.running or self.demo)
        if can_generate and updated.auto_reply and (source_changed or not previous.auto_reply) and self.state.current:
            self.request_reply()
        if can_generate and updated.auto_summarize and (source_changed or not previous.auto_summarize) and self.state.current:
            self.request_summary()
        self._refresh_calendar_buttons()
        self._present()

    def _update_settings_status(self, text: str) -> None:
        if self.settings_window and not sip.isdeleted(self.settings_window):
            self.settings_window.update_status(translate_message(text, self.language), connected=self.calendar_connected, busy=self.io_busy, credentials_configured=self.credentials_configured)

    def _choose_credentials(self) -> None:
        if self.io_busy:
            return
        path, _filter = QFileDialog.getOpenFileName(self._dialog_parent(), self._t("選擇 Google 電腦版 OAuth JSON"), "", "OAuth JSON (*.json)")
        if not path:
            return
        try:
            self.calendar.configure_credentials(Path(path))
            self.credentials_configured = True
            self.calendar_connected = self.calendar.is_connected
            self._update_settings_status(self._t("OAuth JSON 已載入，請登入 Google。"))
            self._refresh_calendar_buttons()
        except (CalendarError, OSError, ValueError) as exc:
            self._update_settings_status(str(exc))

    def _connect_google(self) -> None:
        if self.io_busy:
            return
        self.io_busy = True
        self._update_settings_status(self._t("請在瀏覽器完成登入。"))
        self._refresh_calendar_buttons()
        client = self.calendar
        def run():
            try:
                client.connect(on_status=lambda text: self.messages.put(("auth_status", text)))
                self.messages.put(("auth_done",))
            except (CalendarError, OSError) as exc:
                self.messages.put(("auth_error", str(exc)))
        self.io_pool.submit(run)

    def _disconnect_google(self) -> None:
        if not self.io_busy:
            try:
                self.calendar.disconnect()
                self.calendar_connected = False
                self._refresh_calendar_buttons()
                self._update_settings_status(self._t("已清除這台電腦的 Google 授權。"))
            except (CalendarError, OSError) as exc:
                self._update_settings_status(str(exc))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.state.cancel.set()
        self.state.reply_cancel.set()
        self.debounce_timer.stop()
        self.queue_timer.stop()
        self._foreground_timer.stop()
        self._tray_click_timer.stop()
        self._tray_double_click_timer.stop()
        self.calendar.cancel_auth()
        if self.monitor:
            self.monitor.stop(timeout=.3)
        self.tray.hide()
        self.menu.close()
        if self.ai_setup_window and not sip.isdeleted(self.ai_setup_window):
            self.ai_setup_window.shutdown()
        for window in (getattr(self, "event_editor", None), self.settings_window):
            if window and not sip.isdeleted(window):
                window.close()
        self.summary_panel.dispose()
        self.reply_panel.dispose()
        self.ai_pool.shutdown(wait=False, cancel_futures=True)
        self.io_pool.shutdown(wait=False, cancel_futures=True)
        self.qt_app.quit()
