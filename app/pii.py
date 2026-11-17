"""Маскирование персональных данных в расшифровках, резюме и отчётах.

Маскируются: e-mail, номера карт (с проверкой Луна), расчётные счета (20 цифр), СНИЛС, ИНН,
паспорт, телефоны, длинные номера без контекста, даты (день + месяц + год, числовые даты,
год рождения), имена (словарь русских имён со склонениями, речевые маркеры «меня зовут»,
отчества), а также короткие числа и даты, которые клиент называет в ответ на вопрос о
карте, ИНН, паспорте, дате рождения и т. п. (контекст - предыдущая реплика или текст перед числом).

Правила намеренно консервативны: лучше закрыть лишнюю цифру, чем показать паспорт.
Номера обращений, суммы, проценты, сроки и время не маскируются.
"""

from __future__ import annotations

import re

from app.ru_names import AMBIGUOUS, is_first_name

PLACEHOLDERS = {
    "email": "[EMAIL]",
    "card": "[КАРТА]",
    "account": "[СЧЁТ]",
    "snils": "[СНИЛС]",
    "inn": "[ИНН]",
    "passport": "[ПАСПОРТ]",
    "phone": "[ТЕЛЕФОН]",
    "number": "[НОМЕР]",
    "date": "[ДАТА]",
    "name": "[ФИО]",
}

CONTEXT_CHARS = 160


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def luhn_ok(number: str) -> bool:
    digits = [int(ch) for ch in number if ch.isdigit()]
    if len(digits) < 13:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Последовательность цифр (любой длины) с одиночными пробелами или дефисами между группами.
_DIGIT_RUN_RE = re.compile(r"(?<![\w\[:\u2236])\+?\d(?:[ \u00a0\-()]{0,2}\d)*(?![\w\]:\u2236])")

_INN_CTX = re.compile(r"(?i)\bинн\b[^\d\[]{0,28}$")
_SNILS_CTX = re.compile(r"(?i)\bснилс\b[^\d\[]{0,12}$")
_PASSPORT_CTX = re.compile(r"(?i)\b(паспорт\w*|серия|серии)\b[^\[]{0,24}$")
_ACCOUNT_CTX = re.compile(r"(?i)\b(сч[её]т\w*|р/с|расч[её]тн\w*)\b[^\d\[]{0,16}$")
_PHONE_CTX = re.compile(r"(?i)\b(телефон\w*|номер телефона|мобильн\w*|позвоните)\b[^\d\[]{0,16}$")
_CARD_CTX = re.compile(r"(?i)\bкарт\w*\b[^\d\[.!?]{0,30}$")
_SNILS_FMT = re.compile(r"^\d{3}-\d{3}-\d{3}[ \-]\d{2}$")

# Вопрос оператора о данных клиента: числа и даты в ответе маскируются даже короткие.
_ASK = {
    "card": re.compile(r"(?i)(цифр\w*|номер\w*)\s+(?:\w+\s+){0,2}карт|карт\w*\s+(?:\w+\s+){0,2}(?:номер|цифр)|последни\w+\s+(?:\w+\s+){0,2}цифр"),
    "inn": re.compile(r"(?i)(?<!\[)\bинн\b(?!\])"),
    "passport": re.compile(r"(?i)паспорт|серию и номер"),
    "snils": re.compile(r"(?i)снилс"),
    "phone": re.compile(r"(?i)телефон|номер для связи|контактн\w+ номер"),
    "account": re.compile(r"(?i)номер\w*\s+(?:\w+\s+)?сч[её]т|р/с\b|расч[её]тн\w+ сч[её]т"),
    "date": re.compile(r"(?i)рожд|дат\w* выдачи"),
    "name": re.compile(r"(?i)(?<!\[)\bфио\b(?!\])|фамили|\bимя\b|отчеств|как (?:к вам )?(?:вас )?(?:зовут|обращаться)"),
    "code": re.compile(r"(?i)кодов\w* слов|код\w* из смс|\bпин"),
}
_ASK_ORDER = ("card", "inn", "passport", "snils", "phone", "account", "code")

# Число - не ПДн: сумма, проценты, сроки, количество, время.
_UNIT_AFTER = re.compile(
    r"(?i)^\s*(?:тыс|млн|млрд|руб|р\.|₽|%|процент|мин|час|сек|дн|день|дня|дней|недел|месяц|мес\b|год|лет|"
    r"шт|сотрудник|касс|платеж|платёж|человек|раз|штук|кв\.|м\b|км|кг|ч\b)"
)
_REF_BEFORE = re.compile(r"(?i)(обращени\w*|заявк\w*|заказ\w*|тариф\w*|модел\w*|офис\w*|отделени\w*|дом\w*|кв\.?|№|номер обращения|номер заявки)\s*[«\"]?\w*\s*[»\"]?\s*$")

_MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
_DAY_MONTH_YEAR = re.compile(
    rf"(?i)\b\d{{1,2}}(?:-?(?:го|е|ое))?\s+(?:{_MONTHS})\s+\d{{4}}(?:\s*(?:года|год|г\.?))?(?![\w])"
)
_MONTH_YEAR = re.compile(rf"(?i)\b(?:{_MONTHS})\s+(?:19|20)\d{{2}}(?:\s*(?:года|год|г\.?))?(?![\w])")
_NUM_DATE = re.compile(r"(?<![\d.])\d{1,2}[./]\d{1,2}[./](?:\d{4}|\d{2})(?![\d.])")
_BIRTH_YEAR = re.compile(r"(?i)(рожд\w*[^.\d\[]{0,24}?)((?:19|20)\d{2})(\s*(?:года|год|г\.?))?(?![\d])")
_DAY_MONTH = re.compile(rf"(?i)\b\d{{1,2}}(?:-?(?:го|е|ое))?\s+(?:{_MONTHS})(?![\w])")
_MONTH_ONLY = re.compile(rf"(?i)(?<![\w])(?:{_MONTHS})(?![\w])")
_YEAR_ONLY = re.compile(r"(?<![\d\w])(?:19|20)\d{2}(?![\d\w])")


_SENTENCE_SPLIT = re.compile(r"[.!?…]\s+")


_REQUEST = re.compile(r"(?i)\?|назов|продикт|подскаж|уточн|сообщ|скаж|какой|какие|какая|напомн")


def _own_question(before: str) -> str:
    """Текущая фраза и предыдущая, если та была вопросом или просьбой назвать данные."""
    parts = _SENTENCE_SPLIT.split(before)
    tail = parts[-1]
    if len(parts) > 1:
        # разделитель съеден split: вопросительный знак ищем в исходном тексте перед текущей фразой
        prev_end = len(before) - len(tail)
        prev = parts[-2] + before[prev_end - 2:prev_end]
        if _REQUEST.search(prev):
            tail = prev + " " + tail
    return tail


def _asked(prefix: str) -> set[str]:
    window = prefix[-CONTEXT_CHARS:]
    return {kind for kind, rx in _ASK.items() if rx.search(window)}


def _classify(raw: str, before: str, asked: set[str]) -> str | None:
    digits = _digits(raw)
    n = len(digits)
    tail = before[-40:]
    if n >= 6:
        if 13 <= n <= 19 and luhn_ok(digits) and not raw.strip().startswith("+"):
            return "card"
        if n == 20:
            return "account"
        if _SNILS_FMT.match(raw.strip()) or (n == 11 and _SNILS_CTX.search(tail)):
            return "snils"
        if n in (10, 12) and _INN_CTX.search(tail):
            return "inn"
        if n == 10 and _PASSPORT_CTX.search(tail):
            return "passport"
        if raw.strip().startswith("+7") or (n == 11 and digits[0] in "78") or (n == 10 and digits[0] == "9"):
            return "phone"
        if _PHONE_CTX.search(tail):
            return "phone"
    # Часть документа сразу после его названия: «ИНН 6164…», «паспорт 60 10», «карта 4419».
    if n >= 2:
        if _INN_CTX.search(tail):
            return "inn"
        if _SNILS_CTX.search(tail):
            return "snils"
        if _PASSPORT_CTX.search(tail):
            return "passport"
        if _ACCOUNT_CTX.search(tail) and n >= 4:
            return "account"
        if _CARD_CTX.search(tail) and n == 4:
            return "card"
    if n >= 6:
        if n == 12:
            return "inn"
        if n >= 8:
            return "number"
        if _ACCOUNT_CTX.search(tail):
            return "number"
    # Ответ на вопрос о данных клиента.
    if n >= 2 and asked & set(_ASK_ORDER):
        for kind in _ASK_ORDER:
            if kind in asked:
                return "number" if kind == "code" else kind
    return None


def _mask_numbers(text: str, context: str) -> str:
    out: list[str] = []
    pos = 0
    for match in _DIGIT_RUN_RE.finditer(text):
        raw = match.group(0)
        stripped = raw.rstrip(" ()-\u00a0")
        before = text[: match.start()]
        after = text[match.start() + len(stripped):]
        # Вопрос из своей реплики учитываем только в пределах текущей фразы: «открыть расчётный счёт.
        # Вежливость 49» - не номер счёта. Вопрос собеседника (context) учитывается целиком.
        asked = _asked(context + " " + _own_question(before))
        short = len(_digits(stripped)) < 6
        if short and (_UNIT_AFTER.match(after) or _REF_BEFORE.search(before[-40:])):
            continue
        kind = _classify(stripped, before, asked)
        if kind is None:
            continue
        out.append(text[pos : match.start()])
        out.append(PLACEHOLDERS[kind])
        out.append(raw[len(stripped):])
        pos = match.end()
    out.append(text[pos:])
    return "".join(out)


def _mask_dates(text: str, context: str) -> str:
    tag = PLACEHOLDERS["date"]
    text = _DAY_MONTH_YEAR.sub(tag, text)
    text = _MONTH_YEAR.sub(tag, text)
    text = _NUM_DATE.sub(tag, text)
    text = _BIRTH_YEAR.sub(lambda m: m.group(1) + tag, text)

    def contextual(rx: re.Pattern, value: str) -> str:
        parts: list[str] = []
        pos = 0
        for m in rx.finditer(value):
            if "date" in _asked(context + " " + value[: m.start()]):
                parts.append(value[pos : m.start()])
                parts.append(tag)
                pos = m.end()
        parts.append(value[pos:])
        return "".join(parts)

    for rx in (_DAY_MONTH, _MONTH_ONLY, _YEAR_ONLY):
        text = contextual(rx, text)
    return text


# Серия паспорта отдельным числом: «паспорт 45 10, номер 123456» и т. п.
_PASSPORT_SPLIT_RE = re.compile(r"(?i)(паспорт\w*[^\d\[]{0,20})(\d{2}\s?\d{2})([^\d\[]{0,20}?)(\d{6})\b")

_CAP = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
_NAME_CUES = (
    r"меня зовут",
    r"моя фамилия",
    r"фамилия",
    r"на имя",
    r"зовут",
    r"с вами говорит",
    r"это говорит",
    r"беспокоит",
    r"оператор",
    r"специалист",
)
_NAME_AFTER_CUE = re.compile(
    r"(?i:\b(" + "|".join(_NAME_CUES) + r")\b)([,:]?\s+)(" + _CAP + r"(?:\s+" + _CAP + r"){0,2})"
)
_PATRONYMIC = r"[А-ЯЁ][а-яё]+(?:вич|вна|ична|инична|ич)"
_FULL_NAME = re.compile(r"\b(?:" + _CAP + r"\s+)?" + _CAP + r"\s+" + _PATRONYMIC + r"\b|\b" + _PATRONYMIC + r"\b")
_CAP_WORD = re.compile(r"(?<![А-ЯЁа-яё\-\[])[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?(?![А-ЯЁа-яё\]])")
_SURNAME_END = re.compile(r"(?:(?:ов|ев|ёв|ин|ын)(?:а|у|ым|ой|е|ых|ы)?|(?:ск|цк)(?:ий|ая|ого|ому|им|ой|ую|ие|их)|енко|ко|ук|юк|ян|дзе|швили)$")
_NAME_TAG_RUN = re.compile(r"\[ФИО\](?:[ ,]*\[ФИО\])+")
# Слова с большой буквы, которые не являются именами (начало фразы после маркера).
_NOT_NAMES = {
    "Центр", "Инвест", "Банк", "Здравствуйте", "Добрый", "Спасибо", "Да", "Нет", "Сейчас",
    "Подскажите", "Скажите", "Пожалуйста", "Хорошо", "Конечно", "Я", "Вы", "Мы", "Ну", "Это",
    "Так", "Вот", "Минуту", "Секунду", "Записывайте", "Пишите", "Меня", "Моя", "Мой", "Моё",
    "Мое", "Сбер", "Сбербанк", "Россия", "Ростов", "Москва", "Алло", "Але", "Угу", "Ага",
    "Понял", "Поняла", "Понятно", "Отлично", "Верно", "Всё", "Все", "Как", "Что", "Где",
}


def _mask_names(text: str, context: str) -> str:
    tag = PLACEHOLDERS["name"]

    def after_cue(match: re.Match) -> str:
        words = match.group(3).split()
        if not words or words[0] in _NOT_NAMES:
            return match.group(0)
        kept: list[str] = []
        for word in words:
            if word in _NOT_NAMES:
                break
            kept.append(word)
        rest = match.group(3)[len(" ".join(kept)) :]
        return f"{match.group(1)}{match.group(2)}{tag}{rest}"

    text = _NAME_AFTER_CUE.sub(after_cue, text)
    text = _FULL_NAME.sub(tag, text)

    # Словарь имён: «Дмитрий», «Елена провела…». Соседнее слово с заглавной и фамильным окончанием - тоже.
    words = list(_CAP_WORD.finditer(text))
    hide: set[int] = set()
    for i, m in enumerate(words):
        if is_first_name(m.group(0)):
            # «Слава богу», «Вера в...» в начале фразы - скорее не имя
            if m.group(0).lower().replace("ё", "е") in AMBIGUOUS and re.search(r"(^|[.!?…]\s*)$", text[: m.start()]):
                continue
            hide.add(i)
            for j in (i - 1, i + 1):
                if 0 <= j < len(words):
                    w = words[j].group(0)
                    gap = text[min(m.end(), words[j].end()) : max(m.start(), words[j].start())]
                    if gap.strip() == "" and w not in _NOT_NAMES and _SURNAME_END.search(w.lower()):
                        hide.add(j)
    # Ответ на «назовите ФИО»: все слова с заглавной в ответе, кроме служебных.
    for i, m in enumerate(words):
        if i in hide or m.group(0) in _NOT_NAMES:
            continue
        if "name" in _asked(context + " " + text[: m.start()]):
            hide.add(i)
    out: list[str] = []
    pos = 0
    for i, m in enumerate(words):
        if i in hide:
            out.append(text[pos : m.start()])
            out.append(tag)
            pos = m.end()
    out.append(text[pos:])
    return _NAME_TAG_RUN.sub(tag, "".join(out))


def _passport_sub(match: re.Match) -> str:
    tag = PLACEHOLDERS["passport"]
    if not match.group(3).strip():
        return f"{match.group(1)}{tag}"
    return f"{match.group(1)}{tag}{match.group(3)}{tag}"


_NEAR_DATE = re.compile(r"(^|[^\d\w])(\d{4,5})([ ,.;:—-]{1,4}\[ДАТА\])")
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")


def mask_text(text: str | None, context: str = "") -> str:
    """Маскирует ПДн в text. context - предыдущая реплика: по ней понятно, что клиента спросили о карте, дате рождения и т. п."""
    if not text:
        return text or ""
    context = (context or "")[-CONTEXT_CHARS:]
    value = _EMAIL_RE.sub(PLACEHOLDERS["email"], text)
    value = _PASSPORT_SPLIT_RE.sub(_passport_sub, value)
    value = _mask_dates(value, context)
    # время «19:00» не номер: на время маскирования чисел прячем двоеточие
    value = _TIME_RE.sub(lambda m: f"{m.group(1)}\u2236{m.group(2)}", value)
    value = _mask_numbers(value, context)
    value = value.replace("\u2236", ":")
    # Короткое число рядом с датой рождения («4419, 12 марта 1984») - тоже данные клиента.
    value = _NEAR_DATE.sub(lambda m: m.group(1) + PLACEHOLDERS["number"] + m.group(3), value)
    value = _mask_names(value, context)
    return value


def mask_segments(segments: list[dict]) -> list[dict]:
    """Каждая реплика маскируется с учётом двух предыдущих реплик собеседника (вопрос о карте, дате рождения...)."""
    out = []
    history: list[tuple[str, str]] = []
    for item in segments:
        if isinstance(item, dict):
            copy = dict(item)
            raw = str(copy.get("text") or "")
            role = str(copy.get("role") or "")
            asked = [t for r, t in history[-3:] if not role or not r or r != role][-2:]
            if "text" in copy:
                copy["text"] = mask_text(raw, context=" ".join(asked))
            copy.pop("words", None)
            out.append(copy)
            history.append((role, raw))
    return out
