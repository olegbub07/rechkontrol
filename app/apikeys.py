"""API-ключи внешних систем: rk_<префикс>_<секрет>. В базе только префикс и SHA-256 ключа.

Ключ случайный (256 бит), поэтому быстрый SHA-256 тут достаточен: перебор невозможен,
а проверка не тормозит каждый запрос, как bcrypt.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy.orm import Session

from app.models import ApiKey
from app.timeutil import utcnow

SCOPES = {
    "calls:read": "статус, результат и список звонков",
    "calls:write": "загрузка звонков",
}
DEFAULT_SCOPES = ("calls:read", "calls:write")


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def normalize_scopes(scopes) -> str:
    items = [s.strip() for s in (scopes.split(",") if isinstance(scopes, str) else scopes or []) if s and s.strip()]
    if not items:
        items = list(DEFAULT_SCOPES)
    bad = [s for s in items if s not in SCOPES]
    if bad:
        raise ValueError(f"Неизвестные права: {', '.join(bad)}. Есть: {', '.join(SCOPES)}")
    return ",".join(sorted(set(items)))


def create_key(session: Session, name: str, scopes=None, created_by: str = "") -> tuple[ApiKey, str]:
    clean = (name or "").strip()[:80]
    if len(clean) < 2:
        raise ValueError("Название ключа: от 2 символов, например «CRM» или «АТС»")
    prefix = secrets.token_hex(4)
    while session.query(ApiKey).filter(ApiKey.prefix == prefix).first() is not None:
        prefix = secrets.token_hex(4)
    key = f"rk_{prefix}_{secrets.token_urlsafe(32)}"
    row = ApiKey(name=clean, prefix=prefix, key_hash=_hash(key), scopes=normalize_scopes(scopes), created_by=created_by[:64])
    session.add(row)
    session.flush()
    return row, key


def verify_key(session: Session, key: str | None) -> ApiKey | None:
    if not key or not key.startswith("rk_") or key.count("_") < 2:
        return None
    prefix = key.split("_", 2)[1]
    row = session.query(ApiKey).filter(ApiKey.prefix == prefix).one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if not hmac.compare_digest(row.key_hash, _hash(key)):
        return None
    return row


def revoke_key(session: Session, key_id: int) -> ApiKey | None:
    row = session.get(ApiKey, key_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
    return row


def has_scope(row: ApiKey, scope: str) -> bool:
    return scope in (row.scopes or "").split(",")


def key_out(row: ApiKey) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "prefix": f"rk_{row.prefix}_…",
        "scopes": [s for s in (row.scopes or "").split(",") if s],
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked": row.revoked_at is not None,
    }
