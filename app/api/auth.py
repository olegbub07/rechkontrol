from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.timeutil import utcnow
from app.api.deps import bearer, current_user, db_session
from app.audit import write_audit
from app.auth import (
    account_lock_key,
    clear_login_failures,
    create_token,
    decode_token,
    hash_password,
    note_login_failure,
    revoke_jti,
    forget_initial_credentials,
    needs_password_change,
    clear_password_failures,
    note_password_failure,
    password_lock_wait,
    verify_password,
)
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
        must_change_password=needs_password_change(user),
        operator_id=user.operator_id,
    )


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, session: Session = Depends(db_session)) -> TokenOut:
    username = body.username.strip()
    key = client_key(request)
    account = account_lock_key(username)
    if login_gate.blocked(account) or login_gate.blocked(key):
        raise HTTPException(status_code=429, detail="Слишком много попыток входа. Подождите несколько минут.")
    user = session.query(User).filter(User.username == username).one_or_none()
    hashed = user.password_hash if user is not None else None
    if user is None or not verify_password(body.password, hashed):
        note_login_failure(key, username, request)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    if user.is_active == 0:
        note_login_failure(key, username, request)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    clear_login_failures(key, account)
    user.last_login_at = utcnow()
    user.last_seen_at = int(time.time())
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
    if db_user is None:
        raise HTTPException(status_code=401, detail="Сессия истекла, войдите снова")
    wait = password_lock_wait(db_user.id)
    if wait:
        minutes = max(1, (wait + 59) // 60)
        raise HTTPException(
            status_code=429,
            detail=f"Слишком много попыток смены пароля. Повторите через {minutes} мин.",
        )
    if not verify_password(body.old_password, db_user.password_hash):
        note_password_failure(db_user.id, request)
        raise HTTPException(status_code=400, detail="Старый пароль неверен")
    clear_password_failures(db_user.id)
    try:
        validate_password(body.new_password, db_user.username)
    except PasswordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if verify_password(body.new_password, db_user.password_hash):
        raise HTTPException(status_code=400, detail="Новый пароль должен отличаться от старого")
    db_user.password_hash = hash_password(body.new_password)
    db_user.must_change_password = 0
    forget_initial_credentials(db_user.username)
    db_user.token_version = int(db_user.token_version or 1) + 1
    write_audit(session, "password_change", user=db_user, request=request)
    session.flush()
    return TokenOut(token=create_token(db_user), user=_user_out(db_user))
