from __future__ import annotations

import secrets
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import settings

_fernet: Fernet | None = None
_jwt_secret: str | None = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    path = settings.key_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
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
