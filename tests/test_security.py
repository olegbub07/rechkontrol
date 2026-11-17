import pytest

from app.audio import AudioError, sniff_audio
from app.security import (
    content_disposition,
    escape_like,
    public_bind_allowed,
    safe_audio_mime,
    safe_audio_path,
    sanitize_filename,
)
from app.transcribe import decode_profile


def test_sanitize_filename_strips_path():
    assert sanitize_filename("../../secret.wav") == "secret.wav"
    assert sanitize_filename("разговор 12.mp3") == "разговор 12.mp3"


def test_safe_audio_path_rejects_traversal():
    with pytest.raises(ValueError):
        safe_audio_path("../master.key")
    with pytest.raises(ValueError):
        safe_audio_path("not-a-uuid.bin")
    with pytest.raises(ValueError):
        safe_audio_path(None)


def test_sniff_rejects_non_audio():
    with pytest.raises(AudioError):
        sniff_audio(b"MZ..............", "payload.wav")
    with pytest.raises(AudioError):
        sniff_audio(b"RIFF....NOTW", "call.wav")


def test_escape_like_neutralizes_wildcards():
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"


def test_content_disposition_strips_header_injection():
    header = content_disposition('evil\r\nSet-Cookie: a=b.wav')
    assert "\r" not in header
    assert "\n" not in header
    assert "filename*" in header


def test_audio_mime_rejects_html():
    assert safe_audio_mime("text/html") == "application/octet-stream"
    assert safe_audio_mime("audio/mpeg") == "audio/mpeg"


def test_remote_bind_is_opt_in(monkeypatch):
    monkeypatch.setattr("app.security.settings.allow_remote", False)
    assert public_bind_allowed("127.0.0.1")
    assert not public_bind_allowed("0.0.0.0")
    monkeypatch.setattr("app.security.settings.allow_remote", True)
    assert public_bind_allowed("0.0.0.0")


def test_long_call_uses_batched_profile():
    short = decode_profile(40)
    long = decode_profile(600)
    assert short["use_batch"] is False
    assert short["word_timestamps"] is True
    assert short["initial_prompt"]
    assert long["use_batch"] is True
    assert long["beam_size"] == 1
    assert long["word_timestamps"] is False
    assert long["initial_prompt"] is None
    assert long["beam_size"] >= 1


def test_sniff_accepts_case_formats():
    assert sniff_audio(b"RIFF....WAVE", "call.wav") == "audio/wav"
    assert sniff_audio(b"OggS........", "call.ogg") == "audio/ogg"
    assert sniff_audio(b"ID3.........", "call.mp3") == "audio/mpeg"


def test_no_default_passwords_in_code():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    from app.config import Settings

    assert Settings(_env_file=None).admin_password == ""
    for name in ("README.md", ".env.example", "app/config.py", "app/static/js/app.js", "app/static/index.html"):
        text = (root / name).read_text(encoding="utf-8")
        for legacy in ("CentrAdmin2026!", "CentrOperator2026!", "CenterInvest2026!"):
            assert legacy not in text, (name, legacy)


def test_generated_password_passes_policy_and_is_random():
    from app.auth import generate_password
    from app.passwords import validate_password

    first, second = generate_password(), generate_password()
    assert first != second
    assert validate_password(first) == first


def test_client_ip_trusts_forwarded_only_from_configured_proxy(monkeypatch):
    from starlette.requests import Request

    from app.security import client_ip

    def req(peer, xff=None):
        headers = [(b"x-forwarded-for", xff.encode())] if xff else []
        return Request({"type": "http", "client": (peer, 1234), "headers": headers})

    monkeypatch.setattr("app.security.settings.trusted_proxies", "")
    assert client_ip(req("127.0.0.1", "8.8.8.8")) == "127.0.0.1"
    monkeypatch.setattr("app.security.settings.trusted_proxies", "10.0.0.2")
    assert client_ip(req("10.0.0.2", "1.1.1.1, 203.0.113.9")) == "203.0.113.9"
    assert client_ip(req("10.0.0.2", "203.0.113.9, 10.0.0.2")) == "203.0.113.9"
    assert client_ip(req("198.51.100.1", "127.0.0.1")) == "198.51.100.1"


def test_bootstrap_endpoint_is_gone_and_forced_change_works(tmp_path, monkeypatch):
    import os

    from fastapi.testclient import TestClient

    from app.auth import credentials_path, ensure_staff_accounts
    from app.db import SessionLocal
    from app.main import app
    from app.models import User

    with TestClient(app) as client:
        assert client.get("/api/auth/bootstrap").status_code == 404
        with SessionLocal() as session:
            row = session.query(User).filter(User.username == "operator").one()
            row.password_hash = __import__("app.auth", fromlist=["hash_password"]).hash_password("CentrOperator2026!")
            session.commit()
            issued = ensure_staff_accounts(session)
            session.commit()
        fresh = {name: (role, pwd) for name, role, pwd in issued}
        assert "operator" in fresh
        role, password = fresh["operator"]
        assert role == "operator" and password != "CentrOperator2026!"
        path = credentials_path()
        assert path.exists()
        if os.name != "nt":
            assert path.stat().st_mode & 0o077 == 0
        assert password in path.read_text(encoding="utf-8")

        legacy = client.post("/api/auth/login", json={"username": "operator", "password": "CentrOperator2026!"})
        assert legacy.status_code == 401
        login = client.post("/api/auth/login", json={"username": "operator", "password": password})
        assert login.status_code == 200
        assert login.json()["user"]["must_change_password"] is True
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        blocked = client.get("/api/calls", headers=headers)
        assert blocked.status_code == 403
        same = client.post("/api/auth/password", headers=headers, json={"old_password": password, "new_password": password})
        assert same.status_code == 400
        changed = client.post(
            "/api/auth/password",
            headers=headers,
            json={"old_password": password, "new_password": "Operator-New-2026x"},
        )
        assert changed.status_code == 200
        assert changed.json()["user"]["must_change_password"] is False
        headers = {"Authorization": f"Bearer {changed.json()['token']}"}
        assert client.get("/api/calls", headers=headers).status_code == 200
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        assert password not in text


def test_db_rejects_attach_and_schema_rewrite():
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    from app.db import IS_SQLITE, engine

    if not IS_SQLITE:
        pytest.skip("защита ATTACH и writable_schema есть только у SQLite")

    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
        with pytest.raises(DatabaseError):
            conn.execute(text("ATTACH DATABASE ':memory:' AS evil"))
        with pytest.raises(DatabaseError):
            conn.execute(text("PRAGMA writable_schema = ON"))


def test_restrict_file_keeps_owner_access(tmp_path):
    import os

    from app.crypto import restrict_file

    path = tmp_path / "secret.bin"
    path.write_bytes(b"x")
    restrict_file(path)
    assert path.read_bytes() == b"x"
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_login_reaches_api_and_bootstrap():
    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.db import SessionLocal
    from app.main import app
    from app.models import User

    from app.models import Operator

    name = "qa.listener"
    op_name = "qa.operator"
    with SessionLocal() as session:
        for login_name in (name, op_name):
            old = session.query(User).filter(User.username == login_name).one_or_none()
            if old is not None:
                session.delete(old)
                session.commit()
        person = session.query(Operator).filter(Operator.external_id == "OP-104").one_or_none()
        if person is None:
            person = Operator(name="Иванова", external_id="OP-104")
            session.add(person)
            session.flush()
        session.add(
            User(
                username=op_name,
                password_hash=hash_password("OperatorPass12!"),
                role="operator",
                is_active=1,
                token_version=1,
                operator_id=person.id,
            )
        )
        session.add(
            User(
                username=name,
                password_hash=hash_password("ListenPass12!"),
                role="admin",
                is_active=1,
                token_version=1,
            )
        )
        session.commit()
    try:
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": name, "password": "ListenPass12!"})
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            me = client.get("/api/auth/me", headers=headers)
            assert me.status_code == 200
            assert me.json()["role"] == "admin"
            assert me.json()["must_change_password"] is False
            calls = client.get("/api/calls?page_size=100", headers=headers)
            assert calls.status_code == 200
            assert client.get("/api/admin/posture", headers=headers).status_code == 200
            listed = calls.json()["items"]
            assert listed
            audio = client.get(f"/api/calls/{listed[0]['id']}/audio", headers=headers)
            assert audio.status_code in {200, 404}

            operator = client.post(
                "/api/auth/login",
                json={"username": op_name, "password": "OperatorPass12!"},
            )
            assert operator.status_code == 200
            op_headers = {"Authorization": f"Bearer {operator.json()['token']}"}
            assert operator.json()["user"]["role"] == "operator"
            assert client.get("/api/admin/overview", headers=op_headers).status_code == 403
            assert client.get("/api/admin/posture", headers=op_headers).status_code == 403
            assert client.get("/api/settings", headers=op_headers).status_code == 403
            assert client.get("/api/export/csv", headers=op_headers).status_code == 403
            assert client.get("/api/dataset", headers=op_headers).status_code == 403
            own = client.get("/api/calls?page_size=100", headers=op_headers)
            assert own.status_code == 200
            own_items = own.json()["items"]
            assert own_items
            assert all(item.get("operator_external_id") == "OP-104" for item in own_items)
            foreign = next(item for item in listed if item.get("operator_external_id") != "OP-104")
            hidden = client.get(f"/api/calls/{foreign['id']}", headers=op_headers)
            assert hidden.status_code == 404
            hidden_audio = client.get(f"/api/calls/{foreign['id']}/audio", headers=op_headers)
            assert hidden_audio.status_code == 404
            note = client.patch(f"/api/calls/{own_items[0]['id']}", headers=op_headers, json={"flagged": True})
            assert note.status_code == 403
            assert client.get("/api/dashboard", headers=op_headers).status_code == 200
    finally:
        with SessionLocal() as session:
            for login_name in (name, op_name):
                row = session.query(User).filter(User.username == login_name).one_or_none()
                if row is not None:
                    session.delete(row)
                    session.commit()


def test_login_lock_and_logout_survive_restart():
    import time

    from app.auth import clear_login_failures, is_revoked, note_login_failure, restore_persisted_guards, revoke_jti
    from app.db import SessionLocal
    from app.models import SecurityMark
    from app.security import login_gate

    ip = "203.0.113.55"
    jti = "b" * 32
    clear_login_failures(ip)
    try:
        for _ in range(8):
            note_login_failure(ip, "nobody")
        login_gate.ok(ip)
        assert not login_gate.blocked(ip)
        restore_persisted_guards()
        assert login_gate.blocked(ip)
        revoke_jti(jti, int(time.time()) + 120)
        from app.auth import _revoke_lock, _revoked

        with _revoke_lock:
            _revoked.pop(jti, None)
        assert not is_revoked(jti)
        restore_persisted_guards()
        assert is_revoked(jti)
    finally:
        clear_login_failures(ip)
        with SessionLocal() as session:
            session.query(SecurityMark).filter(SecurityMark.subject == jti).delete()
            session.commit()


def test_oversized_json_is_rejected():
    from fastapi.testclient import TestClient

    from app.main import app

    payload = b'{"username":"admin","password":"' + (b"x" * 1_100_000) + b'"}'
    with TestClient(app) as client:
        response = client.post("/api/auth/login", content=payload, headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_chunked_body_without_length_is_rejected():
    import asyncio

    from app.main import app

    chunks = [
        {"type": "http.request", "body": b'{"password":"' + (b"x" * 700_000), "more_body": True},
        {"type": "http.request", "body": (b"y" * 700_000) + b'"}', "more_body": False},
    ]

    async def receive():
        if chunks:
            return chunks.pop(0)
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    start = next(item for item in sent if item["type"] == "http.response.start")
    assert start["status"] == 413


def test_cross_site_post_is_rejected_and_password_is_not_echoed():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        foreign = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "whatever"},
            headers={"Origin": "https://evil.example"},
        )
        assert foreign.status_code == 403
        same = client.post(
            "/api/auth/login",
            json={"username": "nobody-cross", "password": "whatever"},
            headers={"Origin": "http://testserver"},
        )
        assert same.status_code == 401
        leaked = client.post("/api/auth/login", json={"username": "admin", "password": ["SecretValue99!"]})
        assert leaked.status_code == 422
        assert "SecretValue99!" not in leaked.text


def test_disabled_login_looks_like_a_bad_password():
    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.db import SessionLocal
    from app.main import app
    from app.models import User

    name = "qa.disabled"
    with SessionLocal() as session:
        old = session.query(User).filter(User.username == name).one_or_none()
        if old is not None:
            session.delete(old)
            session.commit()
        session.add(
            User(
                username=name,
                password_hash=hash_password("Disabled-Pass-2026"),
                role="analyst",
                is_active=0,
                token_version=1,
            )
        )
        session.commit()
    try:
        with TestClient(app) as client:
            unknown = client.post("/api/auth/login", json={"username": "nobody-missing", "password": "Disabled-Pass-2026"})
            disabled = client.post("/api/auth/login", json={"username": name, "password": "Disabled-Pass-2026"})
            assert unknown.status_code == 401
            assert disabled.status_code == 401
            assert unknown.json()["detail"] == disabled.json()["detail"]
    finally:
        with SessionLocal() as session:
            row = session.query(User).filter(User.username == name).one_or_none()
            if row is not None:
                session.delete(row)
                session.commit()


def test_analyst_cannot_delete_script_or_full_export():
    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.db import SessionLocal
    from app.main import app
    from app.models import User

    name = "qa.analyst"
    with SessionLocal() as session:
        old = session.query(User).filter(User.username == name).one_or_none()
        if old is not None:
            session.delete(old)
            session.commit()
        session.add(
            User(
                username=name,
                password_hash=hash_password("AnalystPass12!"),
                role="analyst",
                is_active=1,
                token_version=1,
            )
        )
        session.commit()
    try:
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": name, "password": "AnalystPass12!"})
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            assert client.delete("/api/calls/1", headers=headers).status_code == 403
            assert client.put("/api/settings", headers=headers, json={"company_phrases": ["Банк"]}).status_code == 403
            assert client.post("/api/calls/retry-failed", headers=headers).status_code == 403
            assert client.get("/api/export/json?full=true", headers=headers).status_code == 403
            assert client.get("/api/export/csv", headers=headers).status_code == 200
            assert client.get("/api/dashboard", headers=headers).status_code == 200
    finally:
        with SessionLocal() as session:
            row = session.query(User).filter(User.username == name).one_or_none()
            if row is not None:
                session.delete(row)
                session.commit()


def test_whisper_model_setting_is_honored(monkeypatch, tmp_path):
    from app import transcribe

    monkeypatch.setattr("app.transcribe.settings.whisper_model", "small")
    assert transcribe.local_model_dir().name == "faster-whisper-small"
    monkeypatch.setattr("app.transcribe.settings.whisper_model", "base")
    assert transcribe.local_model_dir().name == "faster-whisper-base"
    custom = tmp_path / "my-model"
    custom.mkdir()
    monkeypatch.setattr("app.transcribe.settings.whisper_model", str(custom))
    assert transcribe.local_model_dir() == custom
    assert not transcribe.local_model_ready()


def test_database_url_choice(monkeypatch):
    import pytest as _pytest

    from app import db

    monkeypatch.setattr("app.db.settings.database_url", "")
    assert db.database_url().startswith("sqlite:///")
    monkeypatch.setattr("app.db.settings.database_url", "postgresql://u:p@127.0.0.1:5432/x")
    assert db.database_url() == "postgresql+psycopg://u:p@127.0.0.1:5432/x"
    monkeypatch.setattr("app.db.settings.database_url", "mysql://u:p@h/x")
    with _pytest.raises(RuntimeError):
        db.database_url()


def test_default_compute_type_is_float32():
    from app.config import Settings

    assert Settings(_env_file=None).whisper_compute_type == "float32"
