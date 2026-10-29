from pathlib import Path
import json
import os
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from outlook_maximizer import cli_setup


class CliSetupTests(unittest.TestCase):
    def setUp(self):
        Path(".local").mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(prefix="cli setup ", dir=".local")
        self.root = Path(self.folder.name).resolve()
        self.addCleanup(self.folder.cleanup)

    def executable(self, name="helper.exe"):
        path = self.root / "space in path" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return str(path.resolve())

    def test_provider_requires_known_identifier(self):
        for action in (cli_setup.detect_provider, cli_setup.install_provider):
            with self.subTest(action=action), self.assertRaises(cli_setup.CliSetupError):
                action("unknown")

    def test_discover_claude_finds_native_and_npm_with_spaces(self):
        profile = self.root / "User Name"
        appdata = profile / "App Data" / "Roaming"
        native = profile / ".local" / "bin" / "claude.exe"
        package = appdata / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        native.parent.mkdir(parents=True)
        package.parent.mkdir(parents=True)
        native.touch()
        package.touch()
        with patch.dict(os.environ, {"USERPROFILE": str(profile), "APPDATA": str(appdata)}), \
                patch("outlook_maximizer.cli_setup.shutil.which", return_value=None):
            self.assertEqual(cli_setup.discover_claude("missing.exe"), str(native.resolve()))
            native.unlink()
            self.assertEqual(cli_setup.discover_claude("missing.exe"), str(package.resolve()))

    def test_discover_claude_uses_configured_native_path_without_shell(self):
        executable = self.executable("claude.exe")
        self.assertEqual(cli_setup.discover_claude(executable), executable)
        if os.name == "nt":
            wrapper = self.root / "claude.cmd"
            wrapper.touch()
            with patch("outlook_maximizer.cli_setup.shutil.which", return_value=None), \
                    patch.dict(os.environ, {"USERPROFILE": str(self.root), "APPDATA": str(self.root)}):
                self.assertEqual(cli_setup.discover_claude(str(wrapper)), "")

    def test_discovery_does_not_resolve_vendor_managed_junctions(self):
        executable = self.executable("codex.exe")
        with patch.object(Path, "resolve", side_effect=AssertionError("junction was resolved")), \
                patch("outlook_maximizer.cli_setup.discover_codex") as fallback:
            self.assertEqual(cli_setup._discover("codex", executable), executable)
        fallback.assert_not_called()

    def test_codex_finds_standalone_install_when_process_path_is_stale(self):
        local = self.root / "Local Data"
        executable = local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}, clear=False), \
                patch("outlook_maximizer.cli_setup.discover_codex", return_value=""):
            self.assertEqual(cli_setup._discover("codex", "old location"), str(executable.resolve()))

    def test_antigravity_falls_back_when_configured_binary_disappears(self):
        executable = self.executable("agy.exe")
        with patch("outlook_maximizer.cli_setup.discover_antigravity", return_value=executable):
            self.assertEqual(cli_setup._discover("antigravity", "old agy.exe"), executable)

    def test_missing_provider_does_not_check_credentials(self):
        with patch("outlook_maximizer.cli_setup._discover", return_value=""), \
                patch("outlook_maximizer.cli_setup.subprocess.run") as run:
            status = cli_setup.detect_provider("claude", "missing.exe")
        self.assertFalse(status.installed)
        self.assertEqual(status.auth, "unknown")
        run.assert_not_called()

    def test_antigravity_found_is_never_claimed_as_logged_in(self):
        executable = self.executable("agy.exe")
        with patch("outlook_maximizer.cli_setup._discover", return_value=executable), \
                patch("outlook_maximizer.cli_setup.subprocess.run") as run:
            status = cli_setup.detect_provider("antigravity")
        self.assertTrue(status.installed)
        self.assertEqual(status.auth, "unknown")
        run.assert_not_called()

    def test_antigravity_api_key_environment_is_reported_without_exposing_its_value(self):
        with patch("outlook_maximizer.cli_setup._discover", return_value=self.executable("agy.exe")), \
                patch.dict(os.environ, {"GEMINI_API_KEY": "private-test-value"}, clear=False):
            status = cli_setup.detect_provider("antigravity")
        self.assertEqual(status.auth, "unknown")
        self.assertIn("計費", status.message)
        self.assertNotIn("private-test-value", repr(status))

    def test_codex_auth_status_is_truthful_and_does_not_expose_account_output(self):
        executable = self.executable("codex.exe")
        cases = [(0, "logged in private@example.invalid", "ready"),
                 (1, "Not logged in", "login_required"),
                 (1, "network failed private@example.invalid", "unknown"),
                 (9, "not logged in", "unknown")]
        with patch("outlook_maximizer.cli_setup._discover", return_value=executable), \
                patch("outlook_maximizer.cli_setup.subprocess.run") as run:
            for code, output, expected in cases:
                run.return_value = subprocess.CompletedProcess([], code, stdout="", stderr=output)
                status = cli_setup.detect_provider("codex")
                self.assertEqual(status.auth, expected)
                self.assertNotIn("private@example.invalid", repr(status))
                self.assertEqual(run.call_args.args[0], [executable, "login", "status"])
                self.assertFalse(run.call_args.kwargs["shell"])

    def test_codex_api_key_environment_is_reported_without_exposing_its_value(self):
        with patch("outlook_maximizer.cli_setup._discover", return_value=self.executable("codex.exe")), \
                patch("outlook_maximizer.cli_setup._auth_status", return_value=("ready", "")), \
                patch.dict(os.environ, {"CODEX_API_KEY": "private-test-value"}, clear=False):
            status = cli_setup.detect_provider("codex")
        self.assertEqual(status.auth, "ready")
        self.assertIn("計費", status.message)
        self.assertNotIn("private-test-value", repr(status))

    def test_claude_auth_status_requires_valid_json_and_matching_exit(self):
        executable = self.executable("claude.exe")
        cases = [(0, {"loggedIn": True, "email": "private@example.invalid"}, "ready"),
                 (1, {"loggedIn": False}, "login_required"),
                 (0, {"loggedIn": False}, "login_required"),
                 (1, {"loggedIn": True}, "unknown"),
                 (0, {"loggedIn": "true"}, "unknown"),
                 (0, "not JSON", "unknown")]
        with patch("outlook_maximizer.cli_setup._discover", return_value=executable), \
                patch("outlook_maximizer.cli_setup.subprocess.run") as run:
            for code, data, expected in cases:
                output = json.dumps(data) if isinstance(data, dict) else data
                run.return_value = subprocess.CompletedProcess([], code, stdout=output, stderr="private")
                status = cli_setup.detect_provider("claude")
                self.assertEqual(status.auth, expected)
                self.assertNotIn("private", repr(status))
                self.assertEqual(run.call_args.args[0], [executable, "auth", "status", "--json"])

    def test_claude_auth_method_and_possible_billing_are_reported(self):
        executable = self.executable("claude.exe")
        examples = {
            "claude.ai": "Claude 帳號",
            "oauth_token": "OAuth",
            "api_key": "API 金鑰",
            "api_key_helper": "輔助程式",
            "third_party": "第三方",
        }
        with patch("outlook_maximizer.cli_setup._discover", return_value=executable), \
                patch("outlook_maximizer.cli_setup._claude_compatibility", return_value=True), \
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}, clear=False), \
                patch("outlook_maximizer.cli_setup.subprocess.run") as run:
            # Exclude the environment warnings to test the authMethod itself.
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            for method, expected in examples.items():
                output = json.dumps({"loggedIn": True, "authMethod": method,
                                     "email": "private@example.invalid"})
                run.return_value = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
                status = cli_setup.detect_provider("claude")
                self.assertEqual(status.auth_method, method)
                self.assertTrue(status.compatible)
                self.assertIn(expected, status.message)
                self.assertNotIn("private@example.invalid", repr(status))
                if method in {"api_key", "api_key_helper", "third_party"}:
                    self.assertIn("計費", status.message)

    def test_claude_key_presence_warning_does_not_read_values_or_modify_environment(self):
        class NamesOnlyEnvironment:
            def __init__(self, present):
                self.present = present
            def __iter__(self):
                return iter((self.present,))
            def __getitem__(self, _name):
                raise AssertionError("credential values must not be read")
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            environment = NamesOnlyEnvironment(name)
            with self.subTest(name=name), \
                    patch("outlook_maximizer.cli_setup._discover", return_value="C:/test/claude.exe"), \
                    patch("outlook_maximizer.cli_setup._claude_compatibility", return_value=True), \
                    patch("outlook_maximizer.cli_setup._auth_status", return_value=("ready", "claude.ai")), \
                    patch.object(cli_setup.os, "environ", environment):
                status = cli_setup.detect_provider("claude")
                self.assertIs(cli_setup.os.environ, environment)
            self.assertIn("非互動", status.message)
            self.assertIn("計費", status.message)
            self.assertNotIn(name, repr(status))

    def test_claude_managed_provider_names_warn_without_reading_values(self):
        class NamesOnlyEnvironment:
            def __init__(self, names):
                self.names = names
            def __iter__(self):
                return iter(self.names)
            def __getitem__(self, _name):
                raise AssertionError("credential and managed provider values must not be read")
        cases = {
            "CLAUDE_CODE_USE_BEDROCK": "Bedrock",
            "CLAUDE_CODE_USE_VERTEX": "Vertex",
            "CLAUDE_CODE_USE_FOUNDRY": "Foundry",
        }
        with patch("outlook_maximizer.cli_setup._discover", return_value="C:/test/claude.exe"), \
                patch("outlook_maximizer.cli_setup._claude_compatibility", return_value=True), \
                patch("outlook_maximizer.cli_setup._auth_status", return_value=("ready", "claude.ai")):
            for name, expected in cases.items():
                environment = NamesOnlyEnvironment([name])
                with self.subTest(name=name), patch.object(cli_setup.os, "environ", environment):
                    status = cli_setup.detect_provider("claude")
                    self.assertIs(cli_setup.os.environ, environment)
                self.assertIn(expected, status.message)
                self.assertIn("第三方", status.message)
                self.assertIn("計費", status.message)
            environment = NamesOnlyEnvironment(list(cases))
            with patch.object(cli_setup.os, "environ", environment):
                status = cli_setup.detect_provider("claude")
            for provider in cases.values():
                self.assertIn(provider, status.message)

    def test_claude_auth_method_rejects_untrusted_and_inconsistent_values(self):
        cases = [
            ({"loggedIn": True, "authMethod": "private-secret"}, ("ready", "")),
            ({"loggedIn": True, "authMethod": "none"}, ("unknown", "none")),
            ({"loggedIn": False, "authMethod": "api_key"}, ("login_required", "api_key")),
        ]
        with patch("outlook_maximizer.cli_setup.subprocess.run") as run:
            for data, expected in cases:
                run.return_value = subprocess.CompletedProcess([], 0, stdout=json.dumps(data), stderr="")
                actual = cli_setup._auth_status("claude", "synthetic.exe")
                self.assertEqual(actual, expected)
                self.assertNotIn("private-secret", repr(actual))

    def test_claude_capabilities_require_real_option_declarations(self):
        help_text = "\n".join(f"  {flag} <value>   synthetic help" for flag in cli_setup._CLAUDE_REQUIRED_FLAGS)
        with patch("outlook_maximizer.cli_setup.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout=help_text, stderr="")
            self.assertTrue(cli_setup._claude_compatibility("C:/test/claude.exe"))
            self.assertEqual(run.call_args.args[0], ["C:/test/claude.exe", "--help"])
            self.assertFalse(run.call_args.kwargs["shell"])
            missing = help_text.replace("  --safe-mode <value>   synthetic help", "")
            missing += "\n  Informational text mentions --safe-mode but is not an option declaration."
            run.return_value = subprocess.CompletedProcess([], 0, stdout=missing, stderr="")
            self.assertFalse(cli_setup._claude_compatibility("C:/test/claude.exe"))

    def test_claude_capability_errors_do_not_claim_compatibility(self):
        for result in (
            subprocess.CompletedProcess([], 1, stdout="  --safe-mode", stderr="private"),
            subprocess.CompletedProcess([], 0, stdout="not an option list", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="x" * 200_001, stderr=""),
            subprocess.TimeoutExpired("claude", 8),
        ):
            with self.subTest(kind=type(result).__name__), \
                    patch("outlook_maximizer.cli_setup.subprocess.run") as run:
                if isinstance(result, Exception):
                    run.side_effect = result
                else:
                    run.return_value = result
                self.assertIsNone(cli_setup._claude_compatibility("synthetic.exe"))

    def test_claude_found_but_old_or_unknown_never_claims_usable(self):
        with patch("outlook_maximizer.cli_setup._discover", return_value="C:/test/claude.exe"), \
                patch("outlook_maximizer.cli_setup._auth_status", return_value=("ready", "claude.ai")), \
                patch("outlook_maximizer.cli_setup._claude_compatibility") as compatible:
            for value in (False, None, True):
                compatible.return_value = value
                status = cli_setup.detect_provider("claude")
                self.assertEqual(status.compatible, value)
                if value is False:
                    self.assertIn("更新", status.message)
                if value is None:
                    self.assertIn("無法確認", status.message)
                    self.assertNotIn("已找到本工具所需的 CLI 選項", status.message)

    def test_existing_old_claude_is_not_automatically_overwritten(self):
        status = cli_setup.CliStatus("claude", self.executable("claude.exe"),
                                     "ready", "請更新", False, "claude.ai")
        with patch("outlook_maximizer.cli_setup.detect_provider", return_value=status), \
                patch("outlook_maximizer.cli_setup._run_installer") as install:
            self.assertIs(cli_setup.install_provider("claude"), status)
        install.assert_not_called()

    def test_status_timeout_or_error_is_unknown(self):
        with patch("outlook_maximizer.cli_setup._discover", return_value=self.executable()), \
                patch("outlook_maximizer.cli_setup.subprocess.run", side_effect=subprocess.TimeoutExpired("cli", 1)):
            self.assertEqual(cli_setup.detect_provider("codex").auth, "unknown")

    def test_install_reuses_existing_without_running_installer(self):
        status = cli_setup.CliStatus("codex", self.executable(), "ready", "ok")
        with patch("outlook_maximizer.cli_setup.detect_provider", return_value=status), \
                patch("outlook_maximizer.cli_setup._run_installer") as install:
            self.assertIs(cli_setup.install_provider("codex"), status)
        install.assert_not_called()

    def test_install_runs_only_after_explicit_request_and_redetects(self):
        executable = self.executable("agy.exe")
        missing = cli_setup.CliStatus("antigravity")
        found = cli_setup.CliStatus("antigravity", executable, "unknown", "found")
        with patch("outlook_maximizer.cli_setup.detect_provider", side_effect=[missing, found]) as detect, \
                patch("outlook_maximizer.cli_setup._run_installer") as install:
            self.assertIs(cli_setup.install_provider("antigravity", "old path"), found)
        self.assertEqual(detect.call_count, 2)
        install.assert_called_once()

    def test_install_does_not_claim_success_without_a_binary(self):
        with patch("outlook_maximizer.cli_setup.detect_provider", return_value=cli_setup.CliStatus("claude")), \
                patch("outlook_maximizer.cli_setup._run_installer"):
            with self.assertRaisesRegex(cli_setup.CliSetupError, "尚未找到"):
                cli_setup.install_provider("claude")

    def test_install_cancellation_and_invalid_timeouts(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch("outlook_maximizer.cli_setup.detect_provider") as detect:
            with self.assertRaises(cli_setup.CliSetupCancelled):
                cli_setup.install_provider("claude", cancel=cancelled)
        detect.assert_not_called()
        for invalid in (0, -1, float("inf"), float("nan"), "invalid"):
            with self.subTest(timeout=invalid), self.assertRaises(cli_setup.CliSetupError):
                cli_setup.install_provider("claude", timeout=invalid)

    def test_cancel_during_detection_never_starts_installation(self):
        for executable in ("", "already found"):
            cancelled = threading.Event()
            def detect(provider, configured_path):
                cancelled.set()
                return cli_setup.CliStatus(provider, executable)
            with self.subTest(executable=executable), \
                    patch("outlook_maximizer.cli_setup.detect_provider", side_effect=detect), \
                    patch("outlook_maximizer.cli_setup._run_installer") as install:
                with self.assertRaises(cli_setup.CliSetupCancelled):
                    cli_setup.install_provider("claude", cancel=cancelled)
            install.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows installer")
    def test_installer_uses_only_fixed_official_urls_and_no_shell(self):
        shell = self.executable("powershell.exe")
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        with patch("outlook_maximizer.cli_setup._powershell", return_value=shell), \
                patch("outlook_maximizer.cli_setup.subprocess.Popen", return_value=process) as popen:
            for provider, url in cli_setup._INSTALL_URLS.items():
                cli_setup._run_installer(provider, threading.Event(), 5)
                command = popen.call_args.args[0]
                self.assertEqual(command[:4], [shell, "-NoProfile", "-NonInteractive", "-Command"])
                self.assertIn(url, command[4])
                self.assertFalse(popen.call_args.kwargs["shell"])
                self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
                if provider == "codex":
                    self.assertEqual(popen.call_args.kwargs["env"]["CODEX_NON_INTERACTIVE"], "1")

    @unittest.skipUnless(os.name == "nt", "Windows installer")
    def test_installer_reports_missing_powershell_and_safe_failure(self):
        with patch("outlook_maximizer.cli_setup._powershell", return_value=""):
            with self.assertRaisesRegex(cli_setup.CliSetupError, "PowerShell"):
                cli_setup._run_installer("codex", threading.Event(), 5)
        process = Mock()
        process.wait.return_value = 12
        process.poll.return_value = 12
        with patch("outlook_maximizer.cli_setup._powershell", return_value=self.executable()), \
                patch("outlook_maximizer.cli_setup.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(cli_setup.CliSetupError, "安裝未完成"):
                cli_setup._run_installer("codex", threading.Event(), 5)

    @unittest.skipUnless(os.name == "nt", "Windows installer")
    def test_installer_cancellation_stops_waiting_and_terminates_its_process(self):
        cancelled = threading.Event()
        process = Mock()
        def wait(timeout):
            if not cancelled.is_set():
                cancelled.set()
                raise subprocess.TimeoutExpired("powershell", timeout)
            return 0
        process.wait.side_effect = wait
        process.poll.return_value = None
        with patch("outlook_maximizer.cli_setup._powershell", return_value=self.executable()), \
                patch("outlook_maximizer.cli_setup.subprocess.Popen", return_value=process):
            with self.assertRaises(cli_setup.CliSetupCancelled):
                cli_setup._run_installer("claude", cancelled, 5)
        process.terminate.assert_called_once()
        process.kill.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows installer")
    def test_cancel_during_installer_preparation_does_not_launch_powershell(self):
        cancelled = threading.Event()
        def find_shell():
            cancelled.set()
            return self.executable("powershell.exe")
        with patch("outlook_maximizer.cli_setup._powershell", side_effect=find_shell), \
                patch("outlook_maximizer.cli_setup.subprocess.Popen") as popen:
            with self.assertRaises(cli_setup.CliSetupCancelled):
                cli_setup._run_installer("claude", cancelled, 5)
        popen.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows installer")
    def test_installer_timeout_stops_waiting_and_terminates_its_process(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        with patch("outlook_maximizer.cli_setup._powershell", return_value=self.executable()), \
                patch("outlook_maximizer.cli_setup.subprocess.Popen", return_value=process), \
                patch("outlook_maximizer.cli_setup.time.monotonic", side_effect=[0, 6]):
            with self.assertRaisesRegex(cli_setup.CliSetupError, "逾時"):
                cli_setup._run_installer("codex", threading.Event(), 5)
        process.terminate.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows interactive login")
    def test_login_opens_own_interactive_console_with_argument_list(self):
        executable = self.executable("claude.exe")
        with patch("outlook_maximizer.cli_setup.subprocess.Popen") as popen:
            cli_setup.launch_login("claude", executable)
        self.assertEqual(popen.call_args.args[0], [executable, "auth", "login"])
        self.assertEqual(popen.call_args.kwargs["creationflags"], subprocess.CREATE_NEW_CONSOLE)
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertNotIn("stdout", popen.call_args.kwargs)
        self.assertNotIn("stdin", popen.call_args.kwargs)

    @unittest.skipUnless(os.name == "nt", "Windows interactive login")
    def test_login_uses_isolated_empty_workspace_and_never_deletes_user_files(self):
        executable = self.executable("agy.exe")
        login_dir = self.root / "isolated login"
        login_dir.mkdir()
        process = Mock()
        with patch("outlook_maximizer.cli_setup.tempfile.mkdtemp", return_value=str(login_dir)), \
                patch("outlook_maximizer.cli_setup.subprocess.Popen", return_value=process) as popen, \
                patch("outlook_maximizer.cli_setup.threading.Thread") as thread:
            returned = cli_setup.launch_login("antigravity", executable)
        self.assertIs(returned, process)
        self.assertEqual(popen.call_args.kwargs["cwd"], str(login_dir))
        self.assertEqual(popen.call_args.args[0], [executable])
        thread.return_value.start.assert_called_once()
        cleanup = thread.call_args.kwargs["target"]
        user_file = login_dir / "keep.txt"
        user_file.write_text("user content", encoding="utf-8")
        cleanup()
        self.assertTrue(user_file.exists())
        user_file.unlink()
        cleanup()
        self.assertFalse(login_dir.exists())

    @unittest.skipUnless(os.name == "nt", "Windows interactive login")
    def test_failed_login_start_removes_only_its_empty_workspace(self):
        executable = self.executable("claude.exe")
        login_dir = self.root / "failed login"
        login_dir.mkdir()
        with patch("outlook_maximizer.cli_setup.tempfile.mkdtemp", return_value=str(login_dir)), \
                patch("outlook_maximizer.cli_setup.subprocess.Popen", side_effect=OSError("private path")):
            with self.assertRaises(cli_setup.CliSetupError) as caught:
                cli_setup.launch_login("claude", executable)
        self.assertNotIn("private path", str(caught.exception))
        self.assertFalse(login_dir.exists())


if __name__ == "__main__":
    unittest.main()
