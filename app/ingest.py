"""Общая постановка звонка в очередь для папки входящих и внешнего API.

Файл читается блоками, сразу шифруется в хранилище и попутно хешируется (SHA-256).
По хешу один и тот же файл повторно в очередь не встаёт.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.audio import AudioError, sniff_audio
from app.config import settings
from app.crypto import EncryptedWriter
from app.models import Call, Operator
from app.security import sanitize_filename
from app.timeutil import date_from_filename, local_now, operator_code_from_filename

CHUNK = 256 * 1024


class IngestError(ValueError):
    """Файл не принят: не аудио, пустой или слишком большой."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Stored:
    stored_name: str
    path: Path
    mime: str
    sha256: str
    size: int


def store_chunks(chunks: Iterable[bytes], filename: str) -> Stored:
    """Шифрует поток в data/audio, проверяя сигнатуру по первым байтам и лимит размера."""
    name = sanitize_filename(filename)
    max_bytes = max(1, int(settings.max_upload_mb)) * 1024 * 1024
    stored = f"{uuid.uuid4().hex}.bin"
    path = settings.audio_dir / stored
    digest = hashlib.sha256()
    writer: EncryptedWriter | None = None
    head = b""
    mime = ""
    try:
        for block in chunks:
            if not block:
                continue
            digest.update(block)
            if writer is None:
                head += block
                if len(head) < 64:
                    continue
                try:
                    mime = sniff_audio(head[:16], name)
                except AudioError as exc:
                    raise IngestError(f"{name}: {exc}") from exc
                writer = EncryptedWriter(path)
                block, head = head, b""
            writer.write(block)
            if writer.size > max_bytes:
                raise IngestError(f"{name}: файл больше {settings.max_upload_mb} МБ", 413)
        if writer is None:
            try:
                sniff_audio(head[:16], name)
            except AudioError as exc:
                raise IngestError(f"{name}: {exc}") from exc
            raise IngestError(f"{name}: файл слишком короткий, звука в нём нет")
        writer.close()
        return Stored(stored, path, mime or "application/octet-stream", digest.hexdigest(), writer.size)
    except BaseException:
        if writer is not None:
            writer.abort()
        raise


def file_chunks(path: Path) -> Iterable[bytes]:
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                return
            yield block


def find_duplicate(session: Session, sha256: str) -> Call | None:
    return session.query(Call).filter(Call.source_ref == sha256).order_by(Call.id).first()


def resolve_operator(session: Session, filename: str, external_id: str | None = None, name: str | None = None) -> Operator | None:
    """Оператор по табельному коду (поле или OP-104 в имени файла) или по имени."""
    code = (external_id or "").strip() or operator_code_from_filename(filename)
    if code:
        op = session.query(Operator).filter(Operator.external_id == code).first()
        if op is not None:
            return op
    clean = (name or "").strip()[:120]
    if clean:
        op = session.query(Operator).filter(Operator.name == clean).one_or_none()
        if op is None:
            op = Operator(name=clean)
            session.add(op)
            session.flush()
        return op
    return None


def queue_call(
    session: Session,
    stored: Stored,
    filename: str,
    source: str,
    operator: Operator | None = None,
    call_at: datetime | None = None,
    operator_first: bool = True,
) -> Call:
    name = sanitize_filename(filename)
    call = Call(
        orig_filename=name,
        stored_name=stored.stored_name,
        mime=stored.mime,
        operator_id=operator.id if operator else None,
        call_at=call_at or date_from_filename(name) or local_now(),
        status="queued",
        progress=0,
        is_demo=0,
        operator_first=1 if operator_first else 0,
        source=source,
        source_ref=stored.sha256,
    )
    session.add(call)
    session.flush()
    return call
