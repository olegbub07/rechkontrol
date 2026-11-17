import pytest

from app.passwords import PasswordError, validate_password, validate_role, validate_username


def test_username_rules():
    assert validate_username("analyst_1") == "analyst_1"
    with pytest.raises(PasswordError):
        validate_username("ab")
    with pytest.raises(PasswordError):
        validate_username("bad name")


def test_password_policy():
    assert validate_password("GoodPass12!", "ivan") == "GoodPass12!"
    with pytest.raises(PasswordError):
        validate_password("GoodPass12", "ivan")
    with pytest.raises(PasswordError):
        validate_password("short1", "ivan")
    with pytest.raises(PasswordError):
        validate_password("nodigitsHere", "ivan")
    with pytest.raises(PasswordError):
        validate_password("ivanivan12", "ivanivan12")
    with pytest.raises(PasswordError):
        validate_password("password123", "ivan")
    with pytest.raises(PasswordError):
        validate_password("CenterInvest2026!", "ivan")


def test_roles():
    assert validate_role("Admin") == "admin"
    with pytest.raises(PasswordError):
        validate_role("root")
