from __future__ import annotations

import json

from app.config import settings
from app.highlight import segment_highlights
from app.models import Call
from app.pii import mask_segments, mask_text
from app.schemas import CallDetail, CallListItem, CheckOut, MarkerOut, OperatorOut, QualityOut, SegmentOut, SpanOut


def operator_scores(calls: list) -> dict:
    done = [c for c in calls if getattr(c, "quality", None)]
    if not done:
        return {
            "calls_count": len(calls),
            "avg_overall": None,
            "violation_pct": None,
            "avg_politeness": None,
            "avg_completeness": None,
            "avg_script": None,
            "avg_resolution": None,
        }

    def avg(key: str) -> float:
        return round(sum(getattr(c.quality, key) for c in done) / len(done), 1)

    return {
        "calls_count": len(calls),
        "avg_overall": avg("overall"),
        "violation_pct": avg("violation_pct"),
        "avg_politeness": avg("politeness"),
        "avg_completeness": avg("completeness"),
        "avg_script": avg("script_compliance"),
        "avg_resolution": avg("resolution"),
    }


def operator_out(op, calls: list) -> OperatorOut:
    scores = operator_scores(calls)
    return OperatorOut(
        id=op.id,
        name=op.name,
        external_id=op.external_id,
        calls_count=scores["calls_count"],
        avg_overall=scores["avg_overall"],
        violation_pct=scores["violation_pct"],
        avg_politeness=scores["avg_politeness"],
        avg_completeness=scores["avg_completeness"],
        avg_script=scores["avg_script"],
        avg_resolution=scores["avg_resolution"],
    )


def call_item(call: Call) -> CallListItem:
    q = call.quality
    op = call.operator
    return CallListItem(
        id=call.id,
        orig_filename=call.orig_filename,
        status=call.status,
        progress=call.progress,
        call_at=call.call_at,
        uploaded_at=call.uploaded_at,
        duration_sec=call.duration_sec,
        operator_id=call.operator_id,
        operator_name=op.name if op else None,
        operator_external_id=op.external_id if op else None,
        overall=q.overall if q else None,
        politeness=q.politeness if q else None,
        completeness=q.completeness if q else None,
        script_compliance=q.script_compliance if q else None,
        resolution=q.resolution if q else None,
        tonality=q.tonality if q else None,
        violation_pct=q.violation_pct if q else None,
        avg_response_sec=q.avg_response_sec if q else None,
        is_demo=bool(call.is_demo),
        error_message=call.error_message,
        flagged=bool(call.flagged),
    )


def _json_list(raw: str | None) -> list:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _segments(raw: list) -> list[SegmentOut]:
    segments: list[SegmentOut] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            segments.append(
                SegmentOut(
                    start=float(item.get("start") or 0),
                    end=float(item.get("end") or 0),
                    speaker=str(item.get("speaker") or ""),
                    role=str(item.get("role") or "client"),
                    text=str(item.get("text") or ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return segments


def _checks(raw: list, mask=lambda value: value) -> list[CheckOut]:
    checks: list[CheckOut] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            checks.append(
                CheckOut(
                    id=str(item.get("id") or "check"),
                    title=str(item.get("title") or "Проверка"),
                    category=str(item.get("category") or ""),
                    passed=bool(item.get("passed")),
                    score=float(item.get("score") or 0),
                    weight=float(item.get("weight") or 0),
                    detail=mask(str(item.get("detail") or "")),
                    evidence=None if item.get("evidence") is None else mask(str(item.get("evidence"))),
                )
            )
        except (TypeError, ValueError):
            continue
    return checks


def _mask_violation(item: dict, mask) -> dict:
    return {key: mask(value) if isinstance(value, str) and key in {"evidence", "text", "quote", "detail"} else value for key, value in item.items()}


def call_detail(call: Call, unmasked: bool = False, script: dict | None = None) -> CallDetail:
    """Расшифровка в ответе API маскируется (ПДн), если не запрошен исходный текст.

    script (load_script) - если передан, у реплик появляется подсветка сработавших правил.
    """
    masking = settings.pii_masking and not unmasked
    mask = mask_text if masking else (lambda value: value)
    base = call_item(call)
    full_text = ""
    segments: list[SegmentOut] = []
    if call.transcript:
        full_text = mask(call.transcript.full_text)
        raw_segments = _json_list(call.transcript.segments_json)
        segments = _segments(mask_segments(raw_segments) if masking else raw_segments)
    quality = None
    if call.quality:
        q = call.quality
        quality = QualityOut(
            politeness=q.politeness,
            completeness=q.completeness,
            script_compliance=q.script_compliance,
            resolution=q.resolution,
            overall=q.overall,
            tonality=q.tonality,
            tonality_score=q.tonality_score,
            avg_response_sec=q.avg_response_sec,
            talk_ratio_operator=q.talk_ratio_operator,
            violation_pct=q.violation_pct,
            violations=[_mask_violation(item, mask) for item in _json_list(q.violations_json) if isinstance(item, dict)],
            recommendations=[mask(str(item)) for item in _json_list(q.recommendations_json)],
            checks=_checks(_json_list(q.checks_json), mask),
            summary=mask(q.summary),
        )
    if script is not None and segments:
        raw_checks = _json_list(call.quality.checks_json) if call.quality else []
        marks = segment_highlights(
            [s.model_dump() for s in segments],
            [c for c in raw_checks if isinstance(c, dict)],
            script["checklist"],
            script.get("company_phrases"),
            script.get("extra_script_phrases"),
            masked=masking,
        )
        segments = [
            s.model_copy(update={"spans": [SpanOut(**x) for x in m["spans"]], "markers": [MarkerOut(**x) for x in m["markers"]]})
            for s, m in zip(segments, marks)
        ]
    return CallDetail(
        **base.model_dump(),
        language=call.language,
        processed_sec=call.processed_sec,
        mime=call.mime,
        has_audio=bool(call.stored_name),
        full_text=full_text,
        segments=segments,
        quality=quality,
        review_note=call.review_note or "",
        masked=masking,
    )
