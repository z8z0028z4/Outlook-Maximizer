import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QContextMenuEvent, QPalette
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from outlook_maximizer.floating_panel import FloatingPanel


class FloatingPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = FloatingPanel("summary", animations=False)

    def tearDown(self):
        self.panel.dispose()
        self.app.processEvents()

    def content(self, lines=3):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        for index in range(lines):
            label = QLabel(f"摘要 {index}：這是測試內容。")
            label.setWordWrap(True)
            layout.addWidget(label)
        self.action = QPushButton("加入行事曆")
        layout.addWidget(self.action)
        return widget

    def test_tool_window_has_no_native_caption_buttons(self):
        flags = self.panel.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertEqual(self.panel.windowType(), Qt.WindowType.Tool)
        self.assertFalse(flags & Qt.WindowType.WindowMinimizeButtonHint)
        self.assertFalse(flags & Qt.WindowType.WindowMaximizeButtonHint)
        self.assertFalse(flags & Qt.WindowType.WindowCloseButtonHint)

    def test_background_transparency_does_not_dim_text(self):
        self.panel.show_content(self.content())
        self.panel.show()
        self.app.processEvents()
        image = self.panel.grab().toImage()
        alphas = [image.pixelColor(x, y).alpha() for y in range(0, image.height(), 2) for x in range(0, image.width(), 2)]
        self.assertTrue(self.panel.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertEqual(self.panel.windowOpacity(), 1.0)
        self.assertGreater(max(alphas), 245)
        self.assertTrue(any(20 < alpha < 100 for alpha in alphas))

    def test_state_updates_never_show_a_hidden_panel(self):
        self.panel.show_loading("讀取中")
        self.assertFalse(self.panel.isVisible())
        self.panel.show_content(self.content())
        self.assertFalse(self.panel.isVisible())
        self.panel.show_error("請重試")
        self.assertFalse(self.panel.isVisible())

    def test_collapsed_reply_is_a_compact_generate_button(self):
        reply = FloatingPanel("reply", animations=False)
        try:
            reply.show_idle()
            self.assertEqual(reply.state, "collapsed")
            self.assertLessEqual(reply.height(), 52)
            self.assertLess(reply.width(), 250)
            self.assertEqual("Reply", reply.header.text())
        finally:
            reply.dispose()

    def test_short_titles_are_used_for_both_collapsed_and_expanded_states(self):
        for kind, title in (("summary", "Summary"), ("reply", "Reply")):
            panel = FloatingPanel(kind, animations=False)
            try:
                panel.show_idle()
                self.assertEqual(panel.header.text(), title)
                self.assertLessEqual(panel.width(), 220)
                panel.show_loading("讀取中")
                self.assertEqual(panel.header.text(), title)
                panel.show_content(self.content())
                self.assertEqual(panel.header.text(), title)
                panel.show_error("測試錯誤")
                self.assertEqual(panel.header.text(), title)
            finally:
                panel.dispose()

    def test_spinner_runs_only_while_loading(self):
        self.panel.show_loading("正在整理")
        self.assertEqual(self.panel.state, "loading")
        self.assertTrue(any(timer.isActive() for timer in self.panel.findChildren(QTimer)))
        self.panel.show_content(self.content())
        self.assertEqual(self.panel.state, "ready")
        self.assertFalse(any(timer.isActive() for timer in self.panel.findChildren(QTimer)))

    def test_error_and_collapse_stop_spinner(self):
        self.panel.show_loading("正在整理")
        self.panel.show_error("讀取失敗")
        self.assertEqual(self.panel.state, "error")
        self.assertFalse(any(timer.isActive() for timer in self.panel.findChildren(QTimer)))
        self.panel.collapse()
        self.assertEqual(self.panel.state, "collapsed")
        self.assertFalse(self.panel.scroll.isVisible())

    def test_long_content_has_scrollable_bottom_action(self):
        self.panel.show_content(self.content(35))
        self.panel.show()
        self.app.processEvents()
        bar = self.panel.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertEqual(self.panel.height(), 300)
        bar.setValue(bar.maximum())
        self.app.processEvents()
        viewport = self.panel.scroll.viewport()
        bottom = self.action.mapTo(viewport, self.action.rect().bottomRight()).y()
        self.assertLessEqual(bottom, viewport.height())

    def test_click_emits_request_without_moving_window(self):
        self.panel.move(100, 100)
        self.panel.show()
        self.app.processEvents()
        before = self.panel.pos()
        clicks = QSignalSpy(self.panel.header_clicked)
        QTest.mouseClick(self.panel.header, Qt.MouseButton.LeftButton, pos=QPoint(70, 20))
        self.assertEqual(len(clicks), 1)
        self.assertEqual(self.panel.pos(), before)

    def test_drag_moves_only_this_panel_and_does_not_click(self):
        other = FloatingPanel("reply", animations=False)
        try:
            other.move(700, 150)
            self.panel.move(100, 100)
            self.panel.show()
            self.app.processEvents()
            before, other_before = self.panel.pos(), other.pos()
            clicks = QSignalSpy(self.panel.header_clicked)
            QTest.mousePress(self.panel.header, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
            QTest.mouseMove(self.panel.header, QPoint(80, 35))
            QTest.mouseRelease(self.panel.header, Qt.MouseButton.LeftButton, pos=QPoint(80, 35))
            self.assertNotEqual(self.panel.pos(), before)
            self.assertEqual(other.pos(), other_before)
            self.assertEqual(len(clicks), 0)
        finally:
            other.dispose()

    def test_right_click_emits_context_location(self):
        requests = QSignalSpy(self.panel.context_requested)
        self.app.sendEvent(self.panel, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(105, 105)))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0], QPoint(105, 105))

    def test_style_change_preserves_visibility_and_user_position(self):
        self.panel.move(170, 90)
        self.panel.configure_style(width=390, expanded_height=340, opacity=.3, topmost=False)
        self.assertFalse(self.panel.isVisible())
        self.assertEqual(self.panel.pos(), QPoint(170, 90))
        self.panel.show_content(self.content())
        self.assertEqual(self.panel.width(), 390)
        self.assertEqual(self.panel.height(), 340)

    def test_dispose_stops_loading_and_animation(self):
        panel = FloatingPanel("reply", animations=True)
        panel.show_loading("讀取中")
        panel.dispose()
        self.assertFalse(any(timer.isActive() for timer in panel.findChildren(QTimer)))
        self.assertFalse(panel.isVisible())

    def test_custom_background_can_be_completely_opaque(self):
        self.panel.configure_style(width=348, expanded_height=300, opacity=1.0,
                                   topmost=True, background_color="#123456")
        self.panel.show_content(self.content())
        self.panel.show()
        self.app.processEvents()
        color = self.panel.grab().toImage().pixelColor(6, 80)
        self.assertEqual((color.name(), color.alpha()), ("#123456", 255))
        self.assertEqual(self.panel.windowOpacity(), 1.0)

    def test_dark_color_updates_content_contrast_without_replacing_widgets(self):
        widget = self.content()
        label = widget.findChildren(QLabel)[0]
        self.panel.show_content(widget)
        self.panel.show()
        self.app.processEvents()
        before = label.palette().color(QPalette.ColorRole.WindowText).lightness()
        self.panel.configure_style(width=348, expanded_height=300, opacity=1.0,
                                   topmost=True, background_color="#102030")
        self.app.processEvents()
        after = label.palette().color(QPalette.ColorRole.WindowText).lightness()
        self.assertGreater(after, before)
        self.assertGreater(after, 200)
        self.assertIs(self.panel.scroll.widget().findChildren(QLabel)[0], label)

    def test_custom_color_keeps_background_alpha_separate_from_text(self):
        self.panel.configure_style(width=348, expanded_height=300, opacity=.2,
                                   topmost=True, background_color="#223344")
        self.panel.show_content(self.content())
        self.panel.show()
        self.app.processEvents()
        image = self.panel.grab().toImage()
        self.assertAlmostEqual(image.pixelColor(6, 80).alpha(), 51, delta=1)
        self.assertGreater(max(image.pixelColor(x, 20).alpha() for x in range(43, 140)), 245)

    def test_opaque_medium_gray_has_readable_normal_and_muted_text(self):
        self.panel.configure_style(width=348, expanded_height=300, opacity=1.0,
                                   topmost=True, background_color="#7C7C7C")
        normal, muted = self.panel._colors()
        self.assertEqual(normal.name(), "#000000")
        self.assertLessEqual(muted.lightness(), 15)

    def test_header_pin_requests_topmost_without_collapsing_or_dragging(self):
        self.panel.show_content(self.content())
        self.panel.show()
        self.app.processEvents()
        before = self.panel.pos()
        requests, clicks = QSignalSpy(self.panel.pin_requested), QSignalSpy(self.panel.header_clicked)
        pin = self.panel.header.pin_button
        self.assertTrue(pin.isChecked())
        QTest.mouseClick(pin, Qt.MouseButton.LeftButton)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0], False)
        self.assertEqual(len(clicks), 0)
        self.assertEqual(self.panel.state, "ready")
        self.assertEqual(self.panel.pos(), before)

    def test_programmatic_pin_sync_preserves_hidden_state_and_has_no_request(self):
        requests = QSignalSpy(self.panel.pin_requested)
        self.panel.configure_style(width=348, expanded_height=300, opacity=.2, topmost=False)
        self.assertFalse(self.panel.header.pin_button.isChecked())
        self.assertFalse(self.panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertFalse(self.panel.isVisible())
        self.assertEqual(len(requests), 0)

    def test_collapsed_panel_keeps_pin_visible_and_reserves_title_space(self):
        self.panel.show_idle()
        self.panel.show()
        self.app.processEvents()
        pin = self.panel.header.pin_button
        self.assertTrue(pin.isVisible())
        self.assertGreaterEqual(pin.x(), 43 + self.panel.header.fontMetrics().horizontalAdvance(self.panel.header.text()))
        self.assertLess(pin.geometry().right(), self.panel.width() - 28)
        self.assertLessEqual(self.panel.width(), 220)

    def test_collapsed_pin_click_does_not_generate_content_or_move_window(self):
        self.panel.show_idle()
        self.panel.show()
        self.app.processEvents()
        before = self.panel.pos()
        requests, clicks = QSignalSpy(self.panel.pin_requested), QSignalSpy(self.panel.header_clicked)
        QTest.mouseClick(self.panel.header.pin_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(clicks), 0)
        self.assertEqual(self.panel.state, "collapsed")
        self.assertEqual(self.panel.pos(), before)


if __name__ == "__main__":
    unittest.main()
