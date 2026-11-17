"""Тесты работают на отдельном каталоге данных и не трогают data/ рабочей копии."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TEST_DATA = Path(tempfile.mkdtemp(prefix="rechkontrol-test-"))
os.environ["DATA_DIR"] = str(_TEST_DATA)
# Тесты никогда не берут DATABASE_URL из окружения: там может быть рабочая база.
# PostgreSQL для тестов задаётся отдельно: TEST_DATABASE_URL=postgresql+psycopg://.../имя_test
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "")
os.environ.setdefault("DEMO_VOICE_ENABLED", "false")
os.environ.setdefault("LLM_PROVIDER", "none")
# Автоудаление по сроку в фоне тестам не нужно: его проверяет tests/test_152fz.py напрямую.
os.environ["RETENTION_DAYS"] = "0"
# Ключ только свой, в каталоге тестов.
os.environ.pop("MASTER_KEY", None)
os.environ.pop("MASTER_KEY_FILE", None)

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepared_store():
    from app.auth import ensure_staff_accounts
    from app.db import IS_SQLITE, Base, SessionLocal, engine, init_db
    from app.seed import seed_if_empty

    if not IS_SQLITE:
        # Тесты против PostgreSQL начинают с пустой схемы. Чистим только базу с "test" в имени.
        if "test" not in (engine.url.database or ""):
            raise RuntimeError("TEST_DATABASE_URL должен указывать на отдельную базу с 'test' в имени")
        from app import models  # noqa: F401

        Base.metadata.drop_all(bind=engine)
    init_db()
    with SessionLocal() as session:
        seed_if_empty(session)
        ensure_staff_accounts(session)
        session.commit()
    yield
    shutil.rmtree(_TEST_DATA, ignore_errors=True)
