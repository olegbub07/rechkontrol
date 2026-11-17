import json

from app.checklist import default_checklist
from app.highlight import segment_highlights
from app.pii import mask_segments
from app.quality import analyze, report_to_json
from tests.helpers import auth, ensure_user

RUDE = [
    {"start": 0, "end": 2, "role": "operator", "speaker": "A", "text": "Алло, слушаю."},
    {"start": 2, "end": 6, "role": "client", "speaker": "B", "text": "Карта не работает, мой телефон +7 916 123-45-67."},
    {"start": 6, "end": 9, "role": "operator", "speaker": "A", "text": "Это не наша проблема, сами виноваты."},
    {"start": 9, "end": 11, "role": "operator", "speaker": "A", "text": "Ты меня слышишь?"},
]
GOOD = [
    {"start": 0, "end": 3, "role": "operator", "speaker": "A", "text": "Добрый день, банк Центр-инвест, меня зовут Анна. Чем могу помочь?"},
    {"start": 3, "end": 6, "role": "client", "speaker": "B", "text": "Хочу узнать лимит по карте."},
    {"start": 6, "end": 9, "role": "operator", "speaker": "A", "text": "Спасибо, лимит 100 тысяч в сутки. Всего доброго!"},
]


def _checks(turns, cl=None):
    report = analyze(turns, checklist=cl)
    return [c for c in json.loads(report_to_json(report)["checks_json"])], cl or default_checklist()


def _spans(turns, cl=None, masked=True):
    checks, cl = _checks(turns, cl)
    segs = mask_segments(turns) if masked else turns
    return segs, segment_highlights(segs, checks, cl, masked=masked)


def _texts(segs, marks, kind):
    out = []
    for seg, m in zip(segs, marks):
        out += [(seg["text"][s["start"]:s["end"]], s) for s in m["spans"] if s["kind"] == kind]
    return out


def test_rudeness_tutoyage_and_missing_greeting():
    segs, marks = _spans(RUDE)
    bad = _texts(segs, marks, "bad")
    words = [t.lower() for t, _ in bad]
    assert "это не наша проблема" in words and "сами виноваты" in words and "ты" in words
    rude = next(s for t, s in bad if t.lower() == "сами виноваты")
    assert rude["rule"] == "no_rudeness" and "итог −" in rude["impact"] and "0,72" in rude["impact"]
    rules = {m["rule"] for m in marks[0]["markers"]}
    assert {"greeting", "intro"} <= rules
    assert all(m["kind"] == "miss" and m["impact"] for m in marks[0]["markers"])
    assert not marks[1]["markers"]


def test_pii_masked_and_unmasked():
    segs, marks = _spans(RUDE, masked=True)
    pii = _texts(segs, marks, "pii")
    assert [t for t, _ in pii] == ["[ТЕЛЕФОН]"]
    assert "телефон" in pii[0][1]["title"]
    segs, marks = _spans(RUDE, masked=False)
    pii = _texts(segs, marks, "pii")
    assert pii and "916" in pii[0][0]
    neg = _texts(segs, marks, "neg")
    assert any(t.lower() == "не работает" for t, _ in neg)


def test_passed_items_and_overlap():
    segs, marks = _spans(GOOD)
    ok = {t.lower(): s for t, s in _texts(segs, marks, "ok")}
    assert "добрый день" in ok and "меня зовут" in ok and "всего доброго" in ok
    assert ok["добрый день"]["impact"].startswith("Скрипт: пункт пройден, вклад в итог +")
    for m in marks:
        spans = sorted(m["spans"], key=lambda s: s["start"])
        assert all(a["end"] <= b["start"] for a, b in zip(spans, spans[1:]))
        assert not m["markers"]


def test_disabled_rule_is_not_highlighted():
    cl = default_checklist()
    for c in cl["criteria"]:
        if c["id"] in ("no_rudeness", "no_tutoyage"):
            c["enabled"] = False
    segs, marks = _spans(RUDE, cl)
    assert not _texts(segs, marks, "bad")


def test_utf16_offsets():
    turns = [{"start": 0, "end": 1, "role": "operator", "speaker": "A", "text": "🙂 Добрый день, банк, меня зовут Ира."}]
    segs, marks = _spans(turns)
    span = next(s for s in marks[0]["spans"] if s["rule"] == "greeting")
    assert segs[0]["text"].encode("utf-16-le")[span["start"] * 2 : span["end"] * 2].decode("utf-16-le") == "Добрый день"


def test_call_detail_returns_spans():
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.models import Call

    ensure_user("qa.hl.admin", "admin")
    with TestClient(app) as client:
        h = auth(client, "qa.hl.admin")
        with SessionLocal() as s:
            ids = [c.id for c in s.query(Call).filter(Call.is_demo == 1, Call.status == "done").all()]
        kinds = set()
        for cid in ids:
            data = client.get(f"/api/calls/{cid}", headers=h).json()
            for seg in data["segments"]:
                for sp in seg["spans"]:
                    assert 0 <= sp["start"] < sp["end"] <= len(seg["text"])
                    assert sp["title"] and sp["impact"]
                    kinds.add(sp["kind"])
                kinds.update(m["kind"] for m in seg["markers"])
        assert {"ok", "bad", "miss"} <= kinds
