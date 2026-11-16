from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.api.deps import current_user, db_session
from app.api.serializers import call_item
from app.models import Call, Operator, User
from app.schemas import DashboardKpis, DashboardOut, OperatorOut, QueueOut, TrendPoint

router = APIRouter(tags=["dashboard"])


def _period_start(period: str) -> datetime | None:
    now = datetime.utcnow()
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    return None


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    period: str = Query("30d"),
    session: Session = Depends(db_session),
    _: User = Depends(current_user),
) -> DashboardOut:
    start = _period_start(period)
    q = session.query(Call).options(joinedload(Call.quality), joinedload(Call.operator))
    if start:
        q = q.filter(Call.call_at >= start)
    calls = q.all()
    done = [c for c in calls if c.quality]

    def avg(key: str) -> float:
        if not done:
            return 0.0
        return round(sum(getattr(c.quality, key) for c in done) / len(done), 1)
    responses = [c.quality.avg_response_sec for c in done if c.quality.avg_response_sec is not None]
    processed = [c.processed_sec for c in done if c.processed_sec is not None]
    kpis = DashboardKpis(
        calls_total=len(calls),
        calls_done=len(done),
        overall=avg("overall"),
        violation_pct=avg("violation_pct"),
        politeness=avg("politeness"),
        avg_response_sec=round(sum(responses) / len(responses), 2) if responses else None,
        processed_sec=round(sum(processed) / len(processed), 1) if processed else None,
    )
    by_day: dict[str, list[Call]] = defaultdict(list)
    for c in done:
        by_day[c.call_at.date().isoformat()].append(c)
    trend = []
    for day in sorted(by_day):
        group = by_day[day]
        trend.append(
            TrendPoint(
                date=day,
                overall=round(sum(c.quality.overall for c in group) / len(group), 1),
                violation_pct=round(sum(c.quality.violation_pct for c in group) / len(group), 1),
                count=len(group),
            )
        )
    tonality = {"positive": 0, "neutral": 0, "negative": 0}
    for c in done:
        if c.quality.tonality in tonality:
            tonality[c.quality.tonality] += 1
    metrics_avg = {
        "politeness": avg("politeness"),
        "completeness": avg("completeness"),
        "script_compliance": avg("script_compliance"),
        "resolution": avg("resolution"),
    }
    ops_map: dict[int, list[Call]] = defaultdict(list)
    for c in done:
        if c.operator_id:
            ops_map[c.operator_id].append(c)
    operators_out: list[OperatorOut] = []
    ops = {o.id: o for o in session.query(Operator).all()}
    for oid, group in ops_map.items():
        op = ops.get(oid)
        if not op:
            continue
        operators_out.append(
            OperatorOut(
                id=op.id,
                name=op.name,
                external_id=op.external_id,
                calls_count=len(group),
                avg_overall=round(sum(c.quality.overall for c in group) / len(group), 1),
                violation_pct=round(sum(c.quality.violation_pct for c in group) / len(group), 1),
            )
        )
    operators_out.sort(key=lambda x: -(x.avg_overall or 0))
    recent = sorted(calls, key=lambda c: (c.call_at, c.id), reverse=True)[:8]
    today = datetime.utcnow().date()
    queue = QueueOut(
        queued=session.query(Call).filter(Call.status == "queued").count(),
        processing=session.query(Call).filter(Call.status == "processing").count(),
        done_today=session.query(Call).filter(Call.status == "done", Call.uploaded_at >= datetime(today.year, today.month, today.day)).count(),
        failed=session.query(Call).filter(Call.status == "error").count(),
    )
    return DashboardOut(
        kpis=kpis,
        trend=trend,
        tonality=tonality,
        metrics_avg=metrics_avg,
        operators=operators_out,
        recent=[call_item(c) for c in recent],
        queue=queue,
    )
