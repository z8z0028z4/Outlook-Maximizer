"""Small local settings file; OAuth tokens are owned by calendar_api."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import math
import os
from pathlib import Path
import re
import shutil


def default_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "OutlookMaximizer"


def discover_codex(configured_path: str = "") -> str:
    """Resolve the current native binary, including npm wrappers and app updates."""
    def native(path: str | Path) -> str:
        candidate = Path(path).expanduser()
        try:
            if candidate.is_file() and (os.name != "nt" or candidate.suffix.lower() == ".exe"):
                # Preserve the standalone installer's stable bin junction across updates.
                return str(candidate.absolute())
        except (OSError, ValueError):
            pass
        return ""

    def npm_binary(directory: Path) -> str:
        # New npm releases use vendor/<triple>/bin; older ones used /codex.
        candidates = []
        for package_root in (directory / "@openai", directory / "@openai" / "codex" / "node_modules" / "@openai"):
            try:
                for folder in ("bin", "codex"):
                    candidates.extend(package_root.glob(f"codex-win32-*/vendor/*/{folder}/codex.exe"))
                for folder in ("bin", "codex"):
                    candidates.extend((package_root / "codex").glob(f"vendor/*/{folder}/codex.exe"))
            except OSError:
                continue
        for candidate in candidates:
            found = native(candidate)
            if found:
                return found
        return ""

    if configured_path:
        found = native(configured_path)
        if found:
            return found
        configured = Path(configured_path).expanduser()
        if configured.suffix.lower() in {".cmd", ".ps1"}:
            found = npm_binary(configured.parent / "node_modules")
            if found:
                return found
    direct = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if direct:
        found = native(direct)
        if found:
            return found
    standalone = (Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
                  / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe")
    found = native(standalone)
    if found:
        return found
    app_bin = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "OpenAI" / "Codex" / "bin"
    try:
        candidates = sorted(app_bin.glob("*/codex.exe"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        candidates = []
    for candidate in candidates:
        found = native(candidate)
        if found:
            return found
    wrapper = shutil.which("codex.cmd") if os.name == "nt" else None
    if wrapper:
        found = npm_binary(Path(wrapper).parent / "node_modules")
        if found:
            return found
    npm_root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "npm" / "node_modules"
    return npm_binary(npm_root)


def discover_antigravity() -> str:
    direct = shutil.which("agy.exe" if os.name == "nt" else "agy")
    if direct:
        return direct
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    candidate = base / "agy" / "bin" / "agy.exe"
    return str(candidate) if candidate.is_file() else ""


@dataclass
class Settings:
    ui_language: str = "en"
    ai_provider: str = "codex"
    codex_path: str = ""
    model: str = ""
    codex_effort: str = "low"
    antigravity_path: str = ""
    antigravity_model: str = ""
    antigravity_effort: str = ""
    claude_path: str = ""
    claude_model: str = ""
    claude_effort: str = ""
    about_me: str = ""
    calendar_id: str = "primary"
    timezone: str = "Asia/Taipei"
    ai_timeout_seconds: int = 120
    debounce_ms: int = 650
    always_on_top: bool = True
    auto_summarize: bool = True
    auto_reply: bool = False
    floating_opacity: float = .20
    background_color: str = "#FAFCFF"
    font_family: str = "Microsoft JhengHei UI"
    font_size: int = 10
    font_bold: bool = False
    font_italic: bool = False
    panel_width: int = 348
    summary_height: int = 300
    reply_height: int = 240

    @classmethod
    def load(cls, data_dir: Path) -> Settings:
        from .cli_setup import discover_claude

        path = data_dir / "settings.json"
        config = cls()
        if not path.exists():
            return config
        config.codex_path = discover_codex()
        config.antigravity_path = discover_antigravity()
        config.claude_path = discover_claude()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return config
            for field in fields(cls):
                if field.name not in data:
                    continue
                value = data[field.name]
                if field.name == "floating_opacity":
                    if type(value) in (int, float):
                        try:
                            numeric = float(value)
                        except (ValueError, OverflowError):
                            continue
                        if math.isfinite(numeric):
                            config.floating_opacity = numeric
                elif type(value) is type(getattr(config, field.name)):
                    setattr(config, field.name, value)
            config.ai_timeout_seconds = max(15, min(config.ai_timeout_seconds, 300))
            config.codex_path = discover_codex(config.codex_path) or config.codex_path
            if not config.antigravity_path or not Path(config.antigravity_path).expanduser().is_file():
                config.antigravity_path = discover_antigravity() or config.antigravity_path
            config.claude_path = discover_claude(config.claude_path) or config.claude_path
            config.about_me = config.about_me.strip()[:6000]
            config.debounce_ms = max(250, min(config.debounce_ms, 3000))
            config.floating_opacity = max(.08, min(config.floating_opacity, 1.0))
            if config.ai_provider not in {"codex", "antigravity", "claude"}:
                config.ai_provider = "codex"
            if config.ui_language not in {"en", "zh-TW"}:
                config.ui_language = "en"
            if config.codex_effort not in {"", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
                config.codex_effort = "low"
            if config.antigravity_effort not in {"", "low", "medium", "high", "xhigh", "max"}:
                config.antigravity_effort = ""
            if config.claude_effort not in {"", "low", "medium", "high", "xhigh", "max"}:
                config.claude_effort = ""
            config.background_color = config.background_color.upper()
            if not re.fullmatch(r"#[0-9A-F]{6}", config.background_color):
                config.background_color = "#FAFCFF"
            family = config.font_family.strip()
            config.font_family = (family if family and len(family) <= 100
                                  and not re.search(r"[\x00-\x1f\x7f-\x9f]", family)
                                  else "Microsoft JhengHei UI")
            config.font_size = max(8, min(config.font_size, 24))
            config.panel_width = max(300, min(config.panel_width, 520))
            config.summary_height = max(180, min(config.summary_height, 520))
            config.reply_height = max(160, min(config.reply_height, 460))
            config.calendar_id = config.calendar_id.strip() or "primary"
            if len(config.calendar_id) > 1024 or any(ord(char) < 32 for char in config.calendar_id):
                config.calendar_id = "primary"
        except (OSError, ValueError, TypeError):
            pass
        return config

    def save(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        target = data_dir / "settings.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
