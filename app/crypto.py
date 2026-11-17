from __future__ import annotations

import os
import secrets
import stat
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import settings

_fernet: Fernet | None = None
_jwt_secret: str | None = None


def restrict_file(path: Path) -> None:
    """Файл читает только текущий пользователь и SYSTEM. Сбой прав не роняет программу."""
    try:
        if os.name != "nt":
            os.chmod(path, 0o600)
            return
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        user = os.environ.get("USERNAME", "")
        if not user or any(ord(ch) < 32 or ch in "\"&|<>" for ch in user):
            return
        subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{user}:(F)",
                "/grant:r",
                "SYSTEM:(F)",
            ],
            capture_output=True,
            check=False,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return


class KeyStoreError(RuntimeError):
    """Ключ не найден или конфликтует. Новый ключ в такой ситуации не создаём: старые данные стали бы нечитаемы."""


def _migrate_legacy_key(target: Path) -> None:
    """Переносит data/master.key в MASTER_KEY_FILE. Старый файл удаляется только после проверки копии."""
    legacy = settings.legacy_key_path
    if target.resolve() == legacy.resolve() or not legacy.is_file():
        return
    old = legacy.read_bytes().strip()
    if target.exists():
        if target.read_bytes().strip() != old:
            raise KeyStoreError(
                f"Найдены два разных ключа: {legacy} и {target}. Оставьте тот, которым зашифрованы данные, "
                "второй переименуйте. Новый ключ не создаётся."
            )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(old + b"\n")
        restrict_file(tmp)
        os.replace(tmp, target)
    if target.read_bytes().strip() != old:
        raise KeyStoreError(f"Не удалось проверить копию ключа {target}, старый ключ оставлен на месте")
    settings.key_moved_marker.write_text(
        "Мастер-ключ перенесён из data/ в MASTER_KEY_FILE. Без этого файла ключа данные не расшифровать.\n",
        encoding="utf-8",
    )
    legacy.unlink()


def _load_key() -> bytes:
    if settings.master_key.strip():
        if settings.legacy_key_path.is_file():
            if settings.legacy_key_path.read_bytes().strip() != settings.master_key.strip().encode():
                raise KeyStoreError("MASTER_KEY не совпадает с data/master.key. Новый ключ не применяется.")
            # Ключ теперь приходит из окружения, копия в data/ больше не нужна.
            settings.key_moved_marker.write_text(
                "Мастер-ключ перенесён из data/ в переменную MASTER_KEY. Без неё данные не расшифровать.\n",
                encoding="utf-8",
            )
            settings.legacy_key_path.unlink()
        return settings.master_key.strip().encode()
    path = settings.key_path
    if path.resolve() != settings.legacy_key_path.resolve():
        _migrate_legacy_key(path)
    if not path.exists():
        if settings.key_moved_marker.exists():
            raise KeyStoreError(
                f"Ключ шифрования не найден: {path}. Он был перенесён из data/ (см. data/master.key.moved). "
                "Укажите MASTER_KEY_FILE на перенесённый ключ."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Fernet.generate_key())
    restrict_file(path)
    return path.read_bytes().strip()


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    _fernet = Fernet(_load_key())
    return _fernet


def key_location() -> str:
    """Где лежит ключ, для страницы безопасности: env | file | data."""
    if settings.master_key.strip():
        return "env"
    if settings.key_path.resolve() != settings.legacy_key_path.resolve():
        return "file"
    return "data"


# Текст в базе: «rkenc1:» + токен Fernet. Строки без префикса - старые, открытые, читаются как есть.
TEXT_PREFIX = "rkenc1:"


def encrypt_text(value: str) -> str:
    return TEXT_PREFIX + get_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(value: str) -> str:
    if isinstance(value, str) and value.startswith(TEXT_PREFIX):
        return get_fernet().decrypt(value[len(TEXT_PREFIX):].encode("ascii")).decode("utf-8")
    return value


def jwt_secret() -> str:
    global _jwt_secret
    if _jwt_secret is not None:
        return _jwt_secret
    path = settings.jwt_secret_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    restrict_file(path)
    secret = path.read_text(encoding="utf-8").strip()
    if len(secret) < 32:
        raise RuntimeError("jwt.secret слишком короткий")
    _jwt_secret = secret
    return _jwt_secret


def encrypt_bytes(data: bytes) -> bytes:
    return get_fernet().encrypt(data)


# Потоковый формат: заголовок и последовательность блоков [4 байта длины][токен Fernet].
# Старые файлы (один токен Fernet на весь файл) читаются как раньше.
STREAM_MAGIC = b"RKC1\n"
STREAM_CHUNK = 1024 * 1024
# Fernet от блока 1 МБ занимает около 1,4 МБ. Большая длина в заголовке — порча файла.
_MAX_STREAM_TOKEN = 4 * 1024 * 1024


def _iter_stream_tokens(fh):
    while True:
        head = fh.read(4)
        if not head:
            return
        if len(head) != 4:
            raise ValueError("повреждён зашифрованный файл")
        size = int.from_bytes(head, "big")
        if size <= 0 or size > _MAX_STREAM_TOKEN:
            raise ValueError("повреждён зашифрованный файл")
        token = fh.read(size)
        if len(token) != size:
            raise ValueError("повреждён зашифрованный файл")
        yield token


def decrypt_bytes(data: bytes) -> bytes:
    if data.startswith(STREAM_MAGIC):
        import io

        buf = io.BytesIO(data)
        buf.read(len(STREAM_MAGIC))
        f = get_fernet()
        return b"".join(f.decrypt(token) for token in _iter_stream_tokens(buf))
    return get_fernet().decrypt(data)


class EncryptedWriter:
    """Пишет файл блоками по мере поступления данных: загрузка не держит весь файл в памяти."""

    def __init__(self, dst: Path) -> None:
        self.dst = dst
        self.size = 0
        self._fernet = get_fernet()
        self._buf = bytearray()
        self._fh = open(dst, "wb")
        self._fh.write(STREAM_MAGIC)

    def write(self, data: bytes) -> None:
        self.size += len(data)
        self._buf.extend(data)
        while len(self._buf) >= STREAM_CHUNK:
            self._flush_block(bytes(self._buf[:STREAM_CHUNK]))
            del self._buf[:STREAM_CHUNK]

    def _flush_block(self, block: bytes) -> None:
        token = self._fernet.encrypt(block)
        self._fh.write(len(token).to_bytes(4, "big"))
        self._fh.write(token)

    def close(self) -> None:
        if self._fh.closed:
            return
        if self._buf:
            self._flush_block(bytes(self._buf))
            self._buf.clear()
        self._fh.close()

    def abort(self) -> None:
        if not self._fh.closed:
            self._fh.close()
        self.dst.unlink(missing_ok=True)


def encrypt_file(src: Path, dst: Path) -> None:
    writer = EncryptedWriter(dst)
    try:
        with open(src, "rb") as fh:
            for block in iter(lambda: fh.read(STREAM_CHUNK), b""):
                writer.write(block)
        writer.close()
    except Exception:
        writer.abort()
        raise


def decrypt_file(src: Path, dst: Path) -> None:
    with open(src, "rb") as fh:
        head = fh.read(len(STREAM_MAGIC))
        if head != STREAM_MAGIC:
            dst.write_bytes(get_fernet().decrypt(head + fh.read()))
            return
        f = get_fernet()
        with open(dst, "wb") as out:
            for token in _iter_stream_tokens(fh):
                out.write(f.decrypt(token))
