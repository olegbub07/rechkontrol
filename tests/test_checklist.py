import copy

import pytest

from app.checklist import ChecklistError, default_checklist, validate
from app.demo_calls import DEMO_CALLS
from app.quality import analyze
from tests.helpers import auth, ensure_user


def _crit(cl, cid):
    return next(c for c in cl["criteria"] if c["id"] == cid)


def test_default_checklist_matches_builtin_rules():
    for call in DEMO_CALLS:
        a = analyze(call["segments"])
        b = analyze(call["segments"], checklist=default_checklist())
        assert a == b


def test_disable_weight_and_phrases_change_scoring():
    turns = [
        {"start": 0, "end": 3, "role": "operator", "text": "Алоха, банк Центр-инвест, меня зовут Анна, чем могу помочь?"},
        {"start": 3.5, "end": 6, "role": "client", "text": "Хочу узнать баланс."},
        {"start": 6.5, "end": 9, "role": "operator", "text": "Готово, всего доброго."},
    ]
    base = analyze(turns)
    assert next(c for c in base.checks if c.id == "greeting").passed is False
    cl = default_checklist()
    _crit(cl, "greeting")["phrases"].append("алоха")
    custom = analyze(turns, checklist=validate(cl))
    assert next(c for c in custom.checks if c.id == "greeting").passed is True
    assert custom.script_compliance > base.script_compliance
    cl = default_checklist()
    _crit(cl, "greeting")["enabled"] = False
    off = analyze(turns, checklist=validate(cl))
    assert all(c.id != "greeting" for c in off.checks)
    cl = default_checklist()
    cl["category_weights"] = {"politeness": 0, "completeness": 0, "script": 1, "resolution": 0}
    only_script = analyze(turns, checklist=validate(cl))
    assert only_script.overall == only_script.script_compliance


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda cl: cl["criteria"].append({"id": "hack"}), "Неизвестный"),
        (lambda cl: _crit(cl, "greeting").update(weight=9), "вес от 0 до 5"),
        (lambda cl: _crit(cl, "greeting").update(phrases=[]), "хотя бы одна фраза"),
        (lambda cl: _crit(cl, "greeting").update(phrases=["я" * 100]), "от 2 до 80"),
        (lambda cl: [c.update(enabled=False) for c in cl["criteria"] if c["category"] == "resolution"], "Решение"),
        (lambda cl: cl.update(category_weights={"politeness": 0, "completeness": 0, "script": 0, "resolution": 0}), "больше 0"),
        (lambda cl: _crit(cl, "intro").update(title="x"), "название"),
    ],
)
def test_validation_rejects_bad_input(mutate, message):
    cl = default_checklist()
    mutate(cl)
    with pytest.raises(ChecklistError, match=message):
        validate(cl)


def test_checklist_api_roles_audit_and_rescore():
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.models import AuditLog, Call, Quality

    ensure_user("qa.cl.admin", "admin")
    ensure_user("qa.cl.analyst", "analyst")
    ensure_user("qa.cl.operator", "operator", "OP-104")
    with TestClient(app) as client:
        admin, analyst, operator = (auth(client, n) for n in ("qa.cl.admin", "qa.cl.analyst", "qa.cl.operator"))
        assert client.get("/api/checklist", headers=operator).status_code == 403
        got = client.get("/api/checklist", headers=analyst)
        assert got.status_code == 200
        cl = got.json()["checklist"]
        assert client.put("/api/checklist", headers=analyst, json=cl).status_code == 403
        bad = copy.deepcopy(cl)
        _crit(bad, "greeting")["weight"] = -1
        resp = client.put("/api/checklist", headers=admin, json=bad)
        assert resp.status_code == 422 and "вес" in resp.json()["detail"]

        with SessionLocal() as s:
            call_id = s.query(Call).filter(Call.is_demo == 1, Call.status == "done").order_by(Call.id).first().id
            before = s.query(Quality).filter(Quality.call_id == call_id).one().overall
        new = copy.deepcopy(cl)
        new["category_weights"] = {"politeness": 0, "completeness": 0, "script": 1, "resolution": 0}
        _crit(new, "courtesy")["weight"] = 2.5
        saved = client.put("/api/checklist", headers=admin, json=new)
        assert saved.status_code == 200
        assert _crit(saved.json()["checklist"], "courtesy")["weight"] == 2.5
        text = client.post("/api/analyze-text", headers=analyst, json={"text": "Здравствуйте, банк Центр-инвест.\nДа.\nВсего доброго."}).json()
        assert text["overall"] == text["script_compliance"]
        assert client.post("/api/checklist/rescore", headers=analyst).status_code == 403
        assert client.post("/api/checklist/rescore", headers=admin).json()["rescored"] > 0
        with SessionLocal() as s:
            q = s.query(Quality).filter(Quality.call_id == call_id).one()
            assert q.overall == q.script_compliance
            assert q.overall != before or q.script_compliance == before
            actions = [a.action for a in s.query(AuditLog).filter(AuditLog.username == "qa.cl.admin").all()]
            assert "checklist.update" in actions and "checklist.rescore" in actions
            detail = s.query(AuditLog).filter(AuditLog.action == "checklist.update").order_by(AuditLog.id.desc()).first().detail
            assert "courtesy" in detail
        assert client.post("/api/checklist/reset", headers=admin).status_code == 200
        assert client.get("/api/checklist", headers=admin).json()["checklist"] == default_checklist()
        assert client.post("/api/checklist/rescore", headers=admin).status_code == 200
