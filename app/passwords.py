from __future__ import annotations

import re

USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
ROLES = ("admin", "analyst", "operator")
WEAK = {
    "password",
    "password1",
    "password123",
    "1234567890",
    "qwerty123",
    "admin12345",
    "centerinvest",
    "centerinvest2026",
    "centerinvest2026!",
    "centradmin2026!",
    "centroperator2026!",
}


class PasswordError(ValueError):
    pass


def validate_username(username: str) -> str:
    value = username.strip()
    if not USERNAME_RE.fullmatch(value):
        raise PasswordError("Логин: 3–32 символа, латиница, цифры, точка, _ и -")
    return value


def validate_role(role: str) -> str:
    value = (role or "").strip().lower()
    if value not in ROLES:
        raise PasswordError("Роль: admin, analyst или operator")
    return value


def validate_password(password: str, username: str = "") -> str:
    if len(password) < 10 or len(password) > 128:
        raise PasswordError("Пароль должен быть от 10 до 128 символов")
    if password.isspace() or " " in password[:1]:
        raise PasswordError("Пароль не должен начинаться с пробела")
    if password.lower() == username.lower() and username:
        raise PasswordError("Пароль не должен совпадать с логином")
    if password.lower() in WEAK:
        raise PasswordError("Пароль слишком простой")
    if not re.search(r"[A-Za-zА-Яа-яЁё]", password):
        raise PasswordError("В пароле нужна хотя бы одна буква")
    if not re.search(r"\d", password):
        raise PasswordError("В пароле нужна хотя бы одна цифра")
    if not re.search(r"[^A-Za-zА-Яа-яЁё0-9]", password):
        raise PasswordError("В пароле нужен знак, который не является буквой или цифрой")
    return password
