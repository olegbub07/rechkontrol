import io
import wave


def _wav_bytes(seconds=1.0, rate=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


def test_encrypted_stream_roundtrip_and_legacy(tmp_path):
    from app.crypto import STREAM_CHUNK, EncryptedWriter, decrypt_bytes, decrypt_file, encrypt_bytes

    payload = bytes(range(256)) * (STREAM_CHUNK // 256 * 2 + 3)
    enc = tmp_path / "a.bin"
    writer = EncryptedWriter(enc)
    for i in range(0, len(payload), 300_000):
        writer.write(payload[i : i + 300_000])
    writer.close()
    assert decrypt_bytes(enc.read_bytes()) == payload
    out = tmp_path / "a.out"
    decrypt_file(enc, out)
    assert out.read_bytes() == payload
    legacy = tmp_path / "legacy.bin"
    legacy.write_bytes(encrypt_bytes(b"old-format"))
    decrypt_file(legacy, out)
    assert out.read_bytes() == b"old-format"


def test_upload_streams_to_disk_and_rejects_oversize(monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.config import settings
    from app.crypto import decrypt_bytes
    from app.db import SessionLocal
    from app.main import app
    from app.models import Call, User

    monkeypatch.setattr("app.worker.claim_next", lambda: None)
    name = "qa.uploader"
    with SessionLocal() as session:
        if session.query(User).filter(User.username == name).one_or_none() is None:
            session.add(User(username=name, password_hash=hash_password("Upload-Check-2026"), role="analyst", is_active=1, token_version=1))
            session.commit()
    data = _wav_bytes(2.0)
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": name, "password": "Upload-Check-2026"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        ok = client.post("/api/calls/upload", headers=headers, files=[("files", ("a.wav", data, "audio/wav"))])
        assert ok.status_code == 200, ok.text
        call_id = ok.json()[0]["id"]
        with SessionLocal() as session:
            stored = session.get(Call, call_id).stored_name
        raw = (settings.audio_dir / stored).read_bytes()
        assert raw.startswith(b"RKC1\n")
        assert decrypt_bytes(raw) == data
        bad = client.post("/api/calls/upload", headers=headers, files=[("files", ("x.wav", b"MZ" + b"\0" * 200, "audio/wav"))])
        assert bad.status_code == 400
        monkeypatch.setattr("app.api.calls.settings.max_upload_mb", 0)
        before = set(p.name for p in settings.audio_dir.iterdir())
        big = client.post("/api/calls/upload", headers=headers, files=[("files", ("b.wav", data, "audio/wav"))])
        assert big.status_code == 413
        assert set(p.name for p in settings.audio_dir.iterdir()) == before
