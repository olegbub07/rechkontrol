"""Маскирование персональных данных в расшифровках.

Маскируются: e-mail, номера карт (с проверкой Луна), расчётные счета (20 цифр),
СНИЛС, ИНН, паспорт, телефоны, длинные номера без контекста и ФИО после речевых
маркеров («меня зовут», «моя фамилия» и т. п.) или по русскому отчеству.

Правила намеренно консервативны: лучше закрыть лишнюю цифру, чем показать паспорт.
"""

from __future__ import annotations

import re

PLACEHOLDERS = {
    "email": "[EMAIL]",
    "card": "[КАРТА]",
    "account": "[СЧЁТ]",
    "snils": "[СНИЛС]",
    "inn": "[ИНН]",
    "passport": "[ПАСПОРТ]",
    "phone": "[ТЕЛЕФОН]",
    "number": "[НОМЕР]",
    "name": "[ФИО]",
}

_SEP = r"[ \u00a0\-]?"


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
# Последовательность цифр с одиночными пробелами или дефисами между группами.
_DIGIT_RUN_RE = re.compile(r"(?<![\w\[])\+?\d(?:[ \u00a0\-()]{0,2}\d){5,}(?![\w\]])")

_INN_CTX = re.compile(r"(?i)\bинн\b[^\d\[]{0,28}$")
_SNILS_CTX = re.compile(r"(?i)\bснилс\b[^\d\[]{0,12}$")
_PASSPORT_CTX = re.compile(r"(?i)\b(паспорт\w*|серия|серии)\b[^\[]{0,24}$")
_ACCOUNT_CTX = re.compile(r"(?i)\b(сч[её]т\w*|р/с|расч[её]тн\w*)\b[^\d\[]{0,16}$")
_PHONE_CTX = re.compile(r"(?i)\b(телефон\w*|номер телефона|мобильн\w*|позвоните)\b[^\d\[]{0,16}$")
_SNILS_FMT = re.compile(r"^\d{3}-\d{3}-\d{3}[ \-]\d{2}$")


def _classify(raw: str, before: str) -> str | None:
    digits = _digits(raw)
    n = len(digits)
    tail = before[-40:]
    if n < 6:
        return None
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
    if n == 20 and _ACCOUNT_CTX.search(tail):
        return "account"
    if raw.strip().startswith("+7") or (n == 11 and digits[0] in "78") or (n == 10 and digits[0] == "9"):
        return "phone"
    if _PHONE_CTX.search(tail):
        return "phone"
    if _PASSPORT_CTX.search(tail) and n in (4, 6, 10):
        return "passport"
    if n == 12:
        return "inn"
    if n >= 8:
        return "number"
    if _ACCOUNT_CTX.search(tail) or _INN_CTX.search(tail):
        return "number"
    return None


def _mask_numbers(text: str) -> str:
    out: list[str] = []
    pos = 0
    for match in _DIGIT_RUN_RE.finditer(text):
        raw = match.group(0)
        # Хвостовые скобки или пробелы не входят в номер.
        stripped = raw.rstrip(" ()-\u00a0")
        kind = _classify(stripped, text[: match.start()])
        if kind is None:
            continue
        out.append(text[pos : match.start()])
        out.append(PLACEHOLDERS[kind])
        out.append(raw[len(stripped) :])
        pos = match.end()
    out.append(text[pos:])
    return "".join(out)


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
# Слова с большой буквы, которые не являются именами (начало фразы после маркера).
_NOT_NAMES = {
    "Центр", "Инвест", "Банк", "Здравствуйте", "Добрый", "Спасибо", "Да", "Нет", "Сейчас",
    "Подскажите", "Скажите", "Пожалуйста", "Хорошо", "Конечно", "Я", "Вы", "Мы",
}


def _mask_names(text: str) -> str:
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
        return f"{match.group(1)}{match.group(2)}{PLACEHOLDERS['name']}{rest}"

    text = _NAME_AFTER_CUE.sub(after_cue, text)
    return _FULL_NAME.sub(PLACEHOLDERS["name"], text)


def _passport_sub(match: re.Match) -> str:
    tag = PLACEHOLDERS["passport"]
    if not match.group(3).strip():
        return f"{match.group(1)}{tag}"
    return f"{match.group(1)}{tag}{match.group(3)}{tag}"


def mask_text(text: str | None) -> str:
    if not text:
        return text or ""
    value = _EMAIL_RE.sub(PLACEHOLDERS["email"], text)
    value = _PASSPORT_SPLIT_RE.sub(_passport_sub, value)
    value = _mask_numbers(value)
    value = _mask_names(value)
    return value


def mask_segments(segments: list[dict]) -> list[dict]:
    out = []
    for item in segments:
        if isinstance(item, dict):
            copy = dict(item)
            if "text" in copy:
                copy["text"] = mask_text(str(copy.get("text") or ""))
            copy.pop("words", None)
            out.append(copy)
    return out
