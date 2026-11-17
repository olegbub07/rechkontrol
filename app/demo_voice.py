"""Голос для демо-звонков: локальная модель Silero, без отправки текста в сеть после скачивания."""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import tempfile
import threading
import uuid

import numpy as np
import soundfile as sf

from app.config import settings
from app.crypto import encrypt_bytes
from app.db import SessionLocal, write_lock
from app.models import Call

log = logging.getLogger("rechkontrol.voice")

MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
SAMPLE_RATE = 48000
OUT_RATE = 16000
_model = None
_model_lock = threading.Lock()


def _model_path():
    return settings.models_dir / "silero-ru-v4.pt"


def _torch_path(path) -> str:
    """torch.package на Windows не открывает путь с нелатинскими буквами."""
    text = str(path)
    try:
        text.encode("ascii")
        return text
    except UnicodeEncodeError:
        dest = os.path.join(tempfile.gettempdir(), "rechkontrol-silero-ru-v4.pt")
        if not os.path.exists(dest) or os.path.getsize(dest) != path.stat().st_size:
            shutil.copy2(path, dest)
        return dest


def _load_model():
    global _model
    with _model_lock:
        if _model is not None:
            return _model
        path = _model_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.stat().st_size < 1_000_000:
            import urllib.request

            log.info("Скачиваю русскую модель голоса для прослушивания демо-звонков")
            urllib.request.urlretrieve(MODEL_URL, path)
        import torch

        importer = torch.package.PackageImporter(_torch_path(path))
        model = importer.load_pickle("tts_models", "model")
        model.to(torch.device("cpu"))
        _model = model
        return model


def _resample(wav: np.ndarray, src_rate: int) -> np.ndarray:
    if src_rate == OUT_RATE or len(wav) == 0:
        return wav.astype(np.float32)
    n = max(1, int(len(wav) * OUT_RATE / src_rate))
    old = np.linspace(0.0, 1.0, num=len(wav), endpoint=False)
    new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(new, old, wav).astype(np.float32)


def _speak(model, text: str, speaker: str) -> np.ndarray:
    cleaned = " ".join((text or "").split())[:400]
    if not cleaned:
        return np.zeros(int(0.2 * OUT_RATE), dtype=np.float32)
    try:
        audio = model.apply_tts(text=cleaned, speaker=speaker, sample_rate=SAMPLE_RATE, put_accent=True, put_yo=True)
    except TypeError:
        audio = model.apply_tts(text=cleaned, speaker=speaker, sample_rate=SAMPLE_RATE)
    wav = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
    return _resample(np.asarray(wav, dtype=np.float32), SAMPLE_RATE)


def render_dialogue(model, segments: list[dict]) -> tuple[bytes, list[dict], float]:
    gap = np.zeros(int(0.32 * OUT_RATE), dtype=np.float32)
    pieces: list[np.ndarray] = []
    timed: list[dict] = []
    cursor = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        speaker = "xenia" if segment.get("role") == "operator" else "eugene"
        spoken = _speak(model, str(segment.get("text") or ""), speaker)
        start = cursor / OUT_RATE
        cursor += len(spoken)
        end = cursor / OUT_RATE
        pieces.append(spoken)
        pieces.append(gap)
        cursor += len(gap)
        row = dict(segment)
        row["start"] = round(start, 2)
        row["end"] = round(end, 2)
        timed.append(row)
    if not pieces:
        silence = np.zeros(OUT_RATE, dtype=np.float32)
        pieces = [silence]
    mixed = np.concatenate(pieces)
    peak = float(np.max(np.abs(mixed))) or 1.0
    mixed = (mixed / peak * 0.9).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, mixed, OUT_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue(), timed, round(len(mixed) / OUT_RATE, 2)


def build_demo_voices() -> None:
    try:
        model = _load_model()
    except Exception:
        log.exception("Голос для демо-звонков не подготовлен")
        return
    with SessionLocal() as session:
        rows = (
            session.query(Call)
            .filter(Call.is_demo == 1, Call.stored_name.is_(None))
            .all()
        )
        jobs = []
        for call in rows:
            if call.transcript and call.transcript.segments_json:
                jobs.append((call.id, call.transcript.segments_json))
    if not jobs:
        return
    log.info("Готовлю прослушивание для %s демо-звонков", len(jobs))
    for call_id, raw in jobs:
        try:
            segments = json.loads(raw)
            payload, timed, duration = render_dialogue(model, segments if isinstance(segments, list) else [])
            stored = f"{uuid.uuid4().hex}.bin"
            path = settings.audio_dir / stored
            path.write_bytes(encrypt_bytes(payload))
            with write_lock, SessionLocal() as session:
                call = session.get(Call, call_id)
                if call is None or call.stored_name:
                    path.unlink(missing_ok=True)
                    continue
                call.stored_name = stored
                call.mime = "audio/wav"
                call.duration_sec = duration
                if call.transcript is not None:
                    call.transcript.segments_json = json.dumps(timed, ensure_ascii=False)
                session.commit()
        except Exception:
            log.exception("Не удалось озвучить звонок %s", call_id)
    log.info("Демо-звонки можно прослушивать")


def start_demo_voices() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    threading.Thread(target=build_demo_voices, name="demo-voice", daemon=True).start()
