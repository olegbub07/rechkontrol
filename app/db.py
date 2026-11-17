from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable, Generator
from pathlib import Path
from threading import Lock

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

log = logging.getLogger("rechkontrol.db")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_DDL_RE = re.compile(r"^[A-Za-z0-9 ()]+$")
_BLOCKED_PRAGMAS = frozenset({"writable_schema"})
_BLOCKED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})

def ensure_dirs() -> None:
    """Каталоги данных создаются до первого соединения: на свежем клоне data/ ещё нет."""
    for path in (settings.data_dir, settings.audio_dir, settings.tmp_dir, settings.models_dir):
        path.mkdir(parents=True, exist_ok=True)


ensure_dirs()

def database_url() -> str:
    """По умолчанию SQLite в data/app.db. DATABASE_URL=postgresql+psycopg://... включает PostgreSQL."""
    url = (settings.database_url or "").strip()
    if not url:
        return f"sqlite:///{settings.db_path}"
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix) :]
    if not (url.startswith("postgresql+psycopg://") or url.startswith("sqlite:///")):
        raise RuntimeError("DATABASE_URL: поддерживаются только postgresql+psycopg://... и sqlite:///...")
    return url


def _warn_if_remote(url: str) -> None:
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    host = parsed.host or ""
    if not host or host in {"127.0.0.1", "localhost", "::1"} or host.startswith("/"):
        return
    sslmode = str(parsed.query.get("sslmode") or "")
    if sslmode in {"require", "verify-ca", "verify-full"}:
        log.info("База данных на хосте %s, соединение с TLS (sslmode=%s)", host, sslmode)
    else:
        log.warning(
            "База данных на хосте %s без sslmode=require: если это другая машина, а не контейнер рядом, "
            "расшифровки идут по сети открытым текстом",
            host,
        )


DATABASE_URL = database_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

if IS_SQLITE:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, pool_pre_ping=True)
else:
    _warn_if_remote(DATABASE_URL)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)
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


def _on_connect(dbapi_connection, _connection_record) -> None:
    """Защита SQLite: только для SQLite-соединений."""
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


if IS_SQLITE:
    event.listen(engine, "connect", _on_connect)


def init_db() -> None:
    ensure_dirs()
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
        ("users", "last_seen_at", "BIGINT"),
        ("users", "operator_id", "INTEGER"),
        ("users", "must_change_password", "INTEGER DEFAULT 0"),
        ("calls", "flagged", "INTEGER DEFAULT 0"),
        ("calls", "review_note", "TEXT"),
    )
    with engine.begin() as conn:
        inspector = inspect(conn)
        for table, column, ddl in statements:
            if not _IDENT_RE.fullmatch(table) or not _IDENT_RE.fullmatch(column) or not _DDL_RE.fullmatch(ddl):
                raise RuntimeError("схема миграции повреждена")
            if not IS_SQLITE:
                ddl = ddl.replace("DATETIME", "TIMESTAMP")
            names = {col["name"] for col in inspector.get_columns(table)}
            if column not in names:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))
        conn.execute(text("UPDATE users SET is_active = 1 WHERE is_active IS NULL"))
        conn.execute(text("UPDATE users SET token_version = 1 WHERE token_version IS NULL"))
        conn.execute(text("UPDATE calls SET flagged = 0 WHERE flagged IS NULL"))
        conn.execute(text("UPDATE users SET must_change_password = 0 WHERE must_change_password IS NULL"))


def assert_integrity() -> None:
    if not IS_SQLITE:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA quick_check")).fetchall()
    messages = [str(row[0]) for row in rows]
    if messages != ["ok"]:
        raise RuntimeError("Файл базы не прошёл проверку целостности")


def protect_store() -> None:
    from app.crypto import restrict_file

    paths = [settings.key_path, settings.jwt_secret_path]
    if IS_SQLITE:
        paths += [settings.db_path, Path(str(settings.db_path) + "-wal"), Path(str(settings.db_path) + "-shm")]
    for path in paths:
        if path.is_file():
            restrict_file(path)


def db_guard_status() -> dict:
    if not IS_SQLITE:
        with engine.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar()
            ssl = conn.execute(text("SHOW ssl")).scalar()
        return {
            "backend": "postgresql",
            "quick_check": "ok",
            "server_version": str(version),
            "ssl": str(ssl),
            "foreign_keys": True,
            "trusted_schema_off": True,
            "cell_size_check": True,
            "attach_blocked": True,
        }
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
        "backend": "sqlite",
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


# Поля с ПДн, которые шифруются в базе (см. app.models.EncryptedText).
ENCRYPTED_COLUMNS = (
    ("transcripts", ("full_text", "segments_json")),
    ("quality", ("checks_json",)),
)


def encrypt_plain_texts(batch: int = 200) -> int:
    """Шифрует открытые строки, оставшиеся от версий без шифрования расшифровок. Возвращает число строк.

    Безопасно запускать при каждом старте: уже зашифрованные строки (с префиксом) не трогаются.
    """
    if not settings.encrypt_transcripts:
        return 0
    from app.crypto import TEXT_PREFIX, encrypt_text

    done = 0
    like = TEXT_PREFIX + "%"
    for table, columns in ENCRYPTED_COLUMNS:
        cond = " OR ".join(f'("{c}" IS NOT NULL AND "{c}" NOT LIKE :p)' for c in columns)
        cols = ", ".join(f'"{c}"' for c in columns)
        while True:
            with write_lock, engine.begin() as conn:
                rows = conn.execute(
                    text(f'SELECT id, {cols} FROM "{table}" WHERE {cond} ORDER BY id LIMIT :n'), {"p": like, "n": batch}
                ).fetchall()
                for row in rows:
                    values = {}
                    for i, col in enumerate(columns, start=1):
                        val = row[i]
                        values[col] = val if val is None or str(val).startswith(TEXT_PREFIX) else encrypt_text(str(val))
                    sets = ", ".join(f'"{c}" = :{c}' for c in columns)
                    conn.execute(text(f'UPDATE "{table}" SET {sets} WHERE id = :id'), {**values, "id": row[0]})
            done += len(rows)
            if len(rows) < batch:
                break
    if done:
        log.info("Зашифровано старых записей с текстом звонков: %s", done)
        if IS_SQLITE:
            # secure_delete=ON затирает старые страницы, а checkpoint убирает открытые копии из WAL.
            with engine.connect() as conn:
                conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    return done
