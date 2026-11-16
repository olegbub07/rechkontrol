from __future__ import annotations

import uuid
from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload

from app.api.deps import current_user, db_session
from app.api.serializers import call_detail, call_item
from app.audio import AudioError, sniff_audio
from app.config import settings
from app.crypto import decrypt_bytes, encrypt_bytes
from app.diarize import assign_roles, from_whisper, label_speakers, merge_whisper_segments
from app.models import Call, Operator, User
from app.quality import analyze
from app.security import safe_audio_path, sanitize_filename
from app.transcribe import local_model_ready
from app.schemas import (
    AnalyzeTextIn,
    CallDetail,
    CallListItem,
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
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(db_session),
    _: User = Depends(current_user),
) -> dict:
    query = session.query(Call).options(joinedload(Call.operator), joinedload(Call.quality))
    if status:
        query = query.filter(Call.status == status)
    if operator_id:
        query = query.filter(Call.operator_id == operator_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(Call.orig_filename.ilike(like))
    total = query.count()
    rows = query.order_by(Call.call_at.desc(), Call.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [call_item(c) for c in rows]}


@router.get("/calls/{call_id}", response_model=CallDetail)
def get_call(call_id: int, session: Session = Depends(db_session), _: User = Depends(current_user)) -> CallDetail:
    call = (
        session.query(Call)
        .options(joinedload(Call.operator), joinedload(Call.quality), joinedload(Call.transcript))
        .filter(Call.id == call_id)
        .one_or_none()
    )
    if call is None:
        raise HTTPException(404, "Звонок не найден")
    return call_detail(call)


@router.post("/calls/upload", response_model=list[CallListItem])
async def upload_calls(
    files: list[UploadFile] = File(...),
    operator_id: int | None = Form(None),
    operator_name: str | None = Form(None),
    call_at: str | None = Form(None),
    operator_first: str = Form("true"),
    session: Session = Depends(db_session),
    _: User = Depends(current_user),
) -> list[CallListItem]:
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
    max_bytes = settings.max_upload_mb * 1024 * 1024
    for upload in files:
        data = await upload.read()
        if len(data) > max_bytes:
            raise HTTPException(413, f"{upload.filename}: файл больше {settings.max_upload_mb} МБ")
        if len(data) < 64:
            raise HTTPException(400, f"{upload.filename}: файл слишком маленький")
        try:
            mime = sniff_audio(data[:16], upload.filename or "audio.wav")
        except AudioError as exc:
            raise HTTPException(400, f"{upload.filename}: {exc}") from exc
        stored = f"{uuid.uuid4().hex}.bin"
        (settings.audio_dir / stored).write_bytes(encrypt_bytes(data))
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
    session.commit()
    for c in created:
        session.refresh(c)
    return [call_item(c) for c in created]


@router.get("/calls/{call_id}/audio")
def get_audio(call_id: int, session: Session = Depends(db_session), _: User = Depends(current_user)):
    call = session.get(Call, call_id)
    if call is None or not call.stored_name:
        raise HTTPException(404, "Аудио недоступно")
    try:
        path = safe_audio_path(call.stored_name)
    except ValueError as exc:
        raise HTTPException(404, "Файл не найден") from exc
    if not path.exists():
        raise HTTPException(404, "Файл не найден на диске")
    payload = decrypt_bytes(path.read_bytes())
    filename = sanitize_filename(call.orig_filename)
    headers = {
        "Content-Disposition": f'inline; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(BytesIO(payload), media_type=call.mime or "application/octet-stream", headers=headers)


@router.post("/calls/{call_id}/reprocess", response_model=CallListItem)
def reprocess(call_id: int, session: Session = Depends(db_session), _: User = Depends(current_user)) -> CallListItem:
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "Звонок не найден")
    if not call.stored_name:
        raise HTTPException(400, "Демо-звонок без аудио нельзя отправить в очередь Whisper")
    if call.status == "processing":
        raise HTTPException(409, "Звонок уже обрабатывается")
    call.status = "queued"
    call.progress = 0
    call.error_message = None
    session.commit()
    session.refresh(call)
    return call_item(call)


@router.delete("/calls/{call_id}")
def delete_call(call_id: int, session: Session = Depends(db_session), _: User = Depends(current_user)) -> dict:
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "Звонок не найден")
    if call.stored_name:
        try:
            path = safe_audio_path(call.stored_name)
            path.unlink(missing_ok=True)
        except ValueError:
            pass
    session.delete(call)
    return {"ok": True}


@router.get("/queue")
def queue_status(session: Session = Depends(db_session), _: User = Depends(current_user)) -> dict:
    queued = session.query(Call).filter(Call.status == "queued").count()
    processing = session.query(Call).filter(Call.status == "processing").all()
    failed = session.query(Call).filter(Call.status == "error").count()
    return {
        "queued": queued,
        "processing": [
            {"id": c.id, "filename": c.orig_filename, "progress": c.progress} for c in processing
        ],
        "failed": failed,
        "max_parallel_jobs": settings.max_parallel_jobs,
    }


@router.get("/operators", response_model=list[OperatorOut])
def list_operators(session: Session = Depends(db_session), _: User = Depends(current_user)) -> list[OperatorOut]:
    ops = session.query(Operator).order_by(Operator.name).all()
    out = []
    for op in ops:
        done = [c for c in op.calls if c.quality]
        avg = sum(c.quality.overall for c in done) / len(done) if done else None
        viol = sum(c.quality.violation_pct for c in done) / len(done) if done else None
        out.append(
            OperatorOut(
                id=op.id,
                name=op.name,
                external_id=op.external_id,
                calls_count=len(op.calls),
                avg_overall=round(avg, 1) if avg is not None else None,
                violation_pct=round(viol, 1) if viol is not None else None,
            )
        )
    return out


@router.post("/operators", response_model=OperatorOut)
def create_operator(
    body: OperatorIn, session: Session = Depends(db_session), _: User = Depends(current_user)
) -> OperatorOut:
    op = Operator(name=body.name.strip(), external_id=body.external_id or None)
    session.add(op)
    session.flush()
    return OperatorOut(id=op.id, name=op.name, external_id=op.external_id)


@router.get("/settings", response_model=SettingsOut)
def get_settings(session: Session = Depends(db_session), _: User = Depends(current_user)) -> SettingsOut:
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
    )


@router.put("/settings", response_model=SettingsOut)
def put_settings(
    body: SettingsIn, session: Session = Depends(db_session), _: User = Depends(current_user)
) -> SettingsOut:
    if body.company_phrases is not None:
        phrases = [p.strip()[:80] for p in body.company_phrases if p.strip()][:40]
        put_json(session, "company_phrases", phrases)
    if body.extra_script_phrases is not None:
        phrases = [p.strip()[:80] for p in body.extra_script_phrases if p.strip()][:40]
        put_json(session, "extra_script_phrases", phrases)
    return get_settings(session, _)


@router.post("/analyze-text", response_model=QualityOut)
def analyze_text(body: AnalyzeTextIn, session: Session = Depends(db_session), _: User = Depends(current_user)) -> QualityOut:
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
