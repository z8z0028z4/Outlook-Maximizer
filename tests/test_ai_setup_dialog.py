import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QScrollArea, QWidget

from outlook_maximizer.ai_setup_dialog import AiSetupDialog
from outlook_maximizer.cli_setup import CliSetupCancelled, CliSetupError, CliStatus
from outlook_maximizer.settings import Settings
from outlook_maximizer.ui import CompanionApp


class AiSetupDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialog = AiSetupDialog({"codex": "old-codex"}, "codex", language="zh-TW")
        self.found = []
        self.dialog.path_found.connect(lambda provider, path: self.found.append((provider, path)))

    def tearDown(self):
        self.dialog.shutdown()
        self.dialog.deleteLater()
        self.app.processEvents()

    def wait_until_finished(self):
        for _ in range(100):
            QTest.qWait(10)
            self.dialog._drain()
            if not self.dialog._busy:
                return
        self.fail("setup worker did not finish")

    def test_requires_a_selection_without_installing(self):
        self.dialog.provider_checks["codex"].setChecked(False)
        with patch("outlook_maximizer.ai_setup_dialog.install_provider") as install:
            self.dialog.start_button.click()
        self.assertFalse(self.dialog._busy)
        self.assertIn("勾選", self.dialog.message.text())
        install.assert_not_called()

    def test_search_checks_only_selected_providers_and_never_installs(self):
        self.dialog.provider_checks["claude"].setChecked(True)
        def status(provider, path):
            return CliStatus(provider, f"C:/test/{provider}.exe", "ready", "已找到並確認登入。")
        with patch("outlook_maximizer.ai_setup_dialog.detect_provider", side_effect=status) as detect, \
                patch("outlook_maximizer.ai_setup_dialog.install_provider") as install:
            self.dialog.search_button.click()
            self.wait_until_finished()
        self.assertEqual([call.args[0] for call in detect.call_args_list], ["codex", "claude"])
        install.assert_not_called()
        self.assertEqual(self.found, [("codex", "C:/test/codex.exe"), ("claude", "C:/test/claude.exe")])
        self.assertTrue(self.dialog.login_buttons["claude"].isEnabled())

    def test_install_handles_each_selected_provider_and_retains_partial_results(self):
        self.dialog.provider_checks["antigravity"].setChecked(True)
        self.dialog.provider_checks["claude"].setChecked(True)
        def install(provider, path, *, cancel):
            if provider == "antigravity":
                raise CliSetupError("公司設定阻擋安裝。")
            return CliStatus(provider, f"C:/test/{provider}.exe", "unknown", "已找到；請登入。")
        with patch("outlook_maximizer.ai_setup_dialog.install_provider", side_effect=install) as run:
            self.dialog.start_button.click()
            self.wait_until_finished()
        self.assertEqual([call.args[0] for call in run.call_args_list], ["codex", "antigravity", "claude"])
        self.assertEqual(self.found, [("codex", "C:/test/codex.exe"), ("claude", "C:/test/claude.exe")])
        self.assertIn("阻擋", self.dialog.provider_status["antigravity"].text())
        self.assertTrue(self.dialog.login_buttons["claude"].isEnabled())

    def test_login_opens_vendor_window_then_rechecks_without_capturing_credentials(self):
        self.dialog.provider_checks["claude"].setChecked(True)
        self.dialog._statuses["claude"] = CliStatus("claude", "C:/test/claude.exe", "unknown", "found")
        process = Mock()
        process.poll.return_value = 0
        result = CliStatus("claude", "C:/test/claude.exe", "ready", "已找到並確認登入。")
        with patch("outlook_maximizer.ai_setup_dialog.launch_login", return_value=process) as launch, \
                patch("outlook_maximizer.ai_setup_dialog.detect_provider", return_value=result) as detect:
            self.dialog._login("claude")
            self.dialog._drain()
            self.wait_until_finished()
        launch.assert_called_once_with("claude", "C:/test/claude.exe")
        detect.assert_called_once_with("claude", "")
        self.assertEqual(self.dialog._statuses["claude"].auth, "ready")

    def test_login_requires_the_provider_to_remain_selected(self):
        self.dialog._statuses["claude"] = CliStatus("claude", "C:/test/claude.exe", "unknown", "found")
        self.dialog._set_busy(False)
        self.assertFalse(self.dialog.login_buttons["claude"].isEnabled())
        with patch("outlook_maximizer.ai_setup_dialog.launch_login") as launch:
            self.dialog._login("claude")
            launch.assert_not_called()
        self.dialog.provider_checks["claude"].setChecked(True)
        self.assertTrue(self.dialog.login_buttons["claude"].isEnabled())
        self.dialog.provider_checks["claude"].setChecked(False)
        self.assertFalse(self.dialog.login_buttons["claude"].isEnabled())

    def test_cancel_during_setup_stops_worker_and_restores_controls(self):
        entered = threading.Event()
        def install(_provider, _path, *, cancel):
            entered.set()
            cancel.wait(1)
            raise CliSetupCancelled("已取消")
        with patch("outlook_maximizer.ai_setup_dialog.install_provider", side_effect=install):
            self.dialog.start_button.click()
            self.assertTrue(entered.wait(1))
            self.dialog.close_button.click()
            self.wait_until_finished()
        self.assertFalse(self.dialog._busy)
        self.assertTrue(self.dialog.close_button.isEnabled())
        self.assertIn("取消", self.dialog.message.text())

    def test_cancelled_search_does_not_apply_late_detected_path(self):
        entered, release = threading.Event(), threading.Event()
        def detect(provider, _path):
            entered.set()
            release.wait(1)
            return CliStatus(provider, "C:/test/new.exe", "ready", "已登入")
        with patch("outlook_maximizer.ai_setup_dialog.detect_provider", side_effect=detect):
            self.dialog.search_button.click()
            self.assertTrue(entered.wait(1))
            self.dialog.close_button.click()
            release.set()
            self.wait_until_finished()
        self.assertEqual(self.found, [])
        self.assertEqual(self.dialog._paths["codex"], "old-codex")

    def test_cancelled_search_discards_a_result_already_queued(self):
        self.dialog._cancel.set()
        self.dialog._queue.put(("status", "codex", CliStatus("codex", "C:/test/new.exe", "ready", "已登入")))
        self.dialog._drain()
        self.assertEqual(self.found, [])

    def test_cancelled_search_discards_queued_error_and_reports_cancellation(self):
        previous = CliStatus("codex", "old-codex", "ready", "已登入")
        self.dialog._statuses["codex"] = previous
        self.dialog._cancel.set()
        self.dialog._queue.put(("error", "codex", "late failure"))
        self.dialog._queue.put(("done", False))
        self.dialog._drain()
        self.assertIs(self.dialog._statuses["codex"], previous)
        self.assertIn("取消", self.dialog.message.text())

    def test_failed_recheck_does_not_keep_stale_login_enabled(self):
        self.dialog._statuses["codex"] = CliStatus("codex", "old-codex", "ready", "已登入")
        self.dialog._set_busy(False)
        self.assertTrue(self.dialog.login_buttons["codex"].isEnabled())
        with patch("outlook_maximizer.ai_setup_dialog.install_provider", side_effect=CliSetupError("安裝失敗")):
            self.dialog.start_button.click()
            self.wait_until_finished()
        self.assertFalse(self.dialog.login_buttons["codex"].isEnabled())
        self.assertEqual(self.dialog._paths["codex"], "old-codex")

    def test_short_display_keeps_footer_available_and_content_scrollable(self):
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 480, 240))
        with patch.object(AiSetupDialog, "screen", return_value=screen):
            dialog = AiSetupDialog({}, "codex")
        try:
            dialog.show()
            self.app.processEvents()
            self.assertLessEqual(dialog.height(), 240)
            for button in (dialog.close_button, dialog.search_button, dialog.start_button):
                position = button.mapTo(dialog, QPoint(0, 0))
                self.assertGreaterEqual(position.y(), 0)
                self.assertLessEqual(position.y() + button.height(), dialog.height())
            self.assertGreater(dialog.findChild(QScrollArea).verticalScrollBar().maximum(), 0)
        finally:
            dialog.shutdown()
            dialog.deleteLater()
            self.app.processEvents()

    def test_login_temporarily_yields_settings_windows_and_restores_them(self):
        parent = QWidget()
        dialog = AiSetupDialog({}, "claude", parent=parent)
        parent.show()
        dialog.show()
        self.app.processEvents()
        dialog._statuses["claude"] = CliStatus("claude", "C:/test/claude.exe", "unknown", "found")
        process = Mock()
        process.poll.return_value = None
        result = CliStatus("claude", "C:/test/claude.exe", "ready", "已登入")
        try:
            with patch("outlook_maximizer.ai_setup_dialog.launch_login", return_value=process), \
                    patch("outlook_maximizer.ai_setup_dialog.detect_provider", return_value=result):
                dialog._login("claude")
                self.assertFalse(parent.isVisible())
                self.assertFalse(dialog.isVisible())
                process.poll.return_value = 0
                dialog._drain()
                self.assertTrue(parent.isVisible())
                self.assertTrue(dialog.isVisible())
                for _ in range(100):
                    QTest.qWait(10)
                    dialog._drain()
                    if not dialog._busy:
                        break
                self.assertFalse(dialog._busy)
                self.assertEqual(dialog._statuses["claude"].auth, "ready")
        finally:
            dialog.shutdown()
            dialog.deleteLater()
            parent.close()
            parent.deleteLater()
            self.app.processEvents()

    def test_settings_entry_applies_discovered_path_when_user_saves(self):
        Path(".local").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".local") as folder, \
                patch.object(Settings, "load", return_value=Settings(auto_summarize=False)), \
                patch.object(CompanionApp, "_load_models"):
            companion = CompanionApp(self.app, Path(folder), demo=True, tray=False, animations=False)
            try:
                companion.open_settings()
                settings = companion.settings_window
                settings.ai_setup_button.click()
                wizard = companion.ai_setup_window
                wizard.path_found.emit("claude", "C:/test/claude.exe")
                self.assertEqual(settings.claude_edit.text(), "C:/test/claude.exe")
                self.assertEqual(companion.settings.claude_path, "")
                wizard.reject()
                self.app.processEvents()
                settings.provider_combo.setCurrentIndex(settings.provider_combo.findData("claude"))
                settings.save_button.click()
                self.assertEqual(companion.settings.ai_provider, "claude")
                self.assertEqual(companion.settings.claude_path, "C:/test/claude.exe")
                saved = json.loads((Path(folder) / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["claude_path"], "C:/test/claude.exe")
            finally:
                companion.close()
                self.app.processEvents()

    def test_tray_settings_request_does_not_cover_an_active_login(self):
        Path(".local").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".local") as folder, \
                patch.object(Settings, "load", return_value=Settings(auto_summarize=False)), \
                patch.object(CompanionApp, "_load_models"):
            companion = CompanionApp(self.app, Path(folder), demo=True, tray=False, animations=False)
            process = Mock()
            process.poll.return_value = None
            try:
                companion.open_settings()
                settings = companion.settings_window
                settings.ai_setup_button.click()
                wizard = companion.ai_setup_window
                wizard.provider_checks["claude"].setChecked(True)
                wizard._statuses["claude"] = CliStatus("claude", "C:/test/claude.exe", "unknown", "found")
                with patch("outlook_maximizer.ai_setup_dialog.launch_login", return_value=process):
                    wizard._login("claude")
                self.assertFalse(settings.isVisible())
                companion.open_settings()
                self.assertFalse(settings.isVisible())
                self.assertFalse(wizard.isVisible())
            finally:
                companion.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
