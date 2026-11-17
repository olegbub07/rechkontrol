import json as json_mod


import pytest

from app import llm
from app.quality import analyze

TURNS = [
    {"start": 0, "end": 3, "role": "operator", "text": "Добрый день, банк Центр-инвест, меня зовут Елена. Чем могу помочь?"},
    {"start": 4, "end": 8, "role": "client", "text": "Мой телефон +7 918 123 45 67, хочу открыть счёт."},
    {"start": 9, "end": 14, "role": "operator", "text": "Конечно, оформлю заявку. Всего доброго."},
]


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_disabled_by_default_returns_same_report(monkeypatch):
    monkeypatch.setattr("app.llm.settings.llm_provider", "none")
    report = analyze(TURNS)
    assert llm.enrich(report, TURNS) is report


def test_parse_answer_validates_and_clips():
    good = llm.parse_answer('{"politeness": 120, "completeness": 50, "script_compliance": "70", "resolution": 10, "recommendations": ["a", ""], "summary": "ok"}')
    assert good["politeness"] == 100 and good["script_compliance"] == 70
    assert good["recommendations"] == ["a"]
    assert llm.parse_answer("not json") is None
    assert llm.parse_answer('{"politeness": 1}') is None
    assert llm.parse_answer('Ответ: {"politeness": 1, "completeness": 2, "script_compliance": 3, "resolution": 4}')["resolution"] == 4


def test_ollama_answer_is_blended_and_prompt_is_masked(monkeypatch):
    monkeypatch.setattr("app.llm.settings.llm_provider", "ollama")
    monkeypatch.setattr("app.llm.settings.llm_weight", 0.5)
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent["url"] = url
        sent["body"] = json
        answer = {"politeness": 100, "completeness": 100, "script_compliance": 100, "resolution": 100,
                  "recommendations": ["Уточняйте срок открытия счёта"], "summary": "Хороший звонок"}
        return _Resp({"message": {"content": json_mod.dumps(answer, ensure_ascii=False)}})

    monkeypatch.setattr("httpx.post", fake_post)
    report = analyze(TURNS)
    out = llm.enrich(report, TURNS)
    assert sent["url"].endswith("/api/chat")
    prompt = sent["body"]["messages"][1]["content"]
    assert "918" not in prompt and "[ТЕЛЕФОН]" in prompt
    assert out.politeness == pytest.approx(min(100, round((report.politeness + 100) / 2, 1)), abs=0.11)
    assert out.recommendations[0].startswith("ИИ:")
    assert any(c.id == "llm_review" for c in out.checks)
    assert out.overall >= report.overall


def test_failure_and_remote_url_fall_back_to_rules(monkeypatch):
    monkeypatch.setattr("app.llm.settings.llm_provider", "ollama")

    def boom(*args, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr("httpx.post", boom)
    report = analyze(TURNS)
    assert llm.enrich(report, TURNS) is report
    called = []
    monkeypatch.setattr("httpx.post", lambda *a, **k: called.append(1))
    monkeypatch.setattr("app.llm.settings.ollama_url", "http://10.1.2.3:11434")
    monkeypatch.setattr("app.llm.settings.llm_allow_remote", False)
    assert llm.enrich(report, TURNS) is report
    assert called == []
