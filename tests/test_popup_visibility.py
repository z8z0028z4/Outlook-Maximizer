import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from outlook_maximizer.models import Analysis
from outlook_maximizer.settings import Settings
from outlook_maximizer.ui import CompanionApp
from outlook_maximizer.window_focus import ForegroundWindow


OUTLOOK = ForegroundWindow(101, "outlook")
OTHER = ForegroundWindow(202, "other")
TRAY = ForegroundWindow(303, "shell")
COMPANION = ForegroundWindow(404, "companion")


class PopupVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        Path(".local").mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=".local")
        foreground = patch("outlook_maximizer.ui.foreground_window", return_value=OUTLOOK)
        raised = patch("outlook_maximizer.ui.raise_without_activation", return_value=True)
        above = patch("outlook_maximizer.ui.is_window_above", return_value=True)
        self.foreground, self.raised, self.above = foreground.start(), raised.start(), above.start()
        for mock in (foreground, raised, above):
            self.addCleanup(mock.stop)
        with patch.object(Settings, "load", return_value=Settings(
            always_on_top=False, auto_summarize=False, auto_reply=False,
        )):
            self.app = CompanionApp(self.qt_app, Path(self.folder.name), demo=True,
                                    tray=False, animations=False)
        self.app._foreground_timer.stop()
        self.qt_app.processEvents()
        self.raised.reset_mock()

    def tearDown(self):
        self.app.close()
        self.qt_app.processEvents()
        self.folder.cleanup()

    def assert_visible(self, expected):
        for panel in (self.app.summary_panel, self.app.reply_panel):
            self.assertEqual(panel.isVisible(), expected)

    def test_unpinned_selection_raises_over_outlook_with_auto_generation_off(self):
        self.app.running = True
        source = replace(self.app.state.current, entry_id="different", body="Another synthetic email")
        with patch.object(self.app, "_submit_job") as submit:
            self.app._receive_mail(source)
            self.app._run_auto()
        self.assert_visible(True)
        self.assertEqual(self.raised.call_count, 2)
        self.assertFalse(self.app.debounce_timer.isActive())
        submit.assert_not_called()
        for panel in (self.app.summary_panel, self.app.reply_panel):
            self.assertFalse(panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_return_to_same_mail_restores_order_without_regenerating(self):
        analysis, generation = self.app.state.analysis, self.app.state.generation
        self.app.settings.auto_summarize = self.app.settings.auto_reply = True
        with patch.object(self.app, "request_summary") as summary, patch.object(self.app, "request_reply") as reply:
            self.foreground.return_value = OTHER
            self.app._check_foreground()
            self.raised.assert_not_called()
            self.foreground.return_value = OUTLOOK
            self.app._check_foreground()
            self.app._check_foreground()
            self.assertEqual(self.raised.call_count, 2)
            summary.assert_not_called()
            reply.assert_not_called()
        self.assertIs(self.app.state.analysis, analysis)
        self.assertEqual(self.app.state.generation, generation)

    def test_background_content_update_does_not_raise_normal_windows(self):
        self.foreground.return_value = OTHER
        analysis = Analysis(("Updated synthetic summary",), (), "", ())
        self.app.state.accept(self.app.state.generation, analysis)
        self.app._render_analysis(analysis)
        self.app._present()
        self.raised.assert_not_called()
        self.assert_visible(True)
        self.assertFalse(self.app.summary_panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_same_outlook_window_restack_restores_panels_without_new_ai_work(self):
        self.app._foreground = OUTLOOK
        self.above.return_value = False
        with patch.object(self.app, "_submit_job") as submit:
            self.app._check_foreground()
            self.assertEqual(self.raised.call_count, 2)
            self.above.return_value = True
            self.app._check_foreground()
            self.assertEqual(self.raised.call_count, 2)
            submit.assert_not_called()

    def test_outlook_recovers_after_panel_activation_without_new_ai_work(self):
        self.app._check_foreground()
        self.foreground.return_value = COMPANION
        self.app._check_foreground()
        self.raised.reset_mock()
        self.above.return_value = False
        self.foreground.return_value = OUTLOOK
        with patch.object(self.app, "_submit_job") as submit:
            self.app._check_foreground()
            self.assertEqual(self.raised.call_count, 2)
            submit.assert_not_called()

    def test_double_click_recovers_covered_panels_after_panel_had_focus(self):
        self.app._check_foreground()
        self.foreground.return_value = COMPANION
        self.app._check_foreground()
        self.above.return_value = False
        self.foreground.return_value = TRAY
        reason = QSystemTrayIcon.ActivationReason
        for event in (reason.Trigger, reason.DoubleClick, reason.Trigger):
            self.app.tray.activated.emit(event)
        QTest.qWait(QApplication.doubleClickInterval() + 40)
        self.assertTrue(self.app.panels_visible)
        self.assert_visible(True)

    def test_double_click_uses_state_before_outlook_restores_between_clicks(self):
        self.app._check_foreground()
        self.above.return_value = False
        reason = QSystemTrayIcon.ActivationReason
        self.app.tray.activated.emit(reason.Trigger)
        self.app._check_foreground()
        self.above.return_value = True  # The foreground timer restored the panels.
        self.app.tray.activated.emit(reason.DoubleClick)
        self.app.tray.activated.emit(reason.Trigger)
        QTest.qWait(QApplication.doubleClickInterval() + 40)
        self.assertTrue(self.app.panels_visible)
        self.assert_visible(True)

    def test_double_click_hides_if_panels_were_exposed_at_first_click(self):
        self.app._check_foreground()
        reason = QSystemTrayIcon.ActivationReason
        self.app.tray.activated.emit(reason.Trigger)
        self.above.return_value = False  # An external window moved in front.
        self.app.tray.activated.emit(reason.DoubleClick)
        self.app.tray.activated.emit(reason.Trigger)
        QTest.qWait(QApplication.doubleClickInterval() + 40)
        self.assertFalse(self.app.panels_visible)
        self.assert_visible(False)

    def test_closed_previous_app_does_not_prevent_double_click_hiding(self):
        self.app._foreground = COMPANION
        self.app._last_external_foreground = OUTLOOK
        self.foreground.return_value = TRAY
        def native_visibility(hwnd):
            return hwnd != OUTLOOK.hwnd
        with patch.object(QApplication, "platformName", return_value="windows"), \
                patch("outlook_maximizer.ui.is_native_window_visible", side_effect=native_visibility):
            self.app.tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
        self.assertFalse(self.app.panels_visible)
        self.assert_visible(False)

    def test_first_background_show_waits_for_outlook(self):
        for panel in (self.app.summary_panel, self.app.reply_panel):
            panel.hide()
        self.foreground.return_value = OTHER
        self.app._present()
        self.assert_visible(False)
        self.assertTrue(self.app.panels_visible)
        self.foreground.return_value = OUTLOOK
        self.app._check_foreground()
        self.assert_visible(True)
        self.assertEqual(self.raised.call_count, 2)

    def test_unknown_foreground_defers_auto_reveal_but_explicit_show_works(self):
        for panel in (self.app.summary_panel, self.app.reply_panel):
            panel.hide()
        self.foreground.return_value = ForegroundWindow()
        self.app._present()
        self.assert_visible(False)
        self.raised.assert_not_called()
        self.app.show_panels()
        self.assert_visible(True)
        self.assertEqual(self.raised.call_count, 2)

    def test_pinned_windows_remain_topmost_over_other_apps(self):
        self.app._pin_windows(True)
        self.raised.reset_mock()
        self.foreground.return_value = OTHER
        self.app._check_foreground()
        self.assert_visible(True)
        self.assertEqual(self.raised.call_count, 2)
        for panel in (self.app.summary_panel, self.app.reply_panel):
            self.assertTrue(panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            self.assertTrue(panel.header.pin_button.isChecked())

    def test_pinned_windows_recover_if_hidden_without_foreground_change(self):
        self.app._pin_windows(True)
        self.app._foreground = OTHER
        self.foreground.return_value = OTHER
        self.raised.reset_mock()
        with patch.object(self.app, "_panels_in_front", return_value=False):
            self.app._check_foreground()
        self.assertEqual(self.raised.call_count, 2)

    def test_pinned_windows_do_not_repeatedly_raise_when_already_exposed(self):
        self.app._pin_windows(True)
        self.app._foreground = OTHER
        self.foreground.return_value = OTHER
        self.raised.reset_mock()
        with patch.object(self.app, "_panels_in_front", return_value=True):
            self.app._check_foreground()
        self.raised.assert_not_called()

    def test_explicit_hide_wins_over_pin_focus_and_enabled_auto_settings(self):
        self.app.hide_panels()
        self.app.settings.auto_summarize = self.app.settings.auto_reply = True
        self.app.running = True
        with patch.object(self.app, "request_summary") as summary, patch.object(self.app, "request_reply") as reply:
            self.app._pin_windows(True)
            self.app._check_foreground()
            self.app._present(force=True)
            self.app._run_auto()
            summary.assert_not_called()
            reply.assert_not_called()
        self.assert_visible(False)
        self.raised.assert_not_called()
        self.assertFalse(self.app.retry_action.isEnabled())
        self.assertFalse(self.app.debounce_timer.isActive())

    def test_double_click_recovers_covered_windows_then_hides_exposed_windows(self):
        self.app._foreground = OUTLOOK
        self.foreground.return_value = TRAY
        reason = QSystemTrayIcon.ActivationReason
        for exposed, expected in ((False, True), (True, False)):
            self.above.return_value = exposed
            for event in (reason.Trigger, reason.DoubleClick, reason.Trigger):
                self.app.tray.activated.emit(event)
            QTest.qWait(QApplication.doubleClickInterval() + 40)
            self.assertEqual(self.app.panels_visible, expected)
            self.assert_visible(expected)
        self.assertTrue(self.above.called)

    def test_menu_show_and_hide_are_unambiguous_even_when_covered(self):
        self.foreground.return_value = OTHER
        self.above.return_value = False
        self.app.show_action.trigger()
        self.assert_visible(True)
        self.assertEqual(self.raised.call_count, 2)
        self.app.hide_action.trigger()
        self.assert_visible(False)
        self.assertFalse(self.app.panels_visible)

    def test_loading_cancel_and_resume_follow_only_enabled_auto_options(self):
        self.app.running = True
        self.app.settings.auto_summarize = True
        self.app.settings.auto_reply = False
        old_cancel = self.app.state.restart_summary()
        self.app.summary_panel.show_loading("範例載入中")
        self.app.hide_panels()
        self.assertTrue(old_cancel.is_set())
        self.assertEqual(self.app.summary_panel.state, "collapsed")
        self.app.show_panels()
        with patch.object(self.app, "request_summary") as summary, patch.object(self.app, "request_reply") as reply:
            self.app._run_auto()
            summary.assert_called_once()
            reply.assert_not_called()

    def test_hide_show_preserves_ready_and_empty_reply_results(self):
        self.app.running = True
        self.app.settings.auto_summarize = self.app.settings.auto_reply = True
        self.app.state.accept_reply(self.app.state.generation, "")
        analysis, generation = self.app.state.analysis, self.app.state.generation
        self.app.hide_panels()
        self.app.show_panels()
        with patch.object(self.app, "request_summary") as summary, patch.object(self.app, "request_reply") as reply:
            self.app._run_auto()
            summary.assert_not_called()
            reply.assert_not_called()
        self.assertIs(self.app.state.analysis, analysis)
        self.assertEqual(self.app.state.generation, generation)
        self.assertTrue(self.app.state.reply_received)

    def test_shutdown_stops_foreground_checks(self):
        self.app._foreground_timer.start()
        self.app.close()
        self.assertFalse(self.app._foreground_timer.isActive())
