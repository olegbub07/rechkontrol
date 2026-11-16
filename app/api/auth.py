from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.auth import create_token, verify_password
from app.models import User
from app.schemas import LoginIn, TokenOut, UserOut
from app.security import client_key, login_gate

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, session: Session = Depends(db_session)) -> TokenOut:
    key = client_key(request)
    if login_gate.blocked(key):
        raise HTTPException(status_code=429, detail="Слишком много попыток входа. Подождите несколько минут.")
    user = session.query(User).filter(User.username == body.username).one_or_none()
    hashed = user.password_hash if user is not None else None
    if user is None or not verify_password(body.password, hashed):
        login_gate.fail(key)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    login_gate.ok(key)
    return TokenOut(
        token=create_token(user.username, user.role),
        user=UserOut(username=user.username, role=user.role),
    )


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut(username=user.username, role=user.role)
