from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_admin
from app.audit import write_audit
from app.auth import clear_all_login_locks, clear_password_failures, create_token, hash_password, needs_password_change
from app.config import settings
from app.crypto import key_location
from app.db import db_guard_status
from app.models import AuditLog, Call, Operator, SecurityMark, User
from app.passwords import PasswordError, validate_password, validate_role, validate_username
from app.schemas import (
    AdminPasswordIn,
    AdminPasswordOut,
    AdminUserIn,
    AdminUserOut,
    AdminUserPatch,
    SessionRevokeOut,
    UserOut,
)
from app.security import password_gate
from app.timeutil import display_zone, to_local

router = APIRouter(tags=["admin"])


def _out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=bool(user.is_active),
        last_login_at=user.last_login_at,
        must_change_password=needs_password_change(user),
        operator_id=user.operator_id,
    )


def _operator_link(session: Session, role: str, operator_id: int | None) -> int | None:
    if role != "operator":
        return None
    if operator_id is None:
        raise HTTPException(status_code=400, detail="Оператору нужна карточка сотрудника")
    person = session.get(Operator, operator_id)
    if person is None:
        raise HTTPException(status_code=400, detail="Оператор не найден")
    return person.id


def _active_admins(session: Session) -> int:
    return session.query(User).filter(User.role == "admin", User.is_active == 1).count()


def _password_attempts(session: Session) -> dict[str, int]:
    now = int(time.time())
    rows = (
        session.query(SecurityMark.subject)
        .filter(SecurityMark.kind == "password_fail", SecurityMark.expires_at > now)
        .all()
    )
    counts: dict[str, int] = {}
    for (subject,) in rows:
        counts[subject] = counts.get(subject, 0) + 1
    return counts


@router.get("/admin/overview")
def overview(session: Session = Depends(db_session), _: User = Depends(require_admin)) -> dict:
    now = int(time.time())
    return {
        "users": session.query(User).count(),
        "admins": _active_admins(session),
        "calls": session.query(Call).count(),
        "queued": session.query(Call).filter(Call.status == "queued").count(),
        "failed": session.query(Call).filter(Call.status == "error").count(),
        "audit": session.query(AuditLog).count(),
        "login_locks": session.query(SecurityMark)
        .filter(SecurityMark.kind == "login_fail", SecurityMark.expires_at > now)
        .count(),
    }


@router.get("/admin/posture")
def posture(session: Session = Depends(db_session), _: User = Depends(require_admin)) -> dict:
    admins = session.query(User).filter(User.role == "admin", User.is_active == 1).all()
    guard = db_guard_status()
    return {
        "loopback_only": (not settings.allow_remote) and settings.app_host in {"127.0.0.1", "localhost", "::1"},
        "bootstrap_active": any(needs_password_change(user) for user in admins),
        "login_lock_persisted": True,
        "logout_revokes_until_expiry": True,
        "db": guard,
        "pd": {
            "key_location": key_location(),
            "key_outside_data": key_location() != "data",
            "transcripts_encrypted": bool(settings.encrypt_transcripts),
            "retention_days": int(settings.retention_days),
        },
    }


@router.get("/admin/users", response_model=list[AdminUserOut])
def list_users(session: Session = Depends(db_session), _: User = Depends(require_admin)) -> list[AdminUserOut]:
    rows = session.query(User).order_by(User.id.asc()).all()
    attempts = _password_attempts(session)
    limit = password_gate.max_attempts
    result: list[AdminUserOut] = []
    for user in rows:
        base = _out(user).model_dump()
        count = attempts.get(f"pwd:{user.id}", 0)
        result.append(AdminUserOut(**base, password_attempts=count, password_locked=count >= limit))
    return result


@router.post("/admin/users", response_model=UserOut)
def create_user(
    body: AdminUserIn,
    request: Request,
    session: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> UserOut:
    try:
        username = validate_username(body.username)
        role = validate_role(body.role)
        password = validate_password(body.password, username)
    except PasswordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if session.query(User).filter(User.username == username).one_or_none():
        raise HTTPException(status_code=409, detail="Такой логин уже есть")
    linked = _operator_link(session, role, body.operator_id)
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        is_active=1,
        token_version=1,
        operator_id=linked,
        must_change_password=1,
    )
    session.add(user)
    session.flush()
    write_audit(session, "user.create", user=admin, detail=f"{username} ({role})", request=request)
    return _out(user)


@router.patch("/admin/users/{user_id}", response_model=UserOut)
def patch_user(
    user_id: int,
    body: AdminUserPatch,
    request: Request,
    session: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> UserOut:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if "operator_id" in body.model_fields_set:
        if body.operator_id is None:
            if user.role == "operator":
                raise HTTPException(status_code=400, detail="Оператору нужна карточка сотрудника")
            user.operator_id = None
        else:
            user.operator_id = _operator_link(session, "operator", body.operator_id)
        write_audit(session, "user.operator", user=admin, detail=user.username, request=request)
    if body.role is not None:
        try:
            role = validate_role(body.role)
        except PasswordError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if user.role == "admin" and role != "admin" and _active_admins(session) <= 1:
            raise HTTPException(status_code=400, detail="Нельзя снять последнего администратора")
        if role == "operator" and user.operator_id is None:
            raise HTTPException(status_code=400, detail="Оператору нужна карточка сотрудника")
        user.role = role
        user.token_version = int(user.token_version or 1) + 1
        write_audit(session, "user.role", user=admin, detail=f"{user.username} → {role}", request=request)
    if body.is_active is not None:
        if user.id == admin.id and not body.is_active:
            raise HTTPException(status_code=400, detail="Нельзя отключить свою учётную запись")
        if user.role == "admin" and not body.is_active and _active_admins(session) <= 1:
            raise HTTPException(status_code=400, detail="Нельзя отключить последнего администратора")
        user.is_active = 1 if body.is_active else 0
        if not body.is_active:
            user.token_version = int(user.token_version or 1) + 1
        write_audit(
            session,
            "user.enable" if body.is_active else "user.disable",
            user=admin,
            detail=user.username,
            request=request,
        )
    session.flush()
    return _out(user)


@router.post("/admin/users/{user_id}/password", response_model=AdminPasswordOut)
def reset_password(
    user_id: int,
    body: AdminPasswordIn,
    request: Request,
    session: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> AdminPasswordOut:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    try:
        password = validate_password(body.password, user.username)
    except PasswordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user.password_hash = hash_password(password)
    user.must_change_password = 0 if user.id == admin.id else 1
    user.token_version = int(user.token_version or 1) + 1
    clear_password_failures(user.id)
    write_audit(session, "user.password", user=admin, detail=user.username, request=request)
    session.flush()
    token = create_token(user) if user.id == admin.id else None
    return AdminPasswordOut(**_out(user).model_dump(), token=token)


@router.post("/admin/users/{user_id}/sessions", response_model=SessionRevokeOut)
def revoke_sessions(
    user_id: int,
    request: Request,
    session: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> SessionRevokeOut:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.token_version = int(user.token_version or 1) + 1
    write_audit(session, "user.sessions", user=admin, detail=user.username, request=request)
    session.flush()
    token = create_token(user) if user.id == admin.id else None
    return SessionRevokeOut(token=token)


@router.post("/admin/users/{user_id}/unlock")
def unlock_password(
    user_id: int,
    request: Request,
    session: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="Свою блокировку смены пароля снять нельзя. Подождите указанное время или попросите другого администратора.",
        )
    clear_password_failures(user.id)
    write_audit(session, "user.unlock", user=admin, detail=user.username, request=request)
    return {"ok": True}


@router.post("/admin/locks/login")
def clear_login_lock(
    request: Request,
    session: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> dict:
    cleared = clear_all_login_locks()
    write_audit(session, "login.unlock", user=admin, detail=str(cleared), request=request)
    return {"cleared": cleared}


@router.get("/admin/audit")
def audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    session: Session = Depends(db_session),
    _: User = Depends(require_admin),
) -> dict:
    q = session.query(AuditLog)
    total = q.count()
    rows = q.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    def local(value) -> str:
        when = to_local(value) if isinstance(value, datetime) else None
        return when.strftime("%d.%m.%Y %H:%M:%S") if when else str(value or "")

    return {
        "total": total,
        "page": page,
        "tz": str(display_zone()),
        "items": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() + "Z" if isinstance(r.created_at, datetime) else str(r.created_at),
                "created_at_local": local(r.created_at),
                "username": r.username,
                "action": r.action,
                "detail": r.detail,
                "ip": r.ip,
            }
            for r in rows
        ],
    }
