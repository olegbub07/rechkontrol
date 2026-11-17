from __future__ import annotations

import logging
import os
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
# Пароли, которые когда-то были опубликованы в инструкции. Это не пароли по умолчанию:
# если хеш учётки совпадает с одним из них, пароль при старте заменяется на случайный.
LEGACY_PUBLIC_PASSWORDS = ("CenterInvest2026!", "CentrAdmin2026!", "CentrOperator2026!")
DEMO_OPERATOR_USERNAME = "operator"
DEMO_OPERATOR_EXTERNAL_ID = "OP-104"


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


def needs_password_change(user: User) -> bool:
    return bool(getattr(user, "must_change_password", 0))


def generate_password(length: int = 18) -> str:
    """Случайный пароль, который проходит политику validate_password."""
    import secrets
    import string

    from app.passwords import PasswordError, validate_password

    alphabet = string.ascii_letters + string.digits
    symbols = "!@#%^*-_=+?"
    while True:
        body = [secrets.choice(alphabet) for _ in range(length - 2)]
        body.append(secrets.choice(string.digits))
        body.append(secrets.choice(symbols))
        secrets.SystemRandom().shuffle(body)
        candidate = "".join(body)
        try:
            return validate_password(candidate)
        except PasswordError:
            continue


def _matches_legacy(user: User) -> bool:
    return any(verify_password(item, user.password_hash) for item in LEGACY_PUBLIC_PASSWORDS)


def credentials_path():
    return settings.data_dir / "initial_credentials.txt"


def _write_credentials(issued: list[tuple[str, str, str]]) -> None:
    """Новые стартовые пароли: один раз в консоль и в файл data/initial_credentials.txt (права 0600)."""
    from app.crypto import restrict_file

    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "\t" in line:
                existing[line.split("\t", 1)[0]] = line
    for username, role, password in issued:
        existing[username] = f"{username}\t{role}\t{password}"
    header = (
        "# Стартовые пароли РечьКонтроль. При первом входе программа потребует сменить пароль.\n"
        "# После смены строка удаляется, пустой файл удаляется целиком.\n"
        "# логин\tроль\tпароль\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(header + "\n".join(existing.values()) + "\n")
    restrict_file(path)
    lines = ["", "=" * 64, "Стартовые учётные записи (показываются один раз):"]
    for username, role, password in issued:
        lines.append(f"  {role:<9} логин: {username}   пароль: {password}")
    lines += [f"Копия сохранена в {path} (только для владельца).", "При первом входе пароль нужно сменить.", "=" * 64, ""]
    print("\n".join(lines), flush=True)


def forget_initial_credentials(username: str) -> None:
    path = credentials_path()
    if not path.exists():
        return
    try:
        kept = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and line.split("\t", 1)[0] != username
        ]
        if not kept:
            path.unlink(missing_ok=True)
            return
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("# логин\tроль\tпароль\n" + "\n".join(kept) + "\n")
    except OSError:
        log.exception("initial credentials cleanup failed")


def _issue(user: User, issued: list[tuple[str, str, str]], password: str | None = None) -> None:
    secret = password or generate_password()
    user.password_hash = hash_password(secret)
    user.must_change_password = 1
    user.token_version = int(user.token_version or 1) + 1
    issued.append((user.username, user.role, secret))


def ensure_staff_accounts(session: Session) -> list[tuple[str, str, str]]:
    """Создаёт администратора и демо-оператора со случайными паролями.

    Пароли из старых инструкций (LEGACY_PUBLIC_PASSWORDS) заменяются на случайные.
    Возвращает выданные пароли, чтобы их можно было проверить в тестах.
    """
    from app.models import Operator

    issued: list[tuple[str, str, str]] = []
    admin = ensure_admin(session, issued)
    if admin.password_hash and _matches_legacy(admin):
        _issue(admin, issued)
    person = session.query(Operator).filter(Operator.external_id == DEMO_OPERATOR_EXTERNAL_ID).one_or_none()
    user = session.query(User).filter(User.username == DEMO_OPERATOR_USERNAME).one_or_none()
    if user is None:
        user = User(
            username=DEMO_OPERATOR_USERNAME,
            password_hash="",
            role="operator",
            is_active=1,
            token_version=1,
            operator_id=person.id if person is not None else None,
        )
        session.add(user)
        _issue(user, issued)
        session.flush()
    else:
        if user.role == "operator" and user.operator_id is None and person is not None:
            user.operator_id = person.id
        if _matches_legacy(user):
            _issue(user, issued)
    if issued:
        session.flush()
        _write_credentials(issued)
    return issued


def ensure_admin(session: Session, issued: list[tuple[str, str, str]] | None = None) -> User:
    user = session.query(User).filter(User.username == settings.admin_username).one_or_none()
    if user is None:
        user = User(
            username=settings.admin_username,
            password_hash="",
            role="admin",
            is_active=1,
            token_version=1,
        )
        session.add(user)
        bucket = issued if issued is not None else []
        _issue(user, bucket, settings.admin_password or None)
        session.flush()
        if issued is None:
            _write_credentials(bucket)
        return user
    if user.is_active is None:
        user.is_active = 1
    if user.token_version is None:
        user.token_version = 1
    if not user.role:
        user.role = "admin"
    return user
