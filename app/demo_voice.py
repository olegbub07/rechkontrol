"""Голос для демо-звонков: локальная модель Silero, без отправки текста в сеть после скачивания."""

from __future__ import annotations

import hashlib
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
        if not settings.demo_voice_enabled:
            raise RuntimeError("Озвучка демо-звонков выключена (DEMO_VOICE_ENABLED=false)")
        path = _model_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.stat().st_size < 1_000_000:
            _download(path)
        verify_model(path)
        import torch

        importer = torch.package.PackageImporter(_torch_path(path))
        model = importer.load_pickle("tts_models", "model")
        model.to(torch.device("cpu"))
        _model = model
        return model


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_model(path) -> None:
    """Модель Silero - это torch.package (pickle). Загружаем только файл с известным хешем."""
    expected = (settings.demo_voice_model_sha256 or "").strip().lower()
    if not expected:
        raise RuntimeError("Не задан DEMO_VOICE_MODEL_SHA256: модель голоса не будет загружена без проверки")
    actual = file_sha256(path)
    if actual != expected:
        raise RuntimeError(f"Хеш модели голоса не совпал: {actual}")


def _download(path) -> None:
    import urllib.request

    url = settings.demo_voice_model_url
    if not url.startswith("https://"):
        raise RuntimeError("Модель голоса скачивается только по https")
    log.info("Скачиваю русскую модель голоса для прослушивания демо-звонков")
    tmp = path.with_suffix(".part")
    try:
        urllib.request.urlretrieve(url, tmp)
        verify_model(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


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
    """Стерео-запись, как в колл-центре: оператор в левом канале, клиент в правом.

    Раньше демо было моно с паузой 0,32 с между репликами. Разметка ролей по паузам
    в моно ищет тишину от 0,55 с, поэтому после переобработки весь демо-звонок
    склеивался в одну реплику оператора. В стерео роли берутся из каналов,
    а пауза 0,6 с помогает и при сведении в моно.
    """
    gap_len = int(0.6 * OUT_RATE)
    left: list[np.ndarray] = []
    right: list[np.ndarray] = []
    timed: list[dict] = []
    cursor = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        is_operator = segment.get("role") == "operator"
        speaker = "xenia" if is_operator else "eugene"
        spoken = _speak(model, str(segment.get("text") or ""), speaker)
        silent = np.zeros(len(spoken), dtype=np.float32)
        left.append(spoken if is_operator else silent)
        right.append(silent if is_operator else spoken)
        start = cursor / OUT_RATE
        cursor += len(spoken)
        end = cursor / OUT_RATE
        gap = np.zeros(gap_len, dtype=np.float32)
        left.append(gap)
        right.append(gap)
        cursor += gap_len
        row = dict(segment)
        row["start"] = round(start, 2)
        row["end"] = round(end, 2)
        timed.append(row)
    if not left:
        left = [np.zeros(OUT_RATE, dtype=np.float32)]
        right = [np.zeros(OUT_RATE, dtype=np.float32)]
    stereo = np.stack([np.concatenate(left), np.concatenate(right)], axis=1)
    peak = float(np.max(np.abs(stereo))) or 1.0
    stereo = (stereo / peak * 0.9).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, stereo, OUT_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue(), timed, round(len(stereo) / OUT_RATE, 2)


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
    if not settings.demo_voice_enabled or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    threading.Thread(target=build_demo_voices, name="demo-voice", daemon=True).start()
