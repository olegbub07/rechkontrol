from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.access import is_operator, limit_calls
from app.analytics import summary, top_mistakes, trend_delta, weekly_trend
from app.api.deps import current_user, db_session
from app.audit import write_audit
from app.models import Call, Operator, User
from app.timeutil import utcnow

router = APIRouter(tags=["operators"])
PERIODS = {"7d": 7, "30d": 30, "90d": 90, "all": None}


def _parse_day(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label}: нужна дата ГГГГ-ММ-ДД")


def compare_data(session: Session, user: User, period: str, date_from: str | None, date_to: str | None) -> dict:
    """Сводка по операторам за период. Свой диапазон дат (date_from/date_to) важнее period."""
    start_day = _parse_day(date_from, "date_from")
    end_day = _parse_day(date_to, "date_to")
    if start_day and end_day and start_day > end_day:
        raise HTTPException(status_code=400, detail="Начало периода позже конца")
    custom = bool(start_day or end_day)
    if custom:
        period = "custom"
        days = (end_day - start_day).days + 1 if start_day and end_day else None
    else:
        if period not in PERIODS:
            period = "30d"
        days = PERIODS[period]
    base = limit_calls(session.query(Call).filter(Call.status == "done"), user)
    if custom:
        if start_day:
            base = base.filter(Call.call_at >= datetime(start_day.year, start_day.month, start_day.day))
        if end_day:
            base = base.filter(Call.call_at < datetime(end_day.year, end_day.month, end_day.day) + timedelta(days=1))
    elif days:
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
        "date_from": start_day.isoformat() if start_day else None,
        "date_to": end_day.isoformat() if end_day else None,
        "scope": "self" if mine else "team",
        "operators": rows,
        "unassigned": 0 if mine else unassigned,
        "data_from": span[0].date().isoformat() if span[0] else None,
        "data_to": span[1].date().isoformat() if span[1] else None,
        "period_days": days,
        "team": None if mine else {**summary(all_done), "trend": weekly_trend(all_done), "top_mistakes": top_mistakes(all_done, 5)},
    }


@router.get("/operators/compare")
def compare_operators(
    period: str = Query("30d"),
    date_from: str | None = Query(None, description="ГГГГ-ММ-ДД, свой период вместо period"),
    date_to: str | None = Query(None, description="ГГГГ-ММ-ДД включительно"),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> dict:
    """Сравнение операторов: рейтинг, средние по группам, грубость, тренд по неделям, частые ошибки.
    Оператор видит только себя."""
    return compare_data(session, user, period, date_from, date_to)


SUMMARY_COLUMNS = (
    ("rank", "Место"),
    ("name", "Оператор"),
    ("external_id", "Код"),
    ("calls_done", "Звонков"),
    ("overall", "Итог"),
    ("politeness", "Вежливость"),
    ("completeness", "Полнота"),
    ("script_compliance", "Скрипт"),
    ("resolution", "Решение"),
    ("violation_pct", "Нарушения, %"),
    ("rude_calls", "Звонков с грубостью"),
    ("delta", "К пред. неделе"),
    ("mistakes", "Частые ошибки"),
)


@router.get("/operators/compare.xlsx")
def compare_operators_xlsx(
    request: Request,
    period: str = Query("30d"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    """Та же сводка по операторам в Excel. Оператор получает файл только про себя."""
    from openpyxl import Workbook

    data = compare_data(session, user, period, date_from, date_to)
    write_audit(session, "export.operators_xlsx", user=user, detail=str(len(data["operators"])), request=request)
    session.commit()
    wb = Workbook()
    ws = wb.active
    ws.title = "Операторы"
    label = (
        f"Период: {data['date_from'] or '…'} - {data['date_to'] or '…'}"
        if data["period"] == "custom"
        else f"Период: {data['period']}"
    )
    ws.append([label])
    ws.append([t for _, t in SUMMARY_COLUMNS])
    for cell in ws[2]:
        cell.font = cell.font.copy(bold=True)
    for r in data["operators"]:
        row = dict(r, mistakes="; ".join(f"{m['title']} ({m['share']:.0f}%)" for m in r["top_mistakes"]))
        ws.append([row.get(k) for k, _ in SUMMARY_COLUMNS])
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            elif isinstance(cell.value, float):
                cell.number_format = "0.0"
    widths = (7, 28, 10, 9, 8, 11, 9, 8, 9, 12, 12, 13, 60)
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(ord("A") + i)].width = w
    ws.freeze_panes = "A3"
    team = data.get("team")
    if team:
        ts = wb.create_sheet("Команда")
        ts.append(["Звонков", "Итог", "Вежливость", "Полнота", "Скрипт", "Решение", "Нарушения, %", "Звонков с грубостью"])
        ts.append([team["calls_done"], team["overall"], team["politeness"], team["completeness"], team["script_compliance"],
                   team["resolution"], team["violation_pct"], team["rude_calls"]])
        ts.append([])
        ts.append(["Частые ошибки команды", "Звонков", "Доля, %"])
        for m in team["top_mistakes"]:
            ts.append([m["title"], m["calls"], m["share"]])
        ts.column_dimensions["A"].width = 36
    out = io.BytesIO()
    wb.save(out)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        io.BytesIO(out.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="operators_{stamp}.xlsx"', "X-Content-Type-Options": "nosniff"},
    )
