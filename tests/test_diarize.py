from app.diarize import assign_roles, from_whisper, label_speakers, merge_whisper_segments


def test_merge_and_roles_operator_cues():
    raw = [
        {"start": 0.0, "end": 2.0, "text": "Добрый день, банк Центр-инвест, меня зовут Елена."},
        {"start": 2.2, "end": 4.0, "text": "Чем могу помочь?"},
        {"start": 5.5, "end": 8.0, "text": "Здравствуйте, хочу открыть счёт."},
    ]
    turns = from_whisper(raw, operator_first=True)
    assert turns[0]["role"] == "operator"
    assert turns[-1]["role"] == "client"
    assert "Чем могу помочь" in turns[0]["text"]


def test_label_speakers_splits_on_pause():
    merged = merge_whisper_segments(
        [
            {"start": 0, "end": 1, "text": "а"},
            {"start": 3, "end": 4, "text": "б"},
        ],
        gap=0.4,
    )
    labeled = label_speakers(merged, pause=0.7)
    assert labeled[0]["speaker"] != labeled[1]["speaker"]


def test_classify_keeps_operator_and_client_turns():
    from app.diarize import classify_turns

    turns = [
        {"start": 0, "end": 5, "text": "Добрый день, банк Центр-инвест, меня зовут Елена. Чем могу помочь?"},
        {"start": 7, "end": 12, "text": "Здравствуйте. Хочу открыть расчётный счёт для ИП."},
        {"start": 13, "end": 15, "text": "Подскажите, какие документы нужны?"},
        {"start": 17, "end": 22, "text": "Конечно, помогу. Уточните, пожалуйста."},
    ]
    out = classify_turns(turns, operator_first=True)
    assert out[0]["role"] == "operator"
    assert any(t["role"] == "client" and "хочу открыть" in t["text"].lower() for t in out)


def test_assign_roles_fallback_first_speaker():
    turns = assign_roles(
        [
            {"start": 0, "end": 1, "speaker": "A", "text": "слушаю"},
            {"start": 2, "end": 3, "speaker": "B", "text": "алло"},
        ],
        operator_first=True,
    )
    assert turns[0]["role"] == "operator"


def _stereo(segments, total=12.0, sr=16000):
    import numpy as np

    rng = np.random.default_rng(0)
    data = np.zeros((int(total * sr), 2), dtype=np.float32)
    for start, end, ch in segments:
        a, b = int(start * sr), int(end * sr)
        data[a:b, ch] = 0.3 * rng.standard_normal(b - a)
        data[a:b, 1 - ch] = 0.01 * rng.standard_normal(b - a)
    return data


def test_stereo_channels_assign_roles_even_without_cues():
    stereo = _stereo([(0, 2, 1), (2.5, 4, 0), (4.5, 6, 1), (6.5, 9, 0)])
    raw = [
        {"start": 0.0, "end": 2.0, "text": "слушаю вас"},
        {"start": 2.5, "end": 4.0, "text": "у меня вопрос по карте"},
        {"start": 4.5, "end": 6.0, "text": "да говорите"},
        {"start": 6.5, "end": 9.0, "text": "она не работает"},
    ]
    turns = from_whisper(raw, operator_first=True, stereo=stereo)
    assert [t["role"] for t in turns] == ["operator", "client", "operator", "client"]
    assert all(t.get("source") == "stereo" for t in turns)
    fixed = from_whisper(raw, operator_first=True, stereo=stereo, operator_channel="left")
    assert fixed[0]["role"] == "client"


def test_stereo_uses_operator_cues_for_channel_mapping():
    stereo = _stereo([(0, 2, 0), (2.5, 5, 1)])
    raw = [
        {"start": 0.0, "end": 2.0, "text": "алло, у меня проблема"},
        {"start": 2.5, "end": 5.0, "text": "Добрый день, банк Центр-инвест, меня зовут Елена, чем могу помочь"},
    ]
    turns = from_whisper(raw, operator_first=True, stereo=stereo)
    assert turns[0]["role"] == "client"
    assert turns[1]["role"] == "operator"


def test_stereo_words_split_inside_segment():
    stereo = _stereo([(0, 1.0, 1), (1.2, 2.4, 0)])
    raw = [
        {
            "start": 0.0,
            "end": 2.4,
            "text": "слушаю вас здравствуйте",
            "words": [
                {"start": 0.0, "end": 0.4, "text": "слушаю"},
                {"start": 0.5, "end": 0.9, "text": "вас"},
                {"start": 1.3, "end": 2.3, "text": "здравствуйте"},
            ],
        }
    ]
    turns = from_whisper(raw, operator_first=True, stereo=stereo)
    assert len(turns) == 2
    assert turns[0]["text"] == "слушаю вас" and turns[0]["role"] == "operator"
    assert turns[1]["role"] == "client"


def test_fake_stereo_falls_back_to_text_rules():
    import numpy as np

    mono = 0.2 * np.random.default_rng(1).standard_normal(16000 * 8).astype(np.float32)
    stereo = np.stack([mono, mono], axis=1)
    raw = [
        {"start": 0.0, "end": 2.0, "text": "Добрый день, банк Центр-инвест, меня зовут Елена."},
        {"start": 5.5, "end": 8.0, "text": "Здравствуйте, хочу открыть счёт."},
    ]
    turns = from_whisper(raw, operator_first=True, stereo=stereo)
    assert turns[0]["role"] == "operator" and turns[-1]["role"] == "client"
    assert all(t.get("source") != "stereo" for t in turns)


def test_read_stereo_16k_detects_channels(tmp_path):
    import numpy as np
    import soundfile as sf

    from app.audio import probe_channels, read_stereo_16k

    stereo_path = tmp_path / "st.wav"
    mono_path = tmp_path / "mono.wav"
    data = _stereo([(0, 1, 0), (1, 2, 1)], total=2.0, sr=8000)
    sf.write(str(stereo_path), data, 8000)
    sf.write(str(mono_path), data[:, 0], 8000)
    assert probe_channels(stereo_path) == 2
    assert probe_channels(mono_path) == 1
    out = read_stereo_16k(stereo_path)
    assert out is not None and out.shape[1] == 2 and abs(len(out) - 32000) < 400
    assert float(np.abs(out[:16000, 0]).mean()) > float(np.abs(out[:16000, 1]).mean())
    assert read_stereo_16k(mono_path) is None


def _voice(f0: float, tilt: float, seconds: float, sr: int, rng) -> "np.ndarray":
    import numpy as np

    t = np.arange(int(seconds * sr)) / sr
    vib = 1 + 0.03 * np.sin(2 * np.pi * 5 * t)
    x = sum((1.0 / k**tilt) * np.sin(2 * np.pi * f0 * k * np.cumsum(vib) / sr) for k in range(1, 16))
    return (0.1 * x / np.abs(x).max() + 0.002 * rng.standard_normal(len(t))).astype("float32")


def _dialog(voices):
    import numpy as np

    sr, rng = 16000, np.random.default_rng(1)
    audio, words, pos = [], [], 0.0
    lines = ["Добрый день, компания слушаю вас.", "Здравствуйте, хочу узнать время.", "Подскажите, пожалуйста, номер?",
             "Да.", "Спасибо, сейчас проверю.", "Хорошо, жду ответа.", "Всего доброго.", "До свидания, спасибо."]
    for i, text in enumerate(lines):
        f0, tilt = voices[i % len(voices)]
        dur = 0.4 * len(text.split()) + 0.3
        audio.append(_voice(f0, tilt, dur, sr, rng))
        audio.append(np.zeros(int(0.3 * sr), dtype="float32"))
        toks = text.split()
        step = dur / len(toks)
        for j, tok in enumerate(toks):
            words.append({"start": pos + j * step, "end": pos + (j + 1) * step - 0.02, "text": tok})
        pos += dur + 0.3
    return [{"start": 0.0, "end": pos, "text": " ".join(lines), "words": words}], np.concatenate(audio), sr


def test_voice_split_alternates_speakers_even_without_pauses():
    from app.diarize import from_whisper

    raw, audio, sr = _dialog([(230.0, 1.0), (110.0, 2.2)])
    turns = from_whisper(raw, operator_first=True, audio=audio, sr=sr)
    assert [t["role"] for t in turns] == ["operator", "client"] * 4
    assert turns[0]["text"].startswith("Добрый день") and turns[3]["text"] == "Да."
    assert all(t.get("source") == "voice" for t in turns)


def test_voice_split_skips_single_voice():
    from app.diarize import voice_turns

    raw, audio, sr = _dialog([(180.0, 1.5)])
    assert voice_turns(raw[0]["words"], audio, sr) is None
