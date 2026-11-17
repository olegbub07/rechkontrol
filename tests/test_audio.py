from app.audio import sniff_audio
from app.api.export import CSV_COLUMNS, CSV_FIELDS


def test_sniff_wav_mp3_ogg():
    assert sniff_audio(b"RIFF....WAVE", "call.wav") == "audio/wav"
    assert sniff_audio(b"OggS........", "call.ogg") == "audio/ogg"
    assert sniff_audio(b"ID3.........", "call.mp3") == "audio/mpeg"


def test_export_contains_case_kpi_fields():
    for field in ("violation_pct", "avg_response_sec", "tonality_ru", "datetime", "operator_id"):
        assert field in CSV_FIELDS
    titles = [title for _, title in CSV_COLUMNS]
    for title in ("Процент нарушений", "Среднее время ответа, сек", "Тональность", "Дата и время (МСК)", "Идентификатор оператора"):
        assert title in titles
