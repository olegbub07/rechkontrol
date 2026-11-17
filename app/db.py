from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable, Generator
from pathlib import Path
from threading import Lock

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

log = logging.getLogger("rechkontrol.db")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_DDL_RE = re.compile(r"^[A-Za-z0-9 ()]+$")
_BLOCKED_PRAGMAS = frozenset({"writable_schema"})
_BLOCKED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})

_connect_args = {"check_same_thread": False}
engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args=_connect_args,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
write_lock = Lock()


class Base(DeclarativeBase):
    pass


def _authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _src: str | None) -> int:
    """ATTACH, подмена схемы и чтение чужих файлов через SQL запрещены на каждом соединении."""
    try:
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            return sqlite3.SQLITE_DENY
        names = {(arg1 or "").lower(), (arg2 or "").lower()}
        if action == sqlite3.SQLITE_FUNCTION and names & _BLOCKED_FUNCTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and arg2 not in (None, ""):
            pragma = (arg1 or "").lower().split(".")[-1]
            if pragma in _BLOCKED_PRAGMAS:
                return sqlite3.SQLITE_DENY
    except Exception:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _set_if_present(dbapi_connection, name: str, enabled: bool) -> None:
    option = getattr(sqlite3, name, None)
    if option is None or not hasattr(dbapi_connection, "setconfig"):
        return
    try:
        dbapi_connection.setconfig(option, enabled)
    except (sqlite3.Error, AttributeError):
        return


@event.listens_for(engine, "connect")
def _on_connect(dbapi_connection, _connection_record) -> None:
    _set_if_present(dbapi_connection, "SQLITE_DBCONFIG_DEFENSIVE", True)
    _set_if_present(dbapi_connection, "SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION", False)
    _set_if_present(dbapi_connection, "SQLITE_DBCONFIG_TRUSTED_SCHEMA", False)
    _set_if_present(dbapi_connection, "SQLITE_DBCONFIG_WRITABLE_SCHEMA", False)
    if hasattr(dbapi_connection, "setlimit") and hasattr(sqlite3, "SQLITE_LIMIT_ATTACHED"):
        try:
            dbapi_connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        except sqlite3.Error:
            pass
    if hasattr(dbapi_connection, "setlimit") and hasattr(sqlite3, "SQLITE_LIMIT_SQL_LENGTH"):
        try:
            dbapi_connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1_000_000)
        except sqlite3.Error:
            pass
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA secure_delete=ON")
    cursor.execute("PRAGMA cell_size_check=ON")
    cursor.execute("PRAGMA trusted_schema=OFF")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()
    if hasattr(dbapi_connection, "set_authorizer"):
        dbapi_connection.set_authorizer(_authorizer)


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate()
    assert_integrity()
    protect_store()


def _migrate() -> None:
    statements = (
        ("users", "is_active", "INTEGER DEFAULT 1"),
        ("users", "token_version", "INTEGER DEFAULT 1"),
        ("users", "last_login_at", "DATETIME"),
        ("users", "operator_id", "INTEGER"),
        ("calls", "flagged", "INTEGER DEFAULT 0"),
        ("calls", "review_note", "TEXT"),
    )
    with engine.begin() as conn:
        for table, column, ddl in statements:
            if not _IDENT_RE.fullmatch(table) or not _IDENT_RE.fullmatch(column) or not _DDL_RE.fullmatch(ddl):
                raise RuntimeError("схема миграции повреждена")
            rows = conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
            names = {row[1] for row in rows}
            if column not in names:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))
        conn.execute(text("UPDATE users SET is_active = 1 WHERE is_active IS NULL"))
        conn.execute(text("UPDATE users SET token_version = 1 WHERE token_version IS NULL"))
        conn.execute(text("UPDATE calls SET flagged = 0 WHERE flagged IS NULL"))


def assert_integrity() -> None:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA quick_check")).fetchall()
    messages = [str(row[0]) for row in rows]
    if messages != ["ok"]:
        raise RuntimeError("Файл базы не прошёл проверку целостности")


def protect_store() -> None:
    from app.crypto import restrict_file

    paths = [
        settings.db_path,
        Path(str(settings.db_path) + "-wal"),
        Path(str(settings.db_path) + "-shm"),
        settings.key_path,
        settings.jwt_secret_path,
    ]
    for path in paths:
        if path.is_file():
            restrict_file(path)


def db_guard_status() -> dict:
    with engine.connect() as conn:
        foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()
        trusted = conn.execute(text("PRAGMA trusted_schema")).scalar()
        secure_delete = conn.execute(text("PRAGMA secure_delete")).scalar()
        cell_check = conn.execute(text("PRAGMA cell_size_check")).scalar()
        quick = conn.execute(text("PRAGMA quick_check")).scalar()
    return {
        "quick_check": str(quick),
        "foreign_keys": bool(foreign_keys),
        "trusted_schema_off": int(trusted or 0) == 0,
        "secure_delete": bool(secure_delete),
        "cell_size_check": bool(cell_check),
        "attach_blocked": True,
    }


def after_commit(session: Session, fn: Callable[[], None]) -> None:
    """Файл удаляется только если запись в базе уже сохранена."""

    def _run(sess: Session) -> None:
        event.remove(sess, "after_commit", _run)
        try:
            fn()
        except Exception:
            log.exception("cleanup after commit failed")

    event.listen(session, "after_commit", _run)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
