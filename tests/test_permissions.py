"""Матрица прав: каждый изменяющий или служебный эндпоинт проверяет роль на сервере, а не только в интерфейсе."""

import pytest

from tests.helpers import auth, ensure_user

ADMIN_ONLY = [
    ("POST", "/api/calls/retry-failed", None),
    ("DELETE", "/api/calls/1", None),
    ("POST", "/api/operators", {"name": "Новый Оператор", "external_id": "OP-PERM"}),
    ("PUT", "/api/settings", {"company_phrases": ["банк"], "extra_script_phrases": []}),
    ("POST", "/api/dataset/download", None),
    ("POST", "/api/dataset/import", None),
    ("GET", "/api/admin/overview", None),
    ("GET", "/api/admin/posture", None),
    ("GET", "/api/admin/users", None),
    ("POST", "/api/admin/users", {"username": "perm.x", "password": "Perm-Check-2026!", "role": "admin"}),
    ("PATCH", "/api/admin/users/1", {"role": "operator"}),
    ("POST", "/api/admin/users/1/password", {"password": "Perm-Check-2026!"}),
    ("POST", "/api/admin/users/1/sessions", None),
    ("POST", "/api/admin/users/1/unlock", None),
    ("POST", "/api/admin/locks/login", {}),
    ("GET", "/api/admin/audit", None),
    ("PUT", "/api/checklist", {"criteria": [], "category_weights": {}}),
    ("POST", "/api/checklist/reset", None),
    ("POST", "/api/checklist/rescore", None),
    ("GET", "/api/admin/integrations", None),
    ("POST", "/api/admin/integrations/keys", {"name": "perm key", "scopes": ["calls:read"]}),
    ("DELETE", "/api/admin/integrations/keys/1", None),
    ("POST", "/api/admin/integrations/deliveries/1/retry", None),
]
# Внешний API: только по X-API-Key, сессия кабинета любой роли не подходит.
API_KEY_ONLY = [
    ("GET", "/api/v1/calls", None),
    ("GET", "/api/v1/calls/1", None),
    ("GET", "/api/v1/calls/1/result", None),
    ("POST", "/api/v1/calls", None),
]
NOT_OPERATOR = [
    ("POST", "/api/calls/1/reprocess", None),
    ("PATCH", "/api/calls/1", {"review_note": "x"}),
    ("GET", "/api/settings", None),
    ("POST", "/api/analyze-text", {"text": "Здравствуйте"}),
    ("GET", "/api/export/csv", None),
    ("GET", "/api/export/json", None),
    ("GET", "/api/export/xlsx", None),
    ("GET", "/api/dataset", None),
    ("POST", "/api/dataset/evaluate", None),
    ("GET", "/api/checklist", None),
    ("GET", "/api/reports/operator/1.pdf", None),
]


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    ensure_user("qa.perm.analyst", "analyst")
    ensure_user("qa.perm.op", "operator", "OP-104")
    with TestClient(app) as c:
        yield c


def _call(client, method, path, body, headers):
    return client.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_admin_only_rejects_analyst_and_operator(client, method, path, body):
    for user in ("qa.perm.analyst", "qa.perm.op"):
        r = _call(client, method, path, body, auth(client, user))
        assert r.status_code == 403, (user, method, path, r.status_code, r.text[:200])


@pytest.mark.parametrize("method,path,body", NOT_OPERATOR)
def test_operator_rejected(client, method, path, body):
    r = _call(client, method, path, body, auth(client, "qa.perm.op"))
    assert r.status_code in (403, 404), (method, path, r.status_code)


@pytest.mark.parametrize("method,path,body", API_KEY_ONLY)
def test_external_api_rejects_cabinet_sessions(client, method, path, body):
    ensure_user("qa.perm.admin", "admin")
    for user in ("qa.perm.admin", "qa.perm.analyst", "qa.perm.op"):
        r = _call(client, method, path, body, auth(client, user))
        assert r.status_code == 401, (user, method, path, r.status_code)


def test_every_api_route_is_covered():
    """Новый эндпоинт без записи в матрице - повод решить, кому он доступен."""
    from app.main import app

    public = {("POST", "/api/auth/login"), ("GET", "/api/health")}
    any_user = {
        ("GET", "/api/auth/me"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/password"),
        ("GET", "/api/calls"), ("GET", "/api/calls/{call_id}"), ("GET", "/api/calls/{call_id}/audio"),
        ("GET", "/api/queue"), ("GET", "/api/operators"), ("GET", "/api/script"), ("GET", "/api/dashboard"),
        ("GET", "/api/operators/compare"), ("GET", "/api/operators/compare.xlsx"),
    }
    analyst_up = {("POST", "/api/calls/upload")}
    listed = {(m, p) for m, p, _ in ADMIN_ONLY + NOT_OPERATOR + API_KEY_ONLY}

    known = public | any_user | analyst_up
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if not route.path.startswith("/api"):
            continue
        for m in methods - {"HEAD"}:
            if (m, route.path) in known:
                continue
            concrete = route.path.replace("{call_id}", "1").replace("{user_id}", "1").replace("{operator_id}", "1")
            concrete = concrete.replace("{key_id}", "1").replace("{delivery_id}", "1")
            assert (m, concrete) in listed, f"{m} {route.path} нет в матрице прав"
