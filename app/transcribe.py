from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
import soundfile as sf

from app.config import settings

_model = None
_model_lock = threading.Lock()
ProgressCb = Callable[[int], None]


def local_model_dir() -> Path:
    return settings.models_dir / "faster-whisper-base"


def local_model_ready() -> bool:
    return (local_model_dir() / "model.bin").exists()


def load_model():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            settings.models_dir.mkdir(parents=True, exist_ok=True)
            if local_model_ready():
                os.environ["HF_HUB_OFFLINE"] = "1"
                source = str(local_model_dir())
                _model = WhisperModel(
                    source,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                    local_files_only=True,
                )
            else:
                _model = WhisperModel(
                    settings.whisper_model,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                    download_root=str(settings.models_dir),
                )
        return _model


def model_loaded() -> bool:
    return _model is not None


def transcribe_wav(
    path: Path, duration: float | None = None, progress: ProgressCb | None = None
) -> tuple[list[dict], str]:
    if progress:
        progress(22)
    model = load_model()
    if progress:
        progress(28)
    audio, sr = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        src_duration = len(audio) / float(sr) if sr else 0.0
        target = int(src_duration * 16000)
        x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=max(target, 1), endpoint=False)
        audio = np.interp(x_new, x_old, audio).astype(np.float32)
    segments_iter, info = model.transcribe(
        audio,
        language="ru",
        beam_size=1,
        vad_filter=True,
        word_timestamps=False,
        condition_on_previous_text=False,
    )
    raw: list[dict] = []
    total = duration or 0.0
    for seg in segments_iter:
        raw.append({"start": float(seg.start), "end": float(seg.end), "text": (seg.text or "").strip()})
        if progress and total > 0:
            pct = 20 + int(65 * min(float(seg.end) / total, 1.0))
            progress(min(pct, 88))
    lang = getattr(info, "language", None) or "ru"
    return raw, lang
