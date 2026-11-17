from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.api.deps import bearer, current_user, db_session
from app.audit import write_audit
from app.auth import (
    bootstrap_hint,
    clear_login_failures,
    create_token,
    decode_token,
    hash_password,
    note_login_failure,
    revoke_jti,
    uses_public_bootstrap,
    verify_password,
)
from app.config import settings
from app.models import User
from app.passwords import PasswordError, validate_password
from app.schemas import LoginIn, PasswordChangeIn, TokenOut, UserOut
from app.security import client_key, login_gate

router = APIRouter(tags=["auth"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=bool(user.is_active),
        last_login_at=user.last_login_at,
        must_change_password=uses_public_bootstrap(user),
    )


@router.get("/auth/bootstrap")
def bootstrap(request: Request, session: Session = Depends(db_session)) -> dict:
    user = session.query(User).filter(User.username == settings.admin_username).one_or_none()
    host = request.client.host if request.client else ""
    matches = bool(user is not None and uses_public_bootstrap(user))
    return bootstrap_hint(host, user.username if user is not None else None, matches)


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, session: Session = Depends(db_session)) -> TokenOut:
    key = client_key(request)
    if login_gate.blocked(key):
        raise HTTPException(status_code=429, detail="Слишком много попыток входа. Подождите несколько минут.")
    user = session.query(User).filter(User.username == body.username.strip()).one_or_none()
    hashed = user.password_hash if user is not None else None
    if user is None or not verify_password(body.password, hashed):
        note_login_failure(key, body.username.strip(), request)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    if user.is_active == 0:
        note_login_failure(key, body.username.strip(), request)
        raise HTTPException(status_code=401, detail="Учётная запись отключена")
    clear_login_failures(key)
    user.last_login_at = datetime.utcnow()
    write_audit(session, "login", user=user, request=request)
    return TokenOut(token=create_token(user), user=_user_out(user))


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return _user_out(user)


@router.post("/auth/logout")
def logout(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> dict:
    if creds is not None:
        payload = decode_token(creds.credentials)
        revoke_jti(str(payload.get("jti") or ""), int(payload.get("exp") or 0))
    write_audit(session, "logout", user=user, request=request)
    return {"ok": True}


@router.post("/auth/password", response_model=TokenOut)
def change_password(
    body: PasswordChangeIn,
    request: Request,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> TokenOut:
    db_user = session.get(User, user.id)
    if db_user is None or not verify_password(body.old_password, db_user.password_hash):
        raise HTTPException(status_code=400, detail="Старый пароль неверен")
    try:
        validate_password(body.new_password, db_user.username)
    except PasswordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db_user.password_hash = hash_password(body.new_password)
    db_user.token_version = int(db_user.token_version or 1) + 1
    write_audit(session, "password_change", user=db_user, request=request)
    session.flush()
    return TokenOut(token=create_token(db_user), user=_user_out(db_user))
