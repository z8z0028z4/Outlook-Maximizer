"""User-guided discovery, installation and login for local AI assistants."""

from __future__ import annotations

import queue
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .cli_setup import (
    CliSetupCancelled, CliSetupError, CliStatus, PROVIDERS, PROVIDER_LABELS,
    detect_provider, install_provider, launch_login,
)
from .i18n import tr, translate_message


def _text(value: str, *, muted: bool = False) -> QLabel:
    label = QLabel(value)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if muted:
        label.setObjectName("muted")
    return label


class AiSetupDialog(QDialog):
    path_found = pyqtSignal(str, str)

    def __init__(self, paths: dict[str, str], selected: str, *, language: str = "en", first_run: bool = False, parent=None):
        super().__init__(parent)
        self.language = language
        self._t = lambda text: tr(text, self.language)
        self.setWindowTitle(self._t("登入／搜尋 AI 助理"))
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFont(QFont("Microsoft JhengHei UI", 10))
        area = self.screen().availableGeometry()
        min_width = min(340, max(260, area.width() - 20))
        min_height = min(300, max(160, area.height() - 20))
        self.setMinimumSize(min_width, min_height)
        self.resize(min(540, max(min_width, area.width() - 40)), min(570, max(min_height, area.height() - 40)))
        self._paths = {provider: paths.get(provider, "") for provider in PROVIDERS}
        self._statuses: dict[str, CliStatus] = {}
        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._busy = False
        self._login_processes = {}
        self._pending_refresh: set[str] = set()
        self._hidden_for_login = False
        self._parent_hidden_for_login = None
        self.destroyed.connect(lambda: self._cancel.set())

        self.setStyleSheet("""
            QFrame#setupSurface { background:#f7f9fc; border:1px solid #dfe5ef; border-radius:16px; }
            QFrame#providerCard { background:#ffffff; border:1px solid #dce3ed; border-radius:8px; }
            QScrollArea, QWidget#setupContent { background:transparent; border:none; }
            QLabel { color:#26364a; background:transparent; }
            QLabel#heading { font-size:20px; font-weight:500; }
            QLabel#muted { color:#69788d; font-size:12px; }
            QCheckBox { color:#26364a; spacing:9px; background:transparent; }
            QCheckBox::indicator { width:14px; height:14px; border:1px solid #8a98aa; border-radius:3px; background:#ffffff; }
            QCheckBox::indicator:checked { border:1px solid #315fd4; background:#315fd4; }
            QPushButton { background:#edf1f7; color:#26364a; border:1px solid #dce3ed; border-radius:7px; padding:7px 12px; }
            QPushButton:hover { background:#e4eaf4; }
            QPushButton:disabled { color:#9aa5b4; background:#f0f2f6; }
            QPushButton#primary { background:#315fd4; color:white; border:none; padding:9px 16px; }
            QPushButton#primary:disabled { background:#c7d2eb; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        surface = QFrame()
        surface.setObjectName("setupSurface")
        root.addWidget(surface)
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(24, 22, 24, 20)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        surface_layout.addWidget(scroll, 1)
        content = QWidget()
        content.setObjectName("setupContent")
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        heading = _text(self._t("登入／搜尋 AI 助理"))
        heading.setObjectName("heading")
        layout.addWidget(heading)
        if first_run:
            layout.addWidget(_text(self._t("首次使用可先搜尋或安裝所選的 AI 助理，再登入並儲存設定。你也可以關閉此視窗，稍後再設定。Outlook 監控會保持暫停，直到你自行啟動。"), muted=True))
        layout.addWidget(_text(self._t("勾選你使用的平台。「重新搜尋」只檢查已安裝的工具；「搜尋並安裝」也會安裝缺少的工具。"), muted=True))

        self.provider_checks = {}
        self.provider_status = {}
        self.login_buttons = {}
        for provider in PROVIDERS:
            card = QFrame()
            card.setObjectName("providerCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(13, 10, 13, 10)
            row = QHBoxLayout()
            check = QCheckBox(PROVIDER_LABELS[provider])
            check.setChecked(not first_run and provider == selected)
            row.addWidget(check)
            row.addStretch()
            login = QPushButton(self._t("登入"))
            login.setCursor(Qt.CursorShape.PointingHandCursor)
            login.setEnabled(False)
            login.clicked.connect(lambda _checked=False, value=provider: self._login(value))
            row.addWidget(login)
            card_layout.addLayout(row)
            status = _text(self._t("尚未搜尋"), muted=True)
            card_layout.addWidget(status)
            layout.addWidget(card)
            self.provider_checks[provider] = check
            self.provider_status[provider] = status
            self.login_buttons[provider] = login

        for check in self.provider_checks.values():
            check.toggled.connect(lambda _checked: self._set_busy(self._busy))

        self.message = _text(self._t("登入會在平台自己的視窗完成，請於登入後關閉該視窗；本工具不會要求輸入密碼。"), muted=True)
        layout.addWidget(self.message)
        layout.addStretch()
        actions = QHBoxLayout()
        self.close_button = QPushButton(self._t("關閉"))
        self.close_button.clicked.connect(self._close_or_cancel)
        self.search_button = QPushButton(self._t("重新搜尋"))
        self.search_button.clicked.connect(lambda: self._start("search"))
        self.start_button = QPushButton(self._t("搜尋並安裝"))
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(lambda: self._start("install"))
        actions.addWidget(self.close_button)
        actions.addStretch()
        actions.addWidget(self.search_button)
        actions.addWidget(self.start_button)
        surface_layout.addLayout(actions)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain)
        self._timer.start(100)
        self.finished.connect(self._finished)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.start_button.setEnabled(not busy)
        self.search_button.setEnabled(not busy)
        self.close_button.setEnabled(True)
        self.close_button.setText(self._t("取消設定") if busy else self._t("關閉"))
        for provider in PROVIDERS:
            self.provider_checks[provider].setEnabled(not busy)
            status = self._statuses.get(provider)
            self.login_buttons[provider].setEnabled(not busy and self.provider_checks[provider].isChecked()
                                                    and bool(status and status.installed)
                                                    and provider not in self._login_processes)

    @property
    def login_in_progress(self) -> bool:
        return bool(self._login_processes)

    def _start(self, mode: str, providers: list[str] | None = None) -> None:
        if self._busy:
            return
        selected = providers if providers is not None else [name for name in PROVIDERS if self.provider_checks[name].isChecked()]
        if not selected:
            self.message.setText(self._t("請先勾選至少一個 AI 平台。"))
            return
        selected = list(selected)
        paths = dict(self._paths)
        cancel = self._cancel = threading.Event()
        messages = self._queue
        self._set_busy(True)
        self.message.setText(self._t("正在搜尋與安裝所選平台…") if mode == "install" else self._t("正在重新搜尋所選平台…"))
        for provider in selected:
            self.provider_status[provider].setText(self._t("等待處理…"))

        def run() -> None:
            for provider in selected:
                if cancel.is_set():
                    break
                messages.put(("working", provider))
                try:
                    status = (install_provider(provider, paths[provider], cancel=cancel)
                              if mode == "install" else detect_provider(provider, paths[provider]))
                    if cancel.is_set():
                        break
                    messages.put(("status", provider, status))
                except CliSetupCancelled:
                    cancel.set()
                    break
                except CliSetupError as exc:
                    if cancel.is_set():
                        break
                    messages.put(("error", provider, str(exc)))
                except Exception:
                    if cancel.is_set():
                        break
                    messages.put(("error", provider, self._t("處理失敗，請稍後重新搜尋。")))
            messages.put(("done", cancel.is_set()))

        threading.Thread(target=run, name="ai-setup", daemon=True).start()

    def _login(self, provider: str) -> None:
        status = self._statuses.get(provider)
        if (self._busy or not self.provider_checks[provider].isChecked()
                or provider in self._login_processes or not status or not status.executable):
            return
        self._yield_for_login()
        try:
            self._login_processes[provider] = launch_login(provider, status.executable)
        except CliSetupError as exc:
            self._return_from_login()
            self.provider_status[provider].setText(translate_message(str(exc), self.language))
            return
        self.provider_status[provider].setText(self._t("登入視窗已開啟；完成後請關閉登入視窗。"))
        self.login_buttons[provider].setEnabled(False)
        self.message.setText(self._t("請在平台的登入視窗完成操作；關閉視窗後會重新檢查。"))

    def _yield_for_login(self) -> None:
        """Keep the always-on-top settings from covering the vendor's login UI."""
        parent = self.parentWidget()
        self._hidden_for_login = self.isVisible()
        self._parent_hidden_for_login = parent if parent and parent.isVisible() else None
        if self._hidden_for_login:
            self.hide()
        if self._parent_hidden_for_login:
            self._parent_hidden_for_login.hide()

    def _return_from_login(self) -> None:
        if self._parent_hidden_for_login:
            self._parent_hidden_for_login.show()
            self._parent_hidden_for_login = None
        if self._hidden_for_login:
            self._hidden_for_login = False
            self.show()
            self.raise_()
            self.activateWindow()

    def _drain(self) -> None:
        for _ in range(100):
            try:
                kind, *data = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "working":
                self.provider_status[data[0]].setText(self._t("處理中…"))
            elif kind == "status":
                if self._cancel.is_set():
                    continue
                provider, status = data
                self._statuses[provider] = status
                self.provider_status[provider].setText(translate_message(status.message, self.language))
                if status.executable:
                    self._paths[provider] = status.executable
                    self.path_found.emit(provider, status.executable)
            elif kind == "error":
                if self._cancel.is_set():
                    continue
                provider, message = data
                self._statuses.pop(provider, None)
                self.provider_status[provider].setText(translate_message(message, self.language)[:500])
            elif kind == "done":
                cancelled = data[0] or self._cancel.is_set()
                self._set_busy(False)
                if cancelled:
                    for label in self.provider_status.values():
                        if label.text() in {self._t("等待處理…"), self._t("處理中…")}:
                            label.setText(self._t("已取消。"))
                self.message.setText(self._t("已取消；如安裝仍在執行，可稍後重新搜尋。") if cancelled else
                                     self._t("請依各平台狀態完成登入，再回設定頁儲存。"))
        for provider, process in list(self._login_processes.items()):
            try:
                finished = process.poll() is not None
            except OSError:
                finished = True
            if finished:
                del self._login_processes[provider]
                if not self._login_processes:
                    self._return_from_login()
                self._pending_refresh.add(provider)
        if not self._busy and self._pending_refresh:
            pending = [name for name in PROVIDERS if name in self._pending_refresh and self.provider_checks[name].isChecked()]
            self._pending_refresh.clear()
            if pending:
                self._start("search", pending)

    def _close_or_cancel(self) -> None:
        if self._busy:
            self._cancel.set()
            self.close_button.setEnabled(False)
            self.message.setText(self._t("正在取消，請稍候…"))
        else:
            self.reject()

    def _finished(self, _result: int) -> None:
        self._cancel.set()
        self._timer.stop()

    def shutdown(self) -> None:
        """Cancel ongoing setup when the whole companion application exits."""
        self._cancel.set()
        self._busy = False
        self.close()

    def closeEvent(self, event) -> None:
        if self._busy:
            self._close_or_cancel()
            event.ignore()
            return
        self._cancel.set()
        self._timer.stop()
        super().closeEvent(event)
