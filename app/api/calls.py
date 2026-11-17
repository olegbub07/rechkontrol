from __future__ import annotations

import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.access import is_operator, limit_calls, reject_operator, require_call
from app.api.deps import current_user, db_session, require_admin
from app.api.serializers import call_detail, call_item, operator_out
from app.audio import AudioError, sniff_audio
from app.audit import write_audit
from app.config import settings
from app.crypto import decrypt_bytes, encrypt_bytes
from app.db import after_commit, write_lock
from app.diarize import assign_roles, label_speakers, merge_whisper_segments
from app.models import Call, Operator, Transcript, User
from app.quality import analyze
from app.security import (
    CALL_STATUSES,
    content_disposition,
    escape_like,
    safe_audio_mime,
    safe_audio_path,
    sanitize_filename,
)
from app.transcribe import local_model_ready
from app.schemas import (
    AnalyzeTextIn,
    CallDetail,
    CallListItem,
    CallPatch,
    CheckOut,
    OperatorIn,
    OperatorOut,
    QualityOut,
    SettingsIn,
    SettingsOut,
)
from app.settings_store import load_script, put_json

router = APIRouter(tags=["calls"])


def _get_or_create_operator(session: Session, operator_id: int | None, operator_name: str | None) -> Operator | None:
    if operator_id:
        op = session.get(Operator, operator_id)
        if op is None:
            raise HTTPException(400, "Оператор не найден")
        return op
    name = (operator_name or "").strip()
    if not name:
        return None
    existing = session.query(Operator).filter(Operator.name == name).one_or_none()
    if existing:
        return existing
    op = Operator(name=name)
    session.add(op)
    session.flush()
    return op


@router.get("/calls", response_model=dict)
def list_calls(
    status: str | None = None,
    operator_id: int | None = None,
    q: str | None = Query(default=None, max_length=120),
    flagged: bool | None = None,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> dict:
    query = limit_calls(
        session.query(Call).options(joinedload(Call.operator), joinedload(Call.quality)),
        user,
    )
    if status:
        if status not in CALL_STATUSES:
            raise HTTPException(400, "Неизвестный статус")
        query = query.filter(Call.status == status)
    if operator_id:
        query = query.filter(Call.operator_id == operator_id)
    if flagged is not None:
        query = query.filter(Call.flagged == (1 if flagged else 0))
    if q and q.strip():
        like = f"%{escape_like(q.strip())}%"
        text_ids = session.query(Transcript.call_id).filter(Transcript.full_text.ilike(like, escape="\\"))
        query = query.filter(or_(Call.orig_filename.ilike(like, escape="\\"), Call.id.in_(text_ids)))
    total = query.count()
    offset = min((page - 1) * page_size, 100_000)
    rows = query.order_by(Call.call_at.desc(), Call.id.desc()).offset(offset).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [call_item(c) for c in rows]}


@router.get("/calls/{call_id}", response_model=CallDetail)
def get_call(call_id: int, session: Session = Depends(db_session), user: User = Depends(current_user)) -> CallDetail:
    call = (
        session.query(Call)
        .options(joinedload(Call.operator), joinedload(Call.quality), joinedload(Call.transcript))
        .filter(Call.id == call_id)
        .one_or_none()
    )
    require_call(call, user)
    return call_detail(call)


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = await upload.read(1024 * 256)
        if not block:
            break
        total += len(block)
        if total > max_bytes:
            raise HTTPException(413, f"{upload.filename}: файл больше {settings.max_upload_mb} МБ")
        chunks.append(block)
    return b"".join(chunks)


@router.post("/calls/upload", response_model=list[CallListItem])
async def upload_calls(
    request: Request,
    files: list[UploadFile] = File(...),
    operator_id: int | None = Form(None),
    operator_name: str | None = Form(None),
    call_at: str | None = Form(None),
    operator_first: str = Form("true"),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[CallListItem]:
    reject_operator(user)
    if not files:
        raise HTTPException(400, "Добавьте хотя бы один аудиофайл")
    if len(files) > settings.max_upload_files:
        raise HTTPException(400, f"За один раз можно загрузить не больше {settings.max_upload_files} файлов")
    when = datetime.utcnow()
    if call_at:
        try:
            when = datetime.fromisoformat(call_at.replace("Z", ""))
        except ValueError as exc:
            raise HTTPException(400, "Некорректная дата звонка") from exc
    op = _get_or_create_operator(session, operator_id, operator_name)
    created: list[Call] = []
    stored_paths: list[Path] = []
    max_bytes = settings.max_upload_mb * 1024 * 1024
    try:
        for upload in files:
            data = await _read_limited(upload, max_bytes)
            if len(data) < 64:
                raise HTTPException(400, f"{upload.filename}: файл слишком маленький")
            try:
                mime = sniff_audio(data[:16], upload.filename or "audio.wav")
            except AudioError as exc:
                raise HTTPException(400, f"{upload.filename}: {exc}") from exc
            stored = f"{uuid.uuid4().hex}.bin"
            path = settings.audio_dir / stored
            path.write_bytes(encrypt_bytes(data))
            stored_paths.append(path)
            call = Call(
                orig_filename=sanitize_filename(upload.filename),
                stored_name=stored,
                mime=mime,
                operator_id=op.id if op else None,
                call_at=when,
                status="queued",
                progress=0,
                is_demo=0,
                operator_first=1 if operator_first.lower() in {"1", "true", "yes", "on"} else 0,
            )
            session.add(call)
            session.flush()
            created.append(call)
        write_audit(session, "call.upload", user=user, detail=f"{len(created)} файл(ов)", request=request)
        session.commit()
    except Exception:
        session.rollback()
        for path in stored_paths:
            path.unlink(missing_ok=True)
        raise
    for item in created:
        session.refresh(item)
    return [call_item(item) for item in created]


@router.get("/calls/{call_id}/audio")
def get_audio(call_id: int, session: Session = Depends(db_session), user: User = Depends(current_user)):
    call = require_call(session.get(Call, call_id), user)
    if not call.stored_name:
        raise HTTPException(404, "Аудио недоступно")
    try:
        path = safe_audio_path(call.stored_name)
    except ValueError as exc:
        raise HTTPException(404, "Файл не найден") from exc
    if not path.exists():
        raise HTTPException(404, "Файл не найден на диске")
    payload = decrypt_bytes(path.read_bytes())
    headers = {
        "Content-Disposition": content_disposition(call.orig_filename),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(BytesIO(payload), media_type=safe_audio_mime(call.mime), headers=headers)


@router.post("/calls/{call_id}/reprocess", response_model=CallListItem)
def reprocess(
    call_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> CallListItem:
    reject_operator(user)
    with write_lock:
        call = require_call(session.get(Call, call_id), user)
        if not call.stored_name:
            raise HTTPException(400, "Демо-звонок без аудио нельзя отправить в очередь Whisper")
        if call.status == "processing":
            raise HTTPException(409, "Звонок уже обрабатывается")
        call.status = "queued"
        call.progress = 0
        call.error_message = None
        write_audit(session, "call.reprocess", user=user, detail=str(call.id), request=request)
        session.commit()
        session.refresh(call)
        return call_item(call)


@router.post("/calls/retry-failed")
def retry_failed(
    request: Request,
    session: Session = Depends(db_session),
    user: User = Depends(require_admin),
) -> dict:
    with write_lock:
        rows = session.query(Call).filter(Call.status == "error", Call.stored_name.isnot(None)).all()
        for call in rows:
            call.status = "queued"
            call.progress = 0
            call.error_message = None
        write_audit(session, "call.retry_failed", user=user, detail=str(len(rows)), request=request)
        session.commit()
        return {"retried": len(rows)}


@router.patch("/calls/{call_id}", response_model=CallDetail)
def patch_call(
    call_id: int,
    body: CallPatch,
    request: Request,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> CallDetail:
    call = (
        session.query(Call)
        .options(joinedload(Call.operator), joinedload(Call.quality), joinedload(Call.transcript))
        .filter(Call.id == call_id)
        .one_or_none()
    )
    require_call(call, user)
    if is_operator(user):
        raise HTTPException(403, "Заметку ставит руководитель")
    if body.review_note is not None:
        call.review_note = body.review_note.strip()[:2000]
    if body.flagged is not None:
        call.flagged = 1 if body.flagged else 0
    write_audit(session, "call.review", user=user, detail=str(call.id), request=request)
    session.commit()
    session.refresh(call)
    return call_detail(call)


@router.delete("/calls/{call_id}")
def delete_call(
    call_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user: User = Depends(require_admin),
) -> dict:
    with write_lock:
        call = session.get(Call, call_id)
        if call is None:
            raise HTTPException(404, "Звонок не найден")
        if call.status == "processing":
            raise HTTPException(409, "Дождитесь окончания обработки, затем удалите звонок")
        if call.stored_name:
            try:
                path = safe_audio_path(call.stored_name)
            except ValueError:
                path = None
            if path is not None:
                after_commit(session, lambda target=path: target.unlink(missing_ok=True))
        write_audit(session, "call.delete", user=user, detail=call.orig_filename[:180], request=request)
        session.delete(call)
        session.commit()
        return {"ok": True}


@router.get("/queue")
def queue_status(session: Session = Depends(db_session), user: User = Depends(current_user)) -> dict:
    def scoped(status: str):
        return limit_calls(session.query(Call).filter(Call.status == status), user)

    queued = scoped("queued").count()
    processing = scoped("processing").all()
    failed = scoped("error").count()
    return {
        "queued": queued,
        "processing": [
            {"id": c.id, "filename": c.orig_filename, "progress": c.progress} for c in processing
        ],
        "failed": failed,
        "max_parallel_jobs": settings.max_parallel_jobs,
    }


@router.get("/operators", response_model=list[OperatorOut])
def list_operators(session: Session = Depends(db_session), user: User = Depends(current_user)) -> list[OperatorOut]:
    query = session.query(Operator).order_by(Operator.name)
    if is_operator(user):
        if user.operator_id is None:
            return []
        query = query.filter(Operator.id == user.operator_id)
    ops = query.all()
    return [operator_out(op, [c for c in op.calls if (not is_operator(user) or c.operator_id == user.operator_id)]) for op in ops]


@router.post("/operators", response_model=OperatorOut)
def create_operator(
    body: OperatorIn, session: Session = Depends(db_session), user: User = Depends(current_user)
) -> OperatorOut:
    reject_operator(user)
    op = Operator(name=body.name.strip(), external_id=(body.external_id or "").strip() or None)
    session.add(op)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "Оператор с таким внешним ID уже есть") from exc
    return OperatorOut(id=op.id, name=op.name, external_id=op.external_id)


@router.get("/settings", response_model=SettingsOut)
def get_settings(session: Session = Depends(db_session), user: User = Depends(current_user)) -> SettingsOut:
    reject_operator(user)
    script = load_script(session)
    return SettingsOut(
        whisper_model=settings.whisper_model,
        whisper_device=settings.whisper_device,
        max_parallel_jobs=settings.max_parallel_jobs,
        max_upload_mb=settings.max_upload_mb,
        max_duration_sec=settings.max_duration_sec,
        local_offline=local_model_ready(),
        company_phrases=script["company_phrases"],
        extra_script_phrases=script["extra_script_phrases"],
        whisper_beam_size=settings.whisper_beam_size,
        whisper_batch_size=settings.whisper_batch_size,
    )


@router.put("/settings", response_model=SettingsOut)
def put_settings(
    body: SettingsIn,
    request: Request,
    session: Session = Depends(db_session),
    user: User = Depends(require_admin),
) -> SettingsOut:
    if body.company_phrases is not None:
        phrases = [p.strip()[:80] for p in body.company_phrases if p.strip()][:40]
        put_json(session, "company_phrases", phrases)
    if body.extra_script_phrases is not None:
        phrases = [p.strip()[:80] for p in body.extra_script_phrases if p.strip()][:40]
        put_json(session, "extra_script_phrases", phrases)
    write_audit(session, "settings.script", user=user, request=request)
    return get_settings(session, user)


@router.post("/analyze-text", response_model=QualityOut)
def analyze_text(body: AnalyzeTextIn, session: Session = Depends(db_session), user: User = Depends(current_user)) -> QualityOut:
    reject_operator(user)
    script = load_script(session)
    if body.segments:
        turns = [s.model_dump() for s in body.segments]
    elif body.text:
        chunks = [{"start": i * 4.0, "end": i * 4.0 + 3.5, "text": line.strip()} for i, line in enumerate(body.text.splitlines()) if line.strip()]
        turns = assign_roles(label_speakers(merge_whisper_segments(chunks, gap=10)), operator_first=body.operator_first)
    else:
        raise HTTPException(400, "Передайте текст или сегменты")
    report = analyze(turns, script["company_phrases"], script["extra_script_phrases"])
    return QualityOut(
        politeness=report.politeness,
        completeness=report.completeness,
        script_compliance=report.script_compliance,
        resolution=report.resolution,
        overall=report.overall,
        tonality=report.tonality,
        tonality_score=report.tonality_score,
        avg_response_sec=report.avg_response_sec,
        talk_ratio_operator=report.talk_ratio_operator,
        violation_pct=report.violation_pct,
        violations=report.violations,
        recommendations=report.recommendations,
        checks=[CheckOut(**c.__dict__) for c in report.checks],
        summary=report.summary,
    )
