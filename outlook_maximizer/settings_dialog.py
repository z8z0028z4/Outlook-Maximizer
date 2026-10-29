"""Settings reached from the tray; network and persistence stay in the controller."""

from __future__ import annotations

from dataclasses import replace
import re
import webbrowser

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QColorDialog, QComboBox, QDialog, QFontComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from .settings import Settings
from .floating_panel import surface_colors
from .model_catalog import ModelOption
from .i18n import tr


def _label(text: str = "", *, muted: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if muted:
        label.setObjectName("muted")
    return label


def _button(text: str, signal) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(signal)
    return button


class SettingsDialog(QDialog):
    settings_saved = pyqtSignal(object)
    choose_credentials_requested = pyqtSignal()
    connect_requested = pyqtSignal()
    disconnect_requested = pyqtSignal()
    cancel_auth_requested = pyqtSignal()
    models_requested = pyqtSignal()
    ai_setup_requested = pyqtSignal()

    def __init__(
        self,
        settings: Settings,
        *,
        calendar_connected: bool = False,
        credentials_configured: bool = False,
        connection_status: str = "",
        language: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._settings = replace(settings)
        self.language = language or settings.ui_language
        self._t = lambda text: tr(text, self.language)
        self._connected = calendar_connected
        self._credentials_configured = credentials_configured
        self._busy = False
        self._model_options = {"codex": [], "antigravity": [], "claude": []}
        self.setWindowTitle(self._t("設定 · Outlook 摘要助手"))
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont("Microsoft JhengHei UI", 10))
        self.resize(560, 720)
        self.setMinimumSize(460, 480)
        if self.screen():
            self.resize(560, min(720, self.screen().availableGeometry().height() - 60))
        self.setStyleSheet("""
            QFrame#settingsSurface { background: #f7f9fc; border: 1px solid #dfe5ef; border-radius: 16px; }
            QLabel { color: #26364a; background: transparent; }
            QLabel#muted { color: #69788d; font-size: 12px; }
            QLabel#heading { font-size: 21px; font-weight: 500; }
            QLabel#section { font-size: 14px; font-weight: 500; }
            QLabel#error { color: #a82d41; font-size: 12px; }
            QWidget#scrollContent, QScrollArea { background: transparent; border: none; }
            QCheckBox { color: #26364a; spacing: 10px; padding: 4px 0; }
            QLineEdit, QSpinBox, QComboBox, QPlainTextEdit { background: #ffffff; color: #26364a; border: 1px solid #dce3ed; border-radius: 6px; padding: 6px 8px; }
            QComboBox QLineEdit { border: none; padding: 0; }
            QComboBox QAbstractItemView { background: #ffffff; color: #26364a; selection-background-color: #e4eaf4; }
            QPushButton { background: #edf1f7; color: #26364a; border: 1px solid #dce3ed; border-radius: 7px; padding: 7px 12px; }
            QPushButton:hover { background: #e4eaf4; }
            QPushButton:disabled { color: #9aa5b4; background: #f0f2f6; }
            QPushButton#primary { background: #315fd4; color: white; border: none; padding: 9px 18px; }
            QPushButton#primary:hover { background: #2853bf; }
            QPushButton#primary:disabled { background: #c7d2eb; }
            QSlider::groove:horizontal { height: 4px; background: #dbe2ee; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; margin: -5px 0; background: #315fd4; border-radius: 7px; }
            QScrollBar:vertical { width: 5px; background: transparent; }
            QScrollBar::handle:vertical { background: #c6cfde; border-radius: 2px; min-height: 20px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        surface = QFrame()
        surface.setObjectName("settingsSurface")
        root.addWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(25, 22, 25, 20)
        layout.setSpacing(13)
        heading = _label(self._t("設定"))
        heading.setObjectName("heading")
        layout.addWidget(heading)
        self.connection_label = _label(connection_status or self._t("等待連接 Outlook"), muted=True)
        layout.addWidget(self.connection_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.scroll, 1)
        content = QWidget()
        content.setObjectName("scrollContent")
        self.scroll.setWidget(content)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 7, 0)
        body.setSpacing(12)
        self._section(body, self._t("語言"))
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("繁體中文", "zh-TW")
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(settings.ui_language)))
        self.language_combo.setAccessibleName(self._t("語言"))
        body.addWidget(self.language_combo)
        body.addWidget(_label(self._t("儲存後請重新啟動，才能完整套用語言。"), muted=True))
        self._section(body, self._t("閱讀與回覆"))
        self.auto_summary_check = QCheckBox(self._t("閱讀時自動摘要"))
        self.auto_summary_check.setChecked(settings.auto_summarize)
        self.auto_reply_check = QCheckBox(self._t("自動產生建議回覆"))
        self.auto_reply_check.setChecked(settings.auto_reply)
        self.topmost_check = QCheckBox(self._t("浮窗保持在最上層"))
        self.topmost_check.setChecked(settings.always_on_top)
        for check in (self.auto_summary_check, self.auto_reply_check, self.topmost_check):
            body.addWidget(check)
        body.addWidget(_label(self._t("關閉自動回覆後，按浮窗的「回覆」才會生成草稿。"), muted=True))

        self._section(body, self._t("AI 助理"))
        self.ai_setup_button = _button(self._t("登入／搜尋 AI 助理"), self.ai_setup_requested.emit)
        body.addWidget(self.ai_setup_button)
        body.addWidget(_label(self._t("搜尋、安裝與登入 ChatGPT、Antigravity 或 Claude。"), muted=True))

        self._section(body, self._t("About me · 關於我"))
        body.addWidget(_label(self._t("提供你的角色、負責事項與溝通立場，作為摘要及回覆的參考。"), muted=True))
        self.about_me_edit = QPlainTextEdit(settings.about_me)
        self.about_me_edit.setAccessibleName(self._t("About me 關於我"))
        self.about_me_edit.setPlaceholderText(self._t("例如：我的職務與單位、負責的專案、對外溝通立場、回覆時需避免的承諾。"))
        self.about_me_edit.setFixedHeight(140)
        body.addWidget(self.about_me_edit)
        self.about_me_count = _label(muted=True)
        self.about_me_count.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.about_me_edit.textChanged.connect(self._update_profile_count)
        self._update_profile_count()
        body.addWidget(self.about_me_count)
        body.addWidget(_label(self._t("可留白；內容會隨摘要與回覆請求送到你選擇的 AI。最多 6000 字。"), muted=True))

        self._section(body, self._t("浮窗外觀"))
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(_label(self._t("背景不透明度")))
        opacity_row.addStretch()
        self.opacity_value = _label()
        opacity_row.addWidget(self.opacity_value)
        body.addLayout(opacity_row)
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(8, 100)
        self.opacity_slider.setValue(round(settings.floating_opacity * 100))
        self.opacity_slider.setAccessibleName(self._t("浮窗背景濃度"))
        self.opacity_slider.valueChanged.connect(lambda value: self.opacity_value.setText(f"{value}%"))
        self.opacity_value.setText(f"{self.opacity_slider.value()}%")
        body.addWidget(self.opacity_slider)
        color_row = QHBoxLayout()
        color_row.addWidget(_label(self._t("背景顏色")))
        self.background_color_edit = QLineEdit(settings.background_color)
        self.background_color_edit.setMaxLength(7)
        self.background_color_edit.setAccessibleName(self._t("背景顏色 #RRGGBB"))
        self.background_color_edit.setPlaceholderText("#FAFCFF")
        color_row.addWidget(self.background_color_edit, 1)
        self.color_button = _button(self._t("選擇顏色"), self._choose_color)
        self.background_color_edit.textChanged.connect(self._update_color_preview)
        color_row.addWidget(self.color_button)
        body.addLayout(color_row)
        self._update_color_preview()
        body.addWidget(_label(self._t("100% 為完全不透明；文字會依背景顏色調整明暗。"), muted=True))
        dimensions = QFormLayout()
        dimensions.setSpacing(9)
        self.width_spin = self._spin(300, 520, settings.panel_width, " px")
        self.summary_height_spin = self._spin(180, 520, settings.summary_height, " px")
        self.reply_height_spin = self._spin(160, 460, settings.reply_height, " px")
        dimensions.addRow(self._t("浮窗寬度"), self.width_spin)
        dimensions.addRow(self._t("摘要展開高度"), self.summary_height_spin)
        dimensions.addRow(self._t("回覆展開高度"), self.reply_height_spin)
        body.addLayout(dimensions)

        typography = QFormLayout()
        typography.setSpacing(9)
        self.font_combo = QFontComboBox()
        self.font_combo.setAccessibleName(self._t("摘要與回覆字型"))
        self.font_combo.setEditable(True)
        self.font_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.font_combo.setMinimumContentsLength(16)
        self.font_combo.setEditText(settings.font_family)
        self.font_size_spin = self._spin(8, 24, settings.font_size, " pt")
        self.font_size_spin.setAccessibleName(self._t("摘要與回覆字級"))
        typography.addRow(self._t("字型"), self.font_combo)
        typography.addRow(self._t("字級"), self.font_size_spin)
        body.addLayout(typography)
        font_style = QHBoxLayout()
        self.font_bold_check = QCheckBox(self._t("粗體"))
        self.font_bold_check.setChecked(settings.font_bold)
        self.font_italic_check = QCheckBox(self._t("斜體"))
        self.font_italic_check.setChecked(settings.font_italic)
        font_style.addWidget(self.font_bold_check)
        font_style.addWidget(self.font_italic_check)
        font_style.addStretch()
        body.addLayout(font_style)
        self.font_preview = _label(self._t("摘要與回覆預覽\n會議時間已確認，請協助整理後續事項。"))
        self.font_preview.setAccessibleName(self._t("字型效果預覽"))
        self.font_preview.setMinimumHeight(65)
        body.addWidget(self.font_preview)
        body.addWidget(_label(self._t("套用至摘要與回覆；只調整文字外觀，不會重新呼叫 AI。"), muted=True))
        self.font_combo.currentTextChanged.connect(self._update_font_preview)
        self.font_size_spin.valueChanged.connect(self._update_font_preview)
        self.font_bold_check.toggled.connect(self._update_font_preview)
        self.font_italic_check.toggled.connect(self._update_font_preview)
        self._update_font_preview()

        self._section(body, "Google Calendar")
        self.status_label = _label(muted=True)
        body.addWidget(self.status_label)
        actions = QHBoxLayout()
        actions.setSpacing(7)
        self.choose_button = _button(self._t("載入 OAuth JSON"), self.choose_credentials_requested.emit)
        self.connect_button = _button(self._t("登入 Google"), self.connect_requested.emit)
        self.cancel_auth_button = _button(self._t("取消登入"), self.cancel_auth_requested.emit)
        for button in (self.choose_button, self.connect_button, self.cancel_auth_button):
            actions.addWidget(button)
        body.addLayout(actions)
        secondary = QHBoxLayout()
        self.disconnect_button = _button(self._t("清除本機授權"), self.disconnect_requested.emit)
        secondary.addWidget(self.disconnect_button)
        guide = _button(self._t("Google Cloud 設定"), self._open_cloud)
        secondary.addWidget(guide)
        secondary.addStretch()
        body.addLayout(secondary)
        body.addWidget(_label(self._t("首次連線需載入「電腦版應用程式」OAuth JSON，再由瀏覽器登入。"), muted=True))
        calendar_form = QFormLayout()
        self.calendar_edit = QLineEdit(settings.calendar_id)
        self.calendar_edit.setMaxLength(4096)
        self.calendar_edit.setAccessibleName(self._t("Google 行事曆 ID"))
        calendar_form.addRow(self._t("行事曆 ID"), self.calendar_edit)
        body.addLayout(calendar_form)
        body.addWidget(_label(self._t("primary 是目前 Google 帳號的主要行事曆。"), muted=True))

        self._section(body, "AI")
        ai = self.ai_form = QFormLayout()
        ai.setSpacing(9)
        self.provider_combo = QComboBox()
        self.provider_combo.setAccessibleName(self._t("AI 來源"))
        self.provider_combo.setPlaceholderText(self._t("請選擇要使用的 AI 來源。"))
        self.provider_combo.addItem(self._t("Codex（本機登入）"), "codex")
        self.provider_combo.addItem(self._t("Antigravity（本機登入）"), "antigravity")
        self.provider_combo.addItem(self._t("Claude（本機登入）"), "claude")
        self.provider_combo.setCurrentIndex(max(0, self.provider_combo.findData(settings.ai_provider)))
        self.codex_edit = QLineEdit(settings.codex_path)
        self.codex_edit.setMaxLength(8192)
        self.codex_edit.setAccessibleName(self._t("Codex 執行檔"))
        self.antigravity_edit = QLineEdit(settings.antigravity_path)
        self.antigravity_edit.setMaxLength(8192)
        self.antigravity_edit.setAccessibleName(self._t("Antigravity 執行檔"))
        self.claude_edit = QLineEdit(settings.claude_path)
        self.claude_edit.setMaxLength(8192)
        self.claude_edit.setAccessibleName(self._t("Claude 執行檔"))
        self.model_combo = self._model_combo(settings.model)
        self.model_edit = self.model_combo.lineEdit()
        self.model_edit.setAccessibleName(self._t("模型"))
        self.antigravity_model_combo = self._model_combo(settings.antigravity_model)
        self.antigravity_model_edit = self.antigravity_model_combo.lineEdit()
        self.antigravity_model_edit.setAccessibleName(self._t("Antigravity 模型"))
        self.claude_model_combo = self._model_combo(settings.claude_model)
        self.claude_model_edit = self.claude_model_combo.lineEdit()
        self.claude_model_edit.setAccessibleName(self._t("Claude 模型"))
        self.codex_effort_combo, self.antigravity_effort_combo, self.claude_effort_combo = QComboBox(), QComboBox(), QComboBox()
        self.codex_effort_combo.setAccessibleName("Codex effort")
        self.antigravity_effort_combo.setAccessibleName("Antigravity effort")
        self.claude_effort_combo.setAccessibleName("Claude effort")
        self._populate_efforts("codex", settings.codex_effort)
        self._populate_efforts("antigravity", settings.antigravity_effort)
        self._populate_efforts("claude", settings.claude_effort)
        self.timeout_spin = self._spin(15, 300, settings.ai_timeout_seconds, "")
        self.timeout_spin.setAccessibleName(self._t("AI 最長等待秒數"))
        ai.addRow(self._t("AI 來源"), self.provider_combo)
        ai.addRow(self._t("Codex 執行檔"), self.codex_edit)
        ai.addRow(self._t("模型"), self.model_combo)
        ai.addRow(self._t("Effort（思考強度）"), self.codex_effort_combo)
        ai.addRow(self._t("Antigravity 執行檔"), self.antigravity_edit)
        ai.addRow(self._t("模型"), self.antigravity_model_combo)
        ai.addRow(self._t("Effort（思考強度）"), self.antigravity_effort_combo)
        ai.addRow(self._t("Claude 執行檔"), self.claude_edit)
        ai.addRow(self._t("模型"), self.claude_model_combo)
        ai.addRow(self._t("Effort（思考強度）"), self.claude_effort_combo)
        ai.addRow(self._t("最長等待秒數"), self.timeout_spin)
        body.addLayout(ai)
        self.provider_hint = _label(muted=True)
        body.addWidget(self.provider_hint)
        catalog_row = QHBoxLayout()
        self.reload_models_button = _button(self._t("重新載入模型"), self.models_requested.emit)
        catalog_row.addWidget(self.reload_models_button)
        catalog_row.addStretch()
        body.addLayout(catalog_row)
        self.models_status = _label(self._t("模型清單載入中；也可直接輸入模型名稱。"), muted=True)
        body.addWidget(self.models_status)
        self.provider_combo.currentIndexChanged.connect(self._update_provider_controls)
        self.model_combo.currentTextChanged.connect(lambda: self._populate_efforts("codex"))
        self.antigravity_model_combo.currentTextChanged.connect(lambda: self._populate_efforts("antigravity"))
        self.claude_model_combo.currentTextChanged.connect(lambda: self._populate_efforts("claude"))
        self.set_models("claude", [ModelOption("sonnet", "Sonnet"), ModelOption("opus", "Opus"),
                                   ModelOption("haiku", "Haiku")])
        self._update_provider_controls()
        body.addStretch()
        self.error_label = _label()
        self.error_label.setObjectName("error")
        layout.addWidget(self.error_label)
        footer = QHBoxLayout()
        self.cancel_button = _button(self._t("取消"), self.reject)
        footer.addWidget(self.cancel_button)
        footer.addStretch()
        self.save_button = _button(self._t("儲存設定"), self._save)
        self.save_button.setObjectName("primary")
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        self.update_status(
            self._t("Google Calendar 已連線。") if calendar_connected else (
                self._t("OAuth JSON 已載入，請登入 Google。") if credentials_configured else self._t("尚未連接 Google Calendar。")
            ),
        )

    @staticmethod
    def _section(layout: QVBoxLayout, title: str) -> None:
        label = _label(title)
        label.setObjectName("section")
        label.setContentsMargins(0, 10, 0, 0)
        layout.addWidget(label)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int, suffix: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSuffix(suffix)
        spin.setMinimumWidth(105)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        return spin

    @staticmethod
    def _open_cloud() -> None:
        webbrowser.open("https://console.cloud.google.com/apis/library/calendar-json.googleapis.com")

    def _choose_color(self) -> None:
        current = QColor(self.background_color_edit.text())
        selected = QColorDialog.getColor(current if current.isValid() else QColor("#FAFCFF"), self, self._t("選擇浮窗背景顏色"))
        if selected.isValid():
            self.background_color_edit.setText(selected.name().upper())

    def _update_profile_count(self) -> None:
        self.about_me_count.setText(f"{len(self.about_me_edit.toPlainText())} / 6000")

    def _update_font_preview(self) -> None:
        font = QFont(self.font_combo.currentText().strip() or "Microsoft JhengHei UI",
                     self.font_size_spin.value())
        font.setBold(self.font_bold_check.isChecked())
        font.setItalic(self.font_italic_check.isChecked())
        self.font_preview.setFont(font)

    def _update_color_preview(self) -> None:
        color = self.background_color_edit.text().strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            foreground = surface_colors(QColor(color))[0].name()
            self.color_button.setStyleSheet(f"background:{color}; color:{foreground}; border:1px solid #AAB6C8; padding:7px 12px; border-radius:7px;")

    def _model_combo(self, value: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItem(self._t("CLI 預設模型"), "")
        combo.lineEdit().setMaxLength(200)
        combo.lineEdit().setPlaceholderText(self._t("留白使用 CLI 預設模型"))
        combo.setEditText(value)
        return combo

    @staticmethod
    def _model_value(combo: QComboBox) -> str:
        text = combo.currentText().strip()
        return "" if combo.currentData() == "" and text == combo.itemText(0) else text

    def _populate_efforts(self, provider: str, selected: str | None = None) -> None:
        combo = {"codex": self.codex_effort_combo, "antigravity": self.antigravity_effort_combo,
                 "claude": self.claude_effort_combo}[provider]
        model_combo = {"codex": self.model_combo, "antigravity": self.antigravity_model_combo,
                       "claude": self.claude_model_combo}[provider]
        if selected is None:
            selected = combo.currentData() or ""
        levels = {"codex": ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
                  "antigravity": ("low", "medium", "high", "xhigh", "max"),
                  "claude": ("low", "medium", "high", "xhigh", "max")}[provider]
        model = self._model_value(model_combo)
        for option in self._model_options[provider]:
            if option.model_id == model and option.efforts:
                levels = option.efforts
                break
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(self._t("CLI 預設"), "")
        for level in levels:
            combo.addItem(level, level)
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.blockSignals(False)

    def set_models(self, provider: str, options: list) -> None:
        if provider not in self._model_options:
            return
        self._model_options[provider] = options
        combo = {"codex": self.model_combo, "antigravity": self.antigravity_model_combo,
                 "claude": self.claude_model_combo}[provider]
        value = self._model_value(combo)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(self._t("CLI 預設模型"), "")
        for option in options:
            combo.addItem(option.model_id, option.model_id)
            combo.setItemData(combo.count() - 1, option.label, Qt.ItemDataRole.ToolTipRole)
        combo.setEditText(value)
        combo.blockSignals(False)
        self._populate_efforts(provider)
        self._update_models_status()

    def _update_models_status(self) -> None:
        provider = self.provider_combo.currentData()
        if provider not in self._model_options:
            self.models_status.setText(self._t("請選擇要使用的 AI 來源。"))
            return
        count = len(self._model_options[provider])
        loaded = (f"Loaded {count} models; you can also enter a model name." if self.language == "en"
                  else f"已載入 {count} 個模型；也可直接輸入模型名稱。")
        self.models_status.setText(loaded if count else self._t("可直接輸入模型名稱，或按「重新載入模型」。"))

    def _update_provider_controls(self) -> None:
        provider = self.provider_combo.currentData()
        for name, fields in {
            "codex": (self.codex_edit, self.model_combo, self.codex_effort_combo),
            "antigravity": (self.antigravity_edit, self.antigravity_model_combo, self.antigravity_effort_combo),
            "claude": (self.claude_edit, self.claude_model_combo, self.claude_effort_combo),
        }.items():
            for field in fields:
                self.ai_form.setRowVisible(field, provider == name)
        self.provider_hint.setText({
            "codex": self._t("沿用本機 Codex CLI 登入。"),
            "antigravity": self._t("沿用本機 Antigravity CLI 登入，每次摘要與回覆使用獨立請求。"),
            "claude": self._t("沿用本機 Claude Code 登入；公司強制的管理設定仍會套用。"),
        }.get(provider, self._t("請選擇要使用的 AI 來源。")))
        if (provider is not None and hasattr(self, "error_label")
                and self.error_label.text() == self._t("請選擇要使用的 AI 來源。")):
            self.error_label.clear()
        self._update_models_status()

    def require_provider_selection(self) -> None:
        """Require an explicit choice when first-run setup selected multiple CLIs."""
        self.provider_combo.setCurrentIndex(-1)
        self.error_label.setText(self._t("請選擇要使用的 AI 來源。"))

    def set_cli_path(self, provider: str, path: str) -> None:
        """Place discovered paths in the draft; Save applies all edited settings together."""
        field = {"codex": self.codex_edit, "antigravity": self.antigravity_edit,
                 "claude": self.claude_edit}.get(provider)
        if field is not None and path:
            field.setText(path)

    def update_status(
        self, text: str, *, connected: bool | None = None, busy: bool = False,
        credentials_configured: bool | None = None,
    ) -> None:
        if connected is not None:
            self._connected = connected
        if credentials_configured is not None:
            self._credentials_configured = credentials_configured
        self._busy = busy
        self.status_label.setText(text[:1000])
        self.choose_button.setEnabled(not busy)
        self.connect_button.setEnabled(not busy and self._credentials_configured)
        self.disconnect_button.setEnabled(not busy and self._connected)
        self.cancel_auth_button.setEnabled(busy)
        self.save_button.setEnabled(not busy)

    def _save(self) -> None:
        if self._busy:
            return
        try:
            if self.provider_combo.currentData() not in {"codex", "antigravity", "claude"}:
                raise ValueError(self._t("請選擇要使用的 AI 來源。"))
            raw_timeout = self.timeout_spin.lineEdit().text().strip()
            if not raw_timeout.isdecimal() or not 15 <= int(raw_timeout) <= 300:
                raise ValueError(self._t("AI 等待秒數需介於 15–300。"))
            calendar_id = self.calendar_edit.text().strip() or "primary"
            if len(calendar_id) > 1024 or any(ord(char) < 32 for char in calendar_id):
                raise ValueError(self._t("行事曆 ID 格式不正確。"))
            codex_path = self.codex_edit.text().strip().strip('"')
            if len(codex_path) > 4096 or any(ord(char) < 32 for char in codex_path):
                raise ValueError(self._t("Codex 執行檔路徑格式不正確。"))
            antigravity_path = self.antigravity_edit.text().strip().strip('"')
            if len(antigravity_path) > 4096 or any(ord(char) < 32 for char in antigravity_path):
                raise ValueError(self._t("Antigravity 執行檔路徑格式不正確。"))
            claude_path = self.claude_edit.text().strip().strip('"')
            if len(claude_path) > 4096 or any(ord(char) < 32 for char in claude_path):
                raise ValueError(self._t("Claude 執行檔路徑格式不正確。"))
            model = self._model_value(self.model_combo)
            antigravity_model = self._model_value(self.antigravity_model_combo)
            claude_model = self._model_value(self.claude_model_combo)
            if any(len(value) > 200 or any(ord(char) < 32 for char in value) for value in (model, antigravity_model, claude_model)):
                raise ValueError(self._t("模型名稱格式不正確。"))
            color = self.background_color_edit.text().strip().upper()
            if not re.fullmatch(r"#[0-9A-F]{6}", color):
                raise ValueError(self._t("背景顏色請使用 #RRGGBB 格式，或按「選擇顏色」。"))
            about_me = self.about_me_edit.toPlainText().strip()
            if len(about_me) > 6000:
                raise ValueError(self._t("About me 最多 6000 字，請縮短後再儲存。"))
            font_family = self.font_combo.currentText().strip()
            if not font_family or len(font_family) > 100 or re.search(r"[\x00-\x1f\x7f-\x9f]", font_family):
                raise ValueError(self._t("請選擇有效字型，字型名稱最多 100 字。"))
            raw_font_size = self.font_size_spin.cleanText()
            if not raw_font_size.isdecimal() or not 8 <= int(raw_font_size) <= 24:
                raise ValueError(self._t("字級需介於 8–24 pt。"))
            updated = replace(
                self._settings, codex_path=codex_path, model=model,
                ui_language=self.language_combo.currentData(),
                ai_provider=self.provider_combo.currentData(),
                codex_effort=self.codex_effort_combo.currentData(),
                antigravity_path=antigravity_path, antigravity_model=antigravity_model,
                antigravity_effort=self.antigravity_effort_combo.currentData(),
                claude_path=claude_path, claude_model=claude_model,
                claude_effort=self.claude_effort_combo.currentData(),
                about_me=about_me,
                calendar_id=calendar_id, ai_timeout_seconds=int(raw_timeout),
                auto_summarize=self.auto_summary_check.isChecked(),
                auto_reply=self.auto_reply_check.isChecked(),
                always_on_top=self.topmost_check.isChecked(),
                floating_opacity=self.opacity_slider.value() / 100,
                background_color=color,
                font_family=font_family, font_size=int(raw_font_size),
                font_bold=self.font_bold_check.isChecked(),
                font_italic=self.font_italic_check.isChecked(),
                panel_width=self.width_spin.value(),
                summary_height=self.summary_height_spin.value(),
                reply_height=self.reply_height_spin.value(),
            )
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.error_label.clear()
        self.settings_saved.emit(updated)
