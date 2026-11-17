from app.asr_clean import align_words, clean_text, clean_turns, is_hallucination


def test_repeated_word_loop_is_cut():
    assert clean_text("Это ужасно. СПОКОЙ СПОКОЙ СПОКОЙ СПОКОЙ СПОКОЙ СПППППППППП") == "Это ужасно. СПОКОЙ"
    assert clean_text("мне нужно мне нужно мне нужно мне нужно закрыть карту") == "мне нужно закрыть карту"


def test_normal_speech_untouched():
    for text in ("Да да да, всё верно.", "Ну поймите, поймите меня.", "Добрый день. Добрый день.", "Алло, алло, вы меня слышите?"):
        assert clean_text(text) == text


def test_subtitle_outros_removed():
    assert clean_text("Спасибо, до свидания. Субтитры сделал DimaTorzok") == "Спасибо, до свидания."
    assert clean_text("Продолжение следует...") == ""
    assert clean_text("Редактор субтитров А.Синецкая Корректор А.Егорова") == ""
    assert clean_text("Спасибо за просмотр!") == ""


def test_words_follow_cleaned_text():
    words = [{"text": w, "start": i, "end": i + 1} for i, w in enumerate(["Да,", "СПОКОЙ", "СПОКОЙ", "СПОКОЙ", "СПОКОЙ", "СППППП"])]
    text = clean_text(" ".join(w["text"] for w in words))
    assert [w["text"] for w in align_words(words, text)] == ["Да,", "СПОКОЙ"]


def test_low_confidence_segment_dropped():
    assert is_hallucination(0.8, -1.4, 1.2)
    assert is_hallucination(0.1, -0.3, 3.5)
    assert not is_hallucination(0.1, -0.3, 1.6)
    assert not is_hallucination(None, None, None)


def test_stored_turns_cleaned_on_rescore():
    turns = [
        {"role": "operator", "text": "Всего доброго.", "start": 0, "end": 1},
        {"role": "client", "text": "Субтитры сделал DimaTorzok", "start": 1, "end": 2},
    ]
    out, changed = clean_turns(turns)
    assert changed and [t["text"] for t in out] == ["Всего доброго."]
    assert clean_turns(out) == (out, False)
