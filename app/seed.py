from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.demo_calls import DEMO_CALLS, DEMO_OPERATORS, EDGE_FILENAMES
from app.models import Call, Operator, Quality, Setting, Transcript
from app.quality import analyze, report_to_json
from app.settings_store import DEFAULTS, put_json


def _insert_demo(session: Session, item: dict, ops: dict) -> None:
    op = ops.get(item["operator"])
    call_at = datetime.fromisoformat(item["call_at"])
    turns = item["segments"]
    report = analyze(turns)
    call = Call(
        orig_filename=item["filename"],
        stored_name=None,
        mime="audio/wav",
        duration_sec=item["duration"],
        operator_id=op.id if op else None,
        call_at=call_at,
        uploaded_at=call_at,
        status="done",
        progress=100,
        language="ru",
        is_demo=1,
        processed_sec=12.4,
        operator_first=1,
    )
    session.add(call)
    session.flush()
    session.add(
        Transcript(
            call_id=call.id,
            full_text=" ".join(t["text"] for t in turns),
            segments_json=json.dumps(turns, ensure_ascii=False),
            language="ru",
        )
    )
    payload = report_to_json(report)
    session.add(
        Quality(
            call_id=call.id,
            politeness=report.politeness,
            completeness=report.completeness,
            script_compliance=report.script_compliance,
            resolution=report.resolution,
            overall=report.overall,
            tonality=report.tonality,
            tonality_score=report.tonality_score,
            avg_response_sec=report.avg_response_sec,
            talk_ratio_operator=report.talk_ratio_operator,
            violation_pct=report.violation_pct,
            summary=report.summary,
            **payload,
        )
    )


def _ensure_edge_demos(session: Session) -> None:
    """Крайние оценки 0 и 100 дописываются и когда в базе уже есть звонки."""
    found = {
        name
        for (name,) in session.query(Call.orig_filename).filter(Call.orig_filename.in_(EDGE_FILENAMES)).all()
    }
    missing = [item for item in DEMO_CALLS if item["filename"] in EDGE_FILENAMES and item["filename"] not in found]
    if not missing:
        return
    ops = {o.external_id: o for o in session.query(Operator).all()}
    for item in missing:
        _insert_demo(session, item, ops)
    session.flush()


def seed_if_empty(session: Session) -> None:
    if session.query(Operator).count() == 0:
        for item in DEMO_OPERATORS:
            session.add(Operator(name=item["name"], external_id=item["external_id"]))
        session.flush()
    if session.get(Setting, "company_phrases") is None:
        put_json(session, "company_phrases", DEFAULTS["company_phrases"])
    if session.get(Setting, "extra_script_phrases") is None:
        put_json(session, "extra_script_phrases", DEFAULTS["extra_script_phrases"])
    if session.query(Call).count() > 0:
        _ensure_edge_demos(session)
        return
    ops = {o.external_id: o for o in session.query(Operator).all()}
    for item in DEMO_CALLS:
        _insert_demo(session, item, ops)
    session.flush()
