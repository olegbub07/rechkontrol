from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.access import reject_operator
from app.api.deps import current_user, db_session, require_admin
from app.audit import write_audit
from app.checklist import (
    CATEGORIES,
    LIMITS,
    NEGATIVE_CRITERIA,
    ChecklistError,
    default_checklist,
    diff_summary,
    load_checklist,
    save_checklist,
    validate,
)
from app.db import write_lock
from app.models import Call, User
from app.quality import analyze, report_to_json
from app.settings_store import load_script

router = APIRouter(tags=["checklist"])


def _payload(session: Session) -> dict:
    return {
        "checklist": load_checklist(session),
        "defaults": default_checklist(),
        "categories": CATEGORIES,
        "limits": LIMITS,
        "negative_criteria": sorted(NEGATIVE_CRITERIA),
    }


@router.get("/checklist")
def get_checklist(session: Session = Depends(db_session), user: User = Depends(current_user)) -> dict:
    reject_operator(user)
    return _payload(session)


@router.put("/checklist")
def put_checklist(
    request: Request,
    body: dict = Body(...),
    session: Session = Depends(db_session),
    user: User = Depends(require_admin),
) -> dict:
    try:
        clean = validate(body)
    except ChecklistError as exc:
        raise HTTPException(422, str(exc)) from exc
    old = load_checklist(session)
    save_checklist(session, clean)
    write_audit(session, "checklist.update", user=user, detail=diff_summary(old, clean), request=request)
    return _payload(session)


@router.post("/checklist/reset")
def reset_checklist(request: Request, session: Session = Depends(db_session), user: User = Depends(require_admin)) -> dict:
    old = load_checklist(session)
    fresh = default_checklist()
    save_checklist(session, fresh)
    write_audit(session, "checklist.reset", user=user, detail=diff_summary(old, fresh), request=request)
    return _payload(session)


@router.post("/checklist/rescore")
def rescore_calls(request: Request, session: Session = Depends(db_session), user: User = Depends(require_admin)) -> dict:
    """Пересчитать оценки готовых звонков по текущему чек-листу. Whisper не запускается, LLM тоже."""
    script = load_script(session)
    calls = (
        session.query(Call)
        .options(joinedload(Call.transcript), joinedload(Call.quality))
        .filter(Call.status == "done")
        .all()
    )
    changed = 0
    with write_lock:
        for call in calls:
            if call.transcript is None or call.quality is None:
                continue
            try:
                turns = json.loads(call.transcript.segments_json or "[]")
            except json.JSONDecodeError:
                continue
            report = analyze(turns, script["company_phrases"], script["extra_script_phrases"], script["checklist"])
            q = call.quality
            for key in (
                "politeness",
                "completeness",
                "script_compliance",
                "resolution",
                "overall",
                "tonality",
                "tonality_score",
                "avg_response_sec",
                "talk_ratio_operator",
                "violation_pct",
                "summary",
            ):
                setattr(q, key, getattr(report, key))
            for key, value in report_to_json(report).items():
                setattr(q, key, value)
            changed += 1
        write_audit(session, "checklist.rescore", user=user, detail=f"{changed} звонков", request=request)
        session.commit()
    return {"rescored": changed}
