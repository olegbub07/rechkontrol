from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.access import is_operator, limit_calls
from app.analytics import summary, top_mistakes, trend_delta, weekly_trend
from app.api.deps import current_user, db_session
from app.models import Call, Operator, User
from app.timeutil import utcnow

router = APIRouter(tags=["operators"])
PERIODS = {"7d": 7, "30d": 30, "90d": 90, "all": None}


@router.get("/operators/compare")
def compare_operators(
    period: str = Query("30d"),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> dict:
    """Сравнение операторов: рейтинг, тренд по неделям, частые ошибки. Оператор видит только себя."""
    if period not in PERIODS:
        period = "30d"
    days = PERIODS[period]
    base = limit_calls(session.query(Call).filter(Call.status == "done"), user)
    if days:
        base = base.filter(Call.call_at >= utcnow() - timedelta(days=days))
    q = base.options(joinedload(Call.quality)).filter(Call.operator_id.isnot(None))
    calls = q.all()
    # Звонки без оператора входят в общие цифры дашборда, но не в рейтинг и PDF: показываем, сколько их.
    unassigned = base.filter(Call.operator_id.is_(None)).count()
    span = base.with_entities(func.min(Call.call_at), func.max(Call.call_at)).one()
    groups: dict[int, list[Call]] = defaultdict(list)
    for c in calls:
        if c.quality:
            groups[c.operator_id].append(c)
    ops = {o.id: o for o in session.query(Operator).filter(Operator.id.in_(list(groups) or [-1])).all()}
    rows = []
    for oid, group in groups.items():
        op = ops.get(oid)
        if not op:
            continue
        trend = weekly_trend(group)
        rows.append(
            {
                "id": op.id,
                "name": op.name,
                "external_id": op.external_id,
                **summary(group),
                "trend": trend,
                "delta": trend_delta(trend),
                "top_mistakes": top_mistakes(group, 3),
            }
        )
    rows.sort(key=lambda r: (-(r["overall"] or 0), r["name"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    mine = is_operator(user)
    all_done = [c for g in groups.values() for c in g]
    return {
        "period": period,
        "scope": "self" if mine else "team",
        "operators": rows,
        "unassigned": 0 if mine else unassigned,
        "data_from": span[0].date().isoformat() if span[0] else None,
        "data_to": span[1].date().isoformat() if span[1] else None,
        "period_days": days,
        "team": None if mine else {**summary(all_done), "trend": weekly_trend(all_done), "top_mistakes": top_mistakes(all_done, 5)},
    }
