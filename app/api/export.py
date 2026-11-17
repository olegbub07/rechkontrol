from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload

from app.access import reject_operator
from app.api.deps import current_user, db_session
from app.audit import write_audit
from app.api.serializers import call_detail
from app.models import Call, User

router = APIRouter(tags=["export"])

CSV_COLUMNS = [
    ("call_id", "Идентификатор звонка"),
    ("datetime", "Дата и время"),
    ("operator_id", "Идентификатор оператора"),
    ("operator_name", "Оператор"),
    ("filename", "Файл"),
    ("duration_sec", "Длительность, сек"),
    ("overall", "Итоговое качество"),
    ("politeness", "Вежливость"),
    ("completeness", "Полнота ответа"),
    ("script_compliance", "Соблюдение скрипта"),
    ("resolution", "Решение проблемы"),
    ("tonality", "Тональность"),
    ("avg_response_sec", "Среднее время ответа"),
    ("violation_pct", "Процент нарушений"),
    ("status", "Статус"),
]
CSV_FIELDS = [key for key, _ in CSV_COLUMNS]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError as exc:
        raise HTTPException(400, "Некорректная дата фильтра") from exc


def _rows(session: Session, operator_id: int | None, date_from: str | None, date_to: str | None) -> list[Call]:
    q = session.query(Call).options(joinedload(Call.operator), joinedload(Call.quality), joinedload(Call.transcript))
    start = _parse_dt(date_from)
    end = _parse_dt(date_to)
    if operator_id:
        q = q.filter(Call.operator_id == operator_id)
    if start:
        q = q.filter(Call.call_at >= start)
    if end:
        q = q.filter(Call.call_at <= end)
    return q.order_by(Call.call_at.desc()).limit(5000).all()


def csv_cell(value):
    """Числа остаются числами. Текст, похожий на формулу Excel, не исполняется."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + text
    return text


def _flat(call: Call) -> dict:
    q = call.quality
    op = call.operator
    return {
        "call_id": call.id,
        "datetime": call.call_at.isoformat(sep=" ", timespec="minutes"),
        "operator_id": (op.external_id or str(op.id)) if op else "",
        "operator_name": op.name if op else "",
        "filename": call.orig_filename,
        "duration_sec": call.duration_sec,
        "overall": q.overall if q else "",
        "politeness": q.politeness if q else "",
        "completeness": q.completeness if q else "",
        "script_compliance": q.script_compliance if q else "",
        "resolution": q.resolution if q else "",
        "tonality": q.tonality if q else "",
        "avg_response_sec": q.avg_response_sec if q else "",
        "violation_pct": q.violation_pct if q else "",
        "status": call.status,
    }


@router.get("/export/csv")
def export_csv(
    request: Request,
    operator_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    reject_operator(user)
    calls = _rows(session, operator_id, date_from, date_to)
    write_audit(session, "export.csv", user=user, detail=str(len(calls)), request=request)
    session.commit()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([title for _, title in CSV_COLUMNS])
    for call in calls:
        row = _flat(call)
        writer.writerow([csv_cell(row[key]) for key, _ in CSV_COLUMNS])
    payload = buf.getvalue().encode("utf-8-sig")
    headers = {
        "Content-Disposition": 'attachment; filename="kpi_calls.csv"',
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(io.BytesIO(payload), media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/export/json")
def export_json(
    request: Request,
    operator_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    full: bool = Query(False),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    reject_operator(user)
    if full and user.role != "admin":
        raise HTTPException(403, "Полный экспорт с расшифровками доступен администратору")
    calls = _rows(session, operator_id, date_from, date_to)
    write_audit(session, "export.json", user=user, detail=f"{len(calls)} full={int(full)}", request=request)
    session.commit()
    if full:
        data = [json.loads(call_detail(c).model_dump_json()) for c in calls]
    else:
        data = [_flat(c) for c in calls]
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    headers = {
        "Content-Disposition": 'attachment; filename="kpi_calls.json"',
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(io.BytesIO(payload), media_type="application/json", headers=headers)
