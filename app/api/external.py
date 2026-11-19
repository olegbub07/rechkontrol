"""REST API для внешних систем (CRM, АТС, BI). Доступ по API-ключу в заголовке X-API-Key."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Security, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.api.deps import db_session
from app.api.serializers import _checks, _json_list, _mask_violation
from app.apikeys import has_scope, verify_key
from app.audit import write_audit
from app.config import settings
from app.db import write_lock
from app.ingest import CHUNK, IngestError, find_duplicate, queue_call, resolve_operator, store_chunks
from app.models import ApiKey, Call
from app.pii import mask_segments, mask_text
from app.security import CALL_STATUSES, sanitize_filename
from app.timeutil import to_local, utcnow

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Ключ из админки (раздел «Интеграции») или `python -m app.cli apikey create`.",
)

router = APIRouter(prefix="/v1", tags=["external-api"])

_ERRORS = {
    401: {"description": "Нет ключа, ключ неверный или отозван"},
    403: {"description": "У ключа нет нужного права (scope)"},
    429: {"description": "Слишком много запросов с адреса"},
}


def _require(scope: str):
    def dep(request: Request, key: str | None = Security(api_key_header), session: Session = Depends(db_session)) -> ApiKey:
        row = verify_key(session, key)
        if row is None:
            raise HTTPException(401, "Нужен действующий API-ключ в заголовке X-API-Key")
        if not has_scope(row, scope):
            raise HTTPException(403, f"У ключа нет права {scope}")
        now = utcnow()
        if row.last_used_at is None or (now - row.last_used_at).total_seconds() > 60:
            row.last_used_at = now
        request.state.api_key_name = row.name
        return row

    return dep


class OperatorRef(BaseModel):
    id: int
    external_id: str | None = None
    name: str


class CallStatusOut(BaseModel):
    id: int = Field(description="Номер звонка")
    status: Literal["queued", "processing", "done", "error"]
    progress: int = Field(description="0-100")
    source: str = Field(description="upload | inbox | api")
    filename: str
    call_at: datetime | None = None
    uploaded_at: datetime | None = None
    duration_sec: float | None = None
    operator: OperatorRef | None = None
    overall: float | None = Field(None, description="Итоговая оценка 0-100, когда status=done")
    error: str | None = None


class UploadOut(CallStatusOut):
    duplicate: bool = Field(False, description="Такой файл уже загружали: возвращён существующий звонок")


class ScoresOut(BaseModel):
    overall: float
    politeness: float
    completeness: float
    script_compliance: float
    resolution: float
    tonality: str
    tonality_score: float
    violation_pct: float
    avg_response_sec: float | None = None
    talk_ratio_operator: float


class ViolationOut(BaseModel):
    id: str
    title: str
    category: str = ""
    detail: str = ""


class CheckResultOut(BaseModel):
    id: str
    title: str
    category: str
    passed: bool
    score: float
    weight: float
    detail: str
    evidence: str | None = None


class TurnOut(BaseModel):
    start: float
    end: float
    role: Literal["operator", "client"]
    text: str = Field(description="С маскированием ПДн, если PII_MASKING=true")


class CallResultOut(BaseModel):
    call: CallStatusOut
    scores: ScoresOut
    violations: list[ViolationOut]
    checks: list[CheckResultOut]
    recommendations: list[str]
    summary: str
    transcript: list[TurnOut]


class CallListOut(BaseModel):
    total: int
    items: list[CallStatusOut]


def _status(call: Call) -> CallStatusOut:
    op = call.operator
    return CallStatusOut(
        id=call.id,
        status=call.status if call.status in CALL_STATUSES else "error",
        progress=int(call.progress or 0),
        source=call.source or "upload",
        filename=call.orig_filename,
        call_at=call.call_at,
        uploaded_at=to_local(call.uploaded_at),
        duration_sec=call.duration_sec,
        operator=OperatorRef(id=op.id, external_id=op.external_id, name=op.name) if op else None,
        overall=call.quality.overall if call.quality else None,
        error=call.error_message if call.status == "error" else None,
    )


def _load(session: Session, call_id: int) -> Call:
    call = (
        session.query(Call)
        .options(joinedload(Call.operator), joinedload(Call.quality), joinedload(Call.transcript))
        .filter(Call.id == call_id, Call.is_demo == 0)
        .one_or_none()
    )
    if call is None:
        raise HTTPException(404, "Звонок не найден")
    return call


@router.post(
    "/calls",
    response_model=UploadOut,
    status_code=202,
    summary="Загрузить запись звонка",
    responses={**_ERRORS, 200: {"model": UploadOut, "description": "Файл уже был загружен (дубликат по SHA-256)"}, 400: {"description": "Не аудиофайл"}, 413: {"description": "Файл больше MAX_UPLOAD_MB"}},
)
def upload_call(
    request: Request,
    file: UploadFile = File(..., description="WAV, MP3, OGG, FLAC или M4A"),
    operator_external_id: str | None = Form(None, description="Табельный код оператора, например OP-104"),
    operator_name: str | None = Form(None, description="Имя оператора, если кода нет"),
    call_at: datetime | None = Form(None, description="Время звонка, ISO 8601. По умолчанию из имени файла или сейчас"),
    operator_first: bool = Form(True, description="Первым говорит оператор (для моно-записей)"),
    session: Session = Depends(db_session),
    key: ApiKey = Depends(_require("calls:write")),
):
    """Ставит запись в очередь и сразу отвечает 202 с номером звонка. Результат: `GET /api/v1/calls/{id}/result`
    или вебхук `call.processed`. Повторная отправка того же файла не создаёт второй звонок."""
    filename = sanitize_filename(file.filename)

    def chunks():
        while True:
            block = file.file.read(CHUNK)
            if not block:
                return
            yield block

    try:
        stored = store_chunks(chunks(), filename)
    except IngestError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    try:
        with write_lock:
            dup = find_duplicate(session, stored.sha256)
            if dup is not None:
                stored.path.unlink(missing_ok=True)
                out = UploadOut(**_status(dup).model_dump(), duplicate=True)
                return JSONResponse(status_code=200, content=out.model_dump(mode="json"))
            op = resolve_operator(session, filename, operator_external_id, operator_name)
            when = call_at.replace(tzinfo=None) if call_at else None
            call = queue_call(session, stored, filename, "api", operator=op, call_at=when, operator_first=operator_first)
            write_audit(session, "call.api_upload", actor=f"api:{key.name}", detail=f"звонок №{call.id}: {call.orig_filename}", request=request)
            session.commit()
    except BaseException:
        session.rollback()
        stored.path.unlink(missing_ok=True)
        raise
    session.refresh(call)
    return UploadOut(**_status(call).model_dump())


@router.get("/calls", response_model=CallListOut, summary="Список звонков", responses=_ERRORS)
def list_calls(
    status: Literal["queued", "processing", "done", "error"] | None = Query(None),
    since_id: int | None = Query(None, ge=0, description="Только звонки с номером больше этого (для догоняющей выгрузки)"),
    operator_external_id: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(db_session),
    _key: ApiKey = Depends(_require("calls:read")),
) -> CallListOut:
    query = session.query(Call).options(joinedload(Call.operator), joinedload(Call.quality)).filter(Call.is_demo == 0)
    if status:
        query = query.filter(Call.status == status)
    if since_id is not None:
        query = query.filter(Call.id > since_id)
    if operator_external_id:
        from app.models import Operator

        query = query.join(Call.operator).filter(Operator.external_id == operator_external_id)
    total = query.count()
    rows = query.order_by(Call.id.asc() if since_id is not None else Call.id.desc()).limit(limit).all()
    return CallListOut(total=total, items=[_status(c) for c in rows])


@router.get("/calls/{call_id}", response_model=CallStatusOut, summary="Статус звонка", responses={**_ERRORS, 404: {"description": "Нет такого звонка"}})
def call_status(call_id: int, session: Session = Depends(db_session), _key: ApiKey = Depends(_require("calls:read"))) -> CallStatusOut:
    return _status(_load(session, call_id))


@router.get(
    "/calls/{call_id}/result",
    response_model=CallResultOut,
    summary="Результат: оценки, нарушения, расшифровка",
    responses={**_ERRORS, 404: {"description": "Нет такого звонка"}, 409: {"description": "Звонок ещё не обработан"}},
)
def call_result(
    call_id: int, request: Request, session: Session = Depends(db_session), key: ApiKey = Depends(_require("calls:read"))
) -> CallResultOut:
    """Расшифровка отдаётся с маскированием ПДн (карты, телефоны, ФИО), если оно включено. Каждое чтение пишется в журнал аудита."""
    call = _load(session, call_id)
    if call.status != "done" or call.quality is None:
        raise HTTPException(409, f"Звонок в статусе {call.status}, результата пока нет")
    q = call.quality
    masking = settings.pii_masking
    mask = mask_text if masking else (lambda v: v)
    raw = [s for s in _json_list(call.transcript.segments_json if call.transcript else None) if isinstance(s, dict)]
    segs = mask_segments(raw) if masking else raw
    transcript = [
        TurnOut(start=float(s.get("start") or 0), end=float(s.get("end") or 0), role="operator" if s.get("role") == "operator" else "client", text=str(s.get("text") or ""))
        for s in segs
    ]
    violations = []
    for v in _json_list(q.violations_json):
        if isinstance(v, dict):
            v = _mask_violation(v, mask)
            violations.append(ViolationOut(id=str(v.get("id") or ""), title=str(v.get("title") or ""), category=str(v.get("category") or ""), detail=str(v.get("detail") or "")))
    checks = [CheckResultOut(**c.model_dump(include=set(CheckResultOut.model_fields))) for c in _checks(_json_list(q.checks_json), mask)]
    write_audit(session, "call.api_result", actor=f"api:{key.name}", detail=f"звонок №{call.id}", request=request)
    return CallResultOut(
        call=_status(call),
        scores=ScoresOut(
            overall=q.overall,
            politeness=q.politeness,
            completeness=q.completeness,
            script_compliance=q.script_compliance,
            resolution=q.resolution,
            tonality=q.tonality,
            tonality_score=q.tonality_score,
            violation_pct=q.violation_pct,
            avg_response_sec=q.avg_response_sec,
            talk_ratio_operator=q.talk_ratio_operator,
        ),
        violations=violations,
        checks=checks,
        recommendations=[str(r) for r in _json_list(q.recommendations_json)],
        summary=mask(q.summary or ""),
        transcript=transcript,
    )
