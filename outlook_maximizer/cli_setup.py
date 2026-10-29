"""User-initiated setup and discovery for supported local AI CLIs.

This module never reads credential files. Login is handed to each vendor's own
interactive CLI, and installing a CLI is only started by an explicit UI action.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

from .process_launch import popen_external, run_external
from .settings import discover_antigravity, discover_codex


PROVIDERS = ("codex", "antigravity", "claude")
PROVIDER_LABELS = {"codex": "ChatGPT / Codex", "antigravity": "Google Antigravity", "claude": "Claude"}

# These are the vendors' documented Windows installers. They are deliberately
# fixed constants: no user-supplied string is interpolated into a shell command.
_INSTALL_URLS = {
    "codex": "https://chatgpt.com/codex/install.ps1",
    "antigravity": "https://antigravity.google/cli/install.ps1",
    "claude": "https://claude.ai/install.ps1",
}
_LOGIN_ARGS = {"codex": ("login",), "antigravity": (), "claude": ("auth", "login")}
_AUTH_ARGS = {"codex": ("login", "status"), "claude": ("auth", "status", "--json")}
_CLAUDE_AUTH_METHODS = {"none", "claude.ai", "oauth_token", "api_key", "api_key_helper", "third_party"}
_CLAUDE_REQUIRED_FLAGS = {
    "--print", "--safe-mode", "--setting-sources", "--tools", "--disallowedTools",
    "--strict-mcp-config", "--mcp-config", "--settings", "--disable-slash-commands",
    "--no-chrome", "--no-session-persistence", "--permission-mode", "--output-format", "--json-schema",
    "--model", "--effort",
}


class CliSetupError(RuntimeError):
    """A short, non-sensitive error suitable for display in the setup window."""


class CliSetupCancelled(CliSetupError):
    pass


@dataclass(frozen=True)
class CliStatus:
    provider: str
    executable: str = ""
    auth: str = "unknown"  # ready, login_required, or unknown
    message: str = ""
    compatible: bool | None = None
    auth_method: str = ""

    @property
    def installed(self) -> bool:
        return bool(self.executable)

def _require_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise CliSetupError("不支援這個 AI 助理。")


def _native(path: str | Path) -> str:
    try:
        candidate = Path(path).expanduser()
        if candidate.is_file() and (os.name != "nt" or candidate.suffix.lower() == ".exe"):
            # Do not resolve vendor-managed junctions: their stable path keeps
            # working when the CLI swaps its versioned binary after an update.
            return os.path.abspath(candidate)
    except (OSError, ValueError, TypeError):
        pass
    return ""


def discover_claude(configured_path: str = "") -> str:
    """Find Claude's native binary in PATH, its native install, or npm's package."""
    if configured_path:
        found = _native(configured_path)
        if found:
            return found
    direct = shutil.which("claude.exe" if os.name == "nt" else "claude")
    if direct:
        found = _native(direct)
        if found:
            return found

    profile = Path(os.environ.get("USERPROFILE") or Path.home())
    appdata = Path(os.environ.get("APPDATA") or profile / "AppData" / "Roaming")
    candidates = [
        profile / ".local" / "bin" / "claude.exe",
        appdata / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
    ]
    wrapper = shutil.which("claude.cmd") if os.name == "nt" else None
    if wrapper:
        candidates.append(Path(wrapper).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
    for candidate in candidates:
        found = _native(candidate)
        if found:
            return found
    return ""


def _discover(provider: str, configured_path: str) -> str:
    _require_provider(provider)
    if provider == "claude":
        return discover_claude(configured_path)
    if provider == "codex":
        if configured_path:
            found = _native(configured_path)
            if found:
                return found
        found = _native(discover_codex(configured_path))
        if found:
            return found
        # The standalone installer updates the user PATH; a running app need not
        # inherit that change, so check its fixed destination explicitly.
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        install_dir = os.environ.get("CODEX_INSTALL_DIR")
        candidates = ([Path(install_dir) / "codex.exe"] if install_dir else [])
        candidates.append(local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe")
        return next((found for candidate in candidates if (found := _native(candidate))), "")
    if configured_path:
        found = _native(configured_path)
        if found:
            return found
    return _native(discover_antigravity())


def _auth_status(provider: str, executable: str, *, timeout: float = 10) -> tuple[str, str]:
    # AGY's documented --version verifies the binary, not the user's login.
    if provider == "antigravity":
        return "unknown", ""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = run_external(
            [executable, *_AUTH_ARGS[provider]], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=flags, shell=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown", ""
    # Never return or log the vendor's output: it can include account details.
    if provider == "codex":
        if result.returncode == 0:
            return "ready", ""
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        return ("login_required" if result.returncode == 1 and "not logged in" in output.casefold() else "unknown"), ""
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return "unknown", ""
    logged_in = data.get("loggedIn") if isinstance(data, dict) else None
    method = data.get("authMethod") if isinstance(data, dict) else None
    method = method if isinstance(method, str) and method in _CLAUDE_AUTH_METHODS else ""
    if logged_in is True and result.returncode == 0:
        if method == "none":
            return "unknown", method
        return "ready", method
    if logged_in is False and result.returncode in (0, 1):
        return "login_required", method
    return "unknown", ""


def _claude_compatibility(executable: str, *, timeout: float = 8) -> bool | None:
    """Inspect option declarations only; never interpret CLI errors as support."""
    try:
        result = run_external(
            [executable, "--help"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, shell=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode or not isinstance(result.stdout, str) or len(result.stdout) > 200_000:
        return None
    options = set()
    for line in result.stdout.splitlines():
        declaration = line.lstrip()
        if declaration.startswith("-"):
            declaration = re.split(r"\s{2,}", declaration, maxsplit=1)[0]
            options.update(re.findall(r"--[a-zA-Z][a-zA-Z0-9-]*", declaration))
    return _CLAUDE_REQUIRED_FLAGS <= options if options else None


def _claude_message(auth: str, method: str, compatible: bool | None) -> str:
    if auth == "ready":
        label = {
            "claude.ai": "已透過 Claude 帳號登入。",
            "oauth_token": "已透過 Claude OAuth 憑證登入。",
            "api_key": "已透過 API 金鑰驗證，使用量可能另計費。",
            "api_key_helper": "已透過 API 金鑰輔助程式驗證，使用量可能另計費。",
            "third_party": "已透過第三方服務驗證，計費依該服務設定。",
        }.get(method, "已確認登入，但登入方式無法確認。")
    elif auth == "login_required":
        label = "已找到，請完成登入。"
    else:
        label = "已找到，登入狀態無法確認。"
    if compatible is True:
        label += " 已找到本工具所需的 CLI 選項；實際使用仍取決於帳號與網路。"
    elif compatible is False:
        label += " 目前 CLI 缺少必要功能，請依原安裝方式更新後重新搜尋。"
    else:
        label += " 無法確認 CLI 版本是否支援本工具，請檢查或更新後重新搜尋。"
    # Iterating os.environ yields names only. Mapping.__contains__ may retrieve
    # a value internally, so avoid it for these credentials altogether.
    names = {name.upper() for name in os.environ}
    if names & {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}:
        label += " 偵測到 API／Token 環境變數；非互動使用可能依 API 或其他服務計費。"
    providers = [title for name, title in (
        ("CLAUDE_CODE_USE_BEDROCK", "Bedrock"),
        ("CLAUDE_CODE_USE_VERTEX", "Vertex"),
        ("CLAUDE_CODE_USE_FOUNDRY", "Foundry"),
    ) if name in names]
    if providers:
        label += f" 偵測到{'／'.join(providers)} 環境設定；非互動使用可能改走第三方服務並依該服務計費。"
    return label


def detect_provider(provider: str, configured_path: str = "") -> CliStatus:
    """Re-detect an executable and safely check its documented login status."""
    executable = _discover(provider, configured_path)
    if not executable:
        return CliStatus(provider, message="尚未找到執行檔。")
    compatible = _claude_compatibility(executable) if provider == "claude" else None
    auth, method = _auth_status(provider, executable)
    if provider == "claude":
        return CliStatus(provider, executable, auth, _claude_message(auth, method, compatible), compatible, method)
    message = {
        "ready": "已找到並確認登入。",
        "login_required": "已找到，請完成登入。",
        "unknown": "已找到；請開啟登入視窗確認帳號。",
    }[auth]
    if provider == "codex" and any(name.upper() == "CODEX_API_KEY" for name in os.environ):
        message += " 偵測到 Codex API 金鑰環境設定；非互動使用可能另依 API 計費。"
    if provider == "antigravity" and any(name.upper() == "GEMINI_API_KEY" for name in os.environ):
        message += " 偵測到 Gemini API 金鑰環境設定；使用時可能另依 API 計費。"
    return CliStatus(provider, executable, auth, message)


def _powershell() -> str:
    root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    candidate = root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return _native(candidate) or _native(shutil.which("powershell.exe") or "")


def _run_installer(provider: str, cancel: threading.Event, timeout: float) -> None:
    if os.name != "nt":
        raise CliSetupError("自動安裝目前僅支援 Windows。")
    if cancel.is_set():
        raise CliSetupCancelled("已取消安裝。")
    shell = _powershell()
    if cancel.is_set():
        raise CliSetupCancelled("已取消安裝。")
    if not shell:
        raise CliSetupError("找不到 Windows PowerShell，請確認公司電腦允許使用它。")
    # This command is constructed only from the constant URL above. No path or
    # account input enters PowerShell; existing shell profiles are not loaded.
    command = (
        "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
        f"Invoke-RestMethod -Uri '{_INSTALL_URLS[provider]}' | Invoke-Expression"
    )
    env = os.environ.copy()
    if provider == "codex":
        # The vendor's defaults do not uninstall existing package-manager CLIs.
        env["CODEX_NON_INTERACTIVE"] = "1"
    if cancel.is_set():
        raise CliSetupCancelled("已取消安裝。")
    try:
        process = popen_external(
            [shell, "-NoProfile", "-NonInteractive", "-Command", command],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, env=env, shell=False,
        )
    except OSError as exc:
        raise CliSetupError("無法啟動安裝程式。請檢查公司安全設定。") from exc
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel.is_set():
                raise CliSetupCancelled("已停止等待安裝；若安裝程式仍在執行，可稍後重新搜尋。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CliSetupError("安裝等待逾時；請確認網路或稍後重新搜尋。")
            try:
                code = process.wait(timeout=min(.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if code:
            raise CliSetupError("安裝未完成；請確認網路及公司安全設定，再重試。")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def install_provider(provider: str, configured_path: str = "", *,
                     cancel: threading.Event | None = None, timeout: float = 600) -> CliStatus:
    """Reuse an existing CLI or run its official installer on explicit request."""
    _require_provider(provider)
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise CliSetupCancelled("已取消安裝。")
    try:
        timeout = float(timeout)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CliSetupError("安裝等待時間不正確。") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise CliSetupError("安裝等待時間不正確。")
    status = detect_provider(provider, configured_path)
    if cancel.is_set():
        raise CliSetupCancelled("已取消安裝。")
    if status.installed:
        return status
    _run_installer(provider, cancel, min(timeout, 900))
    if cancel.is_set():
        raise CliSetupCancelled("已停止等待安裝；請稍後重新搜尋。")
    status = detect_provider(provider, configured_path)
    if not status.installed:
        raise CliSetupError("安裝結束但尚未找到執行檔，請重新搜尋或檢查安裝位置。")
    return status


def launch_login(provider: str, executable: str) -> subprocess.Popen:
    """Open the vendor's own interactive login window; do not capture credentials."""
    _require_provider(provider)
    executable = _native(executable)
    if not executable:
        raise CliSetupError("找不到執行檔；請先重新搜尋或安裝。")
    if os.name != "nt":
        raise CliSetupError("登入視窗目前僅支援 Windows。")
    # AGY can ask to trust and index the current working directory. Do not point
    # it at the shared temp directory, the user's files, or the app workspace.
    try:
        login_dir = Path(tempfile.mkdtemp(prefix="outlook-ai-login-"))
    except OSError as exc:
        raise CliSetupError("無法建立登入所需的暫存資料夾。") from exc
    try:
        process = popen_external(
            [executable, *_LOGIN_ARGS[provider]], cwd=str(login_dir),
            creationflags=subprocess.CREATE_NEW_CONSOLE, shell=False,
        )
    except OSError as exc:
        try:
            login_dir.rmdir()
        except OSError:
            pass
        raise CliSetupError("無法開啟登入視窗。請檢查公司安全設定並重新搜尋。") from exc

    def cleanup_empty_workspace() -> None:
        try:
            process.wait()
            login_dir.rmdir()  # Keep any user-created files; never delete recursively.
        except OSError:
            pass

    threading.Thread(target=cleanup_empty_workspace, name="cli-login-cleanup", daemon=True).start()
    return process
