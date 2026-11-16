from __future__ import annotations

import json

from app.models import Call
from app.schemas import CallDetail, CallListItem, CheckOut, QualityOut, SegmentOut


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
    )


def call_detail(call: Call) -> CallDetail:
    base = call_item(call)
    segments: list[SegmentOut] = []
    full_text = ""
    if call.transcript:
        full_text = call.transcript.full_text
        try:
            raw = json.loads(call.transcript.segments_json or "[]")
        except json.JSONDecodeError:
            raw = []
        segments = [SegmentOut(**s) for s in raw]
    quality = None
    if call.quality:
        q = call.quality
        try:
            checks_raw = json.loads(q.checks_json or "[]")
        except json.JSONDecodeError:
            checks_raw = []
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
            violations=json.loads(q.violations_json or "[]"),
            recommendations=json.loads(q.recommendations_json or "[]"),
            checks=[CheckOut(**c) for c in checks_raw],
            summary=q.summary,
        )
    return CallDetail(
        **base.model_dump(),
        language=call.language,
        processed_sec=call.processed_sec,
        mime=call.mime,
        has_audio=bool(call.stored_name),
        full_text=full_text,
        segments=segments,
        quality=quality,
    )
