"""Админка: API-ключи и журнал доставки вебхуков."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_admin
from app.apikeys import SCOPES, create_key, key_out, revoke_key
from app.audit import write_audit
from app.config import settings
from app.inbox import inbox_root
from app.models import ApiKey, User, WebhookDelivery
from app.webhooks import delivery_out, enabled as webhooks_enabled, webhook_urls

router = APIRouter(prefix="/admin/integrations", tags=["admin"])


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["calls:read", "calls:write"])


@router.get("")
def overview(
    session: Session = Depends(db_session),
    _admin: User = Depends(require_admin),
    page_size: int = Query(30, ge=1, le=200),
) -> dict:
    keys = session.query(ApiKey).order_by(ApiKey.id.desc()).all()
    rows = session.query(WebhookDelivery).order_by(WebhookDelivery.id.desc()).limit(page_size).all()
    root = inbox_root()
    return {
        "scopes": SCOPES,
        "keys": [key_out(k) for k in keys],
        "inbox": {"enabled": root is not None, "dir": str(root) if root else None, "poll_sec": settings.inbox_poll_sec},
        "webhooks": {
            "enabled": webhooks_enabled(),
            "urls": webhook_urls(),
            "secret_set": bool((settings.webhook_secret or "").strip()),
            "only_violations": settings.webhook_only_violations,
        },
        "deliveries": [delivery_out(r) for r in rows],
    }


@router.post("/keys")
def new_key(body: ApiKeyIn, request: Request, session: Session = Depends(db_session), admin: User = Depends(require_admin)) -> dict:
    try:
        row, key = create_key(session, body.name, body.scopes, created_by=admin.username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    write_audit(session, "apikey.create", user=admin, detail=f"ключ №{row.id} «{row.name}» ({row.scopes})", request=request)
    session.commit()
    return {**key_out(row), "key": key}


@router.delete("/keys/{key_id}")
def drop_key(key_id: int, request: Request, session: Session = Depends(db_session), admin: User = Depends(require_admin)) -> dict:
    row = revoke_key(session, key_id)
    if row is None:
        raise HTTPException(404, "Ключ не найден")
    write_audit(session, "apikey.revoke", user=admin, detail=f"ключ №{row.id} «{row.name}»", request=request)
    session.commit()
    return key_out(row)


@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: int, request: Request, session: Session = Depends(db_session), admin: User = Depends(require_admin)) -> dict:
    from app.timeutil import utcnow

    row = session.get(WebhookDelivery, delivery_id)
    if row is None:
        raise HTTPException(404, "Доставка не найдена")
    row.status, row.attempts, row.next_try_at = "pending", 0, utcnow()
    write_audit(session, "webhook.retry", user=admin, detail=f"доставка №{row.id}", request=request)
    session.commit()
    return delivery_out(row)
