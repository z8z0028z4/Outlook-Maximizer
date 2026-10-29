"""Checks that must run inside the packaged executable using fake inputs only."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta


def _verify(test_root: Path) -> dict:
    from zoneinfo import ZoneInfo

    from PyQt6.QtWidgets import QApplication, QLabel
    from outlook_maximizer.ai import CodexSummarizer, run_cli
    from outlook_maximizer.ai_antigravity import AntigravitySummarizer
    from outlook_maximizer.ai_claude import ClaudeSummarizer
    from outlook_maximizer.cli_setup import CliStatus, discover_claude
    from outlook_maximizer.event_editor import get_timezone
    from outlook_maximizer.outlook_monitor import _win32_modules
    from outlook_maximizer.process_launch import run_external
    from outlook_maximizer.settings import Settings, discover_antigravity, discover_codex
    from outlook_maximizer.ui import CompanionApp

    if not getattr(sys, "frozen", False):
        raise AssertionError("This verification must run inside the built executable")
    if shutil.which("python.exe"):
        raise AssertionError("The isolated test PATH must not contain Python")

    qt = QApplication.instance() or QApplication(["packaged-self-test"])
    label = QLabel("Outlook Maximizer: packaged verification")
    label.show()
    qt.processEvents()
    if not label.isVisible():
        raise AssertionError("Qt failed to render a window")
    label.close()

    pythoncom, dynamic, win32gui, win32process, wrap = _win32_modules()
    pythoncom.CoInitialize()
    pythoncom.CoUninitialize()
    assert dynamic and win32gui and win32process and wrap
    ny = get_timezone("America/New_York")
    assert isinstance(ny, ZoneInfo) and ny.key == "America/New_York"
    assert datetime(2026, 1, 15, tzinfo=ny).utcoffset() == timedelta(hours=-5)
    assert datetime(2026, 7, 15, tzinfo=ny).utcoffset() == timedelta(hours=-4)
    assert datetime(2026, 3, 8, 1, 30, tzinfo=ny).utcoffset() == timedelta(hours=-5)
    assert datetime(2026, 3, 8, 3, 30, tzinfo=ny).utcoffset() == timedelta(hours=-4)

    fake_bin = test_root / "fake-cli"
    expected = {name: (fake_bin / f"{name}.exe").resolve() for name in ("codex", "agy", "claude")}
    discovered = {
        "codex": discover_codex(),
        "agy": discover_antigravity(),
        "claude": discover_claude(),
    }
    for name, value in discovered.items():
        if not value or Path(value).resolve() != expected[name]:
            raise AssertionError(f"Could not discover isolated {name} executable")

    # Exercise the actual packaged onboarding with only synthetic settings and
    # fake executables. Do not start the monitor or invoke an installer/login.
    onboarding_dir = test_root / "first-run"
    app = CompanionApp(qt, onboarding_dir, demo=False, tray=False, animations=False)
    try:
        deadline = time.monotonic() + 10
        while app.ai_setup_window is None and time.monotonic() < deadline:
            qt.processEvents()
            time.sleep(.02)
        wizard, settings_window = app.ai_setup_window, app.settings_window
        if app.running or wizard is None or settings_window is None:
            raise AssertionError("First-run setup did not start with monitoring paused")
        if app.language != "en" or any(check.isChecked() for check in wizard.provider_checks.values()):
            raise AssertionError("First-run defaults were not English with no platform selected")
        wizard.provider_checks["codex"].setChecked(True)
        wizard._statuses["codex"] = CliStatus("codex", str(expected["codex"]), "ready", "Found")
        wizard.path_found.emit("codex", str(expected["codex"]))
        wizard.close_button.click()
        qt.processEvents()
        settings_window.save_button.click()
        settings_file = onboarding_dir / "settings.json"
        if not settings_file.is_file():
            raise AssertionError(f"First-run settings were not saved: {settings_window.error_label.text()}")
        saved = json.loads(settings_file.read_text(encoding="utf-8"))
        if app.running or saved.get("ai_provider") != "codex" or saved.get("codex_path") != str(expected["codex"]):
            raise AssertionError("First-run provider choice was not saved while monitoring remained paused")
    finally:
        app.close()
        qt.processEvents()

    settings = Settings(codex_path=str(expected["codex"]), antigravity_path=str(expected["agy"]),
                        claude_path=str(expected["claude"]))
    codex_command = CodexSummarizer(settings).build_command()
    agy_command = AntigravitySummarizer(settings).build_command()
    claude_command = ClaudeSummarizer(settings).build_command()
    analysis_paths = [
        Path(codex_command[codex_command.index("--output-schema") + 1]),
        Path(agy_command[agy_command.index("--json-schema") + 1]),
    ]
    for path in analysis_paths:
        if json.loads(path.read_text(encoding="utf-8")).get("type") != "object":
            raise AssertionError("Analysis schema is unavailable")
    if json.loads(claude_command[claude_command.index("--json-schema") + 1]).get("type") != "object":
        raise AssertionError("Claude could not read the analysis schema")
    reply = analysis_paths[0].with_name("reply.schema.json")
    if json.loads(reply.read_text(encoding="utf-8")).get("required") != ["reply_draft"]:
        raise AssertionError("Reply schema is unavailable")

    marker = "isolated-packaged-cli-test"
    os.environ["OUTLOOK_TEST_BUNDLE"] = str(sys._MEIPASS)
    outputs = [run_cli([str(path)], marker + "\n", threading.Event(),
                       timeout=15, workspace=test_root, provider="fake CLI")
               for path in expected.values()]
    probe = run_external([str(expected["codex"])], input=marker + "\n", text=True,
                         capture_output=True, timeout=15, check=True, cwd=test_root)
    outputs.append(probe.stdout)
    for output in outputs:
        child = {line.split("=", 1)[0]: line.split("=", 1)[1]
                 for line in output.splitlines() if "=" in line}
        if child.get("INPUT") != marker:
            raise AssertionError("Fake native CLI stdin/stdout failed")
        if child.get("DLL_DIRECTORY") or child.get("BUNDLE_PATH_LEAK") != "False":
            raise AssertionError("Frozen app's library search path leaked into the native CLI")
        if child.get("PYI_HOME_PRESENT") != "False":
            raise AssertionError("Frozen app's bootloader environment leaked into the native CLI")

    return {"status": "ok", "qt_platform": qt.platformName(), "pywin32": "ok", "first_run_setup": "ok",
            "schemas": "ok", "new_york_dst": "ok", "fake_cli_discovery": "ok",
            "fake_cli_stdin_stdout_and_dll_isolation": "ok", "python_in_test_path": False}


def main(args: list[str]) -> int:
    if len(args) != 1:
        return 2
    test_root = Path(args[0])
    result_path = test_root / "packaged-self-test.json"
    try:
        result = _verify(test_root)
    except Exception:
        result_path.write_text(json.dumps({"status": "error", "traceback": traceback.format_exc()},
                                          ensure_ascii=True), encoding="utf-8")
        return 1
    result_path.write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")
    return 0
