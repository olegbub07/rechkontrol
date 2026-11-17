from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
import soundfile as sf

from app.asr_clean import align_words, clean_text, is_hallucination
from app.asr_fix import correct_asr_text
from app.config import settings

_model = None
_batch_pipe = None
_model_lock = threading.Lock()
ProgressCb = Callable[[int], None]
BANK_PROMPT = (
    "Добрый день, банк Центр-инвест. Оператор и клиент. "
    "Расчётный счёт, ИНН, ЕГРИП, заявка, номер обращения."
)


KNOWN_MODELS = ("tiny", "base", "small", "medium", "large-v2", "large-v3")


def model_name() -> str:
    return (settings.whisper_model or "base").strip()


def local_model_dir(name: str | None = None) -> Path:
    """Каталог модели. WHISPER_MODEL может быть именем (base, small, ...) или путём к папке модели."""
    value = (name or model_name()).strip()
    candidate = Path(value).expanduser()
    if value not in KNOWN_MODELS and candidate.is_dir():
        return candidate
    safe = value.replace("/", "_").replace("\\", "_")
    return settings.models_dir / f"faster-whisper-{safe}"


def local_model_ready(name: str | None = None) -> bool:
    return (local_model_dir(name) / "model.bin").exists()


def download_model(name: str | None = None) -> Path:
    """Скачивает модель один раз в data/models/faster-whisper-<name>/, дальше работа офлайн."""
    from huggingface_hub import snapshot_download

    value = name or model_name()
    target = local_model_dir(value)
    repo = f"Systran/faster-whisper-{value}"
    target.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=repo, local_dir=str(target))
    return target


def reset_model() -> None:
    """Выгрузить модель (нужно бенчмарку, чтобы сменить WHISPER_MODEL в одном процессе)."""
    global _model, _batch_pipe
    with _model_lock:
        _model = None
        _batch_pipe = None


def cpu_threads() -> int:
    if settings.whisper_cpu_threads > 0:
        return max(1, min(int(settings.whisper_cpu_threads), 16))
    cores = os.cpu_count() or 2
    return max(1, min(cores, 8))


def decode_profile(duration: float | None) -> dict:
    """Короткий звонок — точный проход. От 3 минут — режим кейса: 10 минут аудио за 3–5 минут на CPU."""
    seconds = duration or 0
    long_audio = seconds >= 90
    case_sla = seconds >= 180
    batch = max(1, min(int(settings.whisper_batch_size), 16))
    configured_beam = max(1, min(int(settings.whisper_beam_size), 5))
    return {
        "long_audio": long_audio,
        "use_batch": long_audio,
        "beam_size": 1 if case_sla else configured_beam,
        "batch_size": batch if long_audio else 1,
        "word_timestamps": not case_sla,
        "initial_prompt": None if long_audio else BANK_PROMPT,
    }


def load_model():
    global _model, _batch_pipe
    with _model_lock:
        if _model is None:
            from faster_whisper import BatchedInferencePipeline, WhisperModel

            settings.models_dir.mkdir(parents=True, exist_ok=True)
            threads = cpu_threads()
            if not local_model_ready():
                download_model()
            os.environ["HF_HUB_OFFLINE"] = "1"
            _model = WhisperModel(
                str(local_model_dir()),
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                cpu_threads=threads,
                num_workers=1,
                local_files_only=True,
            )
            _batch_pipe = BatchedInferencePipeline(model=_model)
        return _model


def model_loaded() -> bool:
    return _model is not None


def transcribe_wav(
    path: Path, duration: float | None = None, progress: ProgressCb | None = None
) -> tuple[list[dict], str, np.ndarray, int]:
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
        sr = 16000
    profile = decode_profile(duration or (len(audio) / float(sr) if sr else None))
    kwargs = dict(
        language="ru",
        task="transcribe",
        beam_size=profile["beam_size"],
        temperature=0.0,
        vad_filter=True,
        vad_parameters={
            "min_silence_duration_ms": 500,
            "speech_pad_ms": 320,
            "threshold": 0.45,
        },
        word_timestamps=profile["word_timestamps"],
        condition_on_previous_text=False,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        hallucination_silence_threshold=2.0,
        initial_prompt=profile["initial_prompt"],
        chunk_length=30,
    )
    try:
        if profile["use_batch"] and _batch_pipe is not None:
            segments_iter, info = _batch_pipe.transcribe(audio, batch_size=profile["batch_size"], **kwargs)
        else:
            segments_iter, info = model.transcribe(audio, **kwargs)
    except TypeError:
        kwargs.pop("hallucination_silence_threshold", None)
        kwargs.pop("chunk_length", None)
        segments_iter, info = model.transcribe(audio, **kwargs)
    raw: list[dict] = []
    total = duration or 0.0
    for seg in segments_iter:
        words = []
        for word in seg.words or []:
            token = (word.word or "").strip()
            if token:
                words.append({"start": float(word.start), "end": float(word.end), "text": token})
        if is_hallucination(
            getattr(seg, "no_speech_prob", None),
            getattr(seg, "avg_logprob", None),
            getattr(seg, "compression_ratio", None),
        ):
            continue
        original = correct_asr_text((seg.text or "").strip())
        text = clean_text(original)
        if text != original:
            words = align_words(words, text)
        if not text:
            continue
        raw.append(
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "text": text,
                "words": words,
            }
        )
        if progress and total > 0:
            pct = 20 + int(65 * min(float(seg.end) / total, 1.0))
            progress(min(pct, 88))
    lang = getattr(info, "language", None) or "ru"
    return raw, lang, audio, sr
