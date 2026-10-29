"""Local hashed Calendar receipts and retry identities; never store email text."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading

from .calendar_api import CalendarError, event_slot_key
from .models import CalendarEvent, ValidationError


_MAX_BYTES = 4 * 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}")
_OCCURRENCE = re.compile(r"[0-9a-f]{32}")


class CalendarReceipts:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "calendar-receipts.json"
        self._lock = threading.RLock()
        self._entries: dict[str, dict] = {}
        self.load_error = ""
        if not self.path.exists():
            return
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                raise ValueError("oversized receipts")
            data = json.loads(raw.decode("utf-8"))
            if (not isinstance(data, dict) or set(data) != {"version", "entries"}
                    or type(data["version"]) is not int or data["version"] != 1):
                raise ValueError("invalid receipts")
            entries = data["entries"]
            if not isinstance(entries, dict):
                raise ValueError("invalid entries")
            for key, entry in entries.items():
                if not _HASH.fullmatch(key) or not isinstance(entry, dict) or set(entry) != {"added", "pending"}:
                    raise ValueError("invalid receipt")
                pending = entry["pending"]
                if type(entry["added"]) is not bool or not (
                    pending is None or isinstance(pending, str) and (pending == "" or _OCCURRENCE.fullmatch(pending))
                ):
                    raise ValueError("invalid receipt state")
            self._entries = entries
        except (OSError, ValueError, UnicodeError, TypeError):
            # Reading summaries remains available; do not silently overwrite a
            # damaged pending repeat identity and accidentally create a duplicate.
            self.load_error = "無法讀取本機行程加入紀錄，請確認資料夾權限與紀錄檔後重新開啟工具。"

    def is_added(self, calendar_id: str, source_key: str, event: CalendarEvent) -> bool:
        try:
            key = event_slot_key(calendar_id, source_key, event)
        except (ValueError, TypeError):
            return False
        with self._lock:
            return bool(self._entries.get(key, {}).get("added", False))

    def begin(self, calendar_id: str, source_key: str, event: CalendarEvent, *, repeat: bool = False) -> str:
        if self.load_error:
            raise CalendarError(self.load_error)
        try:
            event.validate(for_insert=True)
        except ValidationError as exc:
            raise CalendarError(str(exc)) from exc
        key = event_slot_key(calendar_id, source_key, event)
        with self._lock:
            current = self._entries.get(key, {"added": False, "pending": None})
            if current["pending"] is not None:
                return current["pending"]
            if repeat and not current["added"]:
                raise CalendarError("請先加入這筆行程，再使用再次加入。")
            occurrence = secrets.token_hex(16) if repeat else ""
            self._save_entry(key, {"added": current["added"], "pending": occurrence})
            return occurrence

    def complete(self, calendar_id: str, source_key: str, event: CalendarEvent, occurrence_key: str) -> None:
        if self.load_error:
            raise CalendarError(self.load_error)
        key = event_slot_key(calendar_id, source_key, event)
        with self._lock:
            current = self._entries.get(key)
            if current and current["added"] and current["pending"] is None:
                return
            if current is None or current["pending"] != occurrence_key:
                raise CalendarError("行程加入紀錄已變更，請重新確認這筆行程。")
            self._save_entry(key, {"added": True, "pending": None})

    def _save_entry(self, key: str, entry: dict) -> None:
        entries = {**self._entries, key: entry}
        payload = json.dumps({"version": 1, "entries": entries}, separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_BYTES:
            raise CalendarError("本機行程加入紀錄已達大小上限，無法安全新增。")
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".calendar-receipts-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self._entries = entries
        except OSError as exc:
            raise CalendarError("無法儲存本機行程加入紀錄，請確認資料夾權限後重試。") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
