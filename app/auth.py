from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.crypto import jwt_secret
from app.models import User

_DUMMY_HASH: str | None = None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("timing-guard")
    return _DUMMY_HASH


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        hashed = (password_hash or _dummy_hash()).encode("utf-8")
        return bcrypt.checkpw(password.encode("utf-8"), hashed)
    except ValueError:
        return False


def create_token(username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iss": "rechkontrol",
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.jwt_ttl_hours)).timestamp()),
    }
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, jwt_secret(), algorithms=["HS256"], issuer="rechkontrol")


def ensure_admin(session: Session) -> User:
    user = session.query(User).filter(User.username == settings.admin_username).one_or_none()
    if user is None:
        user = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role="admin",
        )
        session.add(user)
        session.flush()
    return user
