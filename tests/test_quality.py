from app.demo_calls import DEMO_CALLS
from app.quality import analyze


def _by_name(name: str) -> list[dict]:
    return next(c for c in DEMO_CALLS if c["filename"] == name)["segments"]


def test_excellent_sme_call_is_high():
    report = analyze(_by_name("2026-11-03_rko_ip.wav"))
    assert report.overall >= 80
    assert report.politeness >= 75
    assert report.script_compliance >= 80
    assert report.resolution >= 75
    assert report.tonality == "positive"
    assert report.violation_pct < 40


def test_rude_call_fails_politeness():
    report = analyze(_by_name("2026-11-04_payment_fail.wav"))
    assert report.politeness < 50
    assert report.overall < 55
    ids = {v["id"] for v in report.violations}
    assert "no_rudeness" in ids
    assert report.recommendations


def test_bounced_call_fails_resolution():
    report = analyze(_by_name("2026-11-16_bounce.wav"))
    assert report.resolution < 55
    ids = {v["id"] for v in report.violations}
    assert "no_hanging" in ids


def test_demo_set_has_spread():
    scores = [analyze(c["segments"]).overall for c in DEMO_CALLS]
    assert max(scores) >= 85
    assert min(scores) <= 45
    assert len(scores) == len(DEMO_CALLS)


def test_empty_transcript():
    report = analyze([])
    assert report.overall == 0
    assert report.violation_pct == 100
