import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QLabel

from app import _startup_error_message
from outlook_maximizer.cli_setup import CliStatus, _claude_message
from outlook_maximizer.event_dialog import EventEditor
from outlook_maximizer.floating_panel import FloatingPanel
from outlook_maximizer.i18n import translate_message
from outlook_maximizer.models import CalendarEvent
from outlook_maximizer.settings import Settings
from outlook_maximizer.settings_dialog import SettingsDialog
from outlook_maximizer.ui import CompanionApp


class LanguageAndFirstRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        Path(".local").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=".local")
        self.data = Path(self.temp.name)

    def tearDown(self):
        self.qt_app.processEvents()
        self.temp.cleanup()

    def _load(self):
        with patch("outlook_maximizer.settings.discover_codex", return_value=""), \
                patch("outlook_maximizer.settings.discover_antigravity", return_value=""), \
                patch("outlook_maximizer.cli_setup.discover_claude", return_value=""):
            return Settings.load(self.data)

    def test_language_default_validation_and_roundtrip(self):
        self.assertEqual(self._load().ui_language, "en")
        (self.data / "settings.json").write_text(json.dumps({"ui_language": "fr"}), encoding="utf-8")
        self.assertEqual(self._load().ui_language, "en")
        Settings(ui_language="zh-TW", about_me="我的背景").save(self.data)
        loaded = self._load()
        self.assertEqual((loaded.ui_language, loaded.about_me), ("zh-TW", "我的背景"))

    def test_new_install_does_not_discover_any_unselected_cli(self):
        with patch("outlook_maximizer.settings.discover_codex") as codex, \
                patch("outlook_maximizer.settings.discover_antigravity") as antigravity, \
                patch("outlook_maximizer.cli_setup.discover_claude") as claude:
            loaded = Settings.load(self.data)
        self.assertEqual((loaded.codex_path, loaded.antigravity_path, loaded.claude_path), ("", "", ""))
        codex.assert_not_called()
        antigravity.assert_not_called()
        claude.assert_not_called()

    def test_dialog_language_selection_is_saved_and_restart_is_explained(self):
        dialog = SettingsDialog(Settings())
        saved = []
        dialog.settings_saved.connect(saved.append)
        try:
            self.assertEqual(dialog.language_combo.currentData(), "en")
            self.assertTrue(any("Restart" in label.text() for label in dialog.findChildren(QLabel)))
            dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("zh-TW"))
            dialog.save_button.click()
            self.assertEqual(saved[0].ui_language, "zh-TW")
            saved[0].save(self.data)
            self.assertEqual(self._load().ui_language, "zh-TW")
            chinese = SettingsDialog(self._load())
            try:
                self.assertEqual(chinese.save_button.text(), "儲存設定")
                self.assertTrue(any("重新啟動" in label.text() for label in chinese.findChildren(QLabel)))
            finally:
                chinese.close()
        finally:
            dialog.close()

    def test_panels_and_event_editor_use_requested_language(self):
        event = CalendarEvent("Meeting", "2026-10-22T10:00:00+08:00", "2026-10-22T11:00:00+08:00",
                              description="Notes", evidence="Original email evidence")
        english = EventEditor(event, lambda _: None, language="en")
        chinese = EventEditor(event, lambda _: None, language="zh-TW")
        panel = FloatingPanel("summary", animations=False, language="zh-TW")
        try:
            self.assertEqual(english.save_button.text(), "Save event details")
            self.assertIn("Email context:", english.description_preview.toPlainText())
            self.assertIn("信件依據：", chinese.description_preview.toPlainText())
            self.assertEqual(panel.header.text(), "摘要")
        finally:
            english.close()
            chinese.close()
            panel.dispose()

    def test_cli_composed_status_and_dynamic_errors_keep_useful_details(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "unused", "CLAUDE_CODE_USE_BEDROCK": "1"}, clear=True):
            original = _claude_message("ready", "api_key", True)
        english = translate_message(original)
        self.assertIn("API key", english)
        self.assertIn("required CLI options", english)
        self.assertIn("Bedrock", english)
        self.assertIn("charges", english)
        self.assertEqual(translate_message(original, "zh-TW"), original)
        self.assertIn("HTTP 403", translate_message("Google 行事曆請求失敗（HTTP 403），請檢查行程資料後重試。"))
        self.assertIn("exit code 7", translate_message("Codex 執行失敗（代碼 7）。請確認登入、網路與模型設定。"))
        self.assertIn("Event title", translate_message("行程名稱 的長度不符合限制。"))
        self.assertIn("network", translate_message("安裝未完成；請確認網路及公司安全設定，再重試。"))

    def test_packaged_startup_error_does_not_require_python_or_start_script(self):
        for language in ("en", "zh-TW"):
            packaged = _startup_error_message(language, "OSError", frozen=True)
            self.assertIn("OSError", packaged)
            self.assertNotIn("Python", packaged)
            self.assertNotIn("start.cmd", packaged)
            self.assertIn("start.cmd", _startup_error_message(language, "OSError", frozen=False))

    def test_first_run_can_be_closed_offline_without_reading_outlook_or_installing(self):
        with patch.object(QTimer, "singleShot"), \
                patch("outlook_maximizer.ui.OutlookMonitor") as monitor, \
                patch("outlook_maximizer.ui.load_codex_models") as models, \
                patch("outlook_maximizer.ai_setup_dialog.detect_provider") as detect, \
                patch("outlook_maximizer.ai_setup_dialog.install_provider") as install, \
                patch("outlook_maximizer.ai_setup_dialog.launch_login") as login:
            app = CompanionApp(self.qt_app, self.data, demo=False, tray=False, animations=False)
            try:
                app._show_first_run()
                self.assertFalse(app.running)
                self.assertEqual(app.monitor_action.text(), "Start monitoring")
                self.assertIsNotNone(app.ai_setup_window)
                self.assertEqual(app.ai_setup_window.close_button.text(), "Close")
                self.assertFalse(any(check.isChecked() for check in app.ai_setup_window.provider_checks.values()))
                app.ai_setup_window.close_button.click()
                app.settings_window.cancel_button.click()
                self.assertFalse((self.data / "settings.json").exists())
                monitor.assert_not_called()
                models.assert_not_called()
                detect.assert_not_called()
                install.assert_not_called()
                login.assert_not_called()
            finally:
                app.close()

    def test_first_run_selects_a_single_discovered_provider(self):
        with patch.object(QTimer, "singleShot"), patch("outlook_maximizer.ui.OutlookMonitor"):
            app = CompanionApp(self.qt_app, self.data, demo=False, tray=False, animations=False)
            try:
                app._show_first_run()
                wizard, settings = app.ai_setup_window, app.settings_window
                wizard.provider_checks["claude"].setChecked(True)
                wizard._statuses["claude"] = CliStatus("claude", "C:/test/claude.exe", "ready", "Found")
                wizard.path_found.emit("claude", "C:/test/claude.exe")
                wizard.close_button.click()
                self.assertEqual(settings.provider_combo.currentData(), "claude")
                settings.save_button.click()
                saved = json.loads((self.data / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual((saved["ai_provider"], saved["claude_path"]), ("claude", "C:/test/claude.exe"))
            finally:
                app.close()

    def test_multiple_first_run_choices_require_an_explicit_provider(self):
        with patch.object(QTimer, "singleShot"), patch("outlook_maximizer.ui.OutlookMonitor"):
            app = CompanionApp(self.qt_app, self.data, demo=False, tray=False, animations=False)
            try:
                app._show_first_run()
                wizard, settings = app.ai_setup_window, app.settings_window
                wizard.provider_checks["codex"].setChecked(True)
                wizard.provider_checks["claude"].setChecked(True)
                wizard.close_button.click()
                self.assertIsNone(settings.provider_combo.currentData())
                self.assertIn("Choose", settings.error_label.text())
                settings.save_button.click()
                self.assertFalse((self.data / "settings.json").exists())
                settings.provider_combo.setCurrentIndex(settings.provider_combo.findData("claude"))
                self.assertFalse(settings.error_label.text())
                settings.save_button.click()
                saved = json.loads((self.data / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["ai_provider"], "claude")
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
