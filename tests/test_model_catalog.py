from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from outlook_maximizer.model_catalog import ModelOption, load_antigravity_models, load_codex_models


AGY_CATALOG = """Fetching available models...
gemini-3.8-flash-high\tGemini 3.8 Flash (High)
gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)
claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)
"""


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        Path(".local").mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=".local")
        self.root = Path(self.folder.name).resolve()
        self.cache = self.root / "models_cache.json"
        self.executable = self.root / "CLI with spaces" / "agy.exe"
        self.executable.parent.mkdir()
        self.executable.touch()

    def tearDown(self):
        self.folder.cleanup()

    def write_cache(self, value):
        self.cache.write_text(json.dumps(value), encoding="utf-8")

    def test_model_options_are_immutable(self):
        option = ModelOption("a", "Model A", ("low", "high"))
        with self.assertRaises(FrozenInstanceError):
            option.label = "Changed"

    def test_codex_filters_hidden_models_and_reads_advertised_efforts(self):
        self.write_cache({"models": [
            {"slug": "visible-model", "display_name": "Visible Model", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "max"}, {"effort": "ultra"}]},
            {"slug": "internal-model", "visibility": "hide"},
            {"slug": "plain-model"},
        ]})
        self.assertEqual(load_codex_models(self.cache), [
            ModelOption("visible-model", "Visible Model", ("low", "max", "ultra")),
            ModelOption("plain-model", "plain-model"),
        ])

    def test_codex_ignores_malformed_entries_and_duplicate_ids(self):
        self.write_cache({"models": [None, "not a record", {"slug": []}, {"slug": "bad\nmodel"},
            {"slug": "good", "display_name": "Good", "supported_reasoning_levels": [
                None, {"effort": "low"}, {"effort": "low"}, {"effort": "unknown"}]},
            {"slug": "good", "display_name": "Duplicate"},
        ]})
        self.assertEqual(load_codex_models(self.cache), [ModelOption("good", "Good", ("low",))])

    def test_codex_missing_corrupt_or_wrong_root_returns_empty(self):
        self.assertEqual(load_codex_models(self.cache), [])
        for value in ("{broken", "[]", '{"models": {}}'):
            self.cache.write_text(value, encoding="utf-8")
            self.assertEqual(load_codex_models(self.cache), [])

    def test_codex_uses_codex_home_without_reading_other_files(self):
        self.write_cache({"models": [{"slug": "local-model"}]})
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root)}):
            self.assertEqual(load_codex_models(), [ModelOption("local-model", "local-model")])

    def test_codex_rejects_oversized_catalog(self):
        self.cache.write_text(" " * 2_000_001, encoding="utf-8")
        self.assertEqual(load_codex_models(self.cache), [])

    @patch("outlook_maximizer.model_catalog.subprocess.run")
    def test_antigravity_reads_tab_separated_records_and_ignores_progress(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, AGY_CATALOG)
        self.assertEqual(load_antigravity_models(str(self.executable)), [
            ModelOption("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
            ModelOption("gemini-3.8-flash-medium", "Gemini 3.8 Flash (Medium)"),
            ModelOption("claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)"),
        ])

    @patch("outlook_maximizer.model_catalog.subprocess.run")
    def test_antigravity_uses_native_argument_list_hidden_process_and_bounded_timeout(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, AGY_CATALOG)
        load_antigravity_models(str(self.executable), timeout=4)
        self.assertEqual(run.call_args.args[0], [str(self.executable), "models"])
        options = run.call_args.kwargs
        self.assertIs(options["shell"], False)
        self.assertEqual(options["timeout"], 4)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertNotEqual(Path(options["cwd"]), Path.cwd())
        self.assertFalse(Path(options["cwd"]).exists())
        if os.name == "nt":
            self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)

    @patch("outlook_maximizer.model_catalog.subprocess.run")
    def test_antigravity_failures_and_timeout_return_empty(self, run):
        for error in (OSError("not available"), subprocess.TimeoutExpired("agy", 8)):
            run.side_effect = error
            self.assertEqual(load_antigravity_models(str(self.executable)), [])
        run.side_effect = None
        run.return_value = subprocess.CompletedProcess([], 3, AGY_CATALOG)
        self.assertEqual(load_antigravity_models(str(self.executable)), [])

    @patch("outlook_maximizer.model_catalog.subprocess.run")
    def test_antigravity_rejects_invalid_rows_without_inventing_effort_metadata(self, run):
        run.return_value = subprocess.CompletedProcess([], 0,
            "model-a\tModel A\nmodel-a\tDuplicate\nnot a model\tBad\nmodel-b\tBad\tExtra\nerror text\n")
        self.assertEqual(load_antigravity_models(str(self.executable)), [ModelOption("model-a", "Model A")])

    @patch("outlook_maximizer.model_catalog.subprocess.run")
    def test_missing_executable_never_invokes_shell_or_model(self, run):
        self.assertEqual(load_antigravity_models(str(self.root / "missing.exe")), [])
        self.assertEqual(load_antigravity_models(None), [])
        self.assertEqual(load_antigravity_models("bad\npath"), [])
        run.assert_not_called()

    @patch("outlook_maximizer.model_catalog.subprocess.run")
    def test_antigravity_rejects_oversized_output(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, " " * 2_000_001)
        self.assertEqual(load_antigravity_models(str(self.executable)), [])


if __name__ == "__main__":
    unittest.main()
