from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.audio import AudioError, to_wav_mono_16k
from app.config import settings
from app.crypto import decrypt_file
from app.db import SessionLocal, write_lock
from app.diarize import from_whisper
from app.models import Call, Quality, Transcript
from app.quality import analyze, report_to_json
from app.security import safe_audio_path, safe_tmp_path
from app.settings_store import load_script
from app.transcribe import transcribe_wav

log = logging.getLogger("rechkontrol.worker")

_stop = threading.Event()
_claim_lock = threading.Lock()
_threads: list[threading.Thread] = []


def _set_progress(session: Session, call: Call, value: int) -> None:
    call.progress = max(call.progress, min(value, 99))
    session.commit()


def claim_next() -> int | None:
    with _claim_lock, write_lock, SessionLocal() as session:
        call = (
            session.query(Call)
            .filter(Call.status == "queued")
            .order_by(Call.uploaded_at.asc(), Call.id.asc())
            .first()
        )
        if call is None:
            return None
        call.status = "processing"
        call.progress = 5
        call.error_message = None
        session.commit()
        return call.id


def process_call(call_id: int) -> None:
    started = time.time()
    tmp_src: Path | None = None
    tmp_wav: Path | None = None
    with SessionLocal() as session:
        call = session.get(Call, call_id)
        if call is None:
            return
        if not call.stored_name:
            call.status = "error"
            call.error_message = "Нет аудиофайла для обработки."
            session.commit()
            return
        token = uuid.uuid4().hex
        tmp_src = safe_tmp_path(f"{token}_src")
        tmp_wav = safe_tmp_path(f"{token}.wav")
        try:
            enc_path = safe_audio_path(call.stored_name)
            _set_progress(session, call, 10)
            decrypt_file(enc_path, tmp_src)
            _set_progress(session, call, 18)
            duration = to_wav_mono_16k(tmp_src, tmp_wav)
            call.duration_sec = duration
            session.commit()

            def on_progress(pct: int) -> None:
                with SessionLocal() as inner:
                    current = inner.get(Call, call_id)
                    if current and current.status == "processing":
                        current.progress = pct
                        inner.commit()

            raw, lang = transcribe_wav(tmp_wav, duration=duration, progress=on_progress)
            script = load_script(session)
            turns = from_whisper(raw, operator_first=bool(call.operator_first))
            report = analyze(
                turns,
                company_phrases=script["company_phrases"],
                extra_script_phrases=script["extra_script_phrases"],
            )
            full_text = " ".join(t["text"] for t in turns).strip()
            if call.transcript:
                session.delete(call.transcript)
                session.flush()
            if call.quality:
                session.delete(call.quality)
                session.flush()
            session.add(
                Transcript(
                    call_id=call.id,
                    full_text=full_text,
                    segments_json=json.dumps(turns, ensure_ascii=False),
                    language=lang,
                )
            )
            payload = report_to_json(report)
            session.add(
                Quality(
                    call_id=call.id,
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
                    summary=report.summary,
                    **payload,
                )
            )
            call.language = lang
            call.status = "done"
            call.progress = 100
            call.processed_sec = round(time.time() - started, 2)
            call.error_message = None
            session.commit()
            log.info("call %s done in %.1fs, overall=%.1f", call_id, call.processed_sec, report.overall)
        except Exception as exc:
            log.exception("call %s failed", call_id)
            call.status = "error"
            if isinstance(exc, (AudioError, ValueError)):
                call.error_message = str(exc)[:400]
            else:
                call.error_message = "Обработка не удалась. Проверьте файл и повторите попытку."
            session.commit()
        finally:
            for item in (tmp_src, tmp_wav):
                if item is not None:
                    item.unlink(missing_ok=True)


def _loop() -> None:
    while not _stop.is_set():
        call_id = claim_next()
        if call_id is None:
            _stop.wait(0.6)
            continue
        process_call(call_id)


def recover_stale_jobs() -> None:
    with write_lock, SessionLocal() as session:
        n = (
            session.query(Call)
            .filter(Call.status == "processing")
            .update({Call.status: "queued", Call.progress: 0, Call.error_message: None})
        )
        session.commit()
        if n:
            log.info("re-queued %s interrupted job(s)", n)


def start_workers() -> None:
    if _threads:
        return
    recover_stale_jobs()
    _stop.clear()
    n = max(1, int(settings.max_parallel_jobs))
    for i in range(n):
        t = threading.Thread(target=_loop, name=f"stt-worker-{i}", daemon=True)
        t.start()
        _threads.append(t)
    log.info("started %s worker(s)", n)


def stop_workers() -> None:
    _stop.set()
