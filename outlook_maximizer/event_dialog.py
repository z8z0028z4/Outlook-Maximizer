"""Small calendar editor shared by the floating summary's event actions."""

from dataclasses import replace
from datetime import date, datetime, timedelta

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .calendar_api import format_event_description
from .event_editor import event_from_fields, get_timezone
from .models import CalendarEvent, ValidationError
from .i18n import tr, translate_message


class EventEditor(QDialog):
    def __init__(self, event: CalendarEvent, on_save, *, language: str = "en", parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.language = language
        self._t = lambda text: tr(text, self.language)
        self.setWindowTitle(self._t("確認行程"))
        self.setMinimumWidth(460)
        self.setStyleSheet("QDialog, QScrollArea, QWidget#eventFields {background:#F8FAFD;} QLabel {color:#30425A;} QLineEdit, QPlainTextEdit {background:white; color:#28384C; border:1px solid #D7DFEA; border-radius:5px; padding:6px;} QPushButton {padding:8px 16px;}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(13)
        title = QLabel(self._t("確認日期與時間"))
        title.setStyleSheet("font-size:17px;")
        outer.addWidget(title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        fields = QWidget()
        fields.setObjectName("eventFields")
        self.scroll.setWidget(fields)
        body = QVBoxLayout(fields)
        body.setContentsMargins(0, 0, 7, 0)
        body.setSpacing(12)
        outer.addWidget(self.scroll, 1)
        defaults = dict(title=event.title, start_date="", start_time="", end_date="", end_time="", timezone=event.timezone, location=event.location)
        try:
            if event.all_day and event.start and event.end:
                defaults.update(start_date=event.start, end_date=(date.fromisoformat(event.end) - timedelta(days=1)).isoformat())
            elif event.start and event.end:
                zone = get_timezone(event.timezone)
                start = datetime.fromisoformat(event.start.replace("Z", "+00:00")).astimezone(zone)
                end = datetime.fromisoformat(event.end.replace("Z", "+00:00")).astimezone(zone)
                defaults.update(start_date=start.strftime("%Y-%m-%d"), start_time=start.strftime("%H:%M"), end_date=end.strftime("%Y-%m-%d"), end_time=end.strftime("%H:%M"))
        except (ValueError, ValidationError):
            pass
        self.values = {}
        form = QFormLayout()
        for key, label in (("title", self._t("行程名稱")), ("start_date", self._t("開始日期 YYYY-MM-DD")), ("start_time", self._t("開始時間 HH:MM")), ("end_date", self._t("結束日期（全天含當天）")), ("end_time", self._t("結束時間 HH:MM")), ("timezone", self._t("時區")), ("location", self._t("地點"))):
            field = QLineEdit(defaults[key])
            field.setAccessibleName(label)
            self.values[key] = field
            form.addRow(label, field)
        body.addLayout(form)
        self.all_day = QCheckBox(self._t("全天行程"))
        self.all_day.setChecked(event.all_day)
        body.addWidget(self.all_day)
        body.addWidget(QLabel(self._t("備註")))
        self.notes_edit = QPlainTextEdit(event.description)
        self.notes_edit.setAccessibleName(self._t("行事曆備註"))
        self.notes_edit.setPlaceholderText(self._t("加入會議連結、議程或其他備註，最多 8000 字。"))
        self.notes_edit.setFixedHeight(110)
        body.addWidget(self.notes_edit)
        body.addWidget(QLabel(self._t("預計加入的完整備註（含信件依據）")))
        self.description_preview = QPlainTextEdit()
        self.description_preview.setReadOnly(True)
        self.description_preview.setAccessibleName(self._t("預計加入行事曆的完整備註"))
        self.description_preview.setFixedHeight(110)
        def update_description():
            preview = replace(event, description=self.notes_edit.toPlainText())
            self.description_preview.setPlainText(format_event_description(preview, ui_language=self.language))
        self.notes_edit.textChanged.connect(update_description)
        update_description()
        body.addWidget(self.description_preview)
        body.addStretch()
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color:#A13A49;")
        outer.addWidget(self.error)
        actions = QHBoxLayout()
        cancel = self.cancel_button = QPushButton(self._t("取消"))
        save = self.save_button = QPushButton(self._t("儲存行程資料"))
        cancel.clicked.connect(self.reject)
        def save_event():
            try:
                values = {key: field.text().strip() for key, field in self.values.items()}
                values["timezone_name"] = values.pop("timezone")
                updated = event_from_fields(event, **values, all_day=self.all_day.isChecked(),
                                            description=self.notes_edit.toPlainText())
                on_save(updated)
                self.accept()
            except ValidationError as exc:
                self.error.setText(translate_message(str(exc), self.language))
        save.clicked.connect(save_event)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(save)
        outer.addLayout(actions)
        available = self.screen().availableGeometry().height() if self.screen() else 900
        self.resize(560, min(780, max(420, available - 80)))
