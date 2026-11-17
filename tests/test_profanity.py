from app.quality import _profanity


def test_mat_caught():
    for w in ["пошел ты нахуй", "блять", "заебали", "сука", "пиздец", "долбоеб"]:
        assert _profanity(w), w


def test_normal_words_not_caught():
    for w in ["хлеб", "употреблять", "мандарин", "колебания", "хуже", "страхуем", "оскорблять", "ребенок"]:
        assert _profanity(w) is None, w
