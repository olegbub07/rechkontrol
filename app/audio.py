from __future__ import annotations

import os
import re
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
        raise AudioError("Поддерживаются WAV, MP3, OGG, FLAC и M4A.")
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


def parse_ffmpeg_duration(stderr: str) -> float | None:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr or "")
    if not match:
        return None
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def _probe_duration(src: Path) -> float | None:
    cmd = [_ffmpeg(), "-hide_banner", "-i", str(src)]
    kwargs: dict = {"capture_output": True, "text": True, "timeout": 20}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_ffmpeg_duration(proc.stderr or "")


def to_wav_mono_16k(src: Path, dst: Path, max_duration_sec: int | None = None) -> float:
    limit = max_duration_sec if max_duration_sec is not None else settings.max_duration_sec
    probed = _probe_duration(src)
    if probed is not None and probed > limit + 0.05:
        raise AudioError(f"Длительность записи больше {limit // 60} минут.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(),
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-t",
        str(int(limit) + 2),
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise AudioError("Превышено время декодирования аудио.") from exc
    if proc.returncode != 0 or not dst.exists():
        dst.unlink(missing_ok=True)
        raise AudioError("Не удалось декодировать аудио. Проверьте, что это WAV, MP3, OGG, FLAC или M4A.")
    info = sf.info(str(dst))
    duration = float(info.duration)
    if duration > limit + 0.05 or duration < 0.4:
        dst.unlink(missing_ok=True)
        if duration < 0.4:
            raise AudioError("Запись слишком короткая.")
        raise AudioError(f"Длительность записи больше {limit // 60} минут.")
    return duration


_CHANNELS_RE = re.compile(r"Audio:[^\n]*?(?:,\s*(\d+)\s+channels|,\s*(stereo|mono)\b)")


def probe_channels(src: Path) -> int | None:
    cmd = [_ffmpeg(), "-hide_banner", "-i", str(src)]
    kwargs: dict = {"capture_output": True, "text": True, "timeout": 20}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _CHANNELS_RE.search(proc.stderr or "")
    if not match:
        return None
    if match.group(1):
        return int(match.group(1))
    return 2 if match.group(2) == "stereo" else 1


def read_stereo_16k(src: Path, max_duration_sec: int | None = None):
    """Два канала 16 кГц (float32, форма [n, 2]) для разметки по каналам. None, если запись не стерео."""
    import numpy as np

    if probe_channels(src) != 2:
        return None
    limit = max_duration_sec if max_duration_sec is not None else settings.max_duration_sec
    cmd = [
        _ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-t", str(int(limit) + 2), "-i", str(src), "-vn",
        "-ac", "2", "-ar", "16000", "-f", "s16le", "-",
    ]
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=300, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    pcm = np.frombuffer(proc.stdout, dtype="<i2")
    if pcm.size < 4:
        return None
    pcm = pcm[: pcm.size - pcm.size % 2].reshape(-1, 2)
    return pcm.astype(np.float32) / 32768.0
