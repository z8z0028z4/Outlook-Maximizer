import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from outlook_maximizer.settings import Settings, discover_codex


class SettingsTests(unittest.TestCase):
    def setUp(self):
        Path(".local").mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=".local")
        self.data = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_settings_roundtrip_preserves_user_choices(self):
        settings = Settings(calendar_id="my-owned-calendar", always_on_top=False)
        settings.save(self.data)
        loaded = Settings.load(self.data)
        self.assertEqual(loaded.calendar_id, settings.calendar_id)
        self.assertFalse(loaded.always_on_top)

    def test_corrupt_config_keeps_launchable_defaults(self):
        (self.data / "settings.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(Settings.load(self.data).calendar_id, "primary")

    def test_unexpected_field_types_cannot_change_toggles(self):
        (self.data / "settings.json").write_text(json.dumps({"auto_summarize": "false", "ai_timeout_seconds": -3}), encoding="utf-8")
        settings = Settings.load(self.data)
        self.assertTrue(settings.auto_summarize)
        self.assertEqual(settings.ai_timeout_seconds, 15)

    def test_invalid_calendar_id_recovers_to_primary(self):
        (self.data / "settings.json").write_text(json.dumps({"calendar_id": "bad\nvalue"}), encoding="utf-8")
        self.assertEqual(Settings.load(self.data).calendar_id, "primary")

    def test_existing_disabled_auto_summary_is_preserved_when_new_fields_are_added(self):
        (self.data / "settings.json").write_text(json.dumps({"auto_summarize": False}), encoding="utf-8")
        loaded = Settings.load(self.data)
        self.assertFalse(loaded.auto_summarize)
        self.assertFalse(loaded.auto_reply)
        self.assertEqual(loaded.floating_opacity, .20)

    def test_floating_settings_roundtrip(self):
        settings = Settings(auto_reply=True, floating_opacity=.35, panel_width=400, summary_height=360, reply_height=270)
        settings.save(self.data)
        loaded = Settings.load(self.data)
        self.assertTrue(loaded.auto_reply)
        self.assertEqual((loaded.floating_opacity, loaded.panel_width, loaded.summary_height, loaded.reply_height), (.35, 400, 360, 270))

    def test_display_values_are_clamped_to_supported_limits(self):
        (self.data / "settings.json").write_text(json.dumps({"floating_opacity": 2.0, "panel_width": 2, "summary_height": 9999, "reply_height": -1}), encoding="utf-8")
        loaded = Settings.load(self.data)
        self.assertEqual((loaded.floating_opacity, loaded.panel_width, loaded.summary_height, loaded.reply_height), (1.0, 300, 520, 160))

    def test_malformed_display_types_do_not_replace_defaults(self):
        (self.data / "settings.json").write_text(json.dumps({"auto_reply": "false", "floating_opacity": "transparent", "panel_width": True}), encoding="utf-8")
        loaded = Settings.load(self.data)
        self.assertFalse(loaded.auto_reply)
        self.assertEqual((loaded.floating_opacity, loaded.panel_width), (.20, 348))

    def test_nan_opacity_recovers_to_default(self):
        (self.data / "settings.json").write_text('{"floating_opacity": NaN}', encoding="utf-8")
        self.assertEqual(Settings.load(self.data).floating_opacity, .20)

    def test_integer_opacity_is_accepted_as_numeric_and_clamped(self):
        (self.data / "settings.json").write_text('{"floating_opacity": 0}', encoding="utf-8")
        self.assertEqual(Settings.load(self.data).floating_opacity, .08)

    def test_opaque_color_and_separate_provider_choices_roundtrip(self):
        executable = self.data / "agy.exe"
        executable.write_bytes(b"test fixture")
        Settings(ai_provider="antigravity", model="codex-model", antigravity_model="agy-model",
                 antigravity_path=str(executable), floating_opacity=1.0,
                 background_color="#203040").save(self.data)
        loaded = Settings.load(self.data)
        self.assertEqual((loaded.ai_provider, loaded.model, loaded.antigravity_model),
                         ("antigravity", "codex-model", "agy-model"))
        self.assertEqual(loaded.antigravity_path, str(executable))
        self.assertEqual((loaded.floating_opacity, loaded.background_color), (1.0, "#203040"))

    def test_invalid_provider_and_css_color_recover_to_existing_defaults(self):
        (self.data / "settings.json").write_text(json.dumps({
            "ai_provider": "shell", "background_color": "red; background: url(bad)",
        }), encoding="utf-8")
        loaded = Settings.load(self.data)
        self.assertEqual(loaded.ai_provider, "codex")
        self.assertEqual(loaded.background_color, "#FAFCFF")

    def test_color_is_normalized_without_changing_its_alpha(self):
        Settings(background_color="#abcdef", floating_opacity=.45).save(self.data)
        loaded = Settings.load(self.data)
        self.assertEqual((loaded.background_color, loaded.floating_opacity), ("#ABCDEF", .45))

    def test_independent_efforts_roundtrip_and_invalid_choices_recover(self):
        Settings(codex_effort="ultra", antigravity_effort="max").save(self.data)
        loaded = Settings.load(self.data)
        self.assertEqual((loaded.codex_effort, loaded.antigravity_effort), ("ultra", "max"))
        Settings(codex_effort="invalid", antigravity_effort="ultra").save(self.data)
        loaded = Settings.load(self.data)
        self.assertEqual((loaded.codex_effort, loaded.antigravity_effort), ("low", ""))

    def test_about_me_roundtrip_and_old_settings_remain_optional(self):
        Settings(about_me="我是專案工程師。\n對外先確認需求，不直接承諾時程。").save(self.data)
        self.assertEqual(Settings.load(self.data).about_me, "我是專案工程師。\n對外先確認需求，不直接承諾時程。")
        (self.data / "settings.json").write_text('{"auto_summarize": false}', encoding="utf-8")
        self.assertEqual(Settings.load(self.data).about_me, "")

    def test_oversized_about_me_is_bounded_on_load(self):
        (self.data / "settings.json").write_text(json.dumps({"about_me": "x" * 7000}), encoding="utf-8")
        self.assertEqual(len(Settings.load(self.data).about_me), 6000)

    def test_typography_roundtrip_preserves_other_user_preferences(self):
        settings = Settings(font_family="Noto Sans TC", font_size=18, font_bold=True,
                            font_italic=True, ai_provider="antigravity", antigravity_model="chosen-model",
                            calendar_id="owned-calendar", about_me="專案協調", background_color="#123456")
        settings.save(self.data)
        loaded = Settings.load(self.data)
        self.assertEqual((loaded.font_family, loaded.font_size, loaded.font_bold, loaded.font_italic),
                         ("Noto Sans TC", 18, True, True))
        self.assertEqual((loaded.ai_provider, loaded.antigravity_model, loaded.calendar_id,
                          loaded.about_me, loaded.background_color),
                         ("antigravity", "chosen-model", "owned-calendar", "專案協調", "#123456"))

    def test_older_settings_keep_existing_font_defaults(self):
        (self.data / "settings.json").write_text('{"auto_reply": true}', encoding="utf-8")
        loaded = Settings.load(self.data)
        self.assertEqual((loaded.font_family, loaded.font_size, loaded.font_bold, loaded.font_italic),
                         ("Microsoft JhengHei UI", 10, False, False))
        self.assertTrue(loaded.auto_reply)

    def test_font_size_clamps_and_invalid_types_keep_defaults(self):
        for value, expected in ((1, 8), (999, 24), (True, 10), ("18", 10), (12.5, 10)):
            with self.subTest(value=value):
                (self.data / "settings.json").write_text(json.dumps({"font_size": value,
                    "font_bold": "true", "font_italic": 1}), encoding="utf-8")
                loaded = Settings.load(self.data)
                self.assertEqual(loaded.font_size, expected)
                self.assertFalse(loaded.font_bold)
                self.assertFalse(loaded.font_italic)

    def test_invalid_font_families_recover_to_default_and_whitespace_is_trimmed(self):
        for family in ("", "  ", "x" * 101, "Font\nName", "Font\x7fName", "Font\x85Name", None, 42):
            with self.subTest(family=family):
                (self.data / "settings.json").write_text(json.dumps({"font_family": family}), encoding="utf-8")
                self.assertEqual(Settings.load(self.data).font_family, "Microsoft JhengHei UI")
        (self.data / "settings.json").write_text('{"font_family":"  微軟正黑體  "}', encoding="utf-8")
        self.assertEqual(Settings.load(self.data).font_family, "微軟正黑體")

    def test_stale_configured_codex_path_is_repaired_in_loaded_settings(self):
        (self.data / "settings.json").write_text(json.dumps({"codex_path": "C:/removed/codex.exe"}), encoding="utf-8")
        with patch("outlook_maximizer.settings.discover_codex", return_value="C:/current/codex.exe") as discover:
            self.assertEqual(Settings.load(self.data).codex_path, "C:/current/codex.exe")
            discover.assert_any_call("C:/removed/codex.exe")

    def test_claude_preferences_roundtrip_with_independent_model_and_effort(self):
        Settings(ai_provider="claude", claude_path="C:/tools/claude.exe", claude_model="sonnet",
                 claude_effort="xhigh", codex_effort="low").save(self.data)
        with patch("outlook_maximizer.cli_setup.discover_claude", return_value=""):
            loaded = Settings.load(self.data)
        self.assertEqual((loaded.ai_provider, loaded.claude_path, loaded.claude_model, loaded.claude_effort),
                         ("claude", "C:/tools/claude.exe", "sonnet", "xhigh"))
        self.assertEqual(loaded.codex_effort, "low")

    def test_stale_antigravity_path_is_rediscovered(self):
        Settings(antigravity_path="C:/removed/agy.exe").save(self.data)
        with patch("outlook_maximizer.settings.discover_antigravity", return_value="C:/current/agy.exe"):
            self.assertEqual(Settings.load(self.data).antigravity_path, "C:/current/agy.exe")

    def test_codex_discovers_standalone_windows_install(self):
        executable = self.data / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"discovery fixture")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.data)}), \
                patch("outlook_maximizer.settings.shutil.which", return_value=None):
            self.assertEqual(discover_codex("C:/removed/codex.exe"), str(executable.absolute()))

    def test_codex_discovery_recovers_after_an_app_bundle_directory_changes(self):
        binary = self.data / "OpenAI" / "Codex" / "bin" / "current-bundle" / "codex.exe"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"discovery fixture only")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.data)}), patch("outlook_maximizer.settings.shutil.which", return_value=None):
            self.assertEqual(discover_codex(str(self.data / "old-bundle" / "codex.exe")), str(binary.resolve()))

    @unittest.skipUnless(os.name == "nt", "Windows npm command wrappers")
    def test_codex_cmd_wrapper_resolves_to_native_npm_binary_without_executing_it(self):
        wrapper = self.data / "npm" / "codex.cmd"
        binary = wrapper.parent / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"discovery fixture only")
        wrapper.write_text("do not execute this wrapper", encoding="utf-8")
        self.assertEqual(discover_codex(str(wrapper)), str(binary.resolve()))
