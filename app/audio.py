from __future__ import annotations

import os
import subprocess
from pathlib import Path

import imageio_ffmpeg
import soundfile as sf

from app.config import settings


class AudioError(ValueError):
    pass


def sniff_audio(header: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in settings.allowed_audio_ext:
        raise AudioError("Поддерживаются WAV, MP3 и OGG.")
    if len(header) < 12:
        raise AudioError("Файл слишком короткий или повреждён.")
    if ext == ".wav":
        if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            return "audio/wav"
        raise AudioError("Расширение WAV, но это не WAVE-файл.")
    if ext in {".ogg", ".oga"}:
        if header.startswith(b"OggS"):
            return "audio/ogg"
        raise AudioError("Расширение OGG, но сигнатура не совпадает.")
    if ext == ".mp3":
        if header.startswith(b"ID3") or header[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa", b"\xff\xf4"}:
            return "audio/mpeg"
        raise AudioError("Расширение MP3, но сигнатура не совпадает.")
    if ext == ".flac":
        if header.startswith(b"fLaC"):
            return "audio/flac"
        raise AudioError("Расширение FLAC, но сигнатура не совпадает.")
    if ext in {".m4a", ".mp4"}:
        if b"ftyp" in header[:12]:
            return "audio/mp4"
        raise AudioError("Расширение M4A, но сигнатура не совпадает.")
    raise AudioError("Не удалось определить аудиоформат файла.")


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def to_wav_mono_16k(src: Path, dst: Path, max_duration_sec: int | None = None) -> float:
    limit = max_duration_sec if max_duration_sec is not None else settings.max_duration_sec
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(),
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-t",
        str(int(limit)),
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(dst),
    ]
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise AudioError("Превышено время декодирования аудио.") from exc
    if proc.returncode != 0 or not dst.exists():
        raise AudioError("Не удалось декодировать аудио. Проверьте, что это WAV, MP3 или OGG.")
    info = sf.info(str(dst))
    duration = float(info.duration)
    if duration > limit + 1:
        raise AudioError(f"Длительность записи больше {limit // 60} минут.")
    if duration < 0.4:
        raise AudioError("Запись слишком короткая.")
    return duration
