"""Optional reply direction embedded in the existing floating panel."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .i18n import tr


class ReplyDirectionEditor(QWidget):
    submitted = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, *, language: str = "en", parent=None):
        super().__init__(parent)
        t = lambda text: tr(text, language)
        self.setObjectName("replyDirectionEditor")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.direction_edit = QPlainTextEdit()
        self.direction_edit.setObjectName("replyDirection")
        self.direction_edit.setAccessibleName(t("回覆方向"))
        self.direction_edit.setPlaceholderText(t("想要怎麼回覆? (選填)"))
        self.direction_edit.setFixedHeight(104)
        self.direction_edit.setTabChangesFocus(True)
        layout.addWidget(self.direction_edit)
        actions = QHBoxLayout()
        actions.addStretch()
        self.submit_button = QPushButton(t("送出"))
        self.submit_button.setObjectName("replySubmit")
        self.submit_button.setMinimumHeight(34)
        self.submit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_button.clicked.connect(self._submit)
        actions.addWidget(self.submit_button)
        layout.addLayout(actions)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape_shortcut.activated.connect(self.cancelled.emit)

    @property
    def direction(self) -> str:
        return self.direction_edit.toPlainText().strip()

    def _submit(self) -> None:
        self.submit_button.setEnabled(False)
        self.submitted.emit(self.direction)
