"""Validated, plain-data objects shared by COM, AI and GUI threads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any


class ValidationError(ValueError):
    """Unusable or incomplete structured data."""


def _text(value: Any, name: str, limit: int, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} 必須是文字。")
    result = value.strip()
    if len(result) > limit or (not result and not allow_empty):
        raise ValidationError(f"{name} 的長度不符合限制。")
    return result


@dataclass(frozen=True)
class MailSnapshot:
    entry_id: str
    store_id: str
    subject: str
    sender: str
    body: str
    received_at: str
    internet_message_id: str = ""
    item_type: str = "mail"
    meeting_context: str = ""

    @property
    def key(self) -> str:
        identity = self.internet_message_id or self.entry_id
        return hashlib.sha256(f"{self.store_id}\0{identity}".encode("utf-8")).hexdigest()

    @property
    def analysis_key(self) -> str:
        content = json.dumps(
            [self.key, self.subject, self.sender, self.body, self.received_at,
             self.item_type, self.meeting_context],
            ensure_ascii=False,
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CalendarEvent:
    title: str
    start: str
    end: str
    timezone: str = "Asia/Taipei"
    all_day: bool = False
    location: str = ""
    description: str = ""
    evidence: str = ""
    needs_review: bool = False

    def validate(self, *, for_insert: bool = False) -> None:
        _text(self.title, "行程名稱", 300, allow_empty=False)
        _text(self.location, "地點", 2000)
        _text(self.description, "行程說明", 8000)
        _text(self.evidence, "信件依據", 3000)
        if type(self.all_day) is not bool or type(self.needs_review) is not bool:
            raise ValidationError("行程狀態必須是布林值。")
        if not isinstance(self.timezone, str) or not re.fullmatch(
            r"(?:UTC|[A-Za-z_]+(?:/[A-Za-z0-9_+.-]+)+)", self.timezone
        ):
            raise ValidationError("請指定 IANA 時區，例如 Asia/Taipei。")
        if for_insert and self.needs_review:
            raise ValidationError("請先確認並編輯這筆行程的日期與時間。")
        if not isinstance(self.start, str) or not isinstance(self.end, str):
            raise ValidationError("行程時間必須是文字。")
        if len(self.start) > 80 or len(self.end) > 80:
            raise ValidationError("行程時間格式不正確。")
        if not self.start or not self.end:
            if self.needs_review and not for_insert:
                return
            raise ValidationError("行程必須包含開始與結束時間。")
        try:
            if self.all_day:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.start) or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}", self.end
                ):
                    raise ValueError("date format")
                start, end = date.fromisoformat(self.start), date.fromisoformat(self.end)
            else:
                start = datetime.fromisoformat(self.start.replace("Z", "+00:00"))
                end = datetime.fromisoformat(self.end.replace("Z", "+00:00"))
                if start.tzinfo is None or end.tzinfo is None:
                    raise ValueError("missing offset")
            if end <= start:
                raise ValueError("invalid range")
        except (ValueError, TypeError) as exc:
            raise ValidationError("日期格式不正確，或結束時間沒有晚於開始時間。請使用含時差的 ISO 時間。") from exc

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalendarEvent:
        if not isinstance(data, dict):
            raise ValidationError("行程資料必須是物件。")
        required = set(cls.__dataclass_fields__)
        if set(data) != required:
            raise ValidationError("AI 回傳的行程欄位不完整。")
        event = cls(**data)
        event.validate()
        return event

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Analysis:
    summary: tuple[str, ...]
    action_items: tuple[str, ...]
    reply_draft: str
    events: tuple[CalendarEvent, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Analysis:
        if not isinstance(data, dict) or set(data) != {"summary", "action_items", "reply_draft", "events"}:
            raise ValidationError("AI 回傳的摘要欄位不完整。")
        def lines(key: str, maximum: int, *, required: bool = False) -> tuple[str, ...]:
            value = data[key]
            if not isinstance(value, list) or len(value) > maximum or (required and not value):
                raise ValidationError(f"{key} 的格式不正確。")
            return tuple(_text(line, key, 2000, allow_empty=False) for line in value)
        events = data["events"]
        if not isinstance(events, list) or len(events) > 10:
            raise ValidationError("AI 回傳的行程數量不正確。")
        return cls(
            summary=lines("summary", 8, required=True),
            action_items=lines("action_items", 10),
            reply_draft=_text(data["reply_draft"], "回覆草稿", 16000),
            events=tuple(CalendarEvent.from_dict(item) for item in events),
        )


@dataclass(frozen=True)
class MonitorNotice:
    kind: str
    message: str = ""
    mail: MailSnapshot | None = None
