"""Вебхуки: после обработки звонка (call.processed) POST на адреса из WEBHOOK_URLS.

Подпись: X-RK-Signature: t=<unix>,v1=<hex HMAC-SHA256(WEBHOOK_SECRET, "<t>.<тело>")>.
Повторы с экспоненциальной паузой (WEBHOOK_BACKOFF_SEC * 2^n, не больше часа),
после WEBHOOK_MAX_ATTEMPTS строка помечается failed. Все попытки видны в журнале доставки.
Расшифровки в теле нет: только оценки и нарушения, текст забирается через API по ключу.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, write_lock
from app.models import Call, WebhookDelivery
from app.timeutil import to_local, utcnow

log = logging.getLogger("rechkontrol.webhooks")

EVENT_PROCESSED = "call.processed"
KEEP_DAYS = 30
_stop = threading.Event()
_wake = threading.Event()
_thread: threading.Thread | None = None


def webhook_urls() -> list[str]:
    return [u.strip() for u in (settings.webhook_urls or "").split(",") if u.strip()]


def enabled() -> bool:
    return bool(webhook_urls()) and bool((settings.webhook_secret or "").strip())


def sign(body: bytes, timestamp: int, secret: str | None = None) -> str:
    key = (secret if secret is not None else settings.webhook_secret).encode("utf-8")
    mac = hmac.new(key, f"{timestamp}.".encode("ascii") + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify(body: bytes, header: str, secret: str, tolerance_sec: int = 300, now: int | None = None) -> bool:
    """Проверка подписи на стороне получателя (тот же код в docs/INTEGRATION.md)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (KeyError, ValueError):
        return False
    now = int(time.time()) if now is None else now
    if abs(now - ts) > tolerance_sec:
        return False
    expected = sign(body, ts, secret).split("v1=", 1)[1]
    return hmac.compare_digest(expected, parts.get("v1", ""))


def _json_list(raw: str | None) -> list:
    try:
        out = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return out if isinstance(out, list) else []


def call_payload(call: Call) -> dict:
    q = call.quality
    op = call.operator
    violations = [
        {"id": v.get("id"), "title": v.get("title"), "category": v.get("category")}
        for v in _json_list(q.violations_json if q else None)
        if isinstance(v, dict)
    ]
    return {
        "event": EVENT_PROCESSED,
        "call": {
            "id": call.id,
            "status": call.status,
            "source": call.source or "upload",
            "filename": call.orig_filename,
            "call_at": call.call_at.isoformat() if call.call_at else None,
            "duration_sec": call.duration_sec,
            "operator": {"id": op.id, "external_id": op.external_id, "name": op.name} if op else None,
            "error": call.error_message if call.status == "error" else None,
        },
        "quality": (
            {
                "overall": q.overall,
                "politeness": q.politeness,
                "completeness": q.completeness,
                "script_compliance": q.script_compliance,
                "resolution": q.resolution,
                "tonality": q.tonality,
                "violation_pct": q.violation_pct,
                "violations": violations,
            }
            if q
            else None
        ),
        "result_url": f"/api/v1/calls/{call.id}/result",
    }


def _wanted(payload: dict) -> bool:
    if not settings.webhook_only_violations:
        return True
    quality = payload.get("quality") or {}
    found = {v.get("id") for v in quality.get("violations") or []}
    only = {x.strip() for x in (settings.webhook_violation_ids or "").split(",") if x.strip()}
    return bool(found & only) if only else bool(found)


def enqueue_call(session: Session, call: Call) -> int:
    """Ставит call.processed в журнал доставки. Вызывается воркером после done/error."""
    if not enabled() or call.is_demo:
        return 0
    payload = call_payload(call)
    if not _wanted(payload):
        return 0
    n = 0
    for url in webhook_urls():
        session.add(WebhookDelivery(event=EVENT_PROCESSED, call_id=call.id, url=url[:500], payload=json.dumps(payload, ensure_ascii=False)))
        n += 1
    return n


def notify_processed(call_id: int) -> None:
    """Безопасная обёртка для воркера: сбой вебхука не ломает обработку звонка."""
    if not enabled():
        return
    try:
        with write_lock, SessionLocal() as session:
            call = session.get(Call, call_id)
            if call is not None and enqueue_call(session, call):
                session.commit()
        _wake.set()
    except Exception:
        log.exception("webhook: не удалось поставить событие для звонка %s", call_id)


def url_allowed(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "нужен адрес http:// или https://"
    if settings.webhook_allow_public:
        return True, ""
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError:
        return False, "имя хоста не разрешается"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not (ip.is_private or ip.is_loopback or ip.is_link_local):
            return False, f"адрес {ip} вне внутренней сети (WEBHOOK_ALLOW_PUBLIC=false)"
    return True, ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # переадресация могла бы увести за контур
        return None


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _post(url: str, body: bytes, headers: dict) -> int:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _opener.open(req, timeout=max(1.0, float(settings.webhook_timeout_sec))) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def deliver(row: WebhookDelivery) -> bool:
    ok, why = url_allowed(row.url)
    row.attempts = int(row.attempts or 0) + 1
    if not ok:
        row.status, row.last_error, row.last_code = "failed", why[:300], None
        return False
    body = (row.payload or "{}").encode("utf-8")
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "RechKontrol-Webhook/1",
        "X-RK-Event": row.event,
        "X-RK-Delivery": str(row.id),
        "X-RK-Signature": sign(body, ts),
    }
    try:
        code = _post(row.url, body, headers)
        row.last_code = code
        row.last_error = None if 200 <= code < 300 else f"HTTP {code}"
    except Exception as exc:  # сеть, таймаут, TLS
        code = 0
        row.last_code = None
        row.last_error = f"{exc.__class__.__name__}: {exc}"[:300]
    if 200 <= code < 300:
        row.status = "delivered"
        row.delivered_at = utcnow()
        return True
    if row.attempts >= max(1, int(settings.webhook_max_attempts)):
        row.status = "failed"
    else:
        delay = min(3600.0, float(settings.webhook_backoff_sec) * (2 ** (row.attempts - 1)))
        row.next_try_at = utcnow() + timedelta(seconds=delay)
    return False


def dispatch_due(force: bool = False, limit: int = 20) -> int:
    """Отправляет ожидающие доставки. force - не ждать паузы между повторами."""
    with SessionLocal() as session:
        query = session.query(WebhookDelivery.id).filter(WebhookDelivery.status == "pending")
        if not force:
            query = query.filter(WebhookDelivery.next_try_at <= utcnow())
        ids = [i for (i,) in query.order_by(WebhookDelivery.id).limit(limit).all()]
    for delivery_id in ids:
        with SessionLocal() as session:
            row = session.get(WebhookDelivery, delivery_id)
            if row is None or row.status != "pending":
                continue
            deliver(row)  # сеть без write_lock: медленный получатель не держит очередь звонков
            with write_lock:
                session.commit()
    return len(ids)


def prune_log(days: int = KEEP_DAYS) -> int:
    with write_lock, SessionLocal() as session:
        n = (
            session.query(WebhookDelivery)
            .filter(WebhookDelivery.created_at < utcnow() - timedelta(days=days), WebhookDelivery.status != "pending")
            .delete(synchronize_session=False)
        )
        session.commit()
        return n


def delivery_out(row: WebhookDelivery) -> dict:
    return {
        "id": row.id,
        "event": row.event,
        "call_id": row.call_id,
        "url": row.url,
        "status": row.status,
        "attempts": row.attempts,
        "last_code": row.last_code,
        "last_error": row.last_error,
        "created_at": to_local(row.created_at).isoformat() if row.created_at else None,
        "next_try_at": to_local(row.next_try_at).isoformat() if row.status == "pending" and row.next_try_at else None,
        "delivered_at": to_local(row.delivered_at).isoformat() if row.delivered_at else None,
    }


def _loop() -> None:
    last_prune = 0.0
    while not _stop.is_set():
        try:
            dispatch_due()
            if time.time() - last_prune > 6 * 3600:
                prune_log()
                last_prune = time.time()
        except Exception:
            log.exception("webhook: проход доставки не удался")
        _wake.wait(5.0)
        _wake.clear()


def start_webhooks() -> bool:
    global _thread
    if webhook_urls() and not (settings.webhook_secret or "").strip():
        log.warning("WEBHOOK_URLS задан, но WEBHOOK_SECRET пуст: вебхуки без подписи не отправляются")
        return False
    if not enabled():
        return False
    if _thread is not None and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="webhooks", daemon=True)
    _thread.start()
    log.info("Вебхуки включены: %s адрес(ов)", len(webhook_urls()))
    return True


def stop_webhooks() -> None:
    _stop.set()
    _wake.set()
    if _thread is not None:
        _thread.join(timeout=3)
