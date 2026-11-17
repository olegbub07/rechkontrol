from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path

from app.audio import AudioError, read_stereo_16k, to_wav_mono_16k
from app.config import settings
from app.crypto import decrypt_file
from app.db import SessionLocal, write_lock
from app.diarize import from_whisper
from app.models import Call, Quality, Transcript
from app.llm import enrich as llm_enrich
from app.quality import analyze, report_to_json
from app.security import safe_audio_path, safe_tmp_path
from app.settings_store import load_script
from app.transcribe import transcribe_wav

log = logging.getLogger("rechkontrol.worker")

_stop = threading.Event()
_claim_lock = threading.Lock()
_threads: list[threading.Thread] = []


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


def _touch_progress(call_id: int, value: int) -> None:
    with write_lock, SessionLocal() as session:
        current = session.get(Call, call_id)
        if current and current.status == "processing":
            current.progress = max(current.progress, min(value, 99))
            session.commit()


def process_call(call_id: int) -> None:
    started = time.time()
    tmp_src: Path | None = None
    tmp_wav: Path | None = None
    with write_lock, SessionLocal() as session:
        call = session.get(Call, call_id)
        if call is None:
            return
        if not call.stored_name:
            call.status = "error"
            call.error_message = "Нет аудиофайла для обработки."
            session.commit()
            return
        stored_name = call.stored_name
        operator_first = bool(call.operator_first)
        call.progress = 8
        session.commit()

    token = uuid.uuid4().hex
    tmp_src = safe_tmp_path(f"{token}_src")
    tmp_wav = safe_tmp_path(f"{token}.wav")
    last_tick = 0.0

    def on_progress(pct: int) -> None:
        nonlocal last_tick
        now = time.monotonic()
        if pct < 88 and now - last_tick < 1.2:
            return
        last_tick = now
        _touch_progress(call_id, pct)

    try:
        enc_path = safe_audio_path(stored_name)
        on_progress(12)
        decrypt_file(enc_path, tmp_src)
        on_progress(18)
        duration = to_wav_mono_16k(tmp_src, tmp_wav)
        stereo = read_stereo_16k(tmp_src) if settings.stereo_diarization else None
        with write_lock, SessionLocal() as session:
            call = session.get(Call, call_id)
            if call is None or call.status != "processing":
                return
            call.duration_sec = duration
            call.progress = 20
            session.commit()
            script = load_script(session)
            company = list(script["company_phrases"])
            extra = list(script["extra_script_phrases"])
            checklist = script["checklist"]
        raw, lang, audio, sr = transcribe_wav(tmp_wav, duration=duration, progress=on_progress)
        turns = from_whisper(
            raw,
            operator_first=operator_first,
            audio=audio,
            sr=sr,
            stereo=stereo,
            operator_channel=settings.stereo_operator_channel,
        )
        del audio, stereo
        report = analyze(turns, company_phrases=company, extra_script_phrases=extra, checklist=checklist)
        report = llm_enrich(report, turns, checklist)
        full_text = " ".join(t["text"] for t in turns).strip()
        payload = report_to_json(report)
        with write_lock, SessionLocal() as session:
            call = session.get(Call, call_id)
            if call is None or call.status != "processing":
                return
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
            call.duration_sec = duration
            call.status = "done"
            call.progress = 100
            call.processed_sec = round(time.time() - started, 2)
            call.error_message = None
            session.commit()
            log.info("call %s done in %.1fs, overall=%.1f", call_id, call.processed_sec, report.overall)
    except Exception as exc:
        log.exception("call %s failed", call_id)
        with write_lock, SessionLocal() as session:
            call = session.get(Call, call_id)
            if call is None:
                return
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
    _threads[:] = [t for t in _threads if t.is_alive()]
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
    for thread in list(_threads):
        thread.join(timeout=3)
    _threads[:] = [thread for thread in _threads if thread.is_alive()]
