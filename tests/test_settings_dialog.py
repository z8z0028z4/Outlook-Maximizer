import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from outlook_maximizer.settings import Settings
from outlook_maximizer.model_catalog import ModelOption
from outlook_maximizer.settings_dialog import SettingsDialog


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original = Settings(codex_path="C:/tools/codex.exe", auto_summarize=False)
        self.dialog = SettingsDialog(self.original)
        self.saved = []
        self.dialog.settings_saved.connect(self.saved.append)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def test_dialog_is_frameless_and_has_no_standard_minimize_controls(self):
        flags = self.dialog.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertFalse(flags & Qt.WindowType.WindowMinimizeButtonHint)

    def test_opening_and_cancel_do_not_emit_or_change_original(self):
        self.dialog.auto_summary_check.setChecked(True)
        self.dialog.auto_reply_check.setChecked(True)
        self.dialog.reject()
        self.assertEqual(self.saved, [])
        self.assertFalse(self.original.auto_summarize)
        self.assertFalse(self.original.auto_reply)

    def test_save_emits_copy_with_independent_reply_and_opacity_preferences(self):
        self.dialog.auto_reply_check.setChecked(True)
        self.dialog.opacity_slider.setValue(32)
        self.dialog.width_spin.setValue(390)
        self.dialog.save_button.click()
        self.assertEqual(len(self.saved), 1)
        updated = self.saved[0]
        self.assertFalse(updated.auto_summarize)
        self.assertTrue(updated.auto_reply)
        self.assertAlmostEqual(updated.floating_opacity, .32)
        self.assertEqual(updated.panel_width, 390)
        self.assertFalse(self.original.auto_reply)

    def test_invalid_calendar_id_does_not_emit_settings(self):
        self.dialog.calendar_edit.setText("x" * 1025)
        self.dialog.save_button.click()
        self.assertFalse(self.saved)
        self.assertTrue(self.dialog.error_label.text())

    def test_invalid_timeout_text_is_not_silently_replaced(self):
        self.dialog.timeout_spin.lineEdit().setText("bad")
        self.dialog.save_button.click()
        self.assertFalse(self.saved)
        self.assertTrue(self.dialog.error_label.text())

    def test_empty_calendar_id_maps_to_primary(self):
        self.dialog.calendar_edit.setText("  ")
        self.dialog.save_button.click()
        self.assertEqual(self.saved[0].calendar_id, "primary")

    def test_busy_status_disables_save_and_login_but_allows_cancel(self):
        self.dialog.update_status("正在登入…", busy=True, credentials_configured=True)
        self.assertFalse(self.dialog.save_button.isEnabled())
        self.assertFalse(self.dialog.connect_button.isEnabled())
        self.assertFalse(self.dialog.choose_button.isEnabled())
        self.assertTrue(self.dialog.cancel_auth_button.isEnabled())

    def test_status_updates_do_not_replace_edited_preferences(self):
        self.dialog.auto_reply_check.setChecked(True)
        self.dialog.update_status("已連線", connected=True, credentials_configured=True)
        self.assertTrue(self.dialog.auto_reply_check.isChecked())
        self.assertTrue(self.dialog.connect_button.isEnabled())
        self.assertTrue(self.dialog.disconnect_button.isEnabled())

    def test_google_status_is_plain_text(self):
        self.dialog.update_status("<b>literal status</b>")
        self.assertEqual(self.dialog.status_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(self.dialog.status_label.text(), "<b>literal status</b>")

    def test_auth_buttons_emit_only_intents(self):
        seen = []
        self.dialog.choose_credentials_requested.connect(lambda: seen.append("choose"))
        self.dialog.connect_requested.connect(lambda: seen.append("connect"))
        self.dialog.disconnect_requested.connect(lambda: seen.append("disconnect"))
        self.dialog.cancel_auth_requested.connect(lambda: seen.append("cancel"))
        self.dialog.choose_button.click()
        self.dialog.update_status("已載入", credentials_configured=True, connected=True)
        self.dialog.connect_button.click()
        self.dialog.disconnect_button.click()
        self.dialog.update_status("登入中", busy=True)
        self.dialog.cancel_auth_button.click()
        self.assertEqual(seen, ["choose", "connect", "disconnect", "cancel"])

    def test_ai_setup_button_emits_intent_without_running_install(self):
        seen = []
        self.dialog.ai_setup_requested.connect(lambda: seen.append(True))
        self.dialog.ai_setup_button.click()
        self.assertEqual(seen, [True])

    def test_claude_can_be_selected_and_preserves_other_provider_preferences(self):
        self.dialog.provider_combo.setCurrentIndex(self.dialog.provider_combo.findData("claude"))
        self.dialog.set_cli_path("claude", "C:/tools/claude.exe")
        self.dialog.claude_model_combo.setEditText("sonnet")
        self.dialog.claude_effort_combo.setCurrentIndex(self.dialog.claude_effort_combo.findData("xhigh"))
        self.dialog.model_edit.setText("codex-model")
        self.dialog.save_button.click()
        updated = self.saved[0]
        self.assertEqual((updated.ai_provider, updated.claude_path, updated.claude_model, updated.claude_effort),
                         ("claude", "C:/tools/claude.exe", "sonnet", "xhigh"))
        self.assertEqual(updated.model, "codex-model")

    def test_source_dropdown_preserves_both_providers_models_and_paths(self):
        self.dialog.model_edit.setText("codex-choice")
        self.dialog.provider_combo.setCurrentIndex(self.dialog.provider_combo.findData("antigravity"))
        self.dialog.antigravity_edit.setText("C:/tools/agy.exe")
        self.dialog.antigravity_model_edit.setText("agy-choice")
        self.dialog.save_button.click()
        updated = self.saved[0]
        self.assertEqual(updated.ai_provider, "antigravity")
        self.assertEqual(updated.model, "codex-choice")
        self.assertEqual(updated.antigravity_model, "agy-choice")
        self.assertEqual(updated.antigravity_path, "C:/tools/agy.exe")

    def test_full_opacity_and_custom_hex_color_are_saved(self):
        self.dialog.opacity_slider.setValue(100)
        self.dialog.background_color_edit.setText("#14283c")
        self.dialog.save_button.click()
        self.assertEqual(self.saved[0].floating_opacity, 1.0)
        self.assertEqual(self.saved[0].background_color, "#14283C")

    def test_invalid_hex_color_is_not_saved(self):
        self.dialog.background_color_edit.setText("#12345z")
        self.dialog.save_button.click()
        self.assertFalse(self.saved)
        self.assertTrue(self.dialog.error_label.text())

    def test_color_picker_cancellation_keeps_previous_color(self):
        original = self.dialog.background_color_edit.text()
        with patch("outlook_maximizer.settings_dialog.QColorDialog.getColor", return_value=QColor()):
            self.dialog.color_button.click()
        self.assertEqual(self.dialog.background_color_edit.text(), original)

    def test_color_picker_sets_color_without_overriding_opacity(self):
        self.dialog.opacity_slider.setValue(100)
        with patch("outlook_maximizer.settings_dialog.QColorDialog.getColor", return_value=QColor("#102030")):
            self.dialog.color_button.click()
        self.assertEqual(self.dialog.background_color_edit.text(), "#102030")
        self.assertEqual(self.dialog.opacity_slider.value(), 100)

    def test_model_reload_does_not_replace_manually_entered_model(self):
        self.dialog.model_edit.setText("custom-deployment")
        self.dialog.set_models("codex", [ModelOption("test-model", "Test", ("low", "high"))])
        self.assertEqual(self.dialog.model_edit.text(), "custom-deployment")

    def test_model_effort_dropdown_only_lists_advertised_values(self):
        self.dialog.set_models("codex", [ModelOption("test-model", "Test", ("low", "high"))])
        self.dialog.model_combo.setCurrentIndex(self.dialog.model_combo.findData("test-model"))
        values = [self.dialog.codex_effort_combo.itemData(index) for index in range(self.dialog.codex_effort_combo.count())]
        self.assertEqual(values, ["", "low", "high"])

    def test_source_switch_keeps_independent_model_and_effort_choices(self):
        self.dialog.model_edit.setText("codex-model")
        self.dialog.codex_effort_combo.setCurrentIndex(self.dialog.codex_effort_combo.findData("high"))
        self.dialog.provider_combo.setCurrentIndex(1)
        self.dialog.antigravity_model_edit.setText("agy-model")
        self.dialog.antigravity_effort_combo.setCurrentIndex(self.dialog.antigravity_effort_combo.findData("medium"))
        self.dialog.provider_combo.setCurrentIndex(0)
        self.dialog.save_button.click()
        updated = self.saved[0]
        self.assertEqual((updated.model, updated.codex_effort), ("codex-model", "high"))
        self.assertEqual((updated.antigravity_model, updated.antigravity_effort), ("agy-model", "medium"))

    def test_selecting_cli_default_does_not_save_a_display_label_as_model(self):
        self.dialog.model_combo.setCurrentIndex(0)
        self.dialog.model_combo.setEditText(self.dialog.model_combo.itemText(0))
        self.dialog.save_button.click()
        self.assertEqual(self.saved[0].model, "")

    def test_saved_ultra_effort_survives_initialization_and_async_catalog_loading(self):
        dialog = SettingsDialog(Settings(model="test-model", codex_effort="ultra"))
        try:
            self.assertEqual(dialog.codex_effort_combo.currentData(), "ultra")
            dialog.set_models("codex", [ModelOption("test-model", "Test", ("low", "high", "ultra"))])
            self.assertEqual(dialog.codex_effort_combo.currentData(), "ultra")
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_cli_default_model_preserves_effort_despite_unrelated_catalog_models(self):
        dialog = SettingsDialog(Settings(model="", codex_effort="ultra"))
        try:
            dialog.set_models("codex", [ModelOption("model-a", "A", ("low", "high", "ultra")),
                                        ModelOption("model-b", "B", ("low", "high"))])
            self.assertEqual(dialog.codex_effort_combo.currentData(), "ultra")
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_about_me_is_optional_and_saved_as_plain_multiline_text(self):
        self.dialog.about_me_edit.setPlainText("我的角色：研發工程師\n回覆先確認需求。")
        self.dialog.save_button.click()
        self.assertEqual(self.saved[0].about_me, "我的角色：研發工程師\n回覆先確認需求。")

    def test_too_long_about_me_is_not_silently_saved(self):
        self.dialog.about_me_edit.setPlainText("x" * 6001)
        self.dialog.save_button.click()
        self.assertFalse(self.saved)
        self.assertIn("6000", self.dialog.error_label.text())

    def test_cancel_about_me_does_not_change_original_profile(self):
        self.dialog.about_me_edit.setPlainText("unsaved profile")
        self.dialog.reject()
        self.assertEqual(self.original.about_me, "")
        self.assertFalse(self.saved)

    def test_font_controls_save_without_changing_ai_or_calendar_preferences(self):
        self.dialog.font_combo.setEditText("Noto Sans TC")
        self.dialog.font_size_spin.setValue(18)
        self.dialog.font_bold_check.setChecked(True)
        self.dialog.font_italic_check.setChecked(True)
        self.dialog.save_button.click()
        updated = self.saved[0]
        self.assertEqual((updated.font_family, updated.font_size, updated.font_bold, updated.font_italic),
                         ("Noto Sans TC", 18, True, True))
        self.assertEqual((updated.ai_provider, updated.calendar_id, updated.about_me, updated.background_color),
                         (self.original.ai_provider, self.original.calendar_id, self.original.about_me,
                          self.original.background_color))
        self.assertEqual((self.original.font_family, self.original.font_size, self.original.font_bold,
                          self.original.font_italic), ("Microsoft JhengHei UI", 10, False, False))

    def test_font_preview_updates_locally_and_cancel_does_not_save(self):
        self.dialog.font_combo.setEditText("Arial")
        self.dialog.font_size_spin.setValue(22)
        self.dialog.font_bold_check.setChecked(True)
        self.dialog.font_italic_check.setChecked(True)
        preview = self.dialog.font_preview.font()
        self.assertEqual((preview.family(), preview.pointSize(), preview.bold(), preview.italic()),
                         ("Arial", 22, True, True))
        self.dialog.reject()
        self.assertFalse(self.saved)
        self.assertEqual(self.original.font_size, 10)

    def test_invalid_font_family_and_uncommitted_size_are_not_silently_saved(self):
        for family in ("", "x" * 101):
            self.dialog.font_combo.setEditText(family)
            self.dialog.save_button.click()
            self.assertFalse(self.saved)
            self.assertTrue(self.dialog.error_label.text())
        self.dialog.font_combo.setEditText("Arial")
        self.dialog.font_size_spin.lineEdit().setText("bad")
        self.dialog.save_button.click()
        self.assertFalse(self.saved)
        self.assertTrue(self.dialog.error_label.text())

    def test_missing_saved_font_family_survives_opening_and_unrelated_save(self):
        dialog = SettingsDialog(Settings(font_family="Unavailable Custom Font"))
        saved = []
        dialog.settings_saved.connect(saved.append)
        try:
            self.assertEqual(dialog.font_combo.currentText(), "Unavailable Custom Font")
            dialog.save_button.click()
            self.assertEqual(saved[0].font_family, "Unavailable Custom Font")
        finally:
            dialog.close()
            dialog.deleteLater()
