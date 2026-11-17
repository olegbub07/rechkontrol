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
