"""Two-state text surfaces for the desktop companion; no native window chrome."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractButton, QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget
from .i18n import tr


def _luminance(color: QColor) -> float:
    channels = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                for value in color.getRgbF()[:3]]
    return sum(weight * value for weight, value in zip((.2126, .7152, .0722), channels))


def _contrast(first: QColor, second: QColor) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + .05) / (dark + .05)


def surface_colors(background: QColor) -> tuple[QColor, QColor]:
    """Keep both text roles readable for arbitrary opaque user-selected colors."""
    ink = max((QColor("#28384C"), QColor("#F4F7FC")), key=lambda color: _contrast(color, background))
    if _contrast(ink, background) < 4.5:
        ink = max((QColor("#000000"), QColor("#FFFFFF")), key=lambda color: _contrast(color, background))
    muted = QColor("#B9C6D8" if ink.lightness() > 128 else "#68798D")
    for weight in (0, .25, .5, .75, 1):
        candidate = QColor(*(round(old * (1 - weight) + new * weight)
                             for old, new in zip(muted.getRgb()[:3], ink.getRgb()[:3])))
        if _contrast(candidate, background) >= 4.5:
            return ink, candidate
    return ink, ink


class _PinButton(QAbstractButton):
    def __init__(self, header):
        super().__init__(header)
        self._panel = header._panel
        self._t = self._panel._t
        self.setObjectName("panelPin")
        self.setFixedSize(26, 28)
        self.setCheckable(True)
        self.setChecked(bool(self._panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(self._t("釘選摘要與回覆於所有 App 上方"))
        self.toggled.connect(self._update_hint)
        self._update_hint()

    def _update_hint(self) -> None:
        self.setToolTip(self._t("已釘選：摘要與回覆保持最上層；按一下取消") if self.isChecked() else self._t("釘選摘要與回覆，跨 App 保持最上層"))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink, muted = self._panel._colors()
        if self.isChecked() or self.underMouse() or self.hasFocus():
            fill = QColor(ink)
            fill.setAlpha(22 if self.isChecked() else 12)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(self.rect()), 5, 5)
        color = QColor("#537FB8") if self.isChecked() else muted
        if _contrast(color, self._panel._background) < 3:
            color = ink
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(35 if self.isChecked() else 55)
        painter.setPen(QPen(color, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(color if self.isChecked() else Qt.BrushStyle.NoBrush)
        pin = QPainterPath()
        pin.moveTo(-4, -7)
        for x, y in ((4, -7), (3, -4), (3, 0), (5, 3), (-5, 3), (-3, 0), (-3, -4)):
            pin.lineTo(x, y)
        pin.closeSubpath()
        painter.drawPath(pin)
        painter.drawLine(QPointF(0, 3), QPointF(0, 8))


class _PanelHeader(QAbstractButton):
    """A keyboard-accessible button that becomes a drag handle after a threshold."""

    def __init__(self, panel: FloatingPanel):
        super().__init__(panel)
        self._panel = panel
        self._press_global: QPoint | None = None
        self._press_offset = QPoint()
        self._dragging = False
        self.setObjectName("panelHeader")
        self.setFixedHeight(44)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFont(QFont("Microsoft JhengHei UI", 10))
        self.pin_button = _PinButton(self)
        self.pin_button.clicked.connect(panel.pin_requested.emit)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.pin_button.move(self.width() - 65, (self.height() - self.pin_button.height()) // 2)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink, muted = self._panel._colors()
        painter.setPen(muted)
        painter.setFont(QFont("Segoe UI Symbol", 12))
        painter.drawText(QRectF(17, 0, 19, self.height()), Qt.AlignmentFlag.AlignVCenter, "✧" if self._panel.kind == "summary" else "▤")
        painter.setFont(self.font())
        painter.setPen(ink)
        text_width = max(0, self.width() - 111)
        title = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, text_width)
        painter.drawText(QRectF(43, 0, text_width, self.height()), Qt.AlignmentFlag.AlignVCenter, title)
        painter.setPen(QPen(QColor(muted.red(), muted.green(), muted.blue(), 140), 1.2))
        x, y = self.width() - 23, self.height() // 2
        if self._panel.state == "collapsed":
            painter.drawLine(x - 2, y - 4, x + 2, y)
            painter.drawLine(x + 2, y, x - 2, y + 4)
        else:
            painter.drawLine(x - 4, y - 2, x, y + 2)
            painter.drawLine(x, y + 2, x + 4, y - 2)
        if self.hasFocus():
            painter.setPen(QPen(QColor(muted.red(), muted.green(), muted.blue(), 100), 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(self.rect().adjusted(7, 5, -7, -5), 4, 4)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_global = event.globalPosition().toPoint()
        self._press_offset = self._press_global - self._panel.pos()
        self._dragging = False
        self.setDown(True)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is None:
            return
        current = event.globalPosition().toPoint()
        if not self._dragging and (current - self._press_global).manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True
            self.setDown(False)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            self._panel.move(current - self._press_offset)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press_global is None:
            super().mouseReleaseEvent(event)
            return
        dragged = self._dragging
        inside = self.rect().contains(event.position().toPoint())
        self._press_global = None
        self._dragging = False
        self.setDown(False)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if not dragged and inside:
            self.click()
        event.accept()


class _Spinner(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("loadingSpinner")
        self.setFixedSize(18, 18)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._advance)

    def _advance(self) -> None:
        self.angle = (self.angle + 14) % 360
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(112, 132, 160, 45), 1.5))
        painter.drawEllipse(QRectF(2, 2, 14, 14))
        painter.setPen(QPen(QColor(91, 126, 180), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(2, 2, 14, 14), -self.angle * 16, 90 * 16)


class FloatingPanel(QWidget):
    """A frameless, bounded translucent surface with geometry notifications.

    Updating content deliberately does not show a hidden window. The controller
    decides whether the user's current mail or tray setting should be visible.
    """

    header_clicked = pyqtSignal()
    pin_requested = pyqtSignal(bool)
    context_requested = pyqtSignal(QPoint)
    moved = pyqtSignal()
    resized = pyqtSignal()

    def __init__(self, kind: str, *, width: int = 348, expanded_height: int = 300,
                 opacity: float = .20, background_color: str = "#FAFCFF",
                 topmost: bool = True, animations: bool = True,
                 font_family: str = "Microsoft JhengHei UI", font_size: int = 10,
                 font_bold: bool = False, font_italic: bool = False, language: str = "en"):
        if kind not in {"summary", "reply"}:
            raise ValueError("Panel kind must be summary or reply")
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        if topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.language = language
        self._t = lambda text: tr(text, self.language)
        self.kind = kind
        self.state = "collapsed"
        self._disposed = False
        self._animations = bool(animations)
        self._width = max(240, min(int(width), 720))
        self._expanded_height = max(100, min(int(expanded_height), 900))
        self._opacity = max(0.0, min(float(opacity), 1.0))
        self._background = QColor(background_color)
        if not self._background.isValid():
            self._background = QColor("#FAFCFF")
        self._spinner: _Spinner | None = None
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(260)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(lambda value: self._set_height(int(value)))
        self.setWindowTitle(self._t("摘要") if kind == "summary" else self._t("回覆"))
        self.setAccessibleName(self._t("摘要浮窗") if kind == "summary" else self._t("回覆浮窗"))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAutoFillBackground(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = _PanelHeader(self)
        self.header.clicked.connect(self.header_clicked.emit)
        layout.addWidget(self.header)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("panelScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.scroll.setStyleSheet("""
            QScrollArea, QScrollArea > QWidget { background: transparent; border: 0; }
            QScrollBar:vertical { background: transparent; width: 4px; margin: 6px 0; }
            QScrollBar::handle:vertical { background: rgba(110, 128, 150, 65); border-radius: 2px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        layout.addWidget(self.scroll)
        self._apply_font(font_family, font_size, font_bold, font_italic)
        self._update_surface_style()
        self.show_idle()

    def _apply_font(self, family=None, size=None, bold=None, italic=None) -> None:
        font = QFont(self.font())
        if family is not None:
            font.setFamily(family)
        if size is not None:
            font.setPointSize(max(8, min(int(size), 24)))
        if bold is not None:
            font.setBold(bool(bold))
        if italic is not None:
            font.setItalic(bool(italic))
        self.setFont(font)
        for widget in self.findChildren(QWidget):
            widget.setFont(font)
        self.header.setFont(font)
        self.header.setFixedHeight(max(44, self.header.fontMetrics().height() + 16))

    def _colors(self) -> tuple[QColor, QColor]:
        return surface_colors(self._background)

    def _update_surface_style(self) -> None:
        ink, muted = self._colors()
        dark = ink.lightness() > 128
        border = f"rgba({ink.red()},{ink.green()},{ink.blue()},48)"
        divider = f"rgba({ink.red()},{ink.green()},{ink.blue()},30)"
        fill = "rgba(255,255,255,14)" if dark else "rgba(250,252,255,70)"
        hover = "rgba(255,255,255,30)" if dark else "rgba(240,246,255,190)"
        error = "#F0A9AE" if dark else "#A85F65"
        if _contrast(QColor(error), self._background) < 4.5:
            error = ink.name()
        self.setStyleSheet(f"""
            QLabel {{background:transparent; color:{ink.name()};}}
            QLabel#mailMuted, QLabel#panelLoading {{color:{muted.name()};}}
            QLabel#panelError {{color:{error};}}
            QFrame#mailDivider {{background:{divider};}}
            QFrame#calendarDivider {{background:rgba({ink.red()},{ink.green()},{ink.blue()},80);}}
            QPushButton#mailAction {{color:{ink.name()}; background:{fill}; border:1px solid {border}; border-radius:7px; padding:6px 12px;}}
            QPushButton#mailAction:hover {{background:{hover};}}
            QPushButton#mailAction:disabled {{color:{muted.name()};}}
            QPushButton#eventEdit {{color:{muted.name()}; background:transparent; border:0; padding:3px;}}
            QPushButton#repeatCalendar {{color:{muted.name()}; background:transparent; border:0; padding:6px 3px; font-size:9pt;}}
            QPushButton#repeatCalendar:hover {{color:{ink.name()}; background:{hover}; border-radius:5px;}}
            QPushButton#repeatCalendar:disabled {{color:{muted.name()};}}
        """)
        self.header.update()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QColor(self._background)
        background.setAlphaF(self._opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        if self.state != "collapsed":
            ink, _ = self._colors()
            ink.setAlpha(28)
            painter.setPen(QPen(ink, 1))
            painter.drawLine(18, self.header.height(), self.width() - 18, self.header.height())

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.timer.stop()
            self._spinner = None

    def _replace_content(self, content: QWidget) -> None:
        self._stop_spinner()
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        body = QWidget()
        body.setObjectName("floatingPanelBody")
        body.setAutoFillBackground(False)
        body.setStyleSheet("QWidget#floatingPanelBody { background: transparent; }")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 13, 18, 18)
        layout.setSpacing(0)
        layout.addWidget(content)
        layout.addStretch(1)
        content.setAutoFillBackground(False)
        self.scroll.setWidget(body)
        for widget in (body, content, *content.findChildren(QWidget)):
            widget.setFont(self.font())
        self.scroll.verticalScrollBar().setValue(0)

    def _set_height(self, height: int) -> None:
        if not self._disposed:
            self.setFixedHeight(max(self.header.height(), height))

    def _transition(self, state: str, target_height: int) -> None:
        if self._disposed:
            return
        self._animation.stop()
        self.state = state
        self.header.setText(self._t("摘要") if self.kind == "summary" else self._t("回覆"))
        self.header.setAccessibleName(self.header.text())
        self.header.pin_button.setVisible(True)
        self.scroll.setVisible(state != "collapsed")
        compact_width = max(150, self.header.fontMetrics().horizontalAdvance(self.header.text()) + 111)
        self.setFixedWidth(min(self._width, compact_width) if state == "collapsed" else self._width)
        self.update()
        if self._animations and self.isVisible() and self.height() != target_height:
            self._animation.setStartValue(self.height())
            self._animation.setEndValue(target_height)
            self._animation.start()
        else:
            self._set_height(target_height)

    def show_idle(self, hint: str = "") -> None:
        self._stop_spinner()
        self.header.setToolTip(hint)
        self._transition("collapsed", self.header.height())

    def show_loading(self, message: str) -> None:
        if self._disposed:
            return
        from PyQt6.QtWidgets import QHBoxLayout
        content = QWidget()
        row = QHBoxLayout(content)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        spinner = _Spinner(content)
        text = QLabel(message, content)
        text.setObjectName("panelLoading")
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setWordWrap(True)
        row.addWidget(spinner)
        row.addWidget(text, 1)
        self._replace_content(content)
        self._spinner = spinner
        spinner.timer.start()
        loading_height = max(105, self.header.height() + text.heightForWidth(max(100, self._width - 64)) + 26)
        self._transition("loading", min(self._expanded_height, loading_height))

    def show_content(self, widget: QWidget) -> None:
        if self._disposed:
            return
        self._replace_content(widget)
        self._transition("ready", self._expanded_height)

    def show_error(self, message: str) -> None:
        if self._disposed:
            return
        label = QLabel(message)
        label.setObjectName("panelError")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        self._replace_content(label)
        self._transition("error", min(self._expanded_height, 150))

    def collapse(self) -> None:
        self.show_idle()

    def set_expanded_height(self, height: int) -> None:
        height = max(100, min(int(height), 900))
        if self._disposed or self._expanded_height == height:
            return
        self._expanded_height = height
        if self.state == "ready":
            self._transition("ready", height)

    def configure_style(self, *, width: int, expanded_height: int, opacity: float, topmost: bool,
                        background_color: str | None = None, font_family: str | None = None,
                        font_size: int | None = None, font_bold: bool | None = None,
                        font_italic: bool | None = None) -> None:
        if self._disposed:
            return
        self._width = max(240, min(int(width), 720))
        self._expanded_height = max(100, min(int(expanded_height), 900))
        self._opacity = max(0.0, min(float(opacity), 1.0))
        if background_color is not None:
            color = QColor(background_color)
            if color.isValid():
                self._background = color
        self._apply_font(font_family, font_size, font_bold, font_italic)
        self._update_surface_style()
        was_visible, position = self.isVisible(), self.pos()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(topmost))
        self.header.pin_button.setChecked(bool(topmost))
        self.move(position)
        if was_visible:
            self.show()
        target = self._expanded_height if self.state == "ready" else (self.header.height() if self.state == "collapsed" else min(self._expanded_height, max(105, self.header.height() + 60)) if self.state == "loading" else min(self._expanded_height, 150))
        self._transition(self.state, target)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if not getattr(self, "_disposed", True):
            self.moved.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._disposed:
            self.resized.emit()

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(event.globalPos())
        event.accept()

    def closeEvent(self, event) -> None:
        if self._disposed:
            event.accept()
        else:
            self.hide()
            event.ignore()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._animation.stop()
        self._stop_spinner()
        self.close()
