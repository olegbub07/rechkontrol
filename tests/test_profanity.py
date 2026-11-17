from app.quality import _profanity


def test_mat_caught():
    for w in ["пошел ты нахуй", "блять", "заебали", "сука", "пиздец", "долбоеб"]:
        assert _profanity(w), w


def test_normal_words_not_caught():
    for w in ["хлеб", "употреблять", "мандарин", "колебания", "хуже", "страхуем", "оскорблять", "ребенок"]:
        assert _profanity(w) is None, w


def test_mat_is_highlighted():
    from app.checklist import default_checklist
    from app.highlight import segment_highlights

    segs = [{"role": "operator", "text": "Да нахуй мне это проверять", "start": 0, "end": 2}]
    checks = [{"id": "no_rudeness", "passed": False, "score": 0, "title": "Отсутствие грубости"}]
    out = segment_highlights(segs, checks, default_checklist())
    spans = out[0]["spans"]
    assert any(s["rule"] == "no_rudeness" and segs[0]["text"][s["start"]:s["end"]] == "нахуй" for s in spans)
