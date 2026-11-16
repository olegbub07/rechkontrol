from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}\.bin$")
UNSAFE_NAME_RE = re.compile(r"[^\w.\- ()а-яА-ЯёЁ]+", re.UNICODE)

CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "media-src 'self' blob:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response


class LoginGate:
    def __init__(self, max_attempts: int = 8, window_sec: int = 900) -> None:
        self.max_attempts = max_attempts
        self.window_sec = window_sec
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _prune(self, bucket: deque[float], now: float) -> None:
        while bucket and now - bucket[0] > self.window_sec:
            bucket.popleft()

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            self._prune(bucket, now)
            return len(bucket) >= self.max_attempts

    def fail(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            self._prune(bucket, now)
            bucket.append(now)

    def ok(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


login_gate = LoginGate()


def client_key(request: Request) -> str:
    host = request.client.host if request.client else "local"
    return host


def sanitize_filename(name: str | None) -> str:
    raw = Path(name or "audio").name
    cleaned = UNSAFE_NAME_RE.sub("_", raw).strip(" ._")
    if not cleaned or cleaned in {".", ".."}:
        return "audio.wav"
    return cleaned[:180]


def safe_audio_path(stored_name: str | None) -> Path:
    if not stored_name or not STORED_NAME_RE.fullmatch(stored_name):
        raise ValueError("Некорректный идентификатор файла")
    root = settings.audio_dir.resolve()
    path = (root / stored_name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Некорректный путь к файлу")
    return path


def safe_tmp_path(name: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}(_src|\.wav)", name):
        raise ValueError("Некорректный временный файл")
    root = settings.tmp_dir.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Некорректный путь")
    return path


def clean_tmp_dir() -> None:
    folder = settings.tmp_dir
    if not folder.exists():
        return
    for item in folder.iterdir():
        if item.is_file():
            item.unlink(missing_ok=True)
