from app.pii import luhn_ok, mask_segments, mask_text


def test_luhn():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")


def test_phone_email_card():
    out = mask_text("Мой телефон +7 (918) 123-45-67, почта ivan.petrov@mail.ru, карта 4111 1111 1111 1111")
    assert "[ТЕЛЕФОН]" in out and "[EMAIL]" in out and "[КАРТА]" in out
    assert "918" not in out and "ivan" not in out and "4111" not in out
    assert mask_text("перезвоните на 8 918 555 12 34") == "перезвоните на [ТЕЛЕФОН]"


def test_documents():
    assert mask_text("ИНН 616512345678") == "ИНН [ИНН]"
    assert mask_text("ИНН организации 6165123456") == "ИНН организации [ИНН]"
    assert mask_text("СНИЛС 112-233-445 95") == "СНИЛС [СНИЛС]"
    assert mask_text("паспорт 6010 123456") == "паспорт [ПАСПОРТ]"
    assert "123456" not in mask_text("паспорт серия 60 10 номер 123456")
    assert mask_text("расчётный счёт 40702810123450000001") == "расчётный счёт [СЧЁТ]"


def test_names():
    out = mask_text("Добрый день, меня зовут Анна, банк Центр-инвест. Моя фамилия Петров")
    assert "Анна" not in out and "Петров" not in out
    assert "Центр-инвест" in out
    assert mask_text("Иванов Иван Петрович оформил заявку") == "[ФИО] оформил заявку"


def test_ordinary_numbers_survive():
    text = "сумма 15000 рублей, код 1234, 2026 год, 10 минут, тариф за 490"
    assert mask_text(text) == text


def test_segments_masked_and_words_dropped():
    segs = [{"text": "мой номер +79181234567", "role": "client", "words": [{"word": "+79181234567"}]}]
    out = mask_segments(segs)
    assert out[0]["text"] == "мой номер [ТЕЛЕФОН]"
    assert "words" not in out[0]
    assert segs[0]["text"].endswith("4567")


def test_api_masks_by_default_and_unmask_is_admin_only():
    import json

    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.db import SessionLocal
    from app.main import app
    from app.models import Call, Operator, Transcript, User

    secret = "Меня зовут Пётр, мой телефон +7 918 123 45 67"
    with SessionLocal() as session:
        person = session.query(Operator).filter(Operator.external_id == "OP-104").one()
        call = Call(orig_filename="pii.wav", status="done", operator_id=person.id, is_demo=1)
        session.add(call)
        session.flush()
        session.add(Transcript(call_id=call.id, full_text=secret, segments_json=json.dumps([{"text": secret, "role": "client"}])))
        for name, role in (("qa.pii.admin", "admin"), ("qa.pii.analyst", "analyst")):
            old = session.query(User).filter(User.username == name).one_or_none()
            if old:
                session.delete(old)
                session.flush()
            session.add(User(username=name, password_hash=hash_password("PiiCheck-2026x"), role=role, is_active=1, token_version=1))
        session.commit()
        call_id = call.id
    with TestClient(app) as client:
        def headers(name):
            token = client.post("/api/auth/login", json={"username": name, "password": "PiiCheck-2026x"}).json()["token"]
            return {"Authorization": f"Bearer {token}"}

        admin, analyst = headers("qa.pii.admin"), headers("qa.pii.analyst")
        masked = client.get(f"/api/calls/{call_id}", headers=analyst).json()
        assert masked["masked"] is True
        assert "918" not in masked["full_text"] and "Пётр" not in masked["full_text"]
        assert "918" not in masked["segments"][0]["text"]
        assert client.get(f"/api/calls/{call_id}?unmasked=true", headers=analyst).status_code == 403
        raw = client.get(f"/api/calls/{call_id}?unmasked=true", headers=admin).json()
        assert raw["masked"] is False and raw["full_text"] == secret
        full = client.get("/api/export/json?full=true", headers=admin)
        assert full.status_code == 200
        assert "+7 918 123 45 67" not in full.text
        assert "+7 918 123 45 67" in client.get("/api/export/json?full=true&unmasked=true", headers=admin).text
