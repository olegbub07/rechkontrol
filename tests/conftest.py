"""Тесты работают на отдельном каталоге данных и не трогают data/ рабочей копии."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TEST_DATA = Path(tempfile.mkdtemp(prefix="rechkontrol-test-"))
os.environ["DATA_DIR"] = str(_TEST_DATA)
os.environ.setdefault("DEMO_VOICE_ENABLED", "false")
os.environ.setdefault("LLM_PROVIDER", "none")

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepared_store():
    from app.auth import ensure_staff_accounts
    from app.db import SessionLocal, init_db
    from app.seed import seed_if_empty

    init_db()
    with SessionLocal() as session:
        seed_if_empty(session)
        ensure_staff_accounts(session)
        session.commit()
    yield
    shutil.rmtree(_TEST_DATA, ignore_errors=True)
