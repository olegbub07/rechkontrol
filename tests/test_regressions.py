from datetime import datetime

import pytest

from app.api.export import csv_cell
from app.audio import AudioError, parse_ffmpeg_duration, to_wav_mono_16k
from app.dataset import clip_file
from app.diarize import from_whisper
from app.demo_calls import EDGE_FILENAMES
from app.seed import seed_if_empty


def test_csv_cell_neutralizes_formulas():
    assert csv_cell("=1+1") == "'=1+1"
    assert csv_cell("+cmd") == "'+cmd"
    assert csv_cell("@sum") == "'@sum"
    assert csv_cell(12.5) == 12.5
    assert csv_cell(None) == ""


def test_ffmpeg_duration_parser():
    assert parse_ffmpeg_duration("Duration: 00:10:00.00, start") == 600
    assert parse_ffmpeg_duration("Duration: 01:02:03.50") == 3723.5
    assert parse_ffmpeg_duration("no duration") is None


def test_to_wav_rejects_over_limit_before_decode(monkeypatch, tmp_path):
    called = {"n": 0}

    def forbid(_src):
        called["n"] += 1
        return 1000.0

    monkeypatch.setattr("app.audio._probe_duration", forbid)
    src = tmp_path / "long.wav"
    src.write_bytes(b"not-audio")
    dst = tmp_path / "out.wav"
    with pytest.raises(AudioError):
        to_wav_mono_16k(src, dst, max_duration_sec=900)
    assert called["n"] == 1
    assert not dst.exists()


def test_clip_file_rejects_escape():
    with pytest.raises(RuntimeError):
        clip_file("../master.key")
    with pytest.raises(RuntimeError):
        clip_file("clips/../../outside.wav")
    inside = clip_file("clips/0001.wav")
    assert inside.name == "0001.wav"
    assert inside.parent.name == "clips"


def test_seed_writes_script_only_when_missing(monkeypatch):
    stored: dict = {}
    writes: list[str] = []

    class _Query:
        def count(self):
            return 1

        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return [(name,) for name in EDGE_FILENAMES]

    class _Session:
        def get(self, _model, key):
            return stored.get(key)

        def query(self, _model):
            return _Query()

    def put(_session, key, value):
        writes.append(key)
        stored[key] = value

    monkeypatch.setattr("app.seed.put_json", put)
    seed_if_empty(_Session())
    seed_if_empty(_Session())
    assert writes == ["company_phrases", "extra_script_phrases"]


def test_workers_recover_only_when_idle(monkeypatch):
    import app.worker as worker

    recovered = {"n": 0}
    monkeypatch.setattr(worker, "recover_stale_jobs", lambda: recovered.__setitem__("n", recovered["n"] + 1))
    monkeypatch.setattr(worker, "_loop", lambda: worker._stop.wait(30))
    worker.stop_workers()
    worker._threads.clear()
    worker._stop.clear()
    try:
        worker.start_workers()
        assert recovered["n"] == 1
        worker.start_workers()
        assert recovered["n"] == 1
        worker.stop_workers()
        assert not any(thread.is_alive() for thread in worker._threads)
        worker.start_workers()
        assert recovered["n"] == 2
    finally:
        worker.stop_workers()


def test_call_detail_survives_corrupt_json():
    from app.api.serializers import call_detail
    from app.models import Call, Quality, Transcript

    now = datetime(2026, 11, 16, 9, 7, 0)
    call = Call(
        id=7,
        orig_filename="a.wav",
        status="done",
        progress=100,
        call_at=now,
        uploaded_at=now,
    )
    call.operator = None
    call.transcript = Transcript(
        call_id=7,
        full_text="привет",
        segments_json='[{"start": "bad"}, {"start": 0, "end": 1.5, "speaker": "A", "role": "operator", "text": "да"}, "nope"]',
    )
    call.quality = Quality(
        call_id=7,
        politeness=80,
        completeness=70,
        script_compliance=60,
        resolution=50,
        overall=65,
        tonality="neutral",
        tonality_score=0,
        talk_ratio_operator=0.4,
        violation_pct=10,
        violations_json="{not-json",
        recommendations_json='["совет", 2]',
        checks_json='[{"id": "x", "title": "Приветствие", "passed": true, "score": 90}, "bad"]',
        summary="ок",
    )
    detail = call_detail(call)
    assert detail.full_text == "привет"
    assert len(detail.segments) == 1
    assert detail.segments[0].text == "да"
    assert detail.quality is not None
    assert detail.quality.violations == []
    assert detail.quality.recommendations == ["совет", "2"]
    assert len(detail.quality.checks) == 1
    assert detail.quality.checks[0].title == "Приветствие"


def test_from_whisper_skips_broken_words():
    turns = from_whisper(
        [
            {
                "text": "здравствуйте",
                "start": 0,
                "end": 1,
                "words": [{"text": "здравствуйте", "start": 0, "end": 1}, {"text": "обрыв"}],
            },
            "не словарь",
        ]
    )
    assert any("здравствуйте" in t["text"] for t in turns)
