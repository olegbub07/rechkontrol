from __future__ import annotations

import time
from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.auth import decode_token, needs_password_change, retire_idle_tokens
from app.db import SessionLocal
from app.models import User

bearer = HTTPBearer(auto_error=False)
_BOOTSTRAP_OPEN = frozenset({"/api/auth/me", "/api/auth/password", "/api/auth/logout"})
SESSION_IDLE_SEC = 15 * 60
_TOUCH_SEC = 60


def db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(db_session),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Нужна авторизация")
    try:
        payload = decode_token(creds.credentials)
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия истекла, войдите снова")
    if payload.get("typ") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Некорректный токен")
    username = payload.get("sub")
    user = session.query(User).filter(User.username == username).one_or_none()
    if user is None or user.is_active == 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия истекла, войдите снова")
    if int(payload.get("ver") or 0) != int(user.token_version or 1):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия сброшена, войдите снова")
    now = int(time.time())
    seen = user.last_seen_at
    if seen is not None and now - int(seen) > SESSION_IDLE_SEC:
        retire_idle_tokens(user.id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия закрыта из-за простоя. Войдите снова.",
        )
    if seen is None or now - int(seen) >= _TOUCH_SEC:
        user.last_seen_at = now
    if needs_password_change(user) and request.url.path not in _BOOTSTRAP_OPEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Смените стартовый пароль, прежде чем работать с кабинетом",
        )
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нужны права администратора")
    return user
