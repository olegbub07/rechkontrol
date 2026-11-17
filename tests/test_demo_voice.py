import pytest


def test_demo_voice_is_off_by_default_and_does_not_download(monkeypatch):
    from app import demo_voice
    from app.config import Settings

    assert Settings(_env_file=None).demo_voice_enabled is False
    monkeypatch.setattr("app.demo_voice.settings.demo_voice_enabled", False)
    called = []
    monkeypatch.setattr(demo_voice, "_download", lambda path: called.append(path))
    monkeypatch.setattr(demo_voice, "_model", None)
    with pytest.raises(RuntimeError):
        demo_voice._load_model()
    assert called == []
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    started = []
    monkeypatch.setattr(demo_voice.threading, "Thread", lambda *a, **k: started.append(1))
    demo_voice.start_demo_voices()
    assert started == []


def test_voice_download_allows_only_silero_host(tmp_path, monkeypatch):
    from app import demo_voice

    path = tmp_path / "v4_ru.pt"
    for url in (
        "https://evil.example/v4_ru.pt",
        "http://models.silero.ai/models/tts/ru/v4_ru.pt",
        "https://user:secret@models.silero.ai/models/tts/ru/v4_ru.pt",
    ):
        monkeypatch.setattr("app.demo_voice.settings.demo_voice_model_url", url)
        with pytest.raises(RuntimeError, match="models.silero.ai"):
            demo_voice._download(path)
    assert not path.exists()


def test_voice_model_hash_is_enforced(tmp_path, monkeypatch):
    from app import demo_voice

    fake = tmp_path / "v4_ru.pt"
    fake.write_bytes(b"not a model")
    with pytest.raises(RuntimeError):
        demo_voice.verify_model(fake)
    monkeypatch.setattr("app.demo_voice.settings.demo_voice_model_sha256", demo_voice.file_sha256(fake))
    demo_voice.verify_model(fake)
    monkeypatch.setattr("app.demo_voice.settings.demo_voice_model_sha256", "")
    with pytest.raises(RuntimeError):
        demo_voice.verify_model(fake)


def test_demo_dialogue_is_stereo_operator_left():
    import io

    import numpy as np
    import soundfile as sf

    from app import demo_voice

    class FakeModel:
        def apply_tts(self, text, speaker, sample_rate, **_):
            t = np.arange(int(sample_rate * 0.5)) / sample_rate
            return (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    segments = [
        {"role": "operator", "text": "Добрый день, банк Центр-инвест."},
        {"role": "client", "text": "Здравствуйте, у меня вопрос."},
    ]
    payload, timed, duration = demo_voice.render_dialogue(FakeModel(), segments)
    audio, sr = sf.read(io.BytesIO(payload), dtype="float32")
    assert audio.ndim == 2 and audio.shape[1] == 2
    first = audio[int(timed[0]["start"] * sr) : int(timed[0]["end"] * sr)]
    second = audio[int(timed[1]["start"] * sr) : int(timed[1]["end"] * sr)]
    assert np.abs(first[:, 0]).mean() > 0.1 and np.abs(first[:, 1]).max() == 0
    assert np.abs(second[:, 1]).mean() > 0.1 and np.abs(second[:, 0]).max() == 0
    assert timed[1]["start"] - timed[0]["end"] >= 0.55
