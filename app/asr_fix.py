from __future__ import annotations

import re

# Типичные ошибки Whisper base на русской банковской речи.
# Порядок важен: сначала длинные устойчивые фразы.
_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"(?i)\bцентр[\s\-]*инвест\b", "Центр-инвест"),
    (r"(?i)\bцентренвест\b", "Центр-инвест"),
    (r"(?i)\bявляете с клиентом\b", "являетесь клиентом"),
    (r"(?i)\bявляете с\b", "являетесь"),
    (r"(?i)\bна\s+логобложен", "налогообложен"),
    (r"(?i)\bупрошенк", "упрощёнк"),
    (r"(?i)\bупрощенк", "упрощёнк"),
    (r"(?i)\bпаспорт\s+иный\s+лист\s+записи\b", "паспорт, ИНН и лист записи"),
    (r"(?i)\bпаспорт\s+и\s+нелист\s+записи\b", "паспорт, ИНН и лист записи"),
    (r"(?i)\bи\s+нелист\s+записи\b", "ИНН и лист записи"),
    (r"(?i)\bнелист\s+записи\b", "ИНН и лист записи"),
    (r"(?i)\bиный\s+лист\s+записи\b", "ИНН и лист записи"),
    (r"(?i)\bи\s+грипп\b", "ЕГРИП"),
    (r"(?i)\bегрипп?\b", "ЕГРИП"),
    (r"(?i)\bдля открытия щита\b", "для открытия счёта"),
    (r"(?i)\bоткрытия щита\b", "открытия счёта"),
    (r"(?i)\bиный лист запис[ьие]?\b", "ИНН и лист записи"),
    (r"(?i)\bщё?т откроем\b", "счёт откроем"),
    (r"(?i)\bч[её]т откроем\b", "счёт откроем"),
    (r"(?i)\bщ[её]т откроем\b", "счёт откроем"),
    (r"(?i)\bщита\b", "счёта"),
    (r"(?i)\bрасчетн", "расчётн"),
)

_COMPILED = [(re.compile(pat), repl) for pat, repl in _REPLACEMENTS]


def correct_asr_text(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern, repl in _COMPILED:
        out = pattern.sub(repl, out)
    return re.sub(r"\s{2,}", " ", out).strip()


def correct_segments(segments: list[dict]) -> list[dict]:
    fixed: list[dict] = []
    for seg in segments:
        item = dict(seg)
        item["text"] = correct_asr_text(seg.get("text") or "")
        if item["text"]:
            fixed.append(item)
    return fixed
