"""Замечания тестировщика (BUGS.md): дата из имени файла, экспорт по-русски, журнал аудита."""

import csv
import io
from datetime import datetime

from tests.helpers import auth, ensure_user
from tests.test_upload_stream import _wav_bytes


def test_date_and_operator_from_filename():
    from app.timeutil import date_from_filename, operator_code_from_filename

    assert date_from_filename("2026-11-07_card_block.wav") == datetime(2026, 11, 7, 12, 0)
    assert date_from_filename("20261107_1530_x.mp3") == datetime(2026, 11, 7, 15, 30)
    assert date_from_filename("call_2026.11.07-15-30-12.wav") == datetime(2026, 11, 7, 15, 30, 12)
    assert date_from_filename("2026-13-45_x.wav") is None
    assert date_from_filename("payroll.wav") is None
    assert date_from_filename("12345678901.wav") is None
    assert operator_code_from_filename("2026-11-07_OP-104_card.wav") == "OP-104"
    assert operator_code_from_filename("op_217.wav") == "OP-217"
    assert operator_code_from_filename("stop104.wav") is None


def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr("app.worker.claim_next", lambda: None)
    return TestClient(app)


def test_upload_takes_date_and_operator_from_filename(monkeypatch):
    from app.db import SessionLocal
    from app.models import AuditLog, Call

    ensure_user("qa.fn.analyst", "analyst")
    with _client(monkeypatch) as client:
        h = auth(client, "qa.fn.analyst")
        files = [
            ("files", ("2026-11-07_OP-104_card_block.wav", _wav_bytes(1.0), "audio/wav")),
            ("files", ("no_date.wav", _wav_bytes(1.0), "audio/wav")),
        ]
        out = client.post("/api/calls/upload", headers=h, files=files)
        assert out.status_code == 200, out.text
        first, second = out.json()
        assert first["call_at"].startswith("2026-11-07T12:00")
        assert first["operator_external_id"] == "OP-104"
        assert not second["call_at"].startswith("2026-11-07")
        assert second["operator_external_id"] in (None, "")
        # дата из формы важнее имени файла
        forced = client.post(
            "/api/calls/upload", headers=h, data={"call_at": "2026-11-01T09:00"},
            files=[("files", ("2026-11-07_x.wav", _wav_bytes(1.0), "audio/wav"))],
        ).json()[0]
        assert forced["call_at"].startswith("2026-11-01T09:00")
        with SessionLocal() as s:
            detail = s.query(AuditLog).filter(AuditLog.action == "call.upload", AuditLog.username == "qa.fn.analyst").order_by(AuditLog.id).first().detail
            assert "2026-11-07_OP-104_card_block.wav" in detail and "no_date.wav" in detail
            for c in s.query(Call).filter(Call.id.in_([first["id"], second["id"], forced["id"]])).all():
                s.delete(c)
            s.commit()


def test_csv_and_json_export_for_russian_users(monkeypatch):
    ensure_user("qa.ex.admin", "admin")
    with _client(monkeypatch) as client:
        h = auth(client, "qa.ex.admin")
        res = client.get("/api/export/csv", headers=h)
        assert res.status_code == 200
        assert 'filename="kpi_calls_' in res.headers["content-disposition"]
        rows = list(csv.reader(io.StringIO(res.content.decode("utf-8-sig")), delimiter=";"))
        head = rows[0]
        tone = [r[head.index("Тональность")] for r in rows[1:] if r[head.index("Тональность")]]
        assert tone and set(tone) <= {"позитивная", "нейтральная", "негативная"}
        for r in rows[1:]:
            dur = r[head.index("Длительность, сек")]
            if dur:
                assert len(dur.replace(",", ".").partition(".")[2]) <= 1
        assert {r[head.index("Статус")] for r in rows[1:]} <= {"в очереди", "обрабатывается", "готово", "ошибка"}
        kpi = client.get("/api/export/json", headers=h)
        full = client.get("/api/export/json?full=true", headers=h)
        assert kpi.headers["content-disposition"] != full.headers["content-disposition"]
        assert 'filename="kpi_calls_' in kpi.headers["content-disposition"]
        assert 'filename="calls_full_' in full.headers["content-disposition"]
        assert all("tonality_ru" in row for row in kpi.json())


def test_audit_local_time_and_unmask_detail(monkeypatch):
    from app.db import SessionLocal
    from app.models import AuditLog, Call

    ensure_user("qa.au.admin", "admin")
    with _client(monkeypatch) as client:
        h = auth(client, "qa.au.admin")
        with SessionLocal() as s:
            call = s.query(Call).filter(Call.status == "done").first()
            call_id, fname = call.id, call.orig_filename
        assert client.get(f"/api/calls/{call_id}?unmasked=true", headers=h).status_code == 200
        audit = client.get("/api/admin/audit?page_size=100", headers=h).json()
        assert audit["tz"] == "Europe/Moscow"
        row = next(r for r in audit["items"] if r["action"] == "call.unmask" and r["username"] == "qa.au.admin")
        assert f"№{call_id}" in row["detail"] and fname in row["detail"]
        with SessionLocal() as s:
            stored = s.get(AuditLog, row["id"]).created_at
        local = datetime.strptime(row["created_at_local"], "%d.%m.%Y %H:%M:%S")
        assert round((local - stored.replace(microsecond=0)).total_seconds() / 3600) == 3


def test_week_delta_needs_two_calls_per_week():
    from app.analytics import trend_delta

    one = [{"week": "2026-11-02", "overall": 40.0, "count": 1}, {"week": "2026-11-09", "overall": 92.3, "count": 1}]
    assert trend_delta(one) is None
    two = [{"week": "2026-11-02", "overall": 70.0, "count": 2}, {"week": "2026-11-09", "overall": 75.5, "count": 3}]
    assert trend_delta(two) == 5.5


def test_compare_counts_calls_without_operator_and_assign(monkeypatch):
    from app.db import SessionLocal
    from app.models import Call, Operator

    ensure_user("qa.cmp.analyst", "analyst")
    with _client(monkeypatch) as client:
        h = auth(client, "qa.cmp.analyst")
        up = client.post("/api/calls/upload", headers=h, files=[("files", ("x.wav", _wav_bytes(1.0), "audio/wav"))]).json()[0]
        with SessionLocal() as s:
            call = s.get(Call, up["id"])
            call.status = "done"
            call.call_at = datetime.utcnow()
            s.commit()
            op_id = s.query(Operator).filter(Operator.external_id == "OP-217").one().id
        data = client.get("/api/operators/compare?period=all", headers=h).json()
        assert data["unassigned"] >= 1 and data["data_from"] <= data["data_to"]
        out = client.patch(f"/api/calls/{up['id']}", headers=h, json={"operator_id": op_id})
        assert out.status_code == 200 and out.json()["operator_id"] == op_id
        assert client.patch(f"/api/calls/{up['id']}", headers=h, json={"operator_id": 999999}).status_code == 404
        assert client.patch(f"/api/calls/{up['id']}", headers=h, json={"operator_id": 0}).json()["operator_id"] is None
        with SessionLocal() as s:
            s.delete(s.get(Call, up["id"]))
            s.commit()


def test_unknown_operator_code_warns(monkeypatch):
    from app.db import SessionLocal
    from app.models import Call

    ensure_user("qa.op999.analyst", "analyst")
    with _client(monkeypatch) as client:
        h = auth(client, "qa.op999.analyst")
        files = [
            ("files", ("2026-11-07_OP-999_x.wav", _wav_bytes(1.0), "audio/wav")),
            ("files", ("2026-11-07_OP-104_x.wav", _wav_bytes(1.0), "audio/wav")),
        ]
        out = client.post("/api/calls/upload", headers=h, files=files)
        assert out.status_code == 200, out.text
        unknown, known = out.json()
        assert unknown["warning"] == "оператор OP-999 не найден, звонок загружен без оператора"
        assert unknown["operator_id"] is None
        assert known["warning"] is None and known["operator_external_id"] == "OP-104"
        with SessionLocal() as s:
            for c in s.query(Call).filter(Call.id.in_([unknown["id"], known["id"]])).all():
                s.delete(c)
            s.commit()


def test_non_audio_file_is_named_so(monkeypatch):
    ensure_user("qa.fake.analyst", "analyst")
    with _client(monkeypatch) as client:
        h = auth(client, "qa.fake.analyst")
        cases = [
            (b"this is not audio\n", "внутри текст"),
            (b"hello " * 200, "внутри текст"),
            (b"%PDF-1.7\n" + b"\x00" * 200, "внутри PDF"),
            (b"\x00\x01\x02\x03" * 100, "это не аудиофайл"),
        ]
        for data, expected in cases:
            res = client.post("/api/calls/upload", headers=h, files=[("files", ("fake_audio.wav", data, "audio/wav"))])
            assert res.status_code == 400
            detail = res.json()["detail"]
            assert "это не аудиофайл" in detail and expected in detail and "маленький" not in detail
        tiny = client.post("/api/calls/upload", headers=h, files=[("files", ("t.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav"))])
        assert tiny.status_code == 400 and "слишком короткий" in tiny.json()["detail"]


def test_xlsx_export_has_real_numbers_and_standard_csv(monkeypatch):
    import openpyxl

    ensure_user("qa.xl.analyst", "analyst")
    with _client(monkeypatch) as client:
        h = auth(client, "qa.xl.analyst")
        res = client.get("/api/export/xlsx", headers=h)
        assert res.status_code == 200
        assert 'filename="kpi_calls_' in res.headers["content-disposition"] and ".xlsx" in res.headers["content-disposition"]
        ws = openpyxl.load_workbook(io.BytesIO(res.content)).active
        head = [c.value for c in ws[1]]
        rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
        # в общей базе тестов есть свежие звонки без оценки, берём готовый
        row = next(r for r in rows if r[head.index("Итоговое качество")] is not None)
        assert all(isinstance(r[head.index("Дата и время (МСК)")], datetime) for r in rows)
        assert isinstance(row[head.index("Итоговое качество")], (int, float))
        durations = [r[head.index("Длительность, сек")] for r in rows if r[head.index("Длительность, сек")] is not None]
        assert durations and all(isinstance(d, (int, float)) for d in durations)
        assert isinstance(row[head.index("Дата и время (МСК)")], datetime)
        assert row[head.index("Тональность")] in {"позитивная", "нейтральная", "негативная"}
        std = client.get("/api/export/csv?style=standard", headers=h)
        assert std.status_code == 200 and "_standard.csv" in std.headers["content-disposition"]
        rows = list(csv.reader(io.StringIO(std.content.decode("utf-8-sig"))))
        dur = [r[rows[0].index("Длительность, сек")] for r in rows[1:] if r[rows[0].index("Длительность, сек")]]
        assert dur
        assert dur and all("," not in d for d in dur) and any("." in d for d in dur)
        float(dur[0])
        assert client.get("/api/export/csv?style=bad", headers=h).status_code == 422
        op = auth_operator(client)
        if op:
            assert client.get("/api/export/xlsx", headers=op).status_code == 403


def auth_operator(client):
    from app.db import SessionLocal
    from app.models import Operator

    with SessionLocal() as s:
        if not s.query(Operator).filter(Operator.external_id == "OP-104").first():
            return None
    ensure_user("qa.xl.operator", "operator", "OP-104")
    return auth(client, "qa.xl.operator")


def test_upload_time_is_moscow_in_export(monkeypatch):
    """Звонок без даты в имени получает местное время загрузки, экспорт не отстаёт на 3 часа."""
    from datetime import timedelta

    from app.timeutil import local_now

    ensure_user("qa.tz.analyst", "analyst")
    with _client(monkeypatch) as client:
        h = auth(client, "qa.tz.analyst")
        up = client.post("/api/calls/upload", headers=h, files=[("files", ("tz_check.wav", _wav_bytes(1.0), "audio/wav"))]).json()[0]
        got = datetime.fromisoformat(up["call_at"])
        assert abs(got - local_now()) < timedelta(minutes=2)
        detail = client.get(f"/api/calls/{up['id']}", headers=h).json()
        assert detail["uploaded_at"].endswith("+03:00")
        text = client.get("/api/export/csv", headers=h).content.decode("utf-8-sig")
        assert "Дата и время (МСК)" in text.splitlines()[0]
        assert got.strftime("%H:%M") in text
