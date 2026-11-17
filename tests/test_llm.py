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


def test_prompt_does_not_leak_rule_scores():
    from app import llm
    from app.demo_calls import DEMO_CALLS
    from app.quality import analyze

    turns = DEMO_CALLS[0]["segments"]
    report = analyze(turns)
    prompt = llm.build_prompt(turns, report)
    assert "politeness=" not in prompt and "по правилам" not in prompt
    assert "Оператор:" in prompt


def _fake_answer(monkeypatch, scores, sent=None):
    def fake_post(url, json=None, timeout=None):
        if sent is not None:
            sent["prompt"] = json["messages"][1]["content"]
        answer = dict(scores, recommendations=[], summary="")
        return _Resp({"message": {"content": json_mod.dumps(answer)}})

    monkeypatch.setattr("app.llm.settings.llm_provider", "ollama")
    monkeypatch.setattr("httpx.post", fake_post)


def _checklist(weights=None, disabled=()):
    from app.checklist import default_checklist

    cl = default_checklist()
    if weights:
        cl["category_weights"] = weights
    for row in cl["criteria"]:
        if row["id"] in disabled:
            row["enabled"] = False
    return cl


def test_llm_overall_uses_checklist_category_weights(monkeypatch):
    monkeypatch.setattr("app.llm.settings.llm_weight", 0.5)
    _fake_answer(monkeypatch, {"politeness": 0, "completeness": 0, "script_compliance": 100, "resolution": 0})
    cl = _checklist({"politeness": 0.0, "completeness": 0.0, "script": 1.0, "resolution": 0.0})
    report = analyze(TURNS, checklist=cl)
    assert report.overall == pytest.approx(report.script_compliance, abs=0.11)  # правила: итог = только скрипт
    out = llm.enrich(report, TURNS, cl)
    # LLM обвалил вежливость и решение, но их вес 0 - итог зависит только от скрипта
    assert out.overall == pytest.approx(out.script_compliance, abs=0.11)
    assert out.politeness < report.politeness


def test_llm_overall_matches_rules_when_llm_agrees(monkeypatch):
    monkeypatch.setattr("app.llm.settings.llm_weight", 0.7)
    cl = _checklist({"politeness": 0.1, "completeness": 0.1, "script": 0.1, "resolution": 0.7})
    report = analyze(TURNS, checklist=cl)
    _fake_answer(monkeypatch, {m: getattr(report, m) for m in llm.METRICS})
    out = llm.enrich(report, TURNS, cl)
    assert out.overall == pytest.approx(report.overall, abs=0.2)


def test_llm_keeps_rudeness_penalty(monkeypatch):
    monkeypatch.setattr("app.llm.settings.llm_weight", 1.0)
    rude = [dict(TURNS[0]), dict(TURNS[1]), {"start": 9, "end": 14, "role": "operator", "text": "Сами виноваты, читайте договор."}]
    report = analyze(rude)
    _fake_answer(monkeypatch, {m: 100 for m in llm.METRICS})
    out = llm.enrich(report, rude)
    assert out.overall < 100 * 0.75


def test_prompt_lists_only_enabled_criteria_with_weights(monkeypatch):
    sent = {}
    monkeypatch.setattr("app.llm.settings.llm_weight", 0.5)
    _fake_answer(monkeypatch, {m: 80 for m in llm.METRICS}, sent)
    cl = _checklist(disabled=("empathy",))
    llm.enrich(analyze(TURNS, checklist=cl), TURNS, cl)
    assert "Приветствие (вес 1.2)" in sent["prompt"]
    assert "Эмпатия" not in sent["prompt"]
