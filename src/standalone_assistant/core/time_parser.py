from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Asia/Dhaka")


@dataclass
class ParsedWhen:
    start: datetime
    end: datetime | None
    cleaned_text: str


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ).replace(second=0, microsecond=0)


def parse_when(text: str, *, default_hour: int = 9, duration_minutes: int = 60) -> ParsedWhen:
    original = text.strip()
    lowered = original.lower()
    base = now_local()
    day = base.date()

    if "day after tomorrow" in lowered:
        day = (base + timedelta(days=2)).date()
        lowered = lowered.replace("day after tomorrow", "")
    elif "tomorrow" in lowered:
        day = (base + timedelta(days=1)).date()
        lowered = lowered.replace("tomorrow", "")
    elif "today" in lowered:
        day = base.date()
        lowered = lowered.replace("today", "")

    in_match = re.search(r"\bin\s+(\d+)\s*(minute|minutes|hour|hours|day|days)\b", lowered)
    if in_match:
        amount = int(in_match.group(1))
        unit = in_match.group(2)
        if unit.startswith("minute"):
            start = base + timedelta(minutes=amount)
        elif unit.startswith("hour"):
            start = base + timedelta(hours=amount)
        else:
            start = base + timedelta(days=amount)
        cleaned = remove_span(original, in_match.span())
        return ParsedWhen(start, start + timedelta(minutes=duration_minutes), clean_spaces(cleaned))

    date_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", lowered)
    if date_match:
        day = datetime(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)), tzinfo=LOCAL_TZ).date()
        lowered = lowered.replace(date_match.group(0), "")

    hour = default_hour
    minute = 0
    time_match = re.search(r"\b(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lowered)
    if time_match:
        raw_hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        ampm = time_match.group(3)
        if ampm == "pm" and raw_hour < 12:
            raw_hour += 12
        elif ampm == "am" and raw_hour == 12:
            raw_hour = 0
        if 0 <= raw_hour <= 23:
            hour = raw_hour

    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=LOCAL_TZ)
    if start < base and ("today" not in original.lower()) and not date_match:
        start += timedelta(days=1)

    cleaned = original
    for phrase in ["day after tomorrow", "tomorrow", "today"]:
        cleaned = re.sub(rf"\b{phrase}\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b20\d{2}-\d{1,2}-\d{1,2}\b", "", cleaned)
    cleaned = re.sub(r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", "", cleaned, flags=re.I)
    return ParsedWhen(start, start + timedelta(minutes=duration_minutes), clean_spaces(cleaned))


def google_datetime(dt: datetime) -> dict[str, str]:
    return {"dateTime": dt.isoformat(), "timeZone": "Asia/Dhaka"}


def google_task_due(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,.-")


def remove_span(value: str, span: tuple[int, int]) -> str:
    return clean_spaces(value[: span[0]] + " " + value[span[1] :])
