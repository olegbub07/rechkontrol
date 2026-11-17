import json
import re
import shutil
import subprocess

import pytest

from tests.helpers import auth, ensure_user


def _text(pdf: bytes, tmp_path) -> str:
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext не установлен")
    path = tmp_path / "r.pdf"
    path.write_bytes(pdf)
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode("utf-8")


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _demo_operator():
    from app.db import SessionLocal
    from app.models import Call, Operator

    with SessionLocal() as s:
        op = s.query(Operator).filter(Operator.external_id == "OP-104").one()
        calls = s.query(Call).filter(Call.operator_id == op.id, Call.status == "done").all()
        first = min(c.call_at for c in calls).date().isoformat()
        last = max(c.call_at for c in calls).date().isoformat()
        segs = [json.loads(c.transcript.segments_json) for c in calls if c.transcript]
        return op.id, op.name, first, last, segs


def test_pdf_report_roles_and_content(tmp_path):
    from app.db import SessionLocal
    from app.models import AuditLog
    from app.pii import mask_text

    ensure_user("qa.pdf.admin", "admin")
    ensure_user("qa.pdf.analyst", "analyst")
    ensure_user("qa.pdf.op", "operator", "OP-104")
    op_id, name, first, last, segs = _demo_operator()
    with _client() as client:
        url = f"/api/reports/operator/{op_id}.pdf?date_from={first}&date_to={last}"
        assert client.get(url).status_code == 401
        assert client.get(url, headers=auth(client, "qa.pdf.op")).status_code == 403
        r = client.get(url, headers=auth(client, "qa.pdf.analyst"))
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF") and b"DejaVuSans" in r.content
        assert "filename*=UTF-8''" in r.headers["content-disposition"]
        r2 = client.get(url, headers=auth(client, "qa.pdf.admin"))
        assert r2.status_code == 200
        with SessionLocal() as s:
            assert s.query(AuditLog).filter(AuditLog.action == "report.operator", AuditLog.username == "qa.pdf.analyst").count() == 1
    text = _text(r.content, tmp_path)
    assert name in text
    for word in ("Сводка", "Динамика по неделям", "Частые ошибки", "Примеры звонков", "Итоговая оценка"):
        assert word in text
    # Сырые персональные данные из примеров не попадают в PDF.
    flat = re.sub(r"\s+", " ", text)
    for seg_list in segs:
        for seg in seg_list:
            raw = seg.get("text", "")
            for digits in re.findall(r"\d[\d \-]{8,}\d", raw):
                if digits not in mask_text(raw):
                    assert digits not in flat


def test_pdf_report_validation():
    ensure_user("qa.pdf.admin", "admin")
    op_id, *_ = _demo_operator()
    with _client() as client:
        h = auth(client, "qa.pdf.admin")
        assert client.get("/api/reports/operator/999999.pdf", headers=h).status_code == 404
        assert client.get(f"/api/reports/operator/{op_id}.pdf?date_from=2026-13-01", headers=h).status_code == 400
        assert client.get(f"/api/reports/operator/{op_id}.pdf?date_from=2026-02-01&date_to=2026-01-01", headers=h).status_code == 400
        assert client.get(f"/api/reports/operator/{op_id}.pdf?date_from=2020-01-01&date_to=2026-01-01", headers=h).status_code == 400
        empty = client.get(f"/api/reports/operator/{op_id}.pdf?date_from=2001-01-01&date_to=2001-01-31", headers=h)
        assert empty.status_code == 200 and empty.content.startswith(b"%PDF")
        default = client.get(f"/api/reports/operator/{op_id}.pdf", headers=h)
        assert default.status_code == 200
