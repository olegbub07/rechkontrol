"""Интеграции: API-ключи и REST API, папка входящих, вебхуки."""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.helpers import auth, ensure_user


def _wav(seconds=1.0, rate=8000, tone=1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes([tone % 256, 0]) * int(seconds * rate))
    return buf.getvalue()


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr("app.worker.claim_next", lambda: None)
    with TestClient(app) as c:
        yield c


def _new_key(scopes=("calls:read", "calls:write"), name="CRM test"):
    from app.apikeys import create_key
    from app.db import SessionLocal

    with SessionLocal() as session:
        row, key = create_key(session, name, list(scopes))
        session.commit()
        return row.id, key


def _finish_call(call_id: int, violations=("no_rudeness",)) -> None:
    """Имитирует окончание обработки: оценка и расшифровка с номером карты."""
    from app.db import SessionLocal
    from app.models import Call, Quality, Transcript

    with SessionLocal() as session:
        call = session.get(Call, call_id)
        call.status, call.progress, call.duration_sec = "done", 100, 12.0
        session.add(
            Transcript(
                call_id=call_id,
                full_text="Номер карты 2200 1234 5678 9010",
                segments_json=json.dumps(
                    [
                        {"start": 0, "end": 2, "role": "operator", "speaker": "A", "text": "Подскажите номер карты?"},
                        {"start": 2, "end": 5, "role": "client", "speaker": "B", "text": "2200 1234 5678 9010"},
                    ],
                    ensure_ascii=False,
                ),
            )
        )
        session.add(
            Quality(
                call_id=call_id,
                politeness=50, completeness=70, script_compliance=60, resolution=80, overall=64.5,
                tonality="neutral", tonality_score=0.0, talk_ratio_operator=0.5, violation_pct=10,
                violations_json=json.dumps([{"id": v, "title": v, "category": "politeness", "detail": "x"} for v in violations]),
                recommendations_json='["Без грубости"]', checks_json="[]", summary="итог",
            )
        )
        session.commit()


# ---------- API-ключи и REST API ----------


def test_api_key_is_hashed_scoped_and_revocable(client):
    from app.db import SessionLocal
    from app.models import ApiKey

    ensure_user("qa.integ.admin", "admin")
    admin = auth(client, "qa.integ.admin")
    made = client.post("/api/admin/integrations/keys", headers=admin, json={"name": "АТС", "scopes": ["calls:read"]})
    assert made.status_code == 200, made.text
    key = made.json()["key"]
    assert key.startswith("rk_")
    with SessionLocal() as session:
        row = session.get(ApiKey, made.json()["id"])
        assert row.key_hash == hashlib.sha256(key.encode()).hexdigest()
        assert key not in (row.key_hash + row.prefix)
    h = {"X-API-Key": key}
    assert client.get("/api/v1/calls", headers=h).status_code == 200
    # только чтение: загрузка запрещена
    up = client.post("/api/v1/calls", headers=h, files={"file": ("a.wav", _wav(), "audio/wav")})
    assert up.status_code == 403
    assert client.get("/api/v1/calls", headers={"X-API-Key": key[:-2] + "xx"}).status_code == 401
    assert client.get("/api/v1/calls").status_code == 401
    # сессия кабинета не подходит вместо ключа, а ключ не открывает кабинет
    assert client.get("/api/v1/calls", headers=admin).status_code == 401
    assert client.get("/api/calls", headers={"Authorization": f"Bearer {key}"}).status_code == 401
    listing = client.get("/api/admin/integrations", headers=admin).json()
    assert all("key" not in k for k in listing["keys"])
    assert client.delete(f"/api/admin/integrations/keys/{made.json()['id']}", headers=admin).status_code == 200
    assert client.get("/api/v1/calls", headers=h).status_code == 401
    ensure_user("qa.integ.analyst", "analyst")
    assert client.get("/api/admin/integrations", headers=auth(client, "qa.integ.analyst")).status_code == 403


def test_api_upload_status_result_and_duplicate(client):
    from app.config import settings
    from app.crypto import decrypt_bytes
    from app.db import SessionLocal
    from app.models import AuditLog, Call

    _, key = _new_key()
    h = {"X-API-Key": key}
    data = _wav(1.5, tone=7)
    up = client.post(
        "/api/v1/calls",
        headers=h,
        files={"file": ("2026-11-07_OP-104_test.wav", data, "audio/wav")},
        data={"operator_name": "Внешний Оператор", "operator_first": "true"},
    )
    assert up.status_code == 202, up.text
    body = up.json()
    assert body["status"] == "queued" and body["source"] == "api" and body["duplicate"] is False
    call_id = body["id"]
    with SessionLocal() as session:
        call = session.get(Call, call_id)
        assert call.source_ref == hashlib.sha256(data).hexdigest()
        assert decrypt_bytes((settings.audio_dir / call.stored_name).read_bytes()) == data
        assert session.query(AuditLog).filter(AuditLog.action == "call.api_upload", AuditLog.username == "api:CRM test").count() >= 1
    again = client.post("/api/v1/calls", headers=h, files={"file": ("copy.wav", data, "audio/wav")})
    assert again.status_code == 200 and again.json()["duplicate"] is True and again.json()["id"] == call_id
    bad = client.post("/api/v1/calls", headers=h, files={"file": ("x.wav", b"MZ" + b"\0" * 200, "audio/wav")})
    assert bad.status_code == 400
    st = client.get(f"/api/v1/calls/{call_id}", headers=h)
    assert st.status_code == 200 and st.json()["status"] == "queued"
    assert client.get(f"/api/v1/calls/{call_id}/result", headers=h).status_code == 409
    assert client.get("/api/v1/calls/999999", headers=h).status_code == 404
    _finish_call(call_id)
    res = client.get(f"/api/v1/calls/{call_id}/result", headers=h)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["scores"]["overall"] == 64.5
    assert [v["id"] for v in out["violations"]] == ["no_rudeness"]
    assert [t["role"] for t in out["transcript"]] == ["operator", "client"]
    assert "5678" not in json.dumps(out, ensure_ascii=False)  # номер карты замаскирован
    listed = client.get(f"/api/v1/calls?since_id={call_id - 1}&status=done", headers=h).json()
    assert [c["id"] for c in listed["items"]] == [call_id]


def test_openapi_describes_external_api():
    from app.main import app

    spec = app.openapi()
    for path in ("/api/v1/calls", "/api/v1/calls/{call_id}", "/api/v1/calls/{call_id}/result"):
        assert path in spec["paths"]
    up = spec["paths"]["/api/v1/calls"]["post"]
    assert up["tags"] == ["external-api"] and up["summary"]
    assert {"APIKeyHeader": []} in up["security"]
    assert spec["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-API-Key"
    assert "CallResultOut" in spec["components"]["schemas"]


def test_cli_manages_keys(capsys):
    from app.cli import main

    assert main(["apikey", "create", "--name", "BI выгрузка", "--scopes", "calls:read"]) == 0
    printed = capsys.readouterr().out
    key = [line for line in printed.splitlines() if line.startswith("rk_")][0]
    assert main(["apikey", "list"]) == 0
    assert "BI выгрузка" in capsys.readouterr().out
    from app.apikeys import verify_key
    from app.db import SessionLocal

    with SessionLocal() as session:
        row = verify_key(session, key)
        assert row is not None and row.scopes == "calls:read"
        key_id = row.id
    assert main(["apikey", "revoke", str(key_id)]) == 0
    with SessionLocal() as session:
        assert verify_key(session, key) is None
    assert main(["apikey", "create", "--name", "x", "--scopes", "calls:delete"]) == 2


# ---------- Папка входящих ----------


@pytest.fixture()
def inbox(tmp_path, monkeypatch):
    from app import inbox as ib

    monkeypatch.setattr(ib.settings, "inbox_dir", str(tmp_path))
    monkeypatch.setattr(ib.settings, "inbox_settle_sec", 2.0)
    ib._seen.clear()
    return tmp_path


def _age(path, seconds=10):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_inbox_picks_settled_files_once(inbox):
    from app.db import SessionLocal
    from app.inbox import scan_once
    from app.models import Call

    good = inbox / "2026-11-08_OP-104_inbox.wav"
    good.write_bytes(_wav(1.0, tone=21))
    _age(good)
    (inbox / "upload.wav.part").write_bytes(_wav(1.0, tone=22))
    (inbox / "notes.txt").write_text("не аудио")
    now = time.time()
    assert scan_once(now) == []  # первый проход только запоминает размер
    results = scan_once(now + 3)
    assert [(n, o) for n, o, _ in results] == [(good.name, "queued")]
    call_id = results[0][2]
    assert not good.exists() and (inbox / "done" / good.name).exists()
    assert (inbox / "upload.wav.part").exists() and (inbox / "notes.txt").exists()
    with SessionLocal() as session:
        call = session.get(Call, call_id)
        assert call.source == "inbox" and call.status == "queued"
        assert call.call_at.date().isoformat() == "2026-11-08"
    # тот же файл ещё раз: звонок не дублируется
    dup = inbox / "again.wav"
    dup.write_bytes(_wav(1.0, tone=21))
    _age(dup)
    scan_once(now + 4)
    assert [(o, c) for _, o, c in scan_once(now + 7)] == [("duplicate", call_id)]
    assert scan_once(now + 10) == []


def test_inbox_waits_for_growing_file_and_rejects_bad(inbox):
    from app.inbox import scan_once

    growing = inbox / "big.wav"
    growing.write_bytes(_wav(0.5, tone=31))
    _age(growing)
    now = time.time()
    scan_once(now)
    with open(growing, "ab") as fh:  # файл ещё дописывается
        fh.write(b"\x02\x00" * 4000)
    _age(growing)
    assert scan_once(now + 3) == []
    assert [o for _, o, _ in scan_once(now + 6)] == ["queued"]
    bad = inbox / "broken.mp3"
    bad.write_bytes(b"MZ" + b"\0" * 300)
    _age(bad)
    scan_once(now + 7)
    assert [o for _, o, _ in scan_once(now + 10)] == ["failed"]
    assert (inbox / "failed" / "broken.mp3").exists()
    assert "аудио" in (inbox / "failed" / "broken.mp3.error.txt").read_text(encoding="utf-8")


def test_inbox_recovers_files_left_in_processing(inbox):
    from app.inbox import recover_processing

    (inbox / "processing").mkdir()
    (inbox / "processing" / "deadbeef__left.wav").write_bytes(_wav(0.7, tone=41))
    assert recover_processing() == 1
    assert (inbox / "done" / "left.wav").exists()
    assert not any((inbox / "processing").iterdir())


# ---------- Вебхуки ----------


class _Sink(BaseHTTPRequestHandler):
    code = 200
    hits: list = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).hits.append((self.headers, body))
        self.send_response(type(self).code)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture()
def sink():
    _Sink.hits = []
    _Sink.code = 200
    server = HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/hook", _Sink
    server.shutdown()


@pytest.fixture()
def hooks(monkeypatch):
    from app import webhooks as wh

    monkeypatch.setattr(wh.settings, "webhook_secret", "s3cret-test")
    monkeypatch.setattr(wh.settings, "webhook_only_violations", False)
    monkeypatch.setattr(wh.settings, "webhook_violation_ids", "")
    monkeypatch.setattr(wh.settings, "webhook_backoff_sec", 30.0)
    monkeypatch.setattr(wh.settings, "webhook_max_attempts", 3)
    monkeypatch.setattr(wh.settings, "webhook_allow_public", False)
    return wh


def _queued_call(tone: int) -> int:
    from app.db import SessionLocal
    from app.ingest import queue_call, store_chunks

    with SessionLocal() as session:
        call = queue_call(session, store_chunks([_wav(0.6, tone=tone)], f"hook{tone}.wav"), f"hook{tone}.wav", "api")
        session.commit()
        return call.id


def test_signature_roundtrip(hooks):
    body = b'{"event":"call.processed"}'
    header = hooks.sign(body, 1_700_000_000, "k")
    assert hooks.verify(body, header, "k", now=1_700_000_010)
    assert not hooks.verify(body + b" ", header, "k", now=1_700_000_010)
    assert not hooks.verify(body, header, "other", now=1_700_000_010)
    assert not hooks.verify(body, header, "k", now=1_700_009_999)  # старая подпись


def test_webhook_delivered_signed_and_logged(hooks, sink, monkeypatch):
    from app.db import SessionLocal
    from app.models import WebhookDelivery

    url, handler = sink
    monkeypatch.setattr(hooks.settings, "webhook_urls", url)
    call_id = _queued_call(51)
    _finish_call(call_id)
    hooks.notify_processed(call_id)
    assert hooks.dispatch_due() >= 1
    headers, body = handler.hits[-1]
    assert headers["X-RK-Event"] == "call.processed"
    assert hooks.verify(body, headers["X-RK-Signature"], "s3cret-test")
    payload = json.loads(body)
    assert payload["call"]["id"] == call_id and payload["quality"]["violations"][0]["id"] == "no_rudeness"
    assert "2200" not in body.decode()  # расшифровки в теле нет
    with SessionLocal() as session:
        row = session.query(WebhookDelivery).filter(WebhookDelivery.call_id == call_id).one()
        assert row.status == "delivered" and row.attempts == 1 and row.last_code == 200


def test_webhook_retries_with_backoff_then_fails(hooks, sink, monkeypatch):
    from app.db import SessionLocal
    from app.models import WebhookDelivery

    url, handler = sink
    handler.code = 500
    monkeypatch.setattr(hooks.settings, "webhook_urls", url)
    call_id = _queued_call(52)
    _finish_call(call_id)
    hooks.notify_processed(call_id)
    hooks.dispatch_due()
    with SessionLocal() as session:
        row = session.query(WebhookDelivery).filter(WebhookDelivery.call_id == call_id).one()
        assert row.status == "pending" and row.attempts == 1 and row.last_code == 500
        wait = (row.next_try_at - row.created_at).total_seconds()
        assert 25 <= wait <= 40
    hooks.dispatch_due()  # пауза ещё не прошла: попытки не было
    assert len(handler.hits) == 1
    hooks.dispatch_due(force=True)
    hooks.dispatch_due(force=True)
    with SessionLocal() as session:
        row = session.query(WebhookDelivery).filter(WebhookDelivery.call_id == call_id).one()
        assert row.status == "failed" and row.attempts == 3


def test_webhook_filter_and_outside_network(hooks, monkeypatch):
    from app.db import SessionLocal
    from app.models import Call, WebhookDelivery

    monkeypatch.setattr(hooks.settings, "webhook_urls", "http://8.8.8.8/hook")
    monkeypatch.setattr(hooks.settings, "webhook_only_violations", True)
    monkeypatch.setattr(hooks.settings, "webhook_violation_ids", "no_rudeness")
    clean = _queued_call(53)
    _finish_call(clean, violations=("closing",))
    rude = _queued_call(54)
    _finish_call(rude, violations=("no_rudeness",))
    with SessionLocal() as session:
        assert hooks.enqueue_call(session, session.get(Call, clean)) == 0
        assert hooks.enqueue_call(session, session.get(Call, rude)) == 1
        session.commit()
    hooks.dispatch_due()
    with SessionLocal() as session:
        row = session.query(WebhookDelivery).filter(WebhookDelivery.call_id == rude).one()
        assert row.status == "failed" and "вне внутренней сети" in row.last_error


def test_webhooks_need_secret(hooks, monkeypatch):
    monkeypatch.setattr(hooks.settings, "webhook_urls", "http://127.0.0.1:9/hook")
    monkeypatch.setattr(hooks.settings, "webhook_secret", "")
    assert not hooks.enabled()
    assert hooks.start_webhooks() is False


def test_worker_announces_processed_call(monkeypatch):
    from app import worker
    from app.db import SessionLocal
    from app.models import Call

    call_id = _queued_call(55)
    with SessionLocal() as session:
        call = session.get(Call, call_id)
        call.status = "processing"
        session.commit()
    seen = []
    monkeypatch.setattr(worker, "notify_processed", lambda cid: seen.append(cid))
    monkeypatch.setattr(worker, "decrypt_file", lambda *a, **k: (_ for _ in ()).throw(ValueError("битый файл")))
    worker.process_call(call_id)
    with SessionLocal() as session:
        assert session.get(Call, call_id).status == "error"
    assert seen == [call_id]
