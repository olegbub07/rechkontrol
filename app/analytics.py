"""Сводки по операторам: рейтинг, недельный тренд, частые ошибки. Общие для дашборда и PDF-отчёта."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from app.models import Call

METRICS = ("overall", "politeness", "completeness", "script_compliance", "resolution", "violation_pct")


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def week_start(value: datetime | date) -> date:
    d = value.date() if isinstance(value, datetime) else value
    return d - timedelta(days=d.weekday())


def failed_checks(call: Call) -> list[dict]:
    if not call.quality:
        return []
    try:
        raw = json.loads(call.quality.checks_json or "[]")
    except (TypeError, ValueError):
        return []
    return [c for c in raw if isinstance(c, dict) and not c.get("passed") and c.get("id") != "empty"]


def top_mistakes(calls: list[Call], limit: int = 5) -> list[dict]:
    """Самые частые непройденные пункты: в скольких звонках из группы пункт провален."""
    done = [c for c in calls if c.quality]
    counts: Counter[str] = Counter()
    titles: dict[str, str] = {}
    for c in done:
        seen = set()
        for ch in failed_checks(c):
            cid = str(ch.get("id"))
            if cid in seen:
                continue
            seen.add(cid)
            counts[cid] += 1
            titles.setdefault(cid, str(ch.get("title") or cid))
    total = len(done) or 1
    return [
        {"id": cid, "title": titles[cid], "calls": n, "share": round(100 * n / total, 1)}
        for cid, n in sorted(counts.items(), key=lambda kv: (-kv[1], titles[kv[0]]))[:limit]
    ]


def weekly_trend(calls: list[Call]) -> list[dict]:
    weeks: dict[date, list[Call]] = defaultdict(list)
    for c in calls:
        if c.quality:
            weeks[week_start(c.call_at)].append(c)
    return [
        {
            "week": w.isoformat(),
            "overall": _avg([c.quality.overall for c in weeks[w]]),
            "violation_pct": _avg([c.quality.violation_pct for c in weeks[w]]),
            "count": len(weeks[w]),
        }
        for w in sorted(weeks)
    ]


def summary(calls: list[Call]) -> dict:
    done = [c for c in calls if c.quality]
    out = {"calls": len(calls), "calls_done": len(done)}
    for m in METRICS:
        out[m] = _avg([getattr(c.quality, m) for c in done])
    resp = [c.quality.avg_response_sec for c in done if c.quality.avg_response_sec is not None]
    out["avg_response_sec"] = round(sum(resp) / len(resp), 2) if resp else None
    out["tonality"] = dict(Counter(c.quality.tonality for c in done))
    out["rude_calls"] = sum(1 for c in done if any(ch.get("id") == "no_rudeness" for ch in failed_checks(c)))
    return out


MIN_WEEK_CALLS = 2


def trend_delta(trend: list[dict]) -> float | None:
    """Изменение среднего балла последней недели с оценками к предыдущей.
    Недели с одним звонком не сравниваем: один звонок даёт скачок на десятки баллов."""
    points = [p for p in trend if p["overall"] is not None and p.get("count", 0) >= MIN_WEEK_CALLS]
    if len(points) < 2:
        return None
    return round(points[-1]["overall"] - points[-2]["overall"], 1)
