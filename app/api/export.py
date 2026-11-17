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
from app.config import settings
from app.api.serializers import call_detail
from app.models import Call, User

router = APIRouter(tags=["export"])

TONALITY_RU = {"positive": "позитивная", "neutral": "нейтральная", "negative": "негативная"}
STATUS_RU = {"queued": "в очереди", "processing": "обрабатывается", "done": "готово", "error": "ошибка"}

# Время звонка хранится местным (DISPLAY_TZ), в заголовке это видно сразу.
_DT_TITLE = "Дата и время (МСК)" if (settings.display_tz or "Europe/Moscow") == "Europe/Moscow" else f"Дата и время ({settings.display_tz})"

CSV_COLUMNS = [
    ("call_id", "Идентификатор звонка"),
    ("datetime", _DT_TITLE),
    ("operator_id", "Идентификатор оператора"),
    ("operator_name", "Оператор"),
    ("filename", "Файл"),
    ("duration_sec", "Длительность, сек"),
    ("overall", "Итоговое качество"),
    ("politeness", "Вежливость"),
    ("completeness", "Полнота ответа"),
    ("script_compliance", "Соблюдение скрипта"),
    ("resolution", "Решение проблемы"),
    ("tonality_ru", "Тональность"),
    ("avg_response_sec", "Среднее время ответа, сек"),
    ("violation_pct", "Процент нарушений"),
    ("status_ru", "Статус"),
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


def _round(value, digits: int = 1):
    return round(float(value), digits) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def _flat(call: Call) -> dict:
    q = call.quality
    op = call.operator
    tonality = q.tonality if q else ""
    return {
        "call_id": call.id,
        "datetime": call.call_at.isoformat(sep=" ", timespec="minutes"),
        "operator_id": (op.external_id or str(op.id)) if op else "",
        "operator_name": op.name if op else "",
        "filename": call.orig_filename,
        "duration_sec": _round(call.duration_sec),
        "overall": q.overall if q else "",
        "politeness": q.politeness if q else "",
        "completeness": q.completeness if q else "",
        "script_compliance": q.script_compliance if q else "",
        "resolution": q.resolution if q else "",
        "tonality": tonality,
        "tonality_ru": TONALITY_RU.get(tonality or "", tonality or ""),
        "avg_response_sec": _round(q.avg_response_sec, 2) if q else "",
        "violation_pct": q.violation_pct if q else "",
        "status": call.status,
        "status_ru": STATUS_RU.get(call.status, call.status),
    }


def _ru_number(value):
    """Excel в русской локали ждёт запятую в дробях и точку с запятой между колонками."""
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",") if value >= 0 else value
    return value


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@router.get("/export/csv")
def export_csv(
    request: Request,
    operator_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    style: str = Query("ru", pattern="^(ru|standard)$"),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    """style=ru - «;» и запятая в дробях (русский Excel), style=standard - «,» и точка (LibreOffice, Python, BI)."""
    reject_operator(user)
    calls = _rows(session, operator_id, date_from, date_to)
    write_audit(session, "export.csv", user=user, detail=f"{len(calls)} {style}", request=request)
    session.commit()
    buf = io.StringIO()
    ru = style == "ru"
    writer = csv.writer(buf, delimiter=";" if ru else ",")
    writer.writerow([title for _, title in CSV_COLUMNS])
    for call in calls:
        row = _flat(call)
        writer.writerow([csv_cell(_ru_number(row[key]) if ru else row[key]) for key, _ in CSV_COLUMNS])
    payload = buf.getvalue().encode("utf-8-sig")
    suffix = "" if ru else "_standard"
    headers = {
        "Content-Disposition": f'attachment; filename="kpi_calls_{_stamp()}{suffix}.csv"',
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(io.BytesIO(payload), media_type="text/csv; charset=utf-8", headers=headers)


XLSX_FORMATS = {
    "datetime": "DD.MM.YYYY HH:MM",
    "duration_sec": "0.0",
    "overall": "0.0",
    "politeness": "0.0",
    "completeness": "0.0",
    "script_compliance": "0.0",
    "resolution": "0.0",
    "avg_response_sec": "0.00",
    "violation_pct": "0.0",
}


@router.get("/export/xlsx")
def export_xlsx(
    request: Request,
    operator_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    """Excel-файл: числа и даты - настоящие числа и даты, от языка системы не зависит."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    reject_operator(user)
    calls = _rows(session, operator_id, date_from, date_to)
    write_audit(session, "export.xlsx", user=user, detail=str(len(calls)), request=request)
    session.commit()
    wb = Workbook()
    ws = wb.active
    ws.title = "KPI звонков"
    ws.append([title for _, title in CSV_COLUMNS])
    for cell in ws[1]:
        cell.font = cell.font.copy(bold=True)
    for call in calls:
        row = _flat(call)
        row["datetime"] = call.call_at
        values = []
        for key, _ in CSV_COLUMNS:
            value = row[key]
            values.append(None if value == "" else value)
        ws.append(values)
        r = ws.max_row
        for col, (key, _) in enumerate(CSV_COLUMNS, 1):
            cell = ws.cell(row=r, column=col)
            if isinstance(cell.value, str):
                cell.data_type = "s"  # текст вида «=...» не превращается в формулу
            if key in XLSX_FORMATS and cell.value is not None:
                cell.number_format = XLSX_FORMATS[key]
    for col, (_, title) in enumerate(CSV_COLUMNS, 1):
        ws.column_dimensions[get_column_letter(col)].width = max(12, min(40, len(title) + 2))
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    headers = {
        "Content-Disposition": f'attachment; filename="kpi_calls_{_stamp()}.xlsx"',
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        io.BytesIO(out.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get("/export/json")
def export_json(
    request: Request,
    operator_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    full: bool = Query(False),
    unmasked: bool = Query(False),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    reject_operator(user)
    if full and user.role != "admin":
        raise HTTPException(403, "Полный экспорт с расшифровками доступен администратору")
    if unmasked and not full:
        raise HTTPException(400, "Параметр unmasked имеет смысл только с full=true")
    calls = _rows(session, operator_id, date_from, date_to)
    write_audit(session, "export.json", user=user, detail=f"{len(calls)} full={int(full)} unmasked={int(unmasked)}", request=request)
    session.commit()
    if full:
        data = [json.loads(call_detail(c, unmasked=unmasked).model_dump_json()) for c in calls]
    else:
        data = [_flat(c) for c in calls]
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    kind = "calls_full_unmasked" if unmasked else "calls_full" if full else "kpi_calls"
    name = f"{kind}_{_stamp()}.json"
    headers = {
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(io.BytesIO(payload), media_type="application/json", headers=headers)
