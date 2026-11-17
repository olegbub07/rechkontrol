from datetime import datetime

from app.analytics import top_mistakes, trend_delta, week_start, weekly_trend
from tests.helpers import auth, ensure_user


def test_week_start_is_monday():
    assert week_start(datetime(2026, 11, 19, 15, 0)).isoformat() == "2026-11-16"
    assert week_start(datetime(2026, 11, 16, 0, 0)).isoformat() == "2026-11-16"


def test_trend_delta():
    assert trend_delta([]) is None
    assert trend_delta([{"overall": 70.0, "count": 3}]) is None
    assert trend_delta([{"overall": 70.0, "count": 2}, {"overall": 75.5, "count": 2}]) == 5.5
    # неделя с одним звонком в сравнение не идёт
    assert trend_delta([{"overall": 70.0, "count": 2}, {"overall": 99.0, "count": 1}]) is None


def test_compare_roles_and_shape():
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.models import Call, Operator

    ensure_user("qa.cmp.admin", "admin")
    ensure_user("qa.cmp.analyst", "analyst")
    ensure_user("qa.cmp.op", "operator", "OP-104")
    with TestClient(app) as client:
        assert client.get("/api/operators/compare").status_code == 401
        team = client.get("/api/operators/compare?period=all", headers=auth(client, "qa.cmp.analyst")).json()
        assert team["scope"] == "team" and team["team"]["calls_done"] > 0
        rows = team["operators"]
        assert len(rows) >= 2
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
        assert all((a["overall"] or 0) >= (b["overall"] or 0) for a, b in zip(rows, rows[1:]))
        assert sum(r["calls_done"] for r in rows) == team["team"]["calls_done"]
        for r in rows:
            assert sum(p["count"] for p in r["trend"]) == r["calls_done"]
            assert len(r["top_mistakes"]) <= 3
            assert all(0 < m["share"] <= 100 and m["calls"] <= r["calls_done"] for m in r["top_mistakes"])

        with SessionLocal() as s:
            own = s.query(Operator).filter(Operator.external_id == "OP-104").one()
            calls = s.query(Call).filter(Call.operator_id == own.id, Call.status == "done").all()
            expected = top_mistakes(calls, 3)
            expected_trend = weekly_trend(calls)
        mine = client.get("/api/operators/compare?period=all", headers=auth(client, "qa.cmp.op")).json()
        assert mine["scope"] == "self" and mine["team"] is None
        assert [r["id"] for r in mine["operators"]] == [own.id]
        assert mine["operators"][0]["top_mistakes"] == expected
        assert mine["operators"][0]["trend"] == expected_trend

        admin = auth(client, "qa.cmp.admin")
        assert client.get("/api/operators/compare?period=bogus", headers=admin).json()["period"] == "30d"
        assert client.get("/api/operators/compare?period=7d", headers=admin).status_code == 200
