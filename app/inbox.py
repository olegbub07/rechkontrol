"""Папка входящих (INBOX_DIR): АТС или скрипт кладёт записи, сервис сам ставит их в очередь.

Порядок для каждого файла:
1. Файл берётся, когда его размер и время изменения не менялись INBOX_SETTLE_SEC секунд
   (недописанный файл не трогаем). Имена .part, .tmp, .filepart и скрытые пропускаются.
2. Атомарный rename в inbox/processing: второй экземпляр сервиса тот же файл уже не увидит.
3. Шифрование в хранилище, SHA-256. Если такой файл уже был, новый звонок не создаётся.
4. Исходник уходит в inbox/done (или удаляется), при ошибке в inbox/failed рядом с .error.txt.
После сбоя файлы, оставшиеся в processing, разбираются при следующем запуске.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from app.audit import write_audit
from app.config import settings
from app.db import SessionLocal, write_lock
from app.ingest import IngestError, file_chunks, find_duplicate, queue_call, resolve_operator, store_chunks

log = logging.getLogger("rechkontrol.inbox")

SKIP_SUFFIXES = (".part", ".tmp", ".filepart", ".crdownload", ".partial", ".lock")

_stop = threading.Event()
_thread: threading.Thread | None = None
_seen: dict[str, tuple[int, int, float]] = {}


def inbox_root() -> Path | None:
    raw = (settings.inbox_dir or "").strip()
    return Path(raw).expanduser() if raw else None


def _dirs(root: Path) -> tuple[Path, Path, Path]:
    out = (root / "processing", root / "done", root / "failed")
    for d in out:
        d.mkdir(parents=True, exist_ok=True)
    return out


def _candidate(path: Path) -> bool:
    name = path.name
    if name.startswith(".") or name.startswith("~"):
        return False
    low = name.lower()
    if low.endswith(SKIP_SUFFIXES):
        return False
    return path.suffix.lower() in settings.allowed_audio_ext


def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    n = 1
    while target.exists():
        target = folder / f"{Path(name).stem}.{n}{Path(name).suffix}"
        n += 1
    return target


def _settled(path: Path, now: float) -> bool:
    """Готов, если размер и mtime не менялись с прошлого прохода и прошло INBOX_SETTLE_SEC."""
    try:
        st = path.stat()
    except FileNotFoundError:
        _seen.pop(str(path), None)
        return False
    key = str(path)
    sig = (st.st_size, st.st_mtime_ns)
    prev = _seen.get(key)
    if prev is None or prev[:2] != sig:
        _seen[key] = (sig[0], sig[1], now)
        return False
    return st.st_size > 0 and now - prev[2] >= settings.inbox_settle_sec and now - st.st_mtime >= settings.inbox_settle_sec


def _import(path: Path, original_name: str) -> tuple[str, int | None, str]:
    """Возвращает (итог, id звонка, текст). Итог: queued | duplicate | failed."""
    try:
        stored = store_chunks(file_chunks(path), original_name)
    except IngestError as exc:
        return "failed", None, str(exc)
    try:
        with write_lock, SessionLocal() as session:
            dup = find_duplicate(session, stored.sha256)
            if dup is not None:
                stored.path.unlink(missing_ok=True)
                return "duplicate", dup.id, f"уже загружен как звонок №{dup.id}"
            op = resolve_operator(session, original_name)
            call = queue_call(
                session, stored, original_name, "inbox", operator=op, operator_first=settings.inbox_operator_first
            )
            write_audit(session, "call.inbox", actor="inbox", detail=f"звонок №{call.id}: {call.orig_filename}")
            session.commit()
            return "queued", call.id, ""
    except BaseException:
        stored.path.unlink(missing_ok=True)
        raise


def _finish(src: Path, outcome: str, message: str, done: Path, failed: Path, original_name: str) -> None:
    if outcome == "failed":
        target = _free_name(failed, original_name)
        os.replace(src, target)
        target.with_name(target.name + ".error.txt").write_text(message + "\n", encoding="utf-8")
        return
    if settings.inbox_keep_originals:
        os.replace(src, _free_name(done, original_name))
    else:
        src.unlink(missing_ok=True)


def _original_name(claimed: Path) -> str:
    # processing/<8 hex>__<имя>: префикс нужен, чтобы одинаковые имена не столкнулись
    name = claimed.name
    return name.split("__", 1)[1] if "__" in name else name


def process_claimed(claimed: Path, done: Path, failed: Path) -> tuple[str, int | None]:
    original = _original_name(claimed)
    try:
        outcome, call_id, message = _import(claimed, original)
    except Exception:
        log.exception("inbox: не удалось поставить %s", original)
        outcome, call_id, message = "failed", None, "Внутренняя ошибка при постановке в очередь, подробности в журнале сервера."
    _finish(claimed, outcome, message, done, failed, original)
    if outcome == "failed":
        log.warning("inbox: %s отклонён: %s", original, message)
    else:
        log.info("inbox: %s -> %s (звонок №%s)", original, outcome, call_id)
    return outcome, call_id


def scan_once(now: float | None = None) -> list[tuple[str, str, int | None]]:
    """Один проход по папке. Возвращает [(имя, итог, id звонка)]."""
    root = inbox_root()
    if root is None:
        return []
    root.mkdir(parents=True, exist_ok=True)
    processing, done, failed = _dirs(root)
    now = time.time() if now is None else now
    results: list[tuple[str, str, int | None]] = []
    for path in sorted(p for p in root.iterdir() if p.is_file() and _candidate(p)):
        if not _settled(path, now):
            continue
        claimed = processing / f"{os.urandom(4).hex()}__{path.name}"
        try:
            os.replace(path, claimed)  # атомарно: кто первый переименовал, тот и обрабатывает
        except FileNotFoundError:
            continue
        _seen.pop(str(path), None)
        outcome, call_id = process_claimed(claimed, done, failed)
        results.append((path.name, outcome, call_id))
    return results


def recover_processing() -> int:
    """Файлы, застрявшие в processing после сбоя. Повтор безопасен: дубликат по хешу не создаст звонок."""
    root = inbox_root()
    if root is None or not root.exists():
        return 0
    processing, done, failed = _dirs(root)
    n = 0
    for claimed in sorted(p for p in processing.iterdir() if p.is_file()):
        process_claimed(claimed, done, failed)
        n += 1
    return n


def _loop() -> None:
    try:
        recover_processing()
    except Exception:
        log.exception("inbox: разбор processing не удался")
    while not _stop.is_set():
        try:
            scan_once()
        except Exception:
            log.exception("inbox: проход по папке не удался")
        _stop.wait(max(0.5, float(settings.inbox_poll_sec)))


def start_inbox() -> bool:
    global _thread
    root = inbox_root()
    if root is None:
        return False
    if _thread is not None and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="inbox-watch", daemon=True)
    _thread.start()
    log.info("Папка входящих: %s (проверка каждые %.0f с)", root, settings.inbox_poll_sec)
    return True


def stop_inbox() -> None:
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=3)
