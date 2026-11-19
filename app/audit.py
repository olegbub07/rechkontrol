from __future__ import annotations

from sqlalchemy.orm import Session
from starlette.requests import Request

from app.models import AuditLog, User
from app.security import client_key


def write_audit(
    session: Session,
    action: str,
    *,
    user: User | None = None,
    detail: str = "",
    request: Request | None = None,
    actor: str | None = None,
) -> None:
    session.add(
        AuditLog(
            username=(user.username if user else (actor or ""))[:64],
            action=action[:64],
            detail=(detail or "")[:800],
            ip=client_key(request) if request is not None else "",
        )
    )
