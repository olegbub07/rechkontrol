"""Реальный моно-звонок с телефона: смена говорящего внутри куска Whisper.

Раньше начало каждой реплики приклеивалось к концу чужой («...помогите, пожалуйста.
Да, конечно, подскажите номер карты?» целиком уходило клиенту), и оператору
засчитывались чужие «До свидания» и «долго»."""

import json
from pathlib import Path

import numpy as np

from app.asr_fix import correct_asr_text
from app.diarize import _is_echo_question, from_whisper
from app.quality import analyze

FIXTURE = Path(__file__).parent / "fixtures" / "mono_phone_call.json"

EXPECTED = [
    ("operator", "Чем могу помочь"),
    ("client", "потерял карту"),
    ("operator", "Да, конечно, подскажите"),
    ("client", "Номер карты?"),
    ("client", "22, 17"),
    ("operator", "нашёл вас, блокирую"),
    ("client", "почему так долго"),
    ("operator", "Это не ваше дело"),
    ("operator", "Ожидайте"),
    ("client", "Вы грубаетесь"),
    ("operator", "ваша карта была заблокирована"),
    ("operator", "Какие-то еще вопросы"),
    ("client", "До свидания"),
]


def _raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["segments"]


def _noise_audio(raw, sr=16000, seed=7):
    """Один и тот же «голос» (шум) под каждым словом, тишина между словами."""
    end = max(w["end"] for s in raw for w in s["words"]) + 1.0
    audio = np.zeros(int(end * sr), dtype=np.float32)
    rng = np.random.default_rng(seed)
    for s in raw:
        for w in s["words"]:
            a, b = int(w["start"] * sr), int(w["end"] * sr)
            audio[a:b] = 0.2 * rng.standard_normal(b - a)
    return audio, sr


def _role_of(turns, phrase):
    hits = [t["role"] for t in turns if phrase in t["text"]]
    assert hits, f"нет реплики с «{phrase}»"
    return hits[0]


def _check(turns):
    for role, phrase in EXPECTED:
        assert _role_of(turns, phrase) == role, (phrase, [(t["role"], t["text"]) for t in turns])
    # реплики чередуются, а не слипаются через смену говорящего
    assert len(turns) >= 10


def test_mono_phone_call_splits_inside_whisper_segments():
    _check(from_whisper(_raw(), operator_first=True))


def test_mono_phone_call_with_flat_audio():
    raw = _raw()
    audio, sr = _noise_audio(raw)
    _check(from_whisper(raw, operator_first=True, audio=audio, sr=sr))


def test_client_words_do_not_penalize_operator():
    turns = from_whisper(_raw(), operator_first=True)
    op_text = " ".join(t["text"] for t in turns if t["role"] == "operator").lower()
    assert "долго" not in op_text
    assert "до свидания" not in op_text
    assert "потерял" not in op_text
    report = analyze(turns)
    checks = {c.id: c for c in report.checks}
    # грубость оператора («не ваше дело») ловится именно у оператора
    assert not checks["no_rudeness"].passed
    assert "не ваше дело" in (checks["no_rudeness"].evidence or "").lower()
    # блокировка карты засчитана как решение
    assert checks["resolution_lang"].passed


def test_echo_question_is_not_a_new_question():
    assert _is_echo_question("Номер карты?", "Подскажите, пожалуйста, номер вашей карты?")
    assert _is_echo_question("Как что?", "Ну и что вы от меня хотите?")
    assert not _is_echo_question("Какой у вас тариф?", "Подскажите, пожалуйста, номер вашей карты?")


def test_asr_fix_glued_help_offer():
    assert correct_asr_text("Чемогом помочь?") == "Чем могу помочь?"
    assert correct_asr_text("чемогу помочь") == "чем могу помочь"
    assert correct_asr_text("чемоданом помочь") == "чемоданом помочь"
