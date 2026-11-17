"""Время в UTC без устаревшего datetime.utcnow().

В базе даты хранятся как «наивные» значения в UTC (колонки DateTime без зоны, так было
с первой версии). Поэтому utcnow() считает время через timezone-aware datetime.now(timezone.utc)
и отдаёт его без tzinfo: так новые значения сравнимы со старыми строками в SQLite и PostgreSQL.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def display_zone() -> tzinfo:
    """Пояс для показа времени. Без базы поясов (Windows без пакета tzdata) - фиксированный UTC+3."""
    from app.config import settings

    for name in ((settings.display_tz or "").strip(), "Europe/Moscow"):
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return timezone(timedelta(hours=3), "Europe/Moscow")


def to_local(value: datetime | None) -> datetime | None:
    """Наивное UTC-время из базы в местное (DISPLAY_TZ) с явной зоной."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(display_zone())


_FILE_DATE = re.compile(
    r"(?<!\d)(20\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[T _-]?([01]\d|2[0-3])[-_.:]?([0-5]\d)(?:[-_.:]?([0-5]\d))?)?(?!\d)"
)
_FILE_OPERATOR = re.compile(r"(?i)(?<![a-z])op[-_]?(\d{2,6})(?!\d)")


def date_from_filename(name: str | None) -> datetime | None:
    """2026-11-07_card_block.wav, 20261107_1530_x.mp3, call_2026.11.07-15-30-12.wav -> время звонка.
    Без времени в имени ставим полдень, чтобы смещение пояса не увело звонок на соседний день."""
    m = _FILE_DATE.search(name or "")
    if not m:
        return None
    y, mo, d, hh, mm, ss = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(hh or 12), int(mm or 0), int(ss or 0))
    except ValueError:
        return None


def operator_code_from_filename(name: str | None) -> str | None:
    m = _FILE_OPERATOR.search(name or "")
    return f"OP-{m.group(1)}" if m else None
