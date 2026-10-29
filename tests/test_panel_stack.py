import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QSystemTrayIcon

from outlook_maximizer.ui import CompanionApp
from outlook_maximizer.window_focus import ForegroundWindow


class PanelStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        foreground = patch("outlook_maximizer.ui.foreground_window", return_value=ForegroundWindow())
        foreground.start()
        self.addCleanup(foreground.stop)
        Path(".local").mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=".local")
        self.app = CompanionApp(self.qt_app, Path(self.folder.name), demo=True,
                                tray=False, animations=False)
        self.app.summary_panel.move(100, 70)
        self.qt_app.processEvents()

    def tearDown(self):
        self.app.close()
        self.qt_app.processEvents()
        self.folder.cleanup()

    def assert_stacked(self):
        summary, reply = self.app.summary_panel, self.app.reply_panel
        self.assertEqual(reply.x(), summary.x())
        self.assertEqual(reply.y(), summary.y() + summary.height() + 8)
        self.assertFalse(summary.geometry().intersects(reply.geometry()))

    def test_moving_summary_keeps_reply_attached(self):
        self.app.summary_panel.move(140, 100)
        self.qt_app.processEvents()
        self.assert_stacked()

    def test_dragging_reply_moves_the_whole_group(self):
        summary, reply = self.app.summary_panel, self.app.reply_panel
        before = summary.pos()
        QTest.mousePress(reply.header, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        QTest.mouseMove(reply.header, QPoint(75, 35))
        self.qt_app.processEvents()
        self.assertNotEqual(summary.pos(), before)
        self.assert_stacked()
        QTest.mouseRelease(reply.header, Qt.MouseButton.LeftButton, pos=QPoint(75, 35))
        self.assertEqual(reply.state, "collapsed")

    def test_summary_fold_loading_and_expansion_reflow_reply(self):
        self.app.summary_panel.collapse()
        self.qt_app.processEvents()
        self.assert_stacked()
        self.app.summary_panel.show_loading("測試讀取")
        self.qt_app.processEvents()
        self.assert_stacked()
        self.app._render_analysis(self.app.state.analysis)
        self.qt_app.processEvents()
        self.assert_stacked()

    def test_large_group_near_screen_bottom_is_never_overlapped(self):
        self.app._render_reply()
        area = self.qt_app.primaryScreen().availableGeometry()
        self.app.summary_panel.move(area.left() + 30, area.bottom() - 80)
        self.qt_app.processEvents()
        self.assert_stacked()
        if self.app.summary_panel.height() + self.app.reply_panel.height() + 8 <= area.height():
            self.assertLessEqual(self.app.reply_panel.geometry().bottom(), area.bottom())

    def test_every_animation_frame_keeps_reply_below_summary(self):
        self.app.summary_panel._animations = True
        self.app.summary_panel.collapse()
        for _ in range(12):
            QTest.qWait(25)
            self.assert_stacked()
        self.app._render_analysis(self.app.state.analysis)
        for _ in range(12):
            QTest.qWait(25)
            self.assert_stacked()

    def test_typography_changes_apply_to_existing_content_without_new_requests(self):
        from dataclasses import replace
        from unittest.mock import patch

        self.app._render_reply()
        analysis = self.app.state.analysis
        self.app.settings.auto_summarize = False
        with patch.object(self.app, "request_summary") as summary, patch.object(self.app, "request_reply") as reply:
            self.app._apply_settings(replace(self.app.settings, font_family="Arial", font_size=18,
                                             font_bold=True, font_italic=True))
        self.qt_app.processEvents()
        self.assertIs(self.app.state.analysis, analysis)
        summary.assert_not_called()
        reply.assert_not_called()
        for panel in (self.app.summary_panel, self.app.reply_panel):
            label = panel.findChild(QLabel, "mailText")
            self.assertIsNotNone(label)
            self.assertEqual(label.font().family(), "Arial")
            self.assertEqual(label.font().pointSize(), 18)
            self.assertTrue(label.font().bold())
            self.assertTrue(label.font().italic())
        self.assert_stacked()

    def test_font_extremes_keep_collapsed_titles_and_group_geometry_usable(self):
        from dataclasses import replace
        for size in (8, 24):
            self.app._apply_settings(replace(self.app.settings, font_size=size, font_bold=True))
            self.app.summary_panel.collapse()
            self.qt_app.processEvents()
            for panel in (self.app.summary_panel, self.app.reply_panel):
                self.assertGreaterEqual(panel.header.height(), panel.header.fontMetrics().height() + 16)
                needed = panel.header.fontMetrics().horizontalAdvance(panel.header.text()) + 111
                self.assertGreaterEqual(panel.width(), min(needed, self.app.settings.panel_width))
            self.assert_stacked()
            self.app._render_analysis(self.app.state.analysis)

    def test_maximum_expanded_heights_fit_the_screen_using_scrollable_content(self):
        from dataclasses import replace
        self.app._apply_settings(replace(self.app.settings, summary_height=520, reply_height=460))
        self.app._render_reply()
        self.qt_app.processEvents()
        self.assert_stacked()
        area = self.app.summary_panel.screen().availableGeometry()
        self.assertGreaterEqual(self.app.summary_panel.y(), area.top())
        self.assertLessEqual(self.app.reply_panel.geometry().bottom(), area.bottom())

    def test_tray_double_click_toggles_once_including_single_click_notifications(self):
        reason = QSystemTrayIcon.ActivationReason
        analysis = self.app.state.analysis
        for before in (True, False):
            self.app.panels_visible = before
            self.app._present(force=True)
            for event in (reason.Trigger, reason.DoubleClick, reason.Trigger):
                self.app.tray.activated.emit(event)
            self.qt_app.processEvents()
            self.assertEqual(self.app.panels_visible, not before)
            QTest.qWait(QApplication.doubleClickInterval() + 50)
            self.assertEqual(self.app.panels_visible, not before)
            self.assertEqual(self.app.summary_panel.isVisible(), not before)
            self.assertEqual(self.app.reply_panel.isVisible(), not before)
            self.assertIs(self.app.state.analysis, analysis)

    def test_tray_single_click_still_shows_hidden_panels(self):
        self.app.toggle_panels()
        self.app.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        QTest.qWait(QApplication.doubleClickInterval() + 50)
        self.assertTrue(self.app.summary_panel.isVisible())
        self.assertTrue(self.app.reply_panel.isVisible())

    def test_menu_hide_cancels_pending_single_click_show(self):
        self.app.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        self.app.hide_action.trigger()
        QTest.qWait(QApplication.doubleClickInterval() + 50)
        self.assertFalse(self.app.summary_panel.isVisible())
        self.assertFalse(self.app.reply_panel.isVisible())

    def test_collapsed_pin_updates_both_windows_and_preserves_cached_content(self):
        self.app.summary_panel.collapse()
        analysis = self.app.state.analysis
        self.app.summary_panel.header.pin_button.click()
        self.qt_app.processEvents()
        for panel in (self.app.summary_panel, self.app.reply_panel):
            self.assertEqual(panel.state, "collapsed")
            self.assertFalse(panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            self.assertFalse(panel.header.pin_button.isChecked())
        self.assertIs(self.app.state.analysis, analysis)
        self.assert_stacked()
