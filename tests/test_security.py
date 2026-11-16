import pytest

from app.audio import AudioError, sniff_audio
from app.security import safe_audio_path, sanitize_filename


def test_sanitize_filename_strips_path():
    assert sanitize_filename("../../secret.wav") == "secret.wav"
    assert sanitize_filename("разговор 12.mp3") == "разговор 12.mp3"


def test_safe_audio_path_rejects_traversal():
    with pytest.raises(ValueError):
        safe_audio_path("../master.key")
    with pytest.raises(ValueError):
        safe_audio_path("not-a-uuid.bin")
    with pytest.raises(ValueError):
        safe_audio_path(None)


def test_sniff_rejects_non_audio():
    with pytest.raises(AudioError):
        sniff_audio(b"MZ..............", "payload.wav")
    with pytest.raises(AudioError):
        sniff_audio(b"RIFF....NOTW", "call.wav")


def test_sniff_accepts_case_formats():
    assert sniff_audio(b"RIFF....WAVE", "call.wav") == "audio/wav"
    assert sniff_audio(b"OggS........", "call.ogg") == "audio/ogg"
    assert sniff_audio(b"ID3.........", "call.mp3") == "audio/mpeg"
