"""10 синтетических звонков по продуктам банка: оценка должна попадать в ожидаемый диапазон.

Реплики сняты с прогона Whisper small (17.11.2026), поэтому в них есть живые ошибки
распознавания и роли. Аудио и сценарии: scripts/eval_synth.py, docs/EVALUATION.md.
"""

import json
from pathlib import Path

import pytest

from app.quality import analyze

DATA = json.loads((Path(__file__).parent / "fixtures" / "synth_calls.json").read_text(encoding="utf-8"))
BANDS = {"high": (80, 100), "mid": (55, 79.9), "below": (35, 59.9), "low": (0, 45)}
EXPECT = {"01": "high", "02": "high", "03": "low", "04": "high", "05": "mid",
          "06": "high", "07": "below", "08": "high", "09": "mid", "10": "high"}


def _call(prefix: str):
    name = next(k for k in DATA if k.startswith(prefix))
    return analyze(DATA[name])


def _failed(report) -> set[str]:
    return {c.id for c in report.checks if not c.passed}


@pytest.mark.parametrize("prefix", sorted(EXPECT))
def test_synth_call_in_expected_band(prefix):
    lo, hi = BANDS[EXPECT[prefix]]
    assert lo <= _call(prefix).overall <= hi


@pytest.mark.parametrize("prefix", ["01", "04", "08", "10"])
def test_good_consultation_has_solution_and_next_steps(prefix):
    failed = _failed(_call(prefix))
    assert "resolution_lang" not in failed
    assert "next_steps" not in failed
    assert _call(prefix).resolution == 100


def test_bounced_call_07_below_tutoyage_call_09():
    r07, r09 = _call("07"), _call("09")
    assert r07.overall + 8 < r09.overall
    assert {"answers", "confirm", "no_hanging"} <= _failed(r07)


def test_rude_call_still_lowest():
    scores = {p: _call(p).overall for p in EXPECT}
    assert min(scores, key=scores.get) == "03"


def test_angry_client_makes_call_negative():
    r06 = _call("06")
    assert r06.tonality == "negative"
    assert r06.overall >= 80  # оператор отработал хорошо, оценку злость клиента не роняет


def _turns(*pairs):
    out, t = [], 0.0
    for role, text in pairs:
        out.append({"role": role, "text": text, "start": t, "end": t + 3})
        t += 3.6
    return out


def test_bounce_penalty_needs_unanswered_questions():
    bounce = _turns(
        ("operator", "Добрый день, банк Центр-инвест, меня зовут Игорь."),
        ("client", "Какой у вас льготный период? Сколько стоит обслуживание?"),
        ("operator", "Это зависит."),
        ("client", "А от чего зависит?"),
        ("operator", "Всё на сайте есть. До свидания."),
    )
    helped = _turns(
        ("operator", "Добрый день, банк Центр-инвест, меня зовут Игорь. Чем могу помочь?"),
        ("client", "Какой у вас льготный период по карте?"),
        ("operator", "Льготный период до ста дней, обслуживание бесплатно при тратах от десяти тысяч в месяц."),
        ("client", "Спасибо, понятно."),
        ("operator", "Подробные тарифы тоже есть, всё на сайте есть. Всего доброго, до свидания."),
    )
    rb, rh = analyze(bounce), analyze(helped)
    assert "no_hanging" in _failed(rb) and "no_hanging" in _failed(rh)
    # Штраф за отфутболивание только там, где вопросы остались без ответа.
    assert rb.overall < rh.overall - 20


def test_consultation_phrases_count_as_solution():
    turns = _turns(
        ("operator", "Добрый день, банк Центр-инвест, меня зовут Марина. Чем могу помочь?"),
        ("client", "Как открыть вклад?"),
        ("operator", "В приложении откройте раздел Вклады, выберите вклад и подтвердите кодом. Вклад откроется сразу."),
        ("client", "Спасибо."),
        ("operator", "Остались ли у вас вопросы? Всего доброго."),
    )
    failed = _failed(analyze(turns))
    assert "resolution_lang" not in failed and "next_steps" not in failed
