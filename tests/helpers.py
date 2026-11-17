from __future__ import annotations

PASSWORD = "QaCheck-2026x!"


def ensure_user(name: str, role: str, operator_external_id: str | None = None) -> None:
    from app.auth import hash_password
    from app.db import SessionLocal
    from app.models import Operator, User

    with SessionLocal() as session:
        op_id = None
        if operator_external_id:
            op_id = session.query(Operator).filter(Operator.external_id == operator_external_id).one().id
        user = session.query(User).filter(User.username == name).one_or_none()
        if user is None:
            user = User(username=name)
            session.add(user)
        user.password_hash = hash_password(PASSWORD)
        user.role = role
        user.is_active = 1
        user.token_version = 1
        user.must_change_password = 0
        user.operator_id = op_id
        session.commit()


def auth(client, name: str) -> dict:
    token = client.post("/api/auth/login", json={"username": name, "password": PASSWORD}).json()["token"]
    return {"Authorization": f"Bearer {token}"}
