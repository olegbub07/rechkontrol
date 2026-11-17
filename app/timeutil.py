"""Время в UTC без устаревшего datetime.utcnow().

В базе даты хранятся как «наивные» значения в UTC (колонки DateTime без зоны, так было
с первой версии). Поэтому utcnow() считает время через timezone-aware datetime.now(timezone.utc)
и отдаёт его без tzinfo: так новые значения сравнимы со старыми строками в SQLite и PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
