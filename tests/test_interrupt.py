import numpy as np

from app.diarize import annotate_pauses
from app.quality import _avg_response

LONG = "Мне вчера списали деньги с карты, а я ничего не покупала, объясните"


def _tone(seconds, f0, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    return (0.2 * np.sin(2 * np.pi * f0 * t)).astype("float32")


def _turns(gap):
    return [
        {"role": "client", "start": 0.0, "end": 3.0, "text": LONG},
        {"role": "operator", "start": 3.0 + gap, "end": 5.0, "text": "Понимаю, сейчас проверю."},
    ]


def test_tiny_gap_without_audio_is_not_interrupt():
    assert _avg_response(_turns(0.1))[1] == 0


def test_real_overlap_by_timestamps_is_interrupt():
    assert _avg_response(_turns(-0.5))[1] == 1


def test_tiny_gap_with_silence_in_audio_is_not_interrupt():
    sr = 16000
    # Whisper растянул слова: по таймстемпам зазор 0,1 с, а в звуке пауза 0,4 с.
    audio = np.concatenate([_tone(2.8, 200), np.zeros(int(0.4 * sr), "float32"), _tone(1.9, 120)])
    turns = annotate_pauses(_turns(0.1), audio, sr)
    assert turns[1]["pause_before"] >= 0.3
    assert _avg_response(turns)[1] == 0


def test_overlapping_voices_in_audio_is_interrupt():
    sr = 16000
    client = _tone(3.2, 200)
    operator = _tone(2.0, 120)
    audio = np.zeros(int(5.2 * sr), "float32")
    audio[: len(client)] += client
    audio[int(2.9 * sr) : int(2.9 * sr) + len(operator)] += operator  # оператор влез, пока клиент говорит
    turns = annotate_pauses(_turns(0.05), audio, sr)
    assert turns[1]["pause_before"] < 0.12
    assert _avg_response(turns)[1] == 1
