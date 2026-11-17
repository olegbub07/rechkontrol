from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from app.checklist import RUDE_PENALTY, by_id, default_checklist
from app.diarize import normalize

GREETINGS = (
    "здравствуйте",
    "здравстуйте",
    "добрый день",
    "доброе утро",
    "добрый вечер",
    "доброго дня",
    "приветствую",
)
CLOSINGS = (
    "всего доброго",
    "всего хорошего",
    "до свидания",
    "хорошего дня",
    "хорошего вечера",
    "рады были помочь",
    "обращайтесь",
    "если возникнут вопросы",
    "будем рады помочь",
)
INTRO = (
    "меня зовут",
    "вас беспокоит",
    "ваш оператор",
    "специалист",
    "меня зовут",
)
HELP_OFFER = (
    "чем могу помочь",
    "чем могу быть полезна",
    "чем могу быть полезен",
    "подскажите пожалуйста что",
    "я вас слушаю",
    "какой у вас вопрос",
)
CLARIFY = (
    "уточните",
    "подскажите",
    "назовите",
    "продиктуйте",
    "правильно ли я понял",
    "правильно ли я поняла",
    "если я верно понял",
    "верно ли",
    "давайте проверим",
    "сейчас уточню",
    "сейчас проверю",
    "сейчас посмотрю",
)
COURTESY = (
    "пожалуйста",
    "спасибо",
    "благодарю",
    "будьте добры",
    "будьте любезны",
    "извините",
    "прошу прощения",
    "с вашего разрешения",
    "не могли бы вы",
    "если вам удобно",
)
EMPATHY = (
    "понимаю",
    "понимаю вас",
    "сожалею",
    "как неудобно",
    "это неприятно",
    "разумеется",
    "конечно помогу",
    "обязательно помогу",
    "сейчас разберёмся",
    "сейчас разберемся",
    "давайте вместе",
)
RESOLUTION = (
    "вопрос решён",
    "вопрос решен",
    "готово",
    "оформил",
    "оформила",
    "заявка принята",
    "заявка зарегистрирована",
    "номер обращения",
    "номер заявки",
    "перевод выполнен",
    "платёж прошёл",
    "платеж прошел",
    "карта заблокирована",
    "счёт открыт",
    "счет открыт",
    "подключил",
    "подключила",
    "настроил",
    "настроила",
    "подтверждаю",
    "всё сделано",
    "все сделано",
    "можем продолжить",
    "следующий шаг",
    "вам останется",
    "документы приняты",
)
NEXT_STEPS = (
    "в течение",
    "сегодня",
    "завтра",
    "номер обращения",
    "смс",
    "на почту",
    "личный кабинет",
    "пришлите",
    "ожидайте",
    "перезвоним",
    "заявка",
    "документы",
)
CLIENT_OK = (
    "спасибо",
    "всё понятно",
    "все понятно",
    "хорошо",
    "отлично",
    "разобрались",
    "помогли",
    "супер",
    "ясно",
    "понял",
    "поняла",
    "договорились",
)
RUDENESS = (
    "это не наша проблема",
    "это не моя проблема",
    "сами виноваты",
    "сами разбирайтесь",
    "не тупите",
    "вы что",
    "вы издеваетесь",
    "мне все равно",
    "мне всё равно",
    "отстаньте",
    "не грузите",
    "сколько можно",
    "я же сказал",
    "я же сказала",
    "я уже говорил",
    "читайте договор",
    "это к нам не относится",
    "не туда попали",
    "вам на горячую линию",
    "ну наконец-то",
    "ну наконец то",
)
TY_RE = re.compile(r"(^|[\s,])(ты|тебе|тобой|тебя)([\s,.\?!]|$)")
QUESTION_RE = re.compile(
    r"\b(как|где|когда|сколько|почему|зачем|можно ли|подскажите|скажите|а что если|получится)\b"
)
POS_WORDS = (
    "спасибо",
    "благодар",
    "отлично",
    "хорошо",
    "рада помочь",
    "рад помочь",
    "помогу",
    "обязательно",
    "конечно",
    "с удовольствием",
    "замечательно",
    "удобно",
    "готово",
)
NEG_WORDS = (
    "проблема",
    "ошибка",
    "не работает",
    "не проходит",
    "не могу",
    "нельзя",
    "отказ",
    "жалоба",
    "возмущен",
    "возмущён",
    "ужас",
    "плохо",
    "долго",
    "никто не",
    "достали",
    "раздраж",
)
TOPIC_CUES = (
    ("расчётный счёт", "открытие расчётного счёта"),
    ("расчетный счет", "открытие расчётного счёта"),
    ("рко", "расчётное обслуживание"),
    ("эквайринг", "эквайринг"),
    ("терминал", "торговый терминал"),
    ("кредит для бизнеса", "кредит для бизнеса"),
    ("кредит", "кредит"),
    ("ипотек", "ипотека"),
    ("вклад", "вклад"),
    ("зарплатн", "зарплатный проект"),
    ("карт", "банковская карта"),
    ("перевод", "перевод"),
    ("платёж", "платёж"),
    ("платеж", "платёж"),
    ("мобильн", "мобильное приложение"),
    ("приложен", "мобильное приложение"),
    ("блокир", "блокировка карты"),
    ("ип ", "обслуживание ИП"),
    ("ооо", "обслуживание юрлица"),
)

ADVICE = {
    "greeting": "Начинайте звонок с приветствия в первые секунды: «Добрый день, банк Центр-инвест».",
    "intro": "Представляйтесь по имени — это повышает доверие и соответствует скрипту.",
    "company": "Называйте организацию. Клиент должен понимать, куда он попал.",
    "help_offer": "После представления сразу предложите помощь: «Чем могу помочь?»",
    "clarify": "Уточняйте запрос своими словами, чтобы клиент подтвердил, что вас поняли верно.",
    "closing": "Завершайте диалог вежливой формулой и предложением обратиться снова.",
    "courtesy": "Добавьте «пожалуйста», «спасибо», «будьте добры» — плотность вежливых формул сейчас низкая.",
    "no_rudeness": "Исключите резкие формулировки. Даже при ошибке клиента говорите через факты и следующий шаг.",
    "no_tutoyage": "В деловом контуре только обращение на «вы».",
    "empathy": "Если клиент встревожен, озвучьте понимание: «Понимаю, сейчас разберёмся».",
    "answers": "Не оставляйте вопросы клиента без содержательного ответа.",
    "substance": "Отвечайте конкретикой: суммы, сроки, документы, а не односложными репликами.",
    "next_steps": "Фиксируйте следующий шаг: срок, канал, номер обращения.",
    "confirm": "В конце спросите, решён ли вопрос, и дождитесь подтверждения клиента.",
    "no_hanging": "Не обрывайте разговор и не переадресовывайте без маршрута решения.",
    "response_time": "Сократите паузу перед ответом. Целевой ориентир — до 3 секунд после реплики клиента.",
    "interrupt": "Дослушайте клиента до конца. Пауза 0,4–0,8 сек перед ответом снижает число перебиваний.",
}

DEFAULT_COMPANY = ("центр-инвест", "центр инвест", "банк")


@dataclass
class CheckResult:
    id: str
    title: str
    category: str
    passed: bool
    score: float
    weight: float
    detail: str
    evidence: str | None = None


@dataclass
class QualityReport:
    politeness: float
    completeness: float
    script_compliance: float
    resolution: float
    overall: float
    tonality: str
    tonality_score: float
    avg_response_sec: float | None
    talk_ratio_operator: float
    violation_pct: float
    violations: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        return data


def _join(turns: list[dict], role: str | None = None) -> str:
    parts = [t["text"] for t in turns if role is None or t.get("role") == role]
    return normalize(" ".join(parts))


def _has(text: str, phrases: tuple[str, ...] | list[str]) -> str | None:
    for p in phrases:
        if p in text:
            return p
    return None


# Мат ловим по корням, а не списком фраз: форм слишком много.
_PROFANITY_RE = re.compile(
    r"(?<![а-яё])(?:"
    r"(?:на|по|от|за|ни|до)?ху[йеёяюи][а-яё]*"
    r"|(?:[а-яё]{0,4})?пизд[а-яё]*"
    r"|(?:за|вы|до|на|об|отъ?|подъ?|по|разъ?|съ?|у|въ?|при|про|пере)?(?:еб|ёб)(?:а|у|л|н|ё|и|ок|ыв)[а-яё]*"
    r"|бля[а-яё]*|бл[яе]д[а-яё]*"
    r"|сук[аиеуо]|сучк[а-яё]*"
    r"|муда[кч][а-яё]*|пид[ао]р[а-яё]*|долбо[её]б[а-яё]*|гандон[а-яё]*|залуп[а-яё]*|манд[ау](?![рт])[а-яё]*"
    r")(?![а-яё])"
)


def _profanity(text: str) -> str | None:
    m = _PROFANITY_RE.search(text)
    return m.group(0) if m else None


def _count(text: str, phrases: tuple[str, ...] | list[str]) -> int:
    return sum(text.count(p) for p in phrases)


def _self_named(turns: list[dict]) -> str | None:
    """«Банк Центр-инвест, Дмитрий, чем могу помочь?» - оператор назвался без «меня зовут».

    Считаем, если в первой реплике оператора, сказанной до клиента, есть русское имя.
    """
    from app.ru_names import find_first_names

    for t in turns:
        if t.get("role") == "client":
            return None
        if t.get("role") == "operator":
            found = find_first_names(str(t.get("text") or ""), strict=True)
            return found[0].group(0).lower() if found else None
    return None


def _clip(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _evidence(turns: list[dict], phrases: tuple[str, ...] | list[str], role: str | None = "operator") -> str | None:
    for t in turns:
        if role and t.get("role") != role:
            continue
        hit = _has(normalize(t["text"]), phrases)
        if hit:
            snippet = t["text"].strip()
            if len(snippet) > 160:
                snippet = snippet[:157] + "…"
            return snippet
    return None


def _avg_response(turns: list[dict]) -> tuple[float | None, int]:
    gaps: list[float] = []
    interrupts = 0
    for prev, cur in zip(turns, turns[1:]):
        if prev.get("role") == "client" and cur.get("role") == "operator":
            gap = float(cur["start"]) - float(prev["end"])
            if 0 <= gap < 45:
                gaps.append(gap)
            if -0.05 <= gap <= 0.18 and len(prev.get("text", "")) > 24:
                interrupts += 1
    if not gaps:
        return None, interrupts
    return round(sum(gaps) / len(gaps), 2), interrupts


def _talk_ratio(turns: list[dict]) -> float:
    op = sum(max(0.0, float(t["end"]) - float(t["start"])) for t in turns if t.get("role") == "operator")
    total = sum(max(0.0, float(t["end"]) - float(t["start"])) for t in turns)
    if total <= 0:
        op_len = sum(len(t["text"]) for t in turns if t.get("role") == "operator")
        all_len = sum(len(t["text"]) for t in turns) or 1
        return round(op_len / all_len, 3)
    return round(op / total, 3)


def _client_questions(turns: list[dict]) -> list[dict]:
    out = []
    for t in turns:
        if t.get("role") != "client":
            continue
        text = t["text"].strip()
        n = normalize(text)
        if text.endswith("?") or QUESTION_RE.search(n):
            out.append(t)
    return out


def _answered(question: dict, turns: list[dict]) -> bool:
    for t in turns:
        if t.get("role") != "operator":
            continue
        if float(t["start"]) >= float(question["end"]) - 0.05:
            return len(t["text"].strip()) >= 24
    return False


def _topic(text: str) -> str:
    for cue, label in TOPIC_CUES:
        if cue in text:
            return label
    return "общий запрос"


def _tonality(op_text: str, client_text: str, rude: bool) -> tuple[str, float]:
    if rude:
        return "negative", -0.7
    score = 0.0
    score += 0.12 * _count(op_text, POS_WORDS)
    score -= 0.08 * _count(op_text, NEG_WORDS)
    score += 0.05 * _count(client_text, CLIENT_OK)
    score -= 0.06 * _count(client_text, NEG_WORDS)
    score = max(-1.0, min(1.0, score))
    if score >= 0.18:
        label = "positive"
    elif score <= -0.18:
        label = "negative"
    else:
        label = "neutral"
    return label, round(score, 3)


def _weighted(checks: list[CheckResult], category: str) -> float:
    group = [c for c in checks if c.category == category]
    if not group:
        return 0.0
    num = sum(c.score * c.weight for c in group)
    den = sum(c.weight for c in group) or 1
    return _clip(num / den)


# Опечатки и обрывки нужны оценке, чтобы узнать фразу в расшифровке. В памятке их нет.
_SHEET_SKIP = frozenset({"здравстуйте", "подскажите пожалуйста что", "верно ли"})


def script_sheet(company_phrases: list[str] | None, extra_script_phrases: list[str] | None) -> dict:
    """Памятка оператору: те же шаги, по которым считается метрика «скрипт»."""

    def uniq(items) -> list[str]:
        seen: list[str] = []
        for item in items or []:
            text = str(item).strip()
            if text and text not in seen and text not in _SHEET_SKIP:
                seen.append(text)
        return seen

    company = uniq(company_phrases)
    extra = uniq(extra_script_phrases)
    return {
        "steps": [
            {
                "id": "greeting",
                "title": "Приветствие",
                "hint": "Скажите это в начале разговора.",
                "examples": uniq(GREETINGS),
            },
            {
                "id": "intro",
                "title": "Представление",
                "hint": "Назовите себя.",
                "examples": uniq(INTRO),
            },
            {
                "id": "company",
                "title": "Название компании",
                "hint": "Произнесите одно из названий, которые задал администратор.",
                "examples": company,
            },
            {
                "id": "help",
                "title": "Предложение помощи",
                "hint": "Спросите, с чем помочь.",
                "examples": uniq(HELP_OFFER),
            },
            {
                "id": "clarify",
                "title": "Уточнение запроса",
                "hint": "Уточните детали обращения.",
                "examples": uniq(CLARIFY),
            },
            {
                "id": "closing",
                "title": "Завершение",
                "hint": "Попрощайтесь и оставьте возможность обратиться снова.",
                "examples": uniq(CLOSINGS),
            },
        ],
        "extra_script_phrases": extra,
    }


def analyze(
    turns: list[dict],
    company_phrases: list[str] | None = None,
    extra_script_phrases: list[str] | None = None,
    checklist: dict | None = None,
) -> QualityReport:
    """Оценка по чек-листу. checklist=None - встроенные правила (app/checklist.py, значения по умолчанию)."""
    checklist = checklist or default_checklist()
    cfg = by_id(checklist)
    if not turns:
        empty = CheckResult(
            id="empty",
            title="Расшифровка",
            category="completeness",
            passed=False,
            score=0,
            weight=1,
            detail="Речь не распознана или файл без реплик.",
        )
        return QualityReport(
            politeness=0,
            completeness=0,
            script_compliance=0,
            resolution=0,
            overall=0,
            tonality="neutral",
            tonality_score=0,
            avg_response_sec=None,
            talk_ratio_operator=0,
            violation_pct=100,
            violations=[{"id": "empty", "title": empty.title, "detail": empty.detail}],
            recommendations=["Проверьте качество записи: 16 кГц, без сильных шумов, речь не перекрывается музыкой."],
            checks=[empty],
            summary="Расшифровка пустая — оценка качества невозможна.",
        )

    company = tuple(p.strip().lower().replace("ё", "е") for p in (company_phrases or DEFAULT_COMPANY) if p.strip())
    extra = tuple(p.strip().lower().replace("ё", "е") for p in (extra_script_phrases or []) if p.strip())

    def ph(cid: str) -> tuple[str, ...]:
        source = cfg[cid]["phrases"]
        if source == "company":
            return company
        if source == "extra":
            return extra
        return tuple(source or ())

    def on(cid: str) -> bool:
        return bool(cfg[cid].get("enabled", True)) and float(cfg[cid].get("weight", 0)) > 0
    op_text = _join(turns, "operator")
    client_text = _join(turns, "client")
    all_text = _join(turns)
    op_turns = [t for t in turns if t.get("role") == "operator"]
    client_turns = [t for t in turns if t.get("role") == "client"]
    avg_resp, interrupts = _avg_response(turns)
    questions = _client_questions(turns)
    answered_n = sum(1 for q in questions if _answered(q, turns))
    answer_ratio = 1.0 if not questions else answered_n / len(questions)
    op_avg_len = (sum(len(t["text"]) for t in op_turns) / len(op_turns)) if op_turns else 0
    rude_hit = (_profanity(op_text) or _has(op_text, ph("no_rudeness"))) if on("no_rudeness") else None
    ty_hit = bool(TY_RE.search(" " + op_text + " "))
    courtesy_n = _count(op_text, ph("courtesy"))
    greet_hit = _has(op_text[:220] if op_text else "", ph("greeting")) or (
        _has(normalize(op_turns[0]["text"]), ph("greeting")) if op_turns else None
    )

    checks: list[CheckResult] = []

    def add(cid, title, category, passed, score, weight, detail, evidence=None):
        if not on(cid):
            return
        checks.append(
            CheckResult(
                id=cid,
                title=cfg[cid]["title"],
                category=category,
                passed=bool(passed),
                score=_clip(score),
                weight=float(cfg[cid]["weight"]),
                detail=detail,
                evidence=evidence,
            )
        )

    add(
        "greeting",
        "Приветствие",
        "script",
        bool(greet_hit),
        100 if greet_hit else 15,
        1.2,
        "Оператор поприветствовал клиента." if greet_hit else "В начале разговора нет приветствия.",
        _evidence(op_turns[:2], ph("greeting")) if greet_hit else None,
    )
    intro_hit = _has(op_text, ph("intro")) or _self_named(turns)
    add(
        "intro",
        "Представление оператора",
        "script",
        bool(intro_hit),
        100 if intro_hit else 20,
        1.0,
        "Оператор назвал себя." if intro_hit else "Оператор не представился.",
        (_evidence(turns, ph("intro")) or (op_turns[0]["text"] if op_turns else None)) if intro_hit else None,
    )
    company_hit = _has(op_text, company)
    add(
        "company",
        "Название компании",
        "script",
        bool(company_hit),
        100 if company_hit else 25,
        1.0,
        "Названа организация." if company_hit else "В речи оператора нет названия компании.",
        _evidence(turns, company) if company_hit else None,
    )
    help_hit = _has(op_text, ph("help_offer"))
    add(
        "help_offer",
        "Предложение помощи",
        "script",
        bool(help_hit),
        100 if help_hit else 30,
        0.9,
        "Есть предложение помощи." if help_hit else "Нет явного «чем могу помочь».",
        _evidence(turns, ph("help_offer")) if help_hit else None,
    )
    clarify_hit = _has(op_text, ph("clarify"))
    add(
        "clarify",
        "Уточнение запроса",
        "script",
        bool(clarify_hit),
        100 if clarify_hit else 35,
        1.0,
        "Оператор уточнил детали обращения." if clarify_hit else "Нет уточняющих вопросов по сути запроса.",
        _evidence(turns, ph("clarify")) if clarify_hit else None,
    )
    close_hit = _has(op_text, ph("closing"))
    add(
        "closing",
        "Корректное завершение",
        "script",
        bool(close_hit),
        100 if close_hit else 25,
        1.0,
        "Диалог закрыт вежливой формулой." if close_hit else "Нет прощания и предложения обратиться снова.",
        _evidence(turns, ph("closing")) if close_hit else None,
    )
    extra_hit = _has(op_text, extra) if extra else None
    if extra:
        add(
            "extra_script",
            "Дополнительные фразы скрипта",
            "script",
            bool(extra_hit),
            100 if extra_hit else 20,
            0.8,
            "Есть обязательные фразы из вашего скрипта." if extra_hit else "Не найдены дополнительные фразы скрипта.",
            _evidence(turns, extra) if extra_hit else None,
        )

    courtesy_score = _clip(20 + courtesy_n * 18)
    add(
        "courtesy",
        "Вежливые формулы",
        "politeness",
        courtesy_n >= 2,
        courtesy_score,
        1.1,
        f"Вежливых формул в речи оператора: {courtesy_n}.",
        _evidence(turns, ph("courtesy")) if courtesy_n else None,
    )
    add(
        "no_rudeness",
        "Отсутствие грубости",
        "politeness",
        not rude_hit,
        0 if rude_hit else 100,
        1.6,
        f"Обнаружена резкая формулировка: «{rude_hit}»." if rude_hit else "Грубых формулировок нет.",
        _evidence(turns, [rude_hit]) if rude_hit else None,
    )
    add(
        "no_tutoyage",
        "Обращение на «вы»",
        "politeness",
        not ty_hit,
        10 if ty_hit else 100,
        1.0,
        "Оператор перешёл на «ты»." if ty_hit else "Соблюдено обращение на «вы».",
    )
    empathy_hit = _has(op_text, ph("empathy"))
    client_neg = bool(_has(client_text, NEG_WORDS))
    empathy_needed = client_neg
    empathy_ok = bool(empathy_hit) or not empathy_needed
    add(
        "empathy",
        "Эмпатия",
        "politeness",
        empathy_ok,
        100 if empathy_hit else (70 if not empathy_needed else 25),
        0.9,
        "Оператор обозначил понимание ситуации."
        if empathy_hit
        else ("Клиент спокоен, эмпатия не обязательна." if not empathy_needed else "Клиент встревожен, эмпатии в ответах нет."),
        _evidence(turns, ph("empathy")) if empathy_hit else None,
    )
    interrupt_ok = interrupts == 0
    add(
        "interrupt",
        "Без перебиваний",
        "politeness",
        interrupt_ok,
        100 if interrupt_ok else max(20, 100 - interrupts * 35),
        0.8,
        "Оператор дослушивает клиента." if interrupt_ok else f"Признаков перебивания: {interrupts}.",
    )

    add(
        "answers",
        "Ответы на вопросы клиента",
        "completeness",
        answer_ratio >= 0.8,
        _clip(answer_ratio * 100),
        1.4,
        f"Закрыто вопросов: {answered_n} из {len(questions)}."
        if questions
        else "Клиент не задавал прямых вопросов — оценка по содержанию реплик.",
    )
    substance_ok = op_avg_len >= 55
    add(
        "substance",
        "Содержательность ответов",
        "completeness",
        substance_ok,
        _clip(min(100, op_avg_len * 1.1)),
        1.1,
        f"Средняя длина реплики оператора: {op_avg_len:.0f} символов.",
    )
    if avg_resp is None:
        resp_score, resp_ok = 70.0, True
        resp_detail = "Паузы между репликами не размечены — метрика по времени ответа пропущена."
    else:
        if avg_resp <= 3:
            resp_score, resp_ok = 100.0, True
        elif avg_resp <= 6:
            resp_score, resp_ok = 75.0, True
        else:
            resp_score, resp_ok = max(20.0, 100 - (avg_resp - 3) * 10), False
        resp_detail = f"Средняя пауза перед ответом оператора: {avg_resp:.1f} с."
    add("response_time", "Время ответа", "completeness", resp_ok, resp_score, 0.8, resp_detail)

    res_hit = _has(op_text, ph("resolution_lang"))
    add(
        "resolution_lang",
        "Предложено решение",
        "resolution",
        bool(res_hit),
        100 if res_hit else 30,
        1.3,
        "Оператор озвучил конкретное действие или результат." if res_hit else "Нет явного решения или фиксации результата.",
        _evidence(turns, ph("resolution_lang")) if res_hit else None,
    )
    next_hit = _has(op_text, ph("next_steps"))
    add(
        "next_steps",
        "Понятные следующие шаги",
        "resolution",
        bool(next_hit),
        100 if next_hit else 35,
        1.1,
        "Назван срок, канал или номер обращения." if next_hit else "Клиент не получил ясного следующего шага.",
        _evidence(turns, ph("next_steps")) if next_hit else None,
    )
    confirm_hit = _has(op_text, ph("confirm")) or _has(
        client_text, CLIENT_OK
    )
    add(
        "confirm",
        "Подтверждение, что вопрос закрыт",
        "resolution",
        bool(confirm_hit),
        100 if confirm_hit else 40,
        1.0,
        "Есть подтверждение закрытия вопроса." if confirm_hit else "Нет подтверждения, что клиент удовлетворён.",
    )
    bounce = _has(op_text, ph("no_hanging"))
    hanging = (not close_hit and not res_hit) or bool(bounce)
    add(
        "no_hanging",
        "Диалог не брошен",
        "resolution",
        not hanging,
        25 if hanging else 100,
        1.2,
        "Клиента отправили «не туда» или разговор оборван без решения." if hanging else "Разговор доведён до завершения.",
        _evidence(turns, ph("no_hanging")) if bounce else None,
    )

    politeness = _weighted(checks, "politeness")
    completeness = _weighted(checks, "completeness")
    script = _weighted(checks, "script")
    resolution = _weighted(checks, "resolution")
    cw = checklist.get("category_weights") or {}
    parts = {"politeness": politeness, "completeness": completeness, "script": script, "resolution": resolution}
    total_w = sum(float(cw.get(k, 0)) for k in parts) or 1.0
    overall = _clip(sum(float(cw.get(k, 0)) * v for k, v in parts.items()) / total_w)
    if rude_hit:
        overall = _clip(overall * RUDE_PENALTY)

    violations = [
        {"id": c.id, "title": c.title, "detail": c.detail, "category": c.category}
        for c in checks
        if not c.passed
    ]
    violation_pct = _clip(100.0 * len(violations) / max(1, len(checks)))
    tonality, tonality_score = _tonality(op_text, client_text, bool(rude_hit))

    recs: list[str] = []
    for c in checks:
        if not c.passed and c.id in ADVICE:
            recs.append(ADVICE[c.id])
    if overall >= 88:
        recs.insert(0, "Эталонный звонок — можно разбирать на обучении смены.")
    recs = recs[:6]
    if not recs:
        recs.append("Существенных нарушений нет. Держите тот же ритм: приветствие, уточнение, решение, закрытие.")

    topic = _topic(all_text)
    # Имя оператора в резюме не пишем: резюме уходит в отчёты и экспорт, а имя - ПДн.
    outcome = "вопрос закрыт" if resolution >= 70 and not hanging else "вопрос требует доработки"
    summary = (
        f"Оператор провёл консультацию по теме «{topic}». "
        f"Вежливость {politeness:.0f}, скрипт {script:.0f}, решение {resolution:.0f}. "
        f"Итог: {outcome}. Тональность — {_tonality_ru(tonality)}."
    )

    return QualityReport(
        politeness=politeness,
        completeness=completeness,
        script_compliance=script,
        resolution=resolution,
        overall=overall,
        tonality=tonality,
        tonality_score=tonality_score,
        avg_response_sec=avg_resp,
        talk_ratio_operator=_talk_ratio(turns),
        violation_pct=violation_pct,
        violations=violations,
        recommendations=recs,
        checks=checks,
        summary=summary,
    )


def _tonality_ru(label: str) -> str:
    return {"positive": "позитивная", "negative": "негативная", "neutral": "нейтральная"}.get(label, label)


def report_to_json(report: QualityReport) -> dict[str, str]:
    return {
        "violations_json": json.dumps(report.violations, ensure_ascii=False),
        "recommendations_json": json.dumps(report.recommendations, ensure_ascii=False),
        "checks_json": json.dumps([asdict(c) for c in report.checks], ensure_ascii=False),
    }
