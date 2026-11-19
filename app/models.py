from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.timeutil import utcnow
from app.db import Base


class EncryptedText(TypeDecorator):
    """Текст с ПДн в базе хранится зашифрованным (Fernet, ключ как у аудио).

    Старые открытые строки читаются как есть и шифруются при старте (app.db.encrypt_plain_texts).
    В SQL по таким полям не ищем: поиск по тексту идёт в Python после расшифровки.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        from app.config import settings
        from app.crypto import TEXT_PREFIX, encrypt_text

        if value is None or not settings.encrypt_transcripts or str(value).startswith(TEXT_PREFIX):
            return value
        return encrypt_text(str(value))

    def process_result_value(self, value, dialect):
        from app.crypto import decrypt_text

        return decrypt_text(value) if value is not None else None


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(24), default="analyst")
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"), nullable=True)
    must_change_password: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    calls: Mapped[list[Call]] = relationship(back_populates="operator")


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    orig_filename: Mapped[str] = mapped_column(String(260))
    stored_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mime: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"), nullable=True, index=True)
    call_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_demo: Mapped[int] = mapped_column(Integer, default=0)
    processed_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    operator_first: Mapped[int] = mapped_column(Integer, default=1)
    flagged: Mapped[int] = mapped_column(Integer, default=0)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Откуда пришёл звонок: upload | inbox | api, и SHA-256 исходного файла (повторно не ставится в очередь).
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    operator: Mapped[Operator | None] = relationship(back_populates="calls")
    transcript: Mapped[Transcript | None] = relationship(back_populates="call", uselist=False, cascade="all, delete-orphan")
    quality: Mapped[Quality | None] = relationship(back_populates="call", uselist=False, cascade="all, delete-orphan")


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), unique=True)
    full_text: Mapped[str] = mapped_column(EncryptedText, default="")
    segments_json: Mapped[str] = mapped_column(EncryptedText, default="[]")
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)

    call: Mapped[Call] = relationship(back_populates="transcript")


class Quality(Base):
    __tablename__ = "quality"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), unique=True)
    politeness: Mapped[float] = mapped_column(Float, default=0)
    completeness: Mapped[float] = mapped_column(Float, default=0)
    script_compliance: Mapped[float] = mapped_column(Float, default=0)
    resolution: Mapped[float] = mapped_column(Float, default=0)
    overall: Mapped[float] = mapped_column(Float, default=0)
    tonality: Mapped[str] = mapped_column(String(16), default="neutral")
    tonality_score: Mapped[float] = mapped_column(Float, default=0)
    avg_response_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    talk_ratio_operator: Mapped[float] = mapped_column(Float, default=0)
    violation_pct: Mapped[float] = mapped_column(Float, default=0)
    violations_json: Mapped[str] = mapped_column(Text, default="[]")
    recommendations_json: Mapped[str] = mapped_column(Text, default="[]")
    # Цитаты-доказательства из речи могут содержать имена и номера, поэтому тоже шифруются.
    checks_json: Mapped[str] = mapped_column(EncryptedText, default="[]")
    summary: Mapped[str] = mapped_column(Text, default="")

    call: Mapped[Call] = relationship(back_populates="quality")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class SecurityMark(Base):
    """Неудачные входы и отозванные сессии. Переживают перезапуск, пока не истечёт срок."""

    __tablename__ = "security_marks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(Integer, index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")


class ApiKey(Base):
    """Ключ внешней системы. Хранится только SHA-256 ключа, сам ключ показывается один раз."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[str] = mapped_column(String(120), default="calls:read")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebhookDelivery(Base):
    """Журнал доставки вебхуков: одна строка на событие и адрес, с повторами."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event: Mapped[str] = mapped_column(String(40))
    call_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    url: Mapped[str] = mapped_column(String(500))
    # В теле оценки и имя оператора, поэтому шифруется как расшифровки.
    payload: Mapped[str] = mapped_column(EncryptedText, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_try_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
