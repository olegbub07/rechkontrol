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


# --- случаи из отчёта тестировщика (BUGS.md 1-6, 18-20) ---

CARD_BLOCK = [
    {"role": "operator", "text": "Добрый вечер, банк Центр-инвест, меня зовут Елена. Чем могу помочь?"},
    {"role": "client", "text": "Карту украли, уже списали 12 тысяч, заблокируйте срочно пожалуйста!"},
    {"role": "operator", "text": "Понимаю, как неприятно. Сейчас заблокирую. Назовите, пожалуйста, последние четыре цифры карты и дату рождения."},
    {"role": "client", "text": "4419, 12 марта 1984."},
    {"role": "operator", "text": "Карта заблокирована, повторные списания невозможны. Номер обращения 7731."},
]


def test_card_digits_and_birth_date_in_reply():
    out = [s["text"] for s in mask_segments(CARD_BLOCK)]
    assert out[3] == "[КАРТА], [ДАТА]."
    assert "12 тысяч" in out[1] and "7731" in out[4]
    # без контекста число рядом с датой тоже закрыто
    assert mask_text("4419, 12 марта 1984.") == "[НОМЕР], [ДАТА]."
    full = mask_text(" ".join(s["text"] for s in CARD_BLOCK))
    for leak in ("4419", "1984", "марта", "Елена"):
        assert leak not in full


def test_birth_date_split_by_asr():
    segs = CARD_BLOCK[:3] + [
        {"role": "client", "text": "4419, двенадцатое"},
        {"role": "client", "text": "Марта."},
    ]
    out = [s["text"] for s in mask_segments(segs)]
    assert out[3].startswith("[КАРТА]") and out[4] == "[ДАТА]."


def test_dates():
    assert mask_text("родился 05.06.1979") == "родился [ДАТА]"
    assert mask_text("год рождения 1984") == "год рождения [ДАТА]"
    assert mask_text("до 1 марта погасите") == "до 1 марта погасите"
    assert mask_text("если к 19:00 не придёт") == "если к 19:00 не придёт"


def test_inn_prefix():
    assert mask_text("72 тысячи, ИНН 6164… сейчас, 616412345678.") == "72 тысячи, ИНН [ИНН]… сейчас, [ИНН]."
    segs = [
        {"role": "operator", "text": "Назовите, пожалуйста, сумму и ИНН получателя."},
        {"role": "client", "text": "72 тысячи, 6164 12 34."},
        {"role": "operator", "text": "Номер обращения 3340, если к 19:00 не придёт - перезвоним."},
    ]
    out = [s["text"] for s in mask_segments(segs)]
    assert "6164" not in out[1] and "72 тысячи" in out[1]
    assert out[2] == segs[2]["text"]


def test_names_from_dictionary():
    assert mask_text("Добрый день, банк Центр-инвест, Дмитрий, чем могу помочь?") == "Добрый день, банк Центр-инвест, [ФИО], чем могу помочь?"
    assert mask_text("Елена провела консультацию по теме «карта».").startswith("[ФИО] провела")
    assert mask_text("Передайте Дмитрию Петрову") == "Передайте [ФИО]"
    assert mask_text("Звонила Ольга, спросите Наталью") == "Звонила [ФИО], спросите [ФИО]"
    assert mask_text("Слава богу, всё работает") == "Слава богу, всё работает"
    assert mask_text("Банк Центр-инвест, Ростов") == "Банк Центр-инвест, Ростов"


def test_name_after_question():
    segs = [{"role": "operator", "text": "Назовите ФИО полностью"}, {"role": "client", "text": "Сидоренко Анна Павловна"}]
    assert mask_segments(segs)[1]["text"] == "[ФИО]"


def test_summary_has_no_operator_name():
    from app.quality import analyze

    rep = analyze([dict(s, start=i, end=i + 1, speaker="A" if s["role"] == "operator" else "B") for i, s in enumerate(CARD_BLOCK)])
    assert "Елена" not in rep.summary and rep.summary.startswith("Оператор")


def test_intro_by_name_without_cue():
    from app.quality import analyze

    turns = [
        {"start": 0, "end": 2, "role": "operator", "speaker": "A", "text": "Добрый день, банк Центр-инвест, Дмитрий, чем могу помочь?"},
        {"start": 2, "end": 4, "role": "client", "speaker": "B", "text": "Не могу войти в приложение."},
    ]
    intro = next(c for c in analyze(turns).checks if c.id == "intro")
    assert intro.passed


def _store_call_with_pii():
    import json as _json

    from app.db import SessionLocal
    from app.models import Call, Operator, Quality, Transcript
    from app.quality import analyze, report_to_json
    from app.timeutil import utcnow

    turns = [dict(s, start=i * 3, end=i * 3 + 2, speaker="A" if s["role"] == "operator" else "B") for i, s in enumerate(CARD_BLOCK)]
    rep = analyze(turns)
    with SessionLocal() as s:
        op = s.query(Operator).filter(Operator.external_id == "OP-104").one()
        call = Call(orig_filename="pii_card.wav", status="done", operator_id=op.id, is_demo=0, call_at=utcnow())
        s.add(call)
        s.flush()
        s.add(Transcript(call_id=call.id, full_text=" ".join(t["text"] for t in turns), segments_json=_json.dumps(turns, ensure_ascii=False)))
        fields = {k: getattr(rep, k) for k in ("politeness", "completeness", "script_compliance", "resolution", "overall",
                                               "tonality", "tonality_score", "avg_response_sec", "talk_ratio_operator", "violation_pct")}
        s.add(Quality(call_id=call.id, summary="Елена провела консультацию по теме «банковская карта».", **fields, **report_to_json(rep)))
        s.commit()
        return call.id, op.id


def test_no_pii_leaks_via_search_card_export_pdf(tmp_path):
    import json
    import shutil
    import subprocess

    from fastapi.testclient import TestClient

    from app.main import app
    from tests.helpers import auth, ensure_user

    call_id, op_id = _store_call_with_pii()
    ensure_user("qa.leak.admin", "admin")
    ensure_user("qa.leak.analyst", "analyst")
    ensure_user("qa.leak.op", "operator", "OP-104")
    leaks = ("4419", "1984", "Елена")
    with TestClient(app) as client:
        for who in ("qa.leak.admin", "qa.leak.analyst", "qa.leak.op"):
            h = auth(client, who)
            data = client.get(f"/api/calls/{call_id}", headers=h).json()
            # имя оператора в справочнике (operator_name) показывается намеренно, проверяем тексты звонка
            card = json.dumps([data["full_text"], data["segments"], data["quality"]], ensure_ascii=False)
            assert not any(x in card for x in leaks), who
            for q in ("4419", "1984", "марта", "Елена"):
                ids = [c["id"] for c in client.get("/api/calls", headers=h, params={"q": q, "page_size": 100}).json()["items"]]
                assert call_id not in ids, (who, q)
            ids = [c["id"] for c in client.get("/api/calls", headers=h, params={"q": "заблокирована", "page_size": 100}).json()["items"]]
            assert call_id in ids, who
        admin = auth(client, "qa.leak.admin")
        full = [c for c in client.get("/api/export/json?full=true", headers=admin).json() if c["id"] == call_id]
        assert full and not any(x in json.dumps([full[0]["full_text"], full[0]["segments"], full[0]["quality"]], ensure_ascii=False) for x in leaks)
        raw = client.get(f"/api/calls/{call_id}?unmasked=true", headers=admin).text
        assert "4419" in raw
        pdf = client.get(f"/api/reports/operator/{op_id}.pdf", headers=auth(client, "qa.leak.analyst"))
        assert pdf.status_code == 200
    if shutil.which("pdftotext"):
        path = tmp_path / "r.pdf"
        path.write_bytes(pdf.content)
        text = subprocess.run(["pdftotext", str(path), "-"], capture_output=True).stdout.decode()
        assert not any(x in text for x in ("4419", "1984", "Елена провела"))
