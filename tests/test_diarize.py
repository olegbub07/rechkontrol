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


def test_assign_roles_fallback_first_speaker():
    turns = assign_roles(
        [
            {"start": 0, "end": 1, "speaker": "A", "text": "слушаю"},
            {"start": 2, "end": 3, "speaker": "B", "text": "алло"},
        ],
        operator_first=True,
    )
    assert turns[0]["role"] == "operator"
