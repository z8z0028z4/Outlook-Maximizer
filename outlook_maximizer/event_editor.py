"""Convert human date/time fields to validated calendar data."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import CalendarEvent, ValidationError


def get_timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Taipei":
            return timezone(timedelta(hours=8))
        if name == "UTC":
            return timezone.utc
        raise ValidationError("這台電腦缺少此時區的資料，請使用 Asia/Taipei 或 UTC。") from None


def _aware(day: str, clock: str, zone_name: str) -> datetime:
    zone = get_timezone(zone_name)
    naive = datetime.combine(date.fromisoformat(day), time.fromisoformat(clock))
    if naive.tzinfo is not None:
        raise ValidationError("時間欄位請填 HH:MM，時區請填於時區欄位。")
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    if first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
        raise ValidationError("此時間落在夏令時間跳轉的空檔，請修正時間。")
    if first.utcoffset() != second.utcoffset():
        raise ValidationError("此時間因夏令時間有兩種解讀，請改用 UTC 時區填寫。")
    return first


def event_from_fields(
    original: CalendarEvent, *, title: str, start_date: str, start_time: str,
    end_date: str, end_time: str, timezone_name: str, all_day: bool, location: str,
    description: str | None = None,
) -> CalendarEvent:
    try:
        if all_day:
            start = date.fromisoformat(start_date).isoformat()
            end = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
        else:
            start = _aware(start_date, start_time, timezone_name).isoformat()
            end = _aware(end_date, end_time, timezone_name).isoformat()
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError("日期請填 YYYY-MM-DD，時間請填 HH:MM。") from exc
    result = replace(
        original, title=title.strip(), start=start, end=end, timezone=timezone_name.strip(),
        all_day=all_day, location=location.strip(), needs_review=False,
        description=original.description if description is None else description.strip(),
    )
    result.validate(for_insert=True)
    return result
