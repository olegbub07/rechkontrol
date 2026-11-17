"""Необязательная оценка звонка локальной LLM через Ollama.

Включается LLM_PROVIDER=ollama. Модель получает расшифровку с ролями (ПДн замаскированы)
и итог правил, отвечает JSON с четырьмя оценками 0-100, рекомендациями и кратким итогом.
Любая ошибка (Ollama не запущена, таймаут, кривой JSON) - звонок оценивается только правилами.
Адрес Ollama по умолчанию разрешён только локальный: текст звонка не уходит с машины.
"""

from __future__ import annotations

import copy
import json
import logging
from urllib.parse import urlparse

from app.config import settings
from app.pii import mask_segments, mask_text
from app.checklist import CATEGORIES, default_checklist
from app.quality import CheckResult, QualityReport, _clip

log = logging.getLogger("rechkontrol.llm")

METRICS = ("politeness", "completeness", "script_compliance", "resolution")
# метрика отчёта -> ключ группы в чек-листе
METRIC_CATEGORY = {"politeness": "politeness", "completeness": "completeness", "script_compliance": "script", "resolution": "resolution"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_CHARS = 12000
# Ответ - короткий JSON (около 110 токенов). Потолок не даёт модели генерировать до таймаута:
# на замере qwen2.5:3b один звонок из 14 не ответил за 120 с, повтор занял 5-6 с.
MAX_TOKENS = 600

SYSTEM_PROMPT = (
    "Ты эксперт по контролю качества контакт-центра банка. Оцени работу ОПЕРАТОРА по расшифровке. "
    "Отвечай только JSON без пояснений вида: "
    '{"politeness": 0-100, "completeness": 0-100, "script_compliance": 0-100, "resolution": 0-100, '
    '"recommendations": ["до 4 конкретных советов оператору"], "summary": "1-2 предложения"}. '
    "politeness - вежливость и эмпатия; completeness - полнота и точность ответов клиенту; "
    "script_compliance - приветствие, представление, название банка, уточнение запроса, прощание; "
    "resolution - решён ли вопрос клиента или назначен понятный следующий шаг. Пиши по-русски."
)


def enabled() -> bool:
    return (settings.llm_provider or "").strip().lower() == "ollama"


def _url_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    return parsed.hostname in LOCAL_HOSTS or settings.llm_allow_remote


def category_weights(checklist: dict | None) -> dict[str, float]:
    """Веса групп из чек-листа, как в правилах. Метрика отчёта -> нормированный вес."""
    cw = (checklist or default_checklist()).get("category_weights") or {}
    raw = {m: max(0.0, float(cw.get(cat, 0) or 0)) for m, cat in METRIC_CATEGORY.items()}
    total = sum(raw.values())
    if total <= 0:
        return {m: 0.25 for m in METRICS}
    return {m: w / total for m, w in raw.items()}


def criteria_brief(checklist: dict | None) -> str:
    """Включённые критерии с весами: модель оценивает по тем же пунктам, что и правила."""
    cl = checklist or default_checklist()
    weights = category_weights(cl)
    lines = []
    for metric, cat in METRIC_CATEGORY.items():
        rows = [r for r in cl.get("criteria", []) if r.get("category") == cat and r.get("enabled", True) and float(r.get("weight", 0)) > 0]
        if not rows:
            continue
        items = "; ".join(f"{r['title']} (вес {float(r['weight']):g})" for r in rows)
        lines.append(f"{metric} [{CATEGORIES[cat]}, доля в итоге {weights[metric]:.0%}]: {items}")
    return "\n".join(lines)


def build_prompt(turns: list[dict], report: QualityReport, checklist: dict | None = None) -> str:
    lines = []
    for turn in mask_segments([t for t in turns if isinstance(t, dict)]):
        who = "Оператор" if turn.get("role") == "operator" else "Клиент"
        lines.append(f"{who}: {turn.get('text') or ''}")
    dialog = "\n".join(lines)
    if len(dialog) > MAX_CHARS:
        dialog = dialog[: MAX_CHARS // 2] + "\n…\n" + dialog[-MAX_CHARS // 2 :]
    # Оценки правил в промпт не передаются: на замерах qwen2.5:3b в 4 из 6 звонков
    # просто копировал их, и смешивание теряло смысл. Модель должна оценивать сама.
    del report
    brief = criteria_brief(checklist)
    head = (
        "Критерии оценки (учитывай только их, отключённые пункты не оценивай, важнее пункты с большим весом):\n"
        f"{brief}\n\n"
        if brief
        else ""
    )
    return f"{head}Расшифровка звонка:\n{dialog}"


def parse_answer(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        start, end = (text or "").find("{"), (text or "").rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    out: dict = {}
    for metric in METRICS:
        try:
            out[metric] = _clip(float(data[metric]))
        except (KeyError, TypeError, ValueError):
            return None
    recs = data.get("recommendations") or []
    # Ответ модели тоже может повторить имя или цифры из диалога: маскируем до сохранения.
    out["recommendations"] = [mask_text(str(r).strip()[:300]) for r in recs if str(r).strip()][:4] if isinstance(recs, list) else []
    out["summary"] = mask_text(str(data.get("summary") or "").strip()[:600])
    return out


def ask_ollama(prompt: str) -> dict | None:
    import httpx

    base = settings.ollama_url.rstrip("/")
    if not _url_allowed(base):
        log.warning("OLLAMA_URL не локальный, LLM пропущена (LLM_ALLOW_REMOTE=false)")
        return None
    body = {
        "model": settings.llm_model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": MAX_TOKENS},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        response = httpx.post(f"{base}/api/chat", json=body, timeout=settings.llm_timeout_sec)
        response.raise_for_status()
        content = (response.json().get("message") or {}).get("content") or ""
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            log.warning(
                "В Ollama нет модели %s, оценка только правилами. Скачайте её: ollama pull %s",
                settings.llm_model,
                settings.llm_model,
            )
        else:
            log.warning("Ollama ответила %s, оценка только правилами", exc.response.status_code)
        return None
    except Exception as exc:
        log.warning("Ollama недоступна, оценка только правилами: %s", exc.__class__.__name__)
        return None
    return parse_answer(content)


def blend(report: QualityReport, answer: dict, checklist: dict | None = None) -> QualityReport:
    """Смешивает оценки правил и LLM (вес LLM_WEIGHT) и добавляет советы модели.

    Итог считается с теми же весами групп, что и в правилах (из чек-листа), а штраф
    за грубость переносится как есть: модель не может «отмыть» мат в звонке."""
    weight = max(0.0, min(1.0, float(settings.llm_weight)))
    cw = category_weights(checklist)
    out = copy.deepcopy(report)
    for metric in METRICS:
        setattr(out, metric, _clip((1 - weight) * getattr(report, metric) + weight * answer[metric]))
    base = sum(cw[m] * getattr(report, m) for m in METRICS)
    penalty = (report.overall / base) if base > 0 and report.overall < _clip(base) - 0.5 else 1.0
    out.overall = _clip(sum(cw[m] * getattr(out, m) for m in METRICS) * penalty)
    extra = [f"ИИ: {item}" for item in answer["recommendations"] if item not in out.recommendations]
    out.recommendations = extra + list(out.recommendations)
    if answer["summary"]:
        out.summary = (out.summary + " Мнение модели: " + answer["summary"]).strip()
    detail = ", ".join(f"{m}={answer[m]:.0f}" for m in METRICS)
    out.checks = list(out.checks) + [
        CheckResult(
            id="llm_review",
            title=f"Оценка локальной LLM ({settings.llm_model})",
            category="llm",
            passed=True,
            score=100.0,
            weight=0.0,
            detail=f"Вес модели {weight:.0%}: {detail}",
        )
    ]
    return out


def enrich(report: QualityReport, turns: list[dict], checklist: dict | None = None) -> QualityReport:
    if not enabled() or not turns:
        return report
    answer = ask_ollama(build_prompt(turns, report, checklist))
    if answer is None:
        return report
    return blend(report, answer, checklist)
