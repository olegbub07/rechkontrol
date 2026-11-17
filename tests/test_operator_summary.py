import io
from datetime import datetime, timedelta

from tests.helpers import auth, ensure_user


def _seed_period_calls():
    """Два звонка оператора OP-SUM: один грубый 10 марта 2021, второй хороший 20 марта 2021."""
    import json

    from app.db import SessionLocal
    from app.models import Call, Operator, Quality

    with SessionLocal() as s:
        op = s.query(Operator).filter(Operator.external_id == "OP-SUM").one_or_none()
        if op is None:
            op = Operator(name="Сводкина Ирина", external_id="OP-SUM")
            s.add(op)
            s.flush()
        if s.query(Call).filter(Call.operator_id == op.id).count() == 0:
            for day, overall, rude in ((10, 30.0, True), (20, 90.0, False)):
                call = Call(
                    orig_filename=f"sum_{day}.wav",
                    stored_name=f"sum_{day}.enc",
                    status="done",
                    operator_id=op.id,
                    call_at=datetime(2021, 3, day, 12, 0),
                    uploaded_at=datetime(2021, 3, day, 12, 0),
                )
                s.add(call)
                s.flush()
                checks = [{"id": "no_rudeness", "title": "Отсутствие грубости", "passed": not rude},
                          {"id": "greeting", "title": "Приветствие", "passed": False}]
                s.add(Quality(call_id=call.id, politeness=overall, completeness=overall, script_compliance=overall,
                              resolution=overall, overall=overall, tonality="neutral", tonality_score=0.0,
                              violation_pct=50.0, checks_json=json.dumps(checks, ensure_ascii=False)))
        s.commit()
        return op.id


def test_operator_summary_custom_period_roles_and_xlsx():
    from fastapi.testclient import TestClient
    from openpyxl import load_workbook

    from app.main import app

    op_id = _seed_period_calls()
    ensure_user("qa.sum.admin", "admin")
    ensure_user("qa.sum.op", "operator", "OP-SUM")
    ensure_user("qa.sum.other", "operator", "OP-104")
    with TestClient(app) as client:
        admin = auth(client, "qa.sum.admin")
        whole = client.get("/api/operators/compare?date_from=2021-03-01&date_to=2021-03-31", headers=admin).json()
        assert whole["period"] == "custom" and whole["period_days"] == 31
        row = next(r for r in whole["operators"] if r["id"] == op_id)
        assert row["calls_done"] == 2 and row["overall"] == 60.0 and row["rude_calls"] == 1
        assert row["politeness"] == 60.0 and row["resolution"] == 60.0
        assert row["top_mistakes"][0]["title"] == "Приветствие" and row["top_mistakes"][0]["share"] == 100.0
        assert whole["team"]["rude_calls"] >= 1

        late = client.get("/api/operators/compare?date_from=2021-03-15&date_to=2021-03-20", headers=admin).json()
        row = next(r for r in late["operators"] if r["id"] == op_id)
        assert row["calls_done"] == 1 and row["rude_calls"] == 0 and row["overall"] == 90.0

        assert client.get("/api/operators/compare?date_from=2021-03-20&date_to=2021-03-01", headers=admin).status_code == 400
        assert client.get("/api/operators/compare?date_from=bad", headers=admin).status_code == 400

        mine = client.get("/api/operators/compare?date_from=2021-03-01&date_to=2021-03-31", headers=auth(client, "qa.sum.op")).json()
        assert mine["scope"] == "self" and [r["id"] for r in mine["operators"]] == [op_id] and mine["team"] is None
        other = client.get("/api/operators/compare?date_from=2021-03-01&date_to=2021-03-31", headers=auth(client, "qa.sum.other")).json()
        assert op_id not in [r["id"] for r in other["operators"]]

        res = client.get("/api/operators/compare.xlsx?date_from=2021-03-01&date_to=2021-03-31", headers=admin)
        assert res.status_code == 200 and "spreadsheetml" in res.headers["content-type"]
        wb = load_workbook(io.BytesIO(res.content))
        ws = wb["Операторы"]
        assert ws["A1"].value.startswith("Период: 2021-03-01")
        names = [ws.cell(row=r, column=2).value for r in range(3, ws.max_row + 1)]
        assert "Сводкина Ирина" in names
        assert "Команда" in wb.sheetnames

        res = client.get("/api/operators/compare.xlsx?date_from=2021-03-01&date_to=2021-03-31", headers=auth(client, "qa.sum.other"))
        ws = load_workbook(io.BytesIO(res.content))["Операторы"]
        assert "Сводкина Ирина" not in [ws.cell(row=r, column=2).value for r in range(3, ws.max_row + 1)]
        assert client.get("/api/operators/compare.xlsx").status_code == 401
