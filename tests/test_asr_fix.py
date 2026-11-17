from app.asr_fix import correct_asr_text


def test_bank_name_and_legal_terms():
    text = "Добрый день, банк Центренвест. Паспорт иный лист запись и грипп. Щет откроем."
    fixed = correct_asr_text(text)
    assert "Центр-инвест" in fixed
    assert "ИНН и лист записи" in fixed
    assert "ЕГРИП" in fixed
    assert "счёт откроем" in fixed


def test_politeness_and_tax():
    text = "вы уже являете с клиентом банка. На логобложение упрошенка."
    fixed = correct_asr_text(text)
    assert "являетесь клиентом" in fixed
    assert "налогообложен" in fixed
    assert "упрощёнк" in fixed


def test_extract_from_registry():
    assert correct_asr_text("нужна выписка из и грипп") == "нужна выписка из ЕГРИП"
    assert correct_asr_text("справка из егрип") == "справка из ЕГРИП"


def test_ordinary_words_are_not_rewritten():
    samples = [
        "У меня грипп, перезвоню завтра.",
        "Ребёнок заболел, и грипп у всех дома.",
        "Сломалась дверца щита в подъезде.",
        "Вы являете собой пример терпения.",
        "Он ждёт откроем ли мы офис.",
    ]
    for text in samples:
        assert correct_asr_text(text) == text


def test_spacing_is_normalized():
    assert correct_asr_text("  банк   центр инвест  ") == "банк Центр-инвест"
