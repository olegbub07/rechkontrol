"""Чистка галлюцинаций Whisper: зацикленные повторы, «СППППП», титры из обучающих видео.

Whisper на тишине и шуме дописывает то, что часто видел в субтитрах YouTube
(«Субтитры сделал DimaTorzok», «Продолжение следует...»), или зацикливается
на одном слове. Такой текст портит оценку и расшифровку, поэтому вырезаем его
и в новых звонках, и при пересчёте старых.
"""

from __future__ import annotations

import re

# Целые фразы-титры. Вырезаем предложение, в котором они встретились.
_OUTROS = re.compile(
    r"(?i)(?:^|(?<=[.!?…]))\s*[^.!?…]*?(?:"
    r"продолжение\s+следует"
    r"|спасибо\s+за\s+просмотр"
    r"|подписывайтесь\s+на\s+(?:канал|наш)"
    r"|ставьте\s+лайк"
    r"|dimatorzok|amara\.org"
    r")[^.!?…]*[.!?…]*"
)
# Титры с именами («Редактор субтитров А.Синецкая Корректор А.Егорова») идут в самом конце:
# режем от них до конца фрагмента, точки в инициалах не должны их спасать.
_CREDITS = re.compile(
    r"(?i)(?:субтитр\w*\s+(?:сделал|создал|создавал|подготовил|делал|by)|редактор\w*\s+субтитр|автор\w*\s+субтитр"
    r"|корректор\w*\s+[A-ZА-ЯЁ]\.?\s*[A-ZА-ЯЁ]).*$"
)
_WORD = re.compile(r"\S+")
_STRIP = ".,!?…:;«»\"'()-"


def _norm(token: str) -> str:
    return token.strip(_STRIP).lower().replace("ё", "е")


def _stutter(token: str) -> bool:
    """«СППППП», «ааааааа», «ммммм» и обрывки вида «СПОКО-О-О»."""
    core = _norm(token)
    if not core:
        return False
    if re.search(r"(\w)\1{3,}", core):
        return True
    return len(core) >= 6 and len(set(core)) <= 2


def _collapse_ngrams(tokens: list[str], max_n: int = 4) -> list[str]:
    """Одно слово подряд больше трёх раз или кусок из 2-4 слов три раза и больше - оставляем одно повторение.
    «да да да» и «поймите, поймите» это живая речь и не трогаются."""
    out = list(tokens)
    for n in range(1, max_n + 1):
        too_many = 4 if n == 1 else 3
        i, result = 0, []
        while i < len(out):
            chunk = [_norm(t) for t in out[i:i + n]]
            if len(chunk) < n or not all(chunk):
                result.append(out[i])
                i += 1
                continue
            reps = 1
            while [_norm(t) for t in out[i + reps * n:i + (reps + 1) * n]] == chunk:
                reps += 1
            if reps >= too_many:
                result.extend(out[i:i + n])
                i += reps * n
            else:
                result.append(out[i])
                i += 1
        out = result
    return out


def clean_text(text: str) -> str:
    if not text:
        return ""
    cleaned = _OUTROS.sub(" ", _CREDITS.sub(" ", text))
    tokens = [t for t in _WORD.findall(cleaned) if not _stutter(t)]
    tokens = _collapse_ngrams(tokens)
    out = " ".join(tokens).strip()
    out = re.sub(r"\s+([,.!?…])", r"\1", out)
    return out if re.search(r"\w", out) else ""


def align_words(words: list[dict], text: str) -> list[dict]:
    """Оставить таймкоды только для слов, которые пережили чистку (по порядку)."""
    if not words:
        return []
    wanted = [_norm(t) for t in _WORD.findall(text)]
    kept, j = [], 0
    for w in words:
        if j < len(wanted) and _norm(str(w.get("text") or "")) == wanted[j]:
            kept.append(w)
            j += 1
    return kept


def is_hallucination(no_speech_prob: float | None, avg_logprob: float | None, compression_ratio: float | None) -> bool:
    """Сегмент, где Whisper сам не уверен, что слышал речь, или текст зациклен."""
    if no_speech_prob is not None and avg_logprob is not None and no_speech_prob > 0.6 and avg_logprob < -1.0:
        return True
    return compression_ratio is not None and compression_ratio > 2.8


def clean_turns(turns: list[dict]) -> tuple[list[dict], bool]:
    """Для уже сохранённых расшифровок: чистим текст реплик, пустые выбрасываем."""
    out, changed = [], False
    for t in turns:
        if not isinstance(t, dict):
            continue
        text = str(t.get("text") or "")
        new = clean_text(text)
        if new != text:
            changed = True
            if not new:
                continue
            t = {**t, "text": new}
            if isinstance(t.get("words"), list):
                t["words"] = align_words(t["words"], new)
        out.append(t)
    return out, changed
