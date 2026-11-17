"""Подсветка в расшифровке: какие фрагменты реплик сработали в правилах оценки.

Считается при выдаче карточки звонка по показанному тексту (маскированному или нет),
поэтому смещения всегда совпадают с тем, что видит пользователь. Смещения - в единицах
UTF-16, как у строк JavaScript.
"""

from __future__ import annotations

import difflib
import re
from functools import lru_cache

from app.checklist import CATEGORIES, RUDE_PENALTY, by_id
from app.pii import PLACEHOLDERS, mask_text
from app.quality import DEFAULT_COMPANY, NEG_WORDS, TY_RE

# Критерии, где найденная фраза в речи оператора - это плюс (подсвечиваем, если пункт пройден).
POSITIVE_PHRASE = (
    "greeting", "intro", "company", "help_offer", "clarify", "closing", "extra_script",
    "courtesy", "empathy", "resolution_lang", "next_steps", "confirm",
)
# Найденная фраза - нарушение.
NEGATIVE_PHRASE = ("no_rudeness", "no_hanging")
# Пропуски, которые отмечаем на первой / последней реплике оператора.
MISSING_FIRST = ("greeting", "intro", "company")
MISSING_LAST = ("closing",)

PRIORITY = {"bad": 4, "pii": 3, "neg": 2, "ok": 1}
_PII_RE = re.compile("|".join(re.escape(v) for v in sorted(set(PLACEHOLDERS.values()), key=len, reverse=True)))
_PII_TITLE = {
    "[EMAIL]": "e-mail", "[КАРТА]": "номер карты", "[СЧЁТ]": "номер счёта", "[СНИЛС]": "СНИЛС",
    "[ИНН]": "ИНН", "[ПАСПОРТ]": "паспорт", "[ТЕЛЕФОН]": "телефон", "[НОМЕР]": "номер", "[ФИО]": "ФИО",
}


@lru_cache(maxsize=4096)
def _phrase_re(phrase: str) -> re.Pattern:
    words = [w for w in phrase.split() if w]
    parts = []
    for w in words:
        parts.append("".join("[её]" if ch in "её" else re.escape(ch) for ch in w))
    return re.compile(r"\s+".join(parts), re.IGNORECASE)


def _u16(text: str, index: int) -> int:
    return len(text[:index].encode("utf-16-le")) // 2


def _points(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


class _Impact:
    """Сколько баллов итоговой оценки приходится на пункт чек-листа."""

    def __init__(self, checks: list[dict], category_weights: dict[str, float]):
        self.cat_w = {c: max(0.0, float(category_weights.get(c, 0) or 0)) for c in CATEGORIES}
        total = sum(self.cat_w.values()) or 1.0
        self.cat_share = {c: w / total for c, w in self.cat_w.items()}
        self.sum_w: dict[str, float] = {}
        for ch in checks:
            cat = ch.get("category")
            self.sum_w[cat] = self.sum_w.get(cat, 0.0) + float(ch.get("weight") or 0)

    def share(self, check: dict) -> float:
        cat = check.get("category")
        sw = self.sum_w.get(cat) or 0
        if not sw:
            return 0.0
        return float(check.get("weight") or 0) / sw * self.cat_share.get(cat, 0.0) * 100

    def lost(self, check: dict) -> float:
        return self.share(check) * (100 - float(check.get("score") or 0)) / 100

    def gained(self, check: dict) -> float:
        return self.share(check) * float(check.get("score") or 0) / 100


def _check_text(check: dict, impact: _Impact) -> tuple[str, str]:
    cat = CATEGORIES.get(check.get("category"), check.get("category") or "")
    if check.get("passed"):
        return check.get("title") or check["id"], f"{cat}: пункт пройден, вклад в итог +{_points(impact.gained(check))} б."
    return check.get("title") or check["id"], f"{cat}: пункт не пройден, итог −{_points(impact.lost(check))} б."


def _pii_spans(text: str, masked: bool) -> list[tuple[int, int, str]]:
    if masked:
        return [(m.start(), m.end(), _PII_TITLE.get(m.group(0), "данные")) for m in _PII_RE.finditer(text)]
    hidden = mask_text(text)
    if hidden == text:
        return []
    spans = []
    sm = difflib.SequenceMatcher(None, text, hidden, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete") and i2 > i1:
            ph = _PII_RE.search(hidden[max(0, j1 - 12) : j2 + 12])
            spans.append((i1, i2, _PII_TITLE.get(ph.group(0), "данные") if ph else "данные"))
    return spans


def segment_highlights(
    segments: list[dict],
    checks: list[dict],
    checklist: dict,
    company_phrases: list[str] | None = None,
    extra_script_phrases: list[str] | None = None,
    masked: bool = True,
) -> list[dict]:
    """Для каждой реплики: {"spans": [...], "markers": [...]}.

    span = {start, end, kind: ok|bad|neg|pii, rule, title, impact}; marker = {kind: miss, rule, title, impact}.
    """
    cfg = by_id(checklist)
    by_check = {c.get("id"): c for c in checks if isinstance(c, dict)}
    impact = _Impact([c for c in checks if isinstance(c, dict)], checklist.get("category_weights", {}))
    company = [p.lower().replace("ё", "е") for p in (company_phrases or DEFAULT_COMPANY) if p.strip()]
    extra = [p.lower().replace("ё", "е") for p in (extra_script_phrases or []) if p.strip()]

    def phrases(cid: str) -> list[str]:
        src = cfg.get(cid, {}).get("phrases")
        if src == "company":
            return company
        if src == "extra":
            return extra
        return list(src or [])

    rules: list[tuple[str, str, list[str]]] = []  # (kind, check_id, phrases)
    for cid in POSITIVE_PHRASE:
        ch = by_check.get(cid)
        if ch and ch.get("passed"):
            rules.append(("ok", cid, phrases(cid)))
    for cid in NEGATIVE_PHRASE:
        ch = by_check.get(cid)
        if ch and not ch.get("passed"):
            rules.append(("bad", cid, phrases(cid)))
    tutoyage = by_check.get("no_tutoyage")
    tutoyage_bad = bool(tutoyage and not tutoyage.get("passed"))

    op_idx = [i for i, s in enumerate(segments) if s.get("role") == "operator"]
    out: list[dict] = []
    for i, seg in enumerate(segments):
        text = str(seg.get("text") or "")
        raw: list[tuple[int, int, str, str, str, str]] = []
        is_op = seg.get("role") == "operator"
        if is_op:
            for kind, cid, plist in rules:
                title, note = _check_text(by_check[cid], impact)
                if cid == "no_rudeness":
                    note += f", итог умножен на {str(RUDE_PENALTY).replace(".", ",")}"
                for p in plist:
                    for m in _phrase_re(p).finditer(text):
                        raw.append((m.start(), m.end(), kind, cid, title, note))
            if tutoyage_bad:
                title, note = _check_text(tutoyage, impact)
                for m in TY_RE.finditer(" " + text.lower() + " "):
                    s, e = m.start(2) - 1, m.end(2) - 1
                    raw.append((s, e, "bad", "no_tutoyage", title, note))
        for p in NEG_WORDS:
            for m in _phrase_re(p).finditer(text):
                raw.append((m.start(), m.end(), "neg", "tonality", "Негативная лексика",
                            "Снижает тональность звонка, на итоговую оценку не влияет"))
        for s, e, what in _pii_spans(text, masked):
            raw.append((s, e, "pii", "pii", f"Персональные данные: {what}",
                        "Скрыто маскированием" if masked else "Будет скрыто при маскировании"))
        # Пересечения: оставляем более важный фрагмент (нарушение > ПДн > негатив > плюс), затем более длинный.
        raw.sort(key=lambda r: (-PRIORITY[r[2]], -(r[1] - r[0]), r[0]))
        taken: list[tuple[int, int]] = []
        spans = []
        for s, e, kind, cid, title, note in raw:
            if e <= s or any(s < te and ts < e for ts, te in taken):
                continue
            taken.append((s, e))
            spans.append({"start": _u16(text, s), "end": _u16(text, e), "kind": kind, "rule": cid, "title": title, "impact": note})
        spans.sort(key=lambda x: x["start"])
        markers = []
        if op_idx and i == op_idx[0]:
            for cid in MISSING_FIRST:
                ch = by_check.get(cid)
                if ch and not ch.get("passed"):
                    title, note = _check_text(ch, impact)
                    markers.append({"kind": "miss", "rule": cid, "title": f"Нет: {title.lower()}", "impact": note})
        if op_idx and i == op_idx[-1]:
            for cid in MISSING_LAST:
                ch = by_check.get(cid)
                if ch and not ch.get("passed"):
                    title, note = _check_text(ch, impact)
                    markers.append({"kind": "miss", "rule": cid, "title": f"Нет: {title.lower()}", "impact": note})
        out.append({"spans": spans, "markers": markers})
    return out
