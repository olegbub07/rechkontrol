from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Query

from app.models import Call, User


def is_operator(user: User) -> bool:
    return user.role == "operator"


def limit_calls(query: Query, user: User) -> Query:
    if is_operator(user):
        if user.operator_id is None:
            return query.filter(Call.id < 0)
        return query.filter(Call.operator_id == user.operator_id)
    return query


def call_visible(call: Call | None, user: User) -> bool:
    if call is None:
        return False
    if is_operator(user):
        return call.operator_id is not None and call.operator_id == user.operator_id
    return True


def require_call(call: Call | None, user: User) -> Call:
    if not call_visible(call, user):
        raise HTTPException(404, "Звонок не найден")
    return call


def reject_operator(user: User) -> None:
    if is_operator(user):
        raise HTTPException(403, "Этот раздел есть только у администратора")
