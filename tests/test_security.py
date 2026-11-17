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


def test_bootstrap_hint_only_on_loopback_while_default():
    from app.auth import bootstrap_hint

    shown = bootstrap_hint("127.0.0.1", "admin", True)
    assert shown["username"] == "admin"
    assert shown["password"] == "CenterInvest2026!"
    assert bootstrap_hint("8.8.8.8", "admin", True) == {"show_password": False, "username": None}
    hidden = bootstrap_hint("localhost", "admin", False)
    assert hidden["show_password"] is False
    assert "password" not in hidden


def test_db_rejects_attach_and_schema_rewrite():
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    from app.db import engine

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

    from app.main import app

    with TestClient(app) as client:
        hint = client.get("/api/auth/bootstrap")
        assert hint.status_code == 200
        body = hint.json()
        assert "show_password" in body
        if body["show_password"]:
            assert body["password"] == "CenterInvest2026!"
            login = client.post(
                "/api/auth/login",
                json={"username": body["username"], "password": body["password"]},
            )
            assert login.status_code == 200
            token = login.json()["token"]
            headers = {"Authorization": f"Bearer {token}"}
            me = client.get("/api/auth/me", headers=headers)
            assert me.status_code == 200
            assert me.json()["must_change_password"] is True
            blocked = client.get("/api/calls", headers=headers)
            assert blocked.status_code == 403
            changed = client.get("/api/admin/posture", headers=headers)
            assert changed.status_code == 403


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
