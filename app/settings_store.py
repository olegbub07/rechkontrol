from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import Setting

DEFAULTS = {
    "company_phrases": ["центр-инвест", "центр инвест", "банк"],
    "extra_script_phrases": [],
}


def get_json(session: Session, key: str, default):
    row = session.get(Setting, key)
    if row is None:
        return default
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return default


def put_json(session: Session, key: str, value) -> None:
    row = session.get(Setting, key)
    payload = json.dumps(value, ensure_ascii=False)
    if row is None:
        session.add(Setting(key=key, value=payload))
    else:
        row.value = payload


def load_script(session: Session) -> dict:
    return {
        "company_phrases": get_json(session, "company_phrases", DEFAULTS["company_phrases"]),
        "extra_script_phrases": get_json(session, "extra_script_phrases", DEFAULTS["extra_script_phrases"]),
    }
