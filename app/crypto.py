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


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    path = settings.key_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
    restrict_file(path)
    _fernet = Fernet(path.read_bytes().strip())
    return _fernet


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


def decrypt_bytes(data: bytes) -> bytes:
    return get_fernet().decrypt(data)


def encrypt_file(src: Path, dst: Path) -> None:
    dst.write_bytes(encrypt_bytes(src.read_bytes()))


def decrypt_file(src: Path, dst: Path) -> None:
    dst.write_bytes(decrypt_bytes(src.read_bytes()))
