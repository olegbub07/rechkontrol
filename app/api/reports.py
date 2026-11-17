from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from app.access import reject_operator
from app.api.deps import current_user, db_session
from app.audit import write_audit
from app.models import Call, Operator, User
from app.report_pdf import build_operator_report
from app.timeutil import utcnow

router = APIRouter(tags=["reports"])
MAX_DAYS = 366


def _day(value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(400, "Дата в формате ГГГГ-ММ-ДД") from exc


@router.get("/reports/operator/{operator_id}.pdf")
def operator_report(
    operator_id: int,
    request: Request,
    date_from: str | None = Query(None, description="ГГГГ-ММ-ДД, по умолчанию 30 дней назад"),
    date_to: str | None = Query(None, description="ГГГГ-ММ-ДД включительно, по умолчанию сегодня"),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Response:
    """PDF по оператору за период: метрики, тренд, частые ошибки, примеры звонков с маской ПДн. Только admin и analyst."""
    reject_operator(user)
    operator = session.get(Operator, operator_id)
    if operator is None:
        raise HTTPException(404, "Оператор не найден")
    today = utcnow().date()
    end = _day(date_to, today)
    start = _day(date_from, end - timedelta(days=29))
    if start > end:
        raise HTTPException(400, "Начало периода позже конца")
    if (end - start).days > MAX_DAYS:
        raise HTTPException(400, f"Период не длиннее {MAX_DAYS} дней")
    lo = datetime(start.year, start.month, start.day)
    hi = datetime(end.year, end.month, end.day) + timedelta(days=1)
    team = (
        session.query(Call)
        .options(joinedload(Call.quality), joinedload(Call.transcript))
        .filter(Call.status == "done", Call.call_at >= lo, Call.call_at < hi)
        .all()
    )
    team = [c for c in team if c.quality]
    mine = [c for c in team if c.operator_id == operator.id]
    by_op: dict[int, list[float]] = defaultdict(list)
    for c in team:
        if c.operator_id:
            by_op[c.operator_id].append(c.quality.overall)
    ranking = sorted(by_op, key=lambda oid: -(sum(by_op[oid]) / len(by_op[oid])))
    rank = ranking.index(operator.id) + 1 if operator.id in by_op else None
    pdf = build_operator_report(operator, mine, team, start, end, datetime.now(), rank, len(ranking))
    write_audit(
        session,
        "report.operator",
        user=user,
        detail=f"{operator.external_id or operator.id} {start.isoformat()}..{end.isoformat()} ({len(mine)} зв.)",
        request=request,
    )
    session.commit()
    ascii_name = f"report_{operator.external_id or operator.id}_{start.isoformat()}_{end.isoformat()}.pdf"
    ru_name = f"Отчёт {operator.name} {start.isoformat()}_{end.isoformat()}.pdf"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(ru_name)}",
            "Cache-Control": "no-store",
        },
    )
