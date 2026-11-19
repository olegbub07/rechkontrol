"""Поиск по смыслу (app/semantic.py) с подменённым кодировщиком: модель не скачивается."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app import semantic
from app.checklist import default_checklist
from app.config import settings
from app.highlight import segment_highlights
from app.quality import analyze, report_to_json

ALIASES = {
    "это не в моей компетенции": "это не ко мне",
    "у меня нет времени на ваши глупости": "отстаньте от меня",
    "до связи, удачи": "всего доброго",
}


class FakeEncoder:
    """Одинаковый смысл = одинаковый вектор, остальное - почти ортогональные случайные векторы."""

    def __init__(self, aliases=None):
        self.aliases = aliases or ALIASES
        self.calls: list[list[str]] = []

    def encode(self, texts):
        self.calls.append(list(texts))
        out = []
        for t in texts:
            assert t.startswith(semantic.QUERY_PREFIX)
            key = self.aliases.get(t[len(semantic.QUERY_PREFIX):], t[len(semantic.QUERY_PREFIX):])
            seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
            v = np.random.default_rng(seed).standard_normal(384)
            out.append(v / np.linalg.norm(v))
        return np.array(out)


@pytest.fixture
def fake(monkeypatch):
    enc = FakeEncoder()
    monkeypatch.setattr(settings, "semantic_match", "auto")
    semantic.set_encoder(enc)
    yield enc
    semantic.set_encoder(None)


BOUNCE = [
    {"start": 0, "end": 3, "role": "operator", "text": "Добрый день, банк Центр-инвест, меня зовут Анна. Чем могу помочь?"},
    {"start": 3, "end": 6, "role": "client", "text": "Какая ставка по кредитной карте? И какой льготный период?"},
    {"start": 6, "end": 9, "role": "operator", "text": "Это не в моей компетенции. Всего доброго."},
]
RUDE = [
    {"start": 0, "end": 3, "role": "operator", "text": "Добрый день, банк Центр-инвест, меня зовут Анна. Чем могу помочь?"},
    {"start": 3, "end": 6, "role": "client", "text": "Я уже третий раз звоню."},
    {"start": 6, "end": 9, "role": "operator", "text": "У меня нет времени на ваши глупости. Всего доброго."},
]


def _check(report, cid):
    return next(c for c in report.checks if c.id == cid)


def test_off_by_default_only_phrases():
    assert semantic.mode() == "off"
    assert semantic.get_encoder() is None
    report = analyze(BOUNCE)
    assert _check(report, "no_hanging").passed
    assert _check(report, "no_hanging").semantic is None


def test_bounce_paraphrase_found_by_sense(fake):
    report = analyze(BOUNCE)
    ch = _check(report, "no_hanging")
    assert not ch.passed
    assert ch.semantic["text"] == "Это не в моей компетенции"
    assert ch.semantic["example"] == "это не ко мне"
    assert "по смыслу" in ch.detail
    assert ch.evidence.startswith("Это не в моей компетенции")
    semantic.set_encoder(None)
    settings.semantic_match = "off"
    plain = analyze(BOUNCE)
    assert report.overall < plain.overall


def test_rudeness_without_profanity(fake):
    report = analyze(RUDE)
    ch = _check(report, "no_rudeness")
    assert not ch.passed and ch.semantic
    assert report.tonality == "negative"


def test_closing_paraphrase(fake):
    turns = [dict(t) for t in RUDE[:2]] + [{"start": 6, "end": 9, "role": "operator", "text": "Заявку оформила. До связи, удачи."}]
    closing = _check(analyze(turns), "closing")
    assert closing.passed and closing.semantic["example"] == "всего доброго"


def test_exact_phrase_wins_over_sense(fake):
    report = analyze(RUDE)
    closing = _check(report, "closing")
    assert closing.passed and closing.semantic is None


def test_highlight_marks_semantic_sentence(fake):
    report = analyze(BOUNCE)
    checks = json.loads(report_to_json(report)["checks_json"])
    marks = segment_highlights(BOUNCE, checks, default_checklist(), masked=False)
    bad = [(BOUNCE[i]["text"][s["start"]:s["end"]], s["rule"]) for i, m in enumerate(marks) for s in m["spans"] if s["kind"] == "bad"]
    assert ("Это не в моей компетенции", "no_hanging") in bad


def test_unrelated_sentence_not_matched(fake):
    assert semantic.classify("Ставка двенадцать процентов годовых", "no_hanging") is None


def test_example_embeddings_cached(fake):
    analyze(BOUNCE)
    n = len(fake.calls)
    analyze(BOUNCE)
    assert len(fake.calls) == n  # примеры и предложения второй раз берутся из кэша


def test_auto_without_model_degrades(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "semantic_match", "auto")
    monkeypatch.setattr(settings, "semantic_model_dir", str(tmp_path / "нет"))
    semantic.set_encoder(None)
    try:
        assert semantic.get_encoder() is None
        report = analyze(BOUNCE)
        assert _check(report, "no_hanging").passed
    finally:
        semantic.set_encoder(None)


def test_broken_model_degrades(monkeypatch, tmp_path):
    for name in semantic.MODEL_FILES:
        (tmp_path / name).write_text("не модель")
    monkeypatch.setattr(settings, "semantic_match", "on")
    monkeypatch.setattr(settings, "semantic_model_dir", str(tmp_path))
    semantic.set_encoder(None)
    try:
        assert semantic.get_encoder() is None
        assert analyze(BOUNCE).overall > 0
    finally:
        semantic.set_encoder(None)


def test_heldout_not_in_examples():
    data = json.loads((Path(__file__).parent / "fixtures" / "paraphrases_heldout.json").read_text(encoding="utf-8"))
    used = {x for v in semantic.EXAMPLES.values() for x in v} | set(semantic.NEUTRAL)
    held = [x for v in data["positives"].values() for x in v] + data["negatives"]
    assert len(held) >= 50
    assert not used & set(held)
    assert set(data["positives"]) <= set(semantic.EXAMPLES)
