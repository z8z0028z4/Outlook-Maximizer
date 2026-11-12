"""Collect the user's direction before generating a reply."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .i18n import tr


class ReplyDirectionDialog(QDialog):
    def __init__(self, subject: str, *, language: str = "en", parent=None):
        super().__init__(parent)
        t = lambda text: tr(text, language)
        self.setWindowTitle(t("想要怎麼回覆？"))
        self.resize(520, 340)
        self.setMinimumSize(360, 260)
        self.setStyleSheet("QDialog {background:#F8FAFD;} QLabel {color:#30425A;} "
                           "QPlainTextEdit {background:white; color:#28384C; border:1px solid #D7DFEA; "
                           "border-radius:5px; padding:8px;} QPushButton {padding:8px 16px;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = QLabel(t("想要怎麼回覆？"))
        title.setStyleSheet("font-size:17px;")
        layout.addWidget(title)
        mail_label = QLabel(subject)
        mail_label.setTextFormat(Qt.TextFormat.PlainText)
        mail_label.setWordWrap(True)
        layout.addWidget(mail_label)
        self.direction_edit = QPlainTextEdit()
        self.direction_edit.setAccessibleName(t("回覆方向"))
        self.direction_edit.setPlaceholderText(t("輸入或貼上回覆方向，例如：婉拒邀請，並提議下週再討論。"))
        layout.addWidget(self.direction_edit, 1)
        hint = QLabel(t("以你的輸入為主要方向，About me 作為補充參考。"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_button = QPushButton(t("取消"))
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.submit_button = QPushButton(t("送出"))
        self.submit_button.setEnabled(False)
        self.submit_button.clicked.connect(self.accept)
        actions.addWidget(self.submit_button)
        layout.addLayout(actions)
        self.direction_edit.textChanged.connect(
            lambda: self.submit_button.setEnabled(bool(self.direction)))
        self.direction_edit.setFocus()

    @property
    def direction(self) -> str:
        return self.direction_edit.toPlainText().strip()
