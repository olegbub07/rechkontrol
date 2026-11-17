"""152-ФЗ: шифрование расшифровок, журнал прослушиваний, срок хранения, ключ вне data/, уведомление о записи."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import text

from tests.helpers import auth, ensure_user
from tests.test_upload_stream import _wav_bytes


def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr("app.worker.claim_next", lambda: None)
    return TestClient(app)


def _new_call(session, name="152fz.wav", text_="Добрый день, меня зовут Марина, карта 4276 1234 5678 9012", days_ago=0):
    from app.models import Call, Quality, Transcript
    from app.timeutil import utcnow

    when = utcnow() - timedelta(days=days_ago)
    call = Call(orig_filename=name, status="done", call_at=when, uploaded_at=when)
    session.add(call)
    session.flush()
    segs = [{"start": 0, "end": 3, "role": "operator", "text": text_}]
    session.add(Transcript(call_id=call.id, full_text=text_, segments_json=json.dumps(segs, ensure_ascii=False)))
    session.add(Quality(call_id=call.id, checks_json=json.dumps([{"id": "intro", "evidence": text_}], ensure_ascii=False)))
    session.commit()
    return call.id


def test_transcript_is_encrypted_at_rest_and_readable():
    from app.db import SessionLocal, engine
    from app.models import Transcript

    with SessionLocal() as s:
        cid = _new_call(s)
    with engine.connect() as conn:
        raw_text, raw_segs = conn.execute(
            text("SELECT full_text, segments_json FROM transcripts WHERE call_id = :c"), {"c": cid}
        ).one()
        raw_checks = conn.execute(text("SELECT checks_json FROM quality WHERE call_id = :c"), {"c": cid}).scalar()
    for raw in (raw_text, raw_segs, raw_checks):
        assert raw.startswith("rkenc1:")
        assert "Марина" not in raw and "4276" not in raw
    with SessionLocal() as s:
        tr = s.query(Transcript).filter_by(call_id=cid).one()
        assert "Марина" in tr.full_text
        assert json.loads(tr.segments_json)[0]["role"] == "operator"


def test_old_plaintext_rows_still_read_and_get_encrypted():
    from app.db import SessionLocal, encrypt_plain_texts, engine
    from app.models import Transcript

    with SessionLocal() as s:
        cid = _new_call(s, name="legacy.wav")
    # как в старой версии: открытый текст прямо в базе
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE transcripts SET full_text = :t, segments_json = :j WHERE call_id = :c"),
            {"t": "старый открытый текст", "j": json.dumps([{"start": 0, "end": 1, "role": "client", "text": "старый"}]), "c": cid},
        )
    with SessionLocal() as s:
        assert s.query(Transcript).filter_by(call_id=cid).one().full_text == "старый открытый текст"
    assert encrypt_plain_texts() >= 1
    with engine.connect() as conn:
        raw = conn.execute(text("SELECT full_text FROM transcripts WHERE call_id = :c"), {"c": cid}).scalar()
    assert raw.startswith("rkenc1:")
    with SessionLocal() as s:
        assert s.query(Transcript).filter_by(call_id=cid).one().full_text == "старый открытый текст"
    assert encrypt_plain_texts() == 0  # повторный запуск ничего не трогает


def test_search_works_on_encrypted_text(monkeypatch):
    from app.db import SessionLocal

    with SessionLocal() as s:
        cid = _new_call(s, name="search152.wav", text_="Клиент спрашивает про вклад Сезонный и ставку")
    ensure_user("qa.152.admin", "admin")
    with _client(monkeypatch) as client:
        h = auth(client, "qa.152.admin")
        items = client.get("/api/calls", headers=h, params={"q": "сезонный", "page_size": 100}).json()["items"]
        assert cid in [c["id"] for c in items]
        items = client.get("/api/calls", headers=h, params={"q": "нет-такого-текста-152", "page_size": 100}).json()["items"]
        assert cid not in [c["id"] for c in items]


def test_audio_playback_is_audited(monkeypatch):
    from app.db import SessionLocal
    from app.models import AuditLog

    ensure_user("qa.152.listener", "analyst")
    with _client(monkeypatch) as client:
        h = auth(client, "qa.152.listener")
        up = client.post("/api/calls/upload", headers=h, files=[("files", ("listen.wav", _wav_bytes(1.0), "audio/wav"))])
        cid = up.json()[0]["id"]
        res = client.get(f"/api/calls/{cid}/audio", headers=h)
        assert res.status_code == 200 and res.content[:4] == b"RIFF"
    with SessionLocal() as s:
        row = (
            s.query(AuditLog)
            .filter(AuditLog.action == "call.audio", AuditLog.username == "qa.152.listener")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert row is not None and f"№{cid}" in row.detail


def test_retention_purges_old_calls_with_files(monkeypatch):
    from app.config import settings
    from app.db import SessionLocal
    from app.models import AuditLog, Call, Transcript
    from app.retention import purge_expired

    audio = settings.audio_dir / "0123456789abcdef0123456789abc152.bin"
    audio.write_bytes(b"x")
    with SessionLocal() as s:
        old_id = _new_call(s, name="old.wav", days_ago=400)
        s.get(Call, old_id).stored_name = "0123456789abcdef0123456789abc152.bin"
        s.commit()
        fresh_id = _new_call(s, name="fresh.wav", days_ago=10)
    assert purge_expired(0) == 0  # 0 - автоудаление выключено
    assert purge_expired(365) >= 1
    with SessionLocal() as s:
        assert s.get(Call, old_id) is None
        assert s.query(Transcript).filter_by(call_id=old_id).count() == 0
        assert s.get(Call, fresh_id) is not None
        log = s.query(AuditLog).filter(AuditLog.action == "retention.purge").order_by(AuditLog.id.desc()).first()
        assert log is not None and str(old_id) in log.detail and "old.wav" not in log.detail
    assert not audio.exists()


@pytest.fixture
def fresh_crypto(monkeypatch, tmp_path):
    """Отдельный каталог данных и сброс кеша ключа, чтобы не трогать ключ остальных тестов."""
    import app.crypto as crypto
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(settings, "master_key", "")
    monkeypatch.setattr(settings, "master_key_file", "")
    monkeypatch.setattr(crypto, "_fernet", None)
    yield settings, crypto, tmp_path
    crypto._fernet = None


def test_legacy_key_moves_out_of_data(fresh_crypto, monkeypatch):
    settings, crypto, tmp = fresh_crypto
    legacy = settings.legacy_key_path
    old_key = crypto.Fernet.generate_key()
    legacy.write_bytes(old_key)
    token = crypto.Fernet(old_key).encrypt(b"secret")

    target = tmp / "keys" / "master.key"
    monkeypatch.setattr(settings, "master_key_file", str(target))
    assert crypto.get_fernet().decrypt(token) == b"secret"
    assert target.read_bytes().strip() == old_key
    assert not legacy.exists()
    assert settings.key_moved_marker.exists()
    assert crypto.key_location() == "file"

    # MASTER_KEY_FILE потеряли: новый ключ не создаётся, иначе старые данные стали бы нечитаемы
    crypto._fernet = None
    monkeypatch.setattr(settings, "master_key_file", "")
    with pytest.raises(crypto.KeyStoreError):
        crypto.get_fernet()
    assert not legacy.exists()


def test_two_different_keys_are_refused(fresh_crypto, monkeypatch):
    settings, crypto, tmp = fresh_crypto
    settings.legacy_key_path.write_bytes(crypto.Fernet.generate_key())
    target = tmp / "other.key"
    target.write_bytes(crypto.Fernet.generate_key())
    monkeypatch.setattr(settings, "master_key_file", str(target))
    with pytest.raises(crypto.KeyStoreError):
        crypto.get_fernet()
    assert settings.legacy_key_path.exists()


def test_master_key_from_env(fresh_crypto, monkeypatch):
    settings, crypto, _tmp = fresh_crypto
    key = crypto.Fernet.generate_key()
    settings.legacy_key_path.write_bytes(key)
    monkeypatch.setattr(settings, "master_key", key.decode())
    assert crypto.get_fernet().decrypt(crypto.Fernet(key).encrypt(b"ok")) == b"ok"
    assert not settings.legacy_key_path.exists()
    assert crypto.key_location() == "env"


def test_recording_notice_off_by_default_and_works_when_enabled():
    from app.checklist import default_checklist
    from app.quality import analyze

    turns = [
        {"role": "operator", "text": "Добрый день, разговор записывается. Банк Центр-инвест, меня зовут Ольга.", "start": 0, "end": 3},
        {"role": "client", "text": "Здравствуйте.", "start": 3.5, "end": 4},
    ]
    assert "recording_notice" not in {c.id for c in analyze(turns).checks}
    cl = default_checklist()
    for row in cl["criteria"]:
        if row["id"] == "recording_notice":
            row["enabled"] = True
    checks = {c.id: c for c in analyze(turns, checklist=cl).checks}
    assert checks["recording_notice"].passed
    turns[0]["text"] = "Добрый день, банк Центр-инвест."
    checks = {c.id: c for c in analyze(turns, checklist=cl).checks}
    assert not checks["recording_notice"].passed
