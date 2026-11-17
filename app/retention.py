"""Срок хранения звонков (152-ФЗ, ст. 5 ч. 7): по истечении RETENTION_DAYS звонок удаляется целиком.

Удаляются аудиофайл, расшифровка и оценка. В журнале аудита остаётся запись «retention.purge»
с числом удалённых звонков и их номерами, без имён файлов и текста.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from app.config import settings
from app.timeutil import utcnow

log = logging.getLogger("rechkontrol.retention")

_stop = threading.Event()
_thread: threading.Thread | None = None


def purge_expired(days: int | None = None, now=None) -> int:
    """Удаляет звонки, загруженные раньше чем days дней назад. 0 или меньше - ничего не делает."""
    from app.audit import write_audit
    from app.db import SessionLocal, write_lock
    from app.models import Call
    from app.security import safe_audio_path

    days = settings.retention_days if days is None else days
    if days <= 0:
        return 0
    border = (now or utcnow()) - timedelta(days=days)
    removed: list[int] = []
    files = []
    with write_lock, SessionLocal() as session:
        rows = (
            session.query(Call)
            .filter(Call.uploaded_at < border, Call.status != "processing")
            .order_by(Call.id)
            .limit(500)
            .all()
        )
        for call in rows:
            if call.stored_name:
                try:
                    files.append(safe_audio_path(call.stored_name))
                except ValueError:
                    pass
            removed.append(call.id)
            session.delete(call)
        if removed:
            ids = ", ".join(str(i) for i in removed[:60]) + (" …" if len(removed) > 60 else "")
            write_audit(session, "retention.purge", detail=f"срок хранения {days} дн., удалено звонков: {len(removed)} (№ {ids})")
        session.commit()
    for path in files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.warning("Не удалось удалить файл просроченного звонка: %s", path.name)
    if removed:
        log.info("Срок хранения: удалено звонков %s", len(removed))
    return len(removed)


def _loop() -> None:
    interval = max(0.05, float(settings.retention_check_hours)) * 3600
    while not _stop.is_set():
        try:
            while purge_expired() >= 500:
                pass
        except Exception:
            log.exception("Ошибка автоудаления по сроку хранения")
        _stop.wait(interval)


def start_retention() -> None:
    global _thread
    if settings.retention_days <= 0 or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="retention", daemon=True)
    _thread.start()


def stop_retention() -> None:
    _stop.set()
