"""Настраиваемый чек-лист оценки звонка.

По умолчанию повторяет встроенные правила app/quality.py. Администратор может в кабинете
включать и выключать критерии, менять их вес, название и ключевые фразы, а также вес
четырёх метрик в итоговой оценке. Хранится в таблице settings под ключом "checklist".
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

CATEGORIES = {
    "script": "Скрипт",
    "politeness": "Вежливость",
    "completeness": "Полнота",
    "resolution": "Решение",
}
DEFAULT_CATEGORY_WEIGHTS = {"politeness": 0.28, "completeness": 0.22, "script": 0.25, "resolution": 0.25}
RUDE_PENALTY = 0.72
# «Отфутболили»: клиента отправили на сайт или к менеджеру, а его вопросы остались без ответа.
BOUNCE_PENALTY = 0.8

# phrases: None - критерий считается не по фразам; "company"/"extra" - фразы из настроек скрипта.
_DEFAULTS: list[dict[str, Any]] = [
    {"id": "greeting", "title": "Приветствие", "category": "script", "weight": 1.2, "phrases": [
        "здравствуйте", "здравстуйте", "добрый день", "доброе утро", "добрый вечер", "доброго дня", "приветствую"]},
    {"id": "intro", "title": "Представление оператора", "category": "script", "weight": 1.0, "phrases": [
        "меня зовут", "вас беспокоит", "ваш оператор", "специалист"]},
    {"id": "company", "title": "Название компании", "category": "script", "weight": 1.0, "phrases": "company"},
    {"id": "help_offer", "title": "Предложение помощи", "category": "script", "weight": 0.9, "phrases": [
        "чем могу помочь", "чем могу быть полезна", "чем могу быть полезен", "подскажите пожалуйста что",
        "я вас слушаю", "какой у вас вопрос"]},
    {"id": "clarify", "title": "Уточнение запроса", "category": "script", "weight": 1.0, "phrases": [
        "уточните", "подскажите", "назовите", "продиктуйте", "правильно ли я понял", "правильно ли я поняла",
        "если я верно понял", "верно ли", "давайте проверим", "сейчас уточню", "сейчас проверю", "сейчас посмотрю"]},
    {"id": "closing", "title": "Корректное завершение", "category": "script", "weight": 1.0, "phrases": [
        "всего доброго", "всего хорошего", "до свидания", "хорошего дня", "хорошего вечера", "рады были помочь",
        "обращайтесь", "если возникнут вопросы", "будем рады помочь"]},
    {"id": "extra_script", "title": "Дополнительные фразы скрипта", "category": "script", "weight": 0.8, "phrases": "extra"},
    {"id": "courtesy", "title": "Вежливые формулы", "category": "politeness", "weight": 1.1, "phrases": [
        "пожалуйста", "спасибо", "благодарю", "будьте добры", "будьте любезны", "извините", "прошу прощения",
        "с вашего разрешения", "не могли бы вы", "если вам удобно", "с удовольствием", "очень приятно",
        "рада помочь", "рад помочь", "рады помочь", "рада была помочь", "рад был помочь", "будем рады"]},
    {"id": "no_rudeness", "title": "Отсутствие грубости", "category": "politeness", "weight": 1.6, "phrases": [
        "это не наша проблема", "это не моя проблема", "сами виноваты", "сами разбирайтесь", "не тупите", "вы что",
        "вы издеваетесь", "мне все равно", "отстаньте", "не грузите", "сколько можно", "я же сказал", "я же сказала",
        "я уже говорил", "читайте договор", "это к нам не относится", "не туда попали", "вам на горячую линию",
        "ну наконец-то", "ну наконец то"]},
    {"id": "no_tutoyage", "title": "Обращение на «вы»", "category": "politeness", "weight": 1.0, "phrases": None},
    {"id": "empathy", "title": "Эмпатия", "category": "politeness", "weight": 0.9, "phrases": [
        "понимаю", "понимаю вас", "сожалею", "как неудобно", "это неприятно", "разумеется", "конечно помогу",
        "обязательно помогу", "сейчас разберемся", "давайте вместе"]},
    {"id": "interrupt", "title": "Без перебиваний", "category": "politeness", "weight": 0.8, "phrases": None},
    {"id": "answers", "title": "Ответы на вопросы клиента", "category": "completeness", "weight": 1.4, "phrases": None},
    {"id": "substance", "title": "Содержательность ответов", "category": "completeness", "weight": 1.1, "phrases": None},
    {"id": "response_time", "title": "Время ответа", "category": "completeness", "weight": 0.8, "phrases": None},
    {"id": "resolution_lang", "title": "Предложено решение", "category": "resolution", "weight": 1.3, "phrases": [
        "вопрос решен", "готово", "оформил", "оформила", "заявка принята", "заявка зарегистрирована",
        "номер обращения", "номер заявки", "перевод выполнен", "платеж прошел", "карта заблокирована", "счет открыт",
        "подключил", "подключила", "настроил", "настроила", "подтверждаю", "все сделано", "можем продолжить",
        "следующий шаг", "вам останется", "документы приняты",
        # консультация: оператор говорит, что и где сделать, или предлагает подходящий продукт
        "откройте раздел", "в приложении выберите", "в приложении откройте", "можно открыть", "можно оформить",
        "можно заполнить", "можно подать", "можно подключить", "откроется", "приходите в", "можно рассмотреть",
        "подойдет", "рекомендую", "могу предложить", "предлагаю", "зарегистрирую обращение", "оформлю"]},
    {"id": "next_steps", "title": "Понятные следующие шаги", "category": "resolution", "weight": 1.1, "phrases": [
        "в течение", "сегодня", "завтра", "номер обращения", "смс", "на почту", "личный кабинет", "пришлите",
        "ожидайте", "перезвоним", "заявка", "документы",
        # куда идти и с чем: канал и документы тоже следующий шаг
        "в приложении", "в мобильном банке", "откройте раздел", "в офис", "с паспортом", "перезвонит", "придет смс",
        "откроется", "зачисляется", "не позднее"]},
    {"id": "confirm", "title": "Подтверждение, что вопрос закрыт", "category": "resolution", "weight": 1.0, "phrases": [
        "вопрос решен", "все получилось", "помог", "еще вопросы", "остались ли вопросы", "остались ли у вас",
        "остались вопросы", "есть вопросы"]},
    {"id": "no_hanging", "title": "Диалог не брошен", "category": "resolution", "weight": 1.2, "phrases": [
        "это к нам не относится", "не туда попали", "вам на горячую", "перезвоните сами", "я не знаю",
        # отфутболивание: вместо ответа - «смотрите на сайте» или «спросите у менеджера»
        "все на сайте", "там все написано", "на сайте посмотри", "посмотрите на сайте", "смотрите на сайте",
        "менеджер скажет", "это не ко мне", "это не по моей части", "ничем не могу помочь"]},
]

# Для этих критериев фразы - нарушения: найденная фраза снижает оценку.
NEGATIVE_CRITERIA = {"no_rudeness", "no_hanging"}
CRITERIA_IDS = [c["id"] for c in _DEFAULTS]
LIMITS = {
    "weight_min": 0.0,
    "weight_max": 5.0,
    "phrases_max": 80,
    "phrase_min_len": 2,
    "phrase_max_len": 80,
    "title_min_len": 3,
    "title_max_len": 80,
}


class ChecklistError(ValueError):
    pass


def norm_phrase(text: str) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def default_checklist() -> dict:
    criteria = []
    for item in _DEFAULTS:
        row = copy.deepcopy(item)
        row["enabled"] = True
        criteria.append(row)
    return {"criteria": criteria, "category_weights": dict(DEFAULT_CATEGORY_WEIGHTS)}


def _merge(stored: dict | None) -> dict:
    """Сохранённая версия поверх значений по умолчанию: новые критерии из кода появляются сами."""
    base = default_checklist()
    if not isinstance(stored, dict):
        return base
    by_id = {c["id"]: c for c in stored.get("criteria") or [] if isinstance(c, dict) and c.get("id") in CRITERIA_IDS}
    for row in base["criteria"]:
        saved = by_id.get(row["id"])
        if not saved:
            continue
        row["enabled"] = bool(saved.get("enabled", True))
        try:
            row["weight"] = float(saved.get("weight", row["weight"]))
        except (TypeError, ValueError):
            pass
        if isinstance(saved.get("title"), str) and saved["title"].strip():
            row["title"] = saved["title"].strip()
        if isinstance(row["phrases"], list) and isinstance(saved.get("phrases"), list):
            row["phrases"] = [norm_phrase(p) for p in saved["phrases"] if norm_phrase(p)]
    weights = stored.get("category_weights")
    if isinstance(weights, dict):
        for key in CATEGORIES:
            try:
                base["category_weights"][key] = float(weights.get(key, base["category_weights"][key]))
            except (TypeError, ValueError):
                pass
    return base


def _fmt(value: float) -> str:
    return f"{value:g}".replace(".", ",")


CRITERION_DIGITS = 1  # вес критерия задаётся с шагом 0,1 (как значения по умолчанию: 1,2; 0,9)
CATEGORY_DIGITS = 3  # доля метрики - до тысячных, столько же даёт приведение к сумме 1


def _number(value, label: str, digits: int = 2) -> float:
    """Число из формы: пустое поле, буквы, NaN и логические значения - ошибка, а не 0."""
    if isinstance(value, bool) or value is None:
        raise ChecklistError(f"{label}: укажите число")
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            raise ChecklistError(f"{label}: поле пустое, укажите число")
        value = text
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ChecklistError(f"{label}: «{str(value)[:20]}» не число") from exc
    if num != num or num in (float("inf"), float("-inf")):
        raise ChecklistError(f"{label}: укажите обычное число")
    scaled = num * 10**digits
    if abs(scaled - round(scaled)) > 1e-6:
        rule = {0: "только целое число", 1: "один знак после запятой (шаг 0,1)", 2: "до двух знаков после запятой (шаг 0,01)", 3: "до трёх знаков после запятой (шаг 0,001)"}[digits]
        raise ChecklistError(f"{label}: допускается {rule}, сейчас {_fmt(num)}")
    return round(num, digits)


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Веса метрик приводятся к сумме 1 (доли итоговой оценки), с точностью до 0,001."""
    total = sum(weights.values())
    if total <= 0:
        raise ChecklistError("Хотя бы одна метрика должна иметь вес больше 0")
    keys = list(weights)
    out = {k: round(weights[k] / total, 3) for k in keys}
    drift = round(1 - sum(out.values()), 3)
    if drift:
        top = max(keys, key=lambda k: out[k])
        out[top] = round(out[top] + drift, 3)
    return out


def validate(payload: dict) -> dict:
    """Проверяет присланный из кабинета чек-лист и возвращает очищенную версию."""
    if not isinstance(payload, dict):
        raise ChecklistError("Ожидается объект чек-листа")
    criteria_in = payload.get("criteria")
    if not isinstance(criteria_in, list) or not criteria_in:
        raise ChecklistError("Список критериев пуст")
    seen: set[str] = set()
    for item in criteria_in:
        if not isinstance(item, dict) or item.get("id") not in CRITERIA_IDS:
            raise ChecklistError(f"Неизвестный критерий: {item.get('id') if isinstance(item, dict) else item}")
        if item["id"] in seen:
            raise ChecklistError(f"Критерий {item['id']} указан дважды")
        seen.add(item["id"])
    defaults = {c["id"]: c for c in default_checklist()["criteria"]}
    clean: list[dict] = []
    for item in criteria_in:
        base = defaults[item["id"]]
        title = str(item.get("title") or base["title"]).strip()
        if not LIMITS["title_min_len"] <= len(title) <= LIMITS["title_max_len"]:
            raise ChecklistError(f"«{base['title']}»: название от 3 до 80 символов")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ChecklistError(f"«{title}»: признак «включён» должен быть да или нет")
        weight = _number(item["weight"] if "weight" in item else base["weight"], f"«{title}»: вес", CRITERION_DIGITS)
        if not LIMITS["weight_min"] <= weight <= LIMITS["weight_max"]:
            raise ChecklistError(f"«{title}»: вес должен быть от 0 до 5, сейчас {_fmt(weight)}")
        if enabled and weight <= 0:
            raise ChecklistError(f"«{title}»: у включённого критерия вес должен быть больше 0. Чтобы не учитывать критерий, снимите галочку «Вкл.»")
        row = {"id": item["id"], "title": title, "category": base["category"], "weight": weight, "enabled": enabled,
               "phrases": base["phrases"]}
        if isinstance(base["phrases"], list):
            raw = item.get("phrases", base["phrases"])
            if not isinstance(raw, list):
                raise ChecklistError(f"«{title}»: фразы передаются списком")
            phrases: list[str] = []
            for p in raw:
                n = norm_phrase(p)
                if not n:
                    continue
                if not LIMITS["phrase_min_len"] <= len(n) <= LIMITS["phrase_max_len"]:
                    raise ChecklistError(f"«{title}»: фраза «{n[:40]}» должна быть от 2 до 80 символов")
                if n not in phrases:
                    phrases.append(n)
            if len(phrases) > LIMITS["phrases_max"]:
                raise ChecklistError(f"«{title}»: не больше 80 фраз")
            if enabled and not phrases:
                raise ChecklistError(f"«{title}»: у включённого критерия нужна хотя бы одна фраза")
            row["phrases"] = phrases
        clean.append(row)
    for cid, base in defaults.items():
        if cid not in seen:
            clean.append(copy.deepcopy(base))
    order = {cid: i for i, cid in enumerate(CRITERIA_IDS)}
    clean.sort(key=lambda r: order[r["id"]])
    for cat, label in CATEGORIES.items():
        active = [r for r in clean if r["category"] == cat and r["enabled"] and r["weight"] > 0]
        if not active:
            raise ChecklistError(f"В группе «{label}» должен остаться хотя бы один включённый критерий с весом больше 0")
    weights_in = payload.get("category_weights", DEFAULT_CATEGORY_WEIGHTS)
    if not isinstance(weights_in, dict):
        raise ChecklistError("Веса метрик передаются объектом")
    weights: dict[str, float] = {}
    for cat, label in CATEGORIES.items():
        if cat not in weights_in:
            raise ChecklistError(f"Не указан вес метрики «{label}»")
        w = _number(weights_in[cat], f"Вес метрики «{label}»", CATEGORY_DIGITS)
        if not 0 < w <= 1:
            raise ChecklistError(f"Вес метрики «{label}» должен быть больше 0 и не больше 1, сейчас {_fmt(w)}")
        weights[cat] = w
    weights = normalize_weights(weights)
    return {"criteria": clean, "category_weights": weights}


def diff_summary(old: dict, new: dict) -> str:
    """Короткое описание изменений для журнала аудита."""
    parts: list[str] = []
    old_by = {c["id"]: c for c in old["criteria"]}
    for row in new["criteria"]:
        prev = old_by.get(row["id"]) or {}
        bits = []
        if prev.get("enabled") != row["enabled"]:
            bits.append("вкл" if row["enabled"] else "выкл")
        if prev.get("weight") != row["weight"]:
            bits.append(f"вес {prev.get('weight')}→{row['weight']}")
        if prev.get("title") != row["title"]:
            bits.append("название")
        if isinstance(row["phrases"], list) and prev.get("phrases") != row["phrases"]:
            added = len(set(row["phrases"]) - set(prev.get("phrases") or []))
            removed = len(set(prev.get("phrases") or []) - set(row["phrases"]))
            bits.append(f"фразы +{added}/-{removed}")
        if bits:
            parts.append(f"{row['id']}: {', '.join(bits)}")
    for cat in CATEGORIES:
        if old["category_weights"].get(cat) != new["category_weights"].get(cat):
            parts.append(f"метрика {cat}: {old['category_weights'].get(cat)}→{new['category_weights'].get(cat)}")
    return "; ".join(parts)


def load_checklist(session: Session) -> dict:
    from app.settings_store import get_json

    return _merge(get_json(session, "checklist", None))


def save_checklist(session: Session, checklist: dict) -> None:
    from app.settings_store import put_json

    put_json(session, "checklist", checklist)
    session.flush()


def by_id(checklist: dict | None) -> dict[str, dict]:
    data = checklist or default_checklist()
    return {c["id"]: c for c in data["criteria"]}
