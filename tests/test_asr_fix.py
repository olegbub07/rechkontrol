from app.asr_fix import correct_asr_text


def test_bank_name_and_legal_terms():
    text = "Добрый день, банк Центренвест. Паспорт иный лист запись и грипп. Щет откроем."
    fixed = correct_asr_text(text)
    assert "Центр-инвест" in fixed
    assert "ИНН и лист записи" in fixed
    assert "ЕГРИП" in fixed
    assert "счёт откроем" in fixed.lower() or "счёт откроем" in fixed


def test_politeness_and_tax():
    text = "вы уже являете с клиентом банка. На логобложение упрошенка."
    fixed = correct_asr_text(text)
    assert "являетесь клиентом" in fixed
    assert "налогообложен" in fixed
    assert "упрощёнк" in fixed
