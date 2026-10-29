"""Claude Code subscription login with bounded, text-only mail analysis."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

from .ai import AIError, AnalysisCancelled, CodexSummarizer, run_cli
from .cli_setup import _claude_compatibility, discover_claude
from .models import Analysis, ValidationError


class ClaudeSummarizer(CodexSummarizer):
    """Reuse a locally signed-in Claude Code session without user customizations.

    Claude's safe mode keeps organization-managed policy hooks in effect. The
    regular user/project customizations, tools, MCP and session recording are
    disabled for this run; managed policy remains under the organization's
    control rather than being bypassed by the application.
    """

    def build_command(self, schema_path: Path | None = None, *,
                      schema_override: dict | None = None) -> list[str]:
        configured = getattr(self.settings, "claude_path", "")
        resolved = discover_claude(configured)
        executable = Path(resolved).expanduser()
        if not resolved or not executable.is_file():
            raise AIError("找不到 Claude 執行檔。請在設定重新搜尋或安裝 Claude Code。")
        if os.name == "nt" and executable.suffix.lower() != ".exe":
            raise AIError("請選擇原生 claude.exe；此工具不透過 shell 執行 .cmd 或 .ps1。")
        try:
            schema = schema_override if schema_override is not None else json.loads(
                (schema_path or Path(__file__).with_name("analysis.schema.json")).read_text(encoding="utf-8")
            )
            if not isinstance(schema, dict):
                raise ValueError("schema must be an object")
            schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        except (OSError, ValueError, TypeError) as exc:
            raise AIError("無法載入 Claude 回傳格式，請重新安裝或更新工具。") from exc

        command = [
            str(executable), "--print", "--safe-mode", "--setting-sources", "",
            "--tools", "", "--disallowedTools", "mcp__*",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--settings", '{"disableAllHooks":true}', "--disable-slash-commands", "--no-chrome",
            "--no-session-persistence", "--permission-mode", "dontAsk",
            "--output-format", "json", "--json-schema", schema_json,
        ]
        model = getattr(self.settings, "claude_model", "")
        effort = getattr(self.settings, "claude_effort", "")
        if not isinstance(model, str) or not isinstance(effort, str):
            raise AIError("Claude 模型或思考強度設定不正確，請重新選擇。")
        if effort and effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise AIError("Claude 思考強度設定不正確，請重新選擇。")
        if model.strip():
            command.extend(["--model", model.strip()])
        if effort:
            command.extend(["--effort", effort])
        return command

    @staticmethod
    def _final_data(output: str) -> dict:
        if len(output) > 2_000_000:
            raise AIError("AI 回應超過大小限制，請縮短信件後重試。")
        try:
            envelope = json.loads(output)
        except (ValueError, TypeError) as exc:
            raise AIError("Claude 回傳資料格式不正確，請重新產生。") from exc
        if (not isinstance(envelope, dict) or envelope.get("type") != "result"
                or envelope.get("subtype") != "success" or envelope.get("is_error") is True):
            raise AIError("Claude 沒有完成這次產生。請確認登入、網路與模型設定後重試。")
        data = envelope.get("structured_output")
        if not isinstance(data, dict):
            raise AIError("Claude 回傳資料格式不正確，請重新產生。")
        return data

    @staticmethod
    def _parse_output(output: str) -> Analysis:
        try:
            return Analysis.from_dict(ClaudeSummarizer._final_data(output))
        except ValidationError as exc:
            raise AIError("Claude 回傳資料格式不正確，請重新產生摘要。") from exc

    def _execute(self, prompt: str, cancel: threading.Event | None = None, *,
                 schema_path: Path | None = None, schema_override: dict | None = None) -> str:
        cancel = cancel or threading.Event()
        if cancel.is_set():
            raise AnalysisCancelled("已切換信件。")
        with tempfile.TemporaryDirectory(prefix="outlook-claude-") as workspace:
            command = self.build_command(schema_path, schema_override=schema_override)
            try:
                return run_cli(command, prompt, cancel, timeout=self.settings.ai_timeout_seconds,
                               workspace=workspace, provider="Claude")
            except AnalysisCancelled:
                raise
            except AIError as exc:
                if cancel.is_set():
                    raise AnalysisCancelled("已切換信件。") from exc
                compatible = _claude_compatibility(command[0])
                if cancel.is_set():
                    raise AnalysisCancelled("已切換信件。") from exc
                if compatible is False:
                    raise AIError("目前的 Claude Code 版本缺少必要選項，請更新 CLI 後重新搜尋。") from exc
                raise
