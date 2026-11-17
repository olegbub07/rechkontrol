from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}\.bin$")
UNSAFE_NAME_RE = re.compile(r"[^\w.\- ()а-яА-ЯёЁ]+", re.UNICODE)
CALL_STATUSES = frozenset({"queued", "processing", "done", "error"})
SAFE_AUDIO_MIME = frozenset({"audio/wav", "audio/ogg", "audio/mpeg", "audio/flac", "audio/mp4"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "media-src 'self' blob:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def request_body_limit(path: str) -> int:
    if path.startswith("/api/calls/upload"):
        files = max(1, int(settings.max_upload_files))
        mb = max(1, int(settings.max_upload_mb))
        return (mb * files + 8) * 1024 * 1024
    return 1024 * 1024


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                size = int(raw_length)
            except ValueError:
                from starlette.responses import JSONResponse

                return JSONResponse({"detail": "Некорректный размер запроса"}, status_code=400)
            if size > request_body_limit(path):
                from starlette.responses import JSONResponse

                return JSONResponse({"detail": "Запрос слишком большой"}, status_code=413)
        if path.startswith("/api/") and path not in {"/api/health"}:
            key = client_key(request)
            api_gate.fail(key)
            if api_gate.blocked(key):
                from starlette.responses import JSONResponse

                return JSONResponse({"detail": "Слишком много запросов. Подождите минуту."}, status_code=429)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Server"] = "RechKontrol"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if path.startswith("/api") or path.startswith("/static") or path == "/":
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
        now = time.time()
        with self._lock:
            bucket = self._hits[key]
            self._prune(bucket, now)
            return len(bucket) >= self.max_attempts

    def fail(self, key: str) -> None:
        now = time.time()
        with self._lock:
            bucket = self._hits[key]
            self._prune(bucket, now)
            bucket.append(now)

    def remember(self, key: str, at: float) -> None:
        now = time.time()
        if now - at > self.window_sec:
            return
        with self._lock:
            bucket = self._hits[key]
            self._prune(bucket, now)
            bucket.append(at)

    def ok(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


login_gate = LoginGate()
api_gate = LoginGate(max_attempts=480, window_sec=60)


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


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def safe_audio_mime(mime: str | None) -> str:
    if mime in SAFE_AUDIO_MIME:
        return mime
    return "application/octet-stream"


def content_disposition(filename: str, *, attachment: bool = False) -> str:
    clean = sanitize_filename(filename).replace("\\", "").replace("\r", "").replace("\n", "")
    ascii_name = clean.encode("ascii", "ignore").decode("ascii").strip() or "audio"
    kind = "attachment" if attachment else "inline"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(clean)}"


def public_bind_allowed(host: str) -> bool:
    if host in LOOPBACK_HOSTS:
        return True
    return bool(settings.allow_remote)


def trusted_hosts() -> list[str]:
    hosts = {"127.0.0.1", "localhost", "::1", "testserver"}
    if not settings.allow_remote:
        return sorted(hosts)
    if settings.app_host in {"0.0.0.0", "::", ""}:
        return ["*"]
    hosts.add(settings.app_host)
    return sorted(hosts)


def clean_tmp_dir() -> None:
    folder = settings.tmp_dir
    if not folder.exists():
        return
    for item in folder.iterdir():
        if item.is_file() and re.fullmatch(r"[0-9a-f]{32}(_src|\.wav)", item.name):
            item.unlink(missing_ok=True)
