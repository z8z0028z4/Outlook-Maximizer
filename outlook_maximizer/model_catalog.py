"""Best-effort model metadata from local CLIs; no model requests or credentials."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from .process_launch import run_external


_MAX_CATALOG = 2_000_000
_MAX_MODELS = 100
_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


@dataclass(frozen=True)
class ModelOption:
    model_id: str
    label: str
    efforts: tuple[str, ...] = ()


def _text(value: object, *, identifier: bool = False) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    if identifier and any(char.isspace() for char in value):
        return ""
    return value


def load_codex_models(cache_path: Path | None = None) -> list[ModelOption]:
    """Read only the model cache; missing or outdated caches leave manual entry usable."""
    if cache_path is None:
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        cache_path = codex_home / "models_cache.json"
    try:
        with Path(cache_path).open(encoding="utf-8") as source:
            raw = source.read(_MAX_CATALOG + 1)
        if len(raw) > _MAX_CATALOG:
            return []
        data = json.loads(raw)
    except (OSError, UnicodeError, ValueError, TypeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return []
    options, seen = [], set()
    for record in data["models"]:
        if not isinstance(record, dict) or record.get("visibility", "list") != "list":
            continue
        model_id = _text(record.get("slug"), identifier=True)
        if not model_id or model_id in seen:
            continue
        label = _text(record.get("display_name")) or model_id
        levels = record.get("supported_reasoning_levels", [])
        efforts = []
        if isinstance(levels, list):
            for level in levels:
                effort = level.get("effort") if isinstance(level, dict) else None
                if isinstance(effort, str) and effort in _EFFORTS and effort not in efforts:
                    efforts.append(effort)
        options.append(ModelOption(model_id, label, tuple(efforts)))
        seen.add(model_id)
        if len(options) >= _MAX_MODELS:
            break
    return options


def load_antigravity_models(executable: str, timeout: float = 8) -> list[ModelOption]:
    """Run the CLI's catalog subcommand, which emits model-id TAB display-name rows."""
    if not isinstance(executable, str) or not executable or len(executable) > 4096 or any(ord(char) < 32 for char in executable):
        return []
    path = Path(executable).expanduser()
    if not path.is_file() or (os.name == "nt" and path.suffix.lower() != ".exe"):
        return []
    path = path.resolve()
    try:
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            return []
        timeout = min(timeout, 30)
        with tempfile.TemporaryDirectory(prefix="outlook-models-") as workspace:
            result = run_external(
                [str(path), "models"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                cwd=workspace, timeout=timeout, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return []
    if result.returncode or not isinstance(result.stdout, str) or len(result.stdout) > _MAX_CATALOG:
        return []
    options, seen = [], set()
    for row in result.stdout.splitlines():
        model_id, separator, label = row.partition("\t")
        model_id, label = _text(model_id, identifier=True), _text(label)
        if not separator or not model_id or not label or model_id in seen:
            continue
        options.append(ModelOption(model_id, label))
        seen.add(model_id)
        if len(options) >= _MAX_MODELS:
            break
    return options
