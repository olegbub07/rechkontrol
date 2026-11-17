from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock

import bcrypt
import jwt
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import settings
from app.crypto import jwt_secret
from app.models import User

log = logging.getLogger("rechkontrol.auth")

_DUMMY_HASH: str | None = None
_revoked: dict[str, int] = {}
_revoke_lock = Lock()
PUBLIC_BOOTSTRAP_PASSWORD = "CenterInvest2026!"


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


def _mark_session():
    from app.db import SessionLocal, write_lock

    return write_lock, SessionLocal


def _add_mark(session: Session, kind: str, subject: str, created_at: int, expires_at: int) -> None:
    from app.models import SecurityMark

    session.add(
        SecurityMark(
            kind=kind[:32],
            subject=subject[:80],
            created_at=int(created_at),
            expires_at=int(expires_at),
        )
    )


def revoke_jti(jti: str, exp: int) -> None:
    now = int(time.time())
    with _revoke_lock:
        stale = [key for key, until in _revoked.items() if until < now]
        for key in stale:
            _revoked.pop(key, None)
        if jti:
            _revoked[jti] = int(exp)
    if not jti:
        return
    try:
        lock, factory = _mark_session()
        with lock, factory() as session:
            _add_mark(session, "revoked_jti", jti, now, int(exp))
            session.commit()
    except Exception:
        log.exception("revoke persist failed")


def is_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    now = int(time.time())
    with _revoke_lock:
        until = _revoked.get(jti)
        if until is None:
            return False
        if until < now:
            _revoked.pop(jti, None)
            return False
        return True


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.username,
        "uid": user.id,
        "role": user.role,
        "ver": int(user.token_version or 1),
        "iss": "rechkontrol",
        "typ": "access",
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.jwt_ttl_hours)).timestamp()),
    }
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    payload = jwt.decode(
        token,
        jwt_secret(),
        algorithms=["HS256"],
        issuer="rechkontrol",
        options={"require": ["exp", "iat", "sub", "iss", "jti"]},
    )
    if payload.get("typ") != "access" or is_revoked(payload.get("jti")):
        raise jwt.InvalidTokenError("revoked")
    return payload


def note_login_failure(key: str, username: str, request: Request | None = None) -> None:
    """Пишет неудачу сразу: ответ 401 откатывает сессию запроса, и журнал иначе теряется."""
    from app.audit import write_audit
    from app.models import SecurityMark
    from app.security import login_gate

    login_gate.fail(key)
    now = int(time.time())
    try:
        lock, factory = _mark_session()
        with lock, factory() as session:
            session.query(SecurityMark).filter(SecurityMark.expires_at <= now).delete()
            _add_mark(session, "login_fail", key, now, now + login_gate.window_sec)
            write_audit(session, "login_fail", detail=(username or "")[:64], request=request)
            session.commit()
    except Exception:
        log.exception("login failure persist failed")


def clear_login_failures(key: str) -> None:
    from app.models import SecurityMark
    from app.security import login_gate

    login_gate.ok(key)
    try:
        lock, factory = _mark_session()
        with lock, factory() as session:
            session.query(SecurityMark).filter(SecurityMark.kind == "login_fail", SecurityMark.subject == key[:80]).delete()
            session.commit()
    except Exception:
        log.exception("login failure clear failed")


def restore_persisted_guards() -> None:
    from app.models import SecurityMark
    from app.security import login_gate

    now = int(time.time())
    lock, factory = _mark_session()
    with lock, factory() as session:
        session.query(SecurityMark).filter(SecurityMark.expires_at <= now).delete()
        session.commit()
        rows = session.query(SecurityMark).all()
    for row in rows:
        if row.kind == "revoked_jti" and row.subject:
            with _revoke_lock:
                _revoked[row.subject] = int(row.expires_at)
        elif row.kind == "login_fail" and row.subject:
            login_gate.remember(row.subject, float(row.created_at))


def uses_public_bootstrap(user: User) -> bool:
    if user.role != "admin":
        return False
    return verify_password(PUBLIC_BOOTSTRAP_PASSWORD, user.password_hash)


def bootstrap_hint(host: str, username: str | None, password_matches: bool) -> dict:
    """Пароль из инструкции виден только с этой машины и только пока он ещё не сменён."""
    loopback = host in {"127.0.0.1", "::1", "localhost", "testclient"}
    if not loopback or not password_matches or not username:
        return {"show_password": False, "username": username if loopback else None}
    return {
        "show_password": True,
        "username": username,
        "password": PUBLIC_BOOTSTRAP_PASSWORD,
    }


def ensure_admin(session: Session) -> User:
    user = session.query(User).filter(User.username == settings.admin_username).one_or_none()
    if user is None:
        user = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role="admin",
            is_active=1,
            token_version=1,
        )
        session.add(user)
        session.flush()
        return user
    if user.is_active is None:
        user.is_active = 1
    if user.token_version is None:
        user.token_version = 1
    if not user.role:
        user.role = "admin"
    return user
