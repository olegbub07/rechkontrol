from __future__ import annotations

import json
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urlparse

from starlette.requests import Request

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
    "script-src-attr 'none'; "
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


_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    (b"content-security-policy", CSP.encode("ascii")),
    (b"x-permitted-cross-domain-policies", b"none"),
    (b"x-robots-tag", b"noindex, nofollow"),
    (b"server", b"RechKontrol"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"x-dns-prefetch-control", b"off"),
)


def _header_map(scope) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        found[key.decode("latin1").lower()] = value.decode("latin1")
    return found


def origin_matches_host(origin: str, host: str) -> bool:
    """Origin браузера должен совпадать с Host. Иначе это запрос с другого сайта."""
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not host:
        return False
    return parsed.netloc.lower() == host.lower()


class SecurityHeadersMiddleware:
    """Заголовки, лимит тела и отказ чужому Origin. Чистый ASGI: лимит действует и без Content-Length."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        method = (scope.get("method") or "GET").upper()
        headers = _header_map(scope)
        if method in _MUTATING:
            origin = headers.get("origin")
            if origin and not origin_matches_host(origin, headers.get("host", "")):
                await _json_response(send, 403, "Запрос с другого сайта отклонён", path)
                return
            if headers.get("sec-fetch-site") == "cross-site":
                await _json_response(send, 403, "Запрос с другого сайта отклонён", path)
                return
        limit = request_body_limit(path)
        raw_length = headers.get("content-length")
        if raw_length is not None:
            try:
                size = int(raw_length)
            except ValueError:
                await _json_response(send, 400, "Некорректный размер запроса", path)
                return
            if size > limit:
                await _json_response(send, 413, "Запрос слишком большой", path)
                return
        if path.startswith("/api/") and path != "/api/health":
            key = _peer_ip(scope.get("client"), headers)
            api_gate.fail(key)
            if api_gate.blocked(key):
                await _json_response(send, 429, "Слишком много запросов. Подождите минуту.", path)
                return
        incoming = receive
        if raw_length is None and method not in {"GET", "HEAD", "OPTIONS"}:
            if path.startswith("/api/calls/upload"):
                incoming = _capped_receive(receive, limit)
            else:
                body, too_big = await _buffer_body(receive, limit)
                if too_big:
                    await _json_response(send, 413, "Запрос слишком большой", path)
                    return
                incoming = _replay(body)
        await self.app(scope, incoming, _secured_send(send, path))


def _secured_send(send, path: str):
    async def wrapped(message):
        if message["type"] == "http.response.start":
            raw = [(key, value) for key, value in (message.get("headers") or [])]
            names = {key.lower() for key, _ in raw}
            for key, value in _SECURITY_HEADERS:
                if key not in names:
                    raw.append((key, value))
            if path.startswith("/api") or path.startswith("/static") or path == "/":
                raw = [(key, value) for key, value in raw if key.lower() != b"cache-control"]
                raw.append((b"cache-control", b"no-store"))
            message = {**message, "headers": raw}
        await send(message)

    return wrapped


async def _json_response(send, status: int, detail: str, path: str) -> None:
    payload = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    headers = list(_SECURITY_HEADERS) + [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode("ascii")),
    ]
    if path.startswith("/api") or path == "/":
        headers.append((b"cache-control", b"no-store"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


async def _buffer_body(receive, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return b"".join(chunks), False
        if message["type"] != "http.request":
            continue
        piece = message.get("body") or b""
        total += len(piece)
        if total > limit:
            return b"", True
        chunks.append(piece)
        if not message.get("more_body", False):
            return b"".join(chunks), False


def _replay(body: bytes):
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _capped_receive(receive, limit: int):
    total = 0

    async def wrapped():
        nonlocal total
        message = await receive()
        if message["type"] == "http.request":
            total += len(message.get("body") or b"")
            if total > limit:
                return {"type": "http.disconnect"}
        return message

    return wrapped


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
password_gate = LoginGate(max_attempts=8, window_sec=900)


def _trusted_proxies() -> frozenset[str]:
    raw = getattr(settings, "trusted_proxies", "") or ""
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def _peer_ip(client, headers: dict[str, str]) -> str:
    """Адрес клиента. X-Forwarded-For учитывается только от прокси из TRUSTED_PROXIES.

    Берётся самый правый адрес цепочки, который не является доверенным прокси:
    левые элементы заголовка клиент может подделать.
    """
    peer = client[0] if client else "local"
    proxies = _trusted_proxies()
    if not proxies or peer not in proxies:
        return peer
    chain = [item.strip() for item in headers.get("x-forwarded-for", "").split(",") if item.strip()]
    for hop in reversed(chain):
        if hop not in proxies:
            return hop[:64]
    return peer


def client_ip(request: Request) -> str:
    headers = {key.lower(): value for key, value in request.headers.items()}
    client = (request.client.host, request.client.port) if request.client else None
    return _peer_ip(client, headers)


def client_key(request: Request) -> str:
    return client_ip(request)


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
