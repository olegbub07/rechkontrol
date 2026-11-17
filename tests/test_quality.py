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


def test_edge_demos_are_added_when_database_already_has_calls():
    from app.db import SessionLocal
    from app.demo_calls import EDGE_FILENAMES
    from app.models import Call
    from app.seed import seed_if_empty

    with SessionLocal() as session:
        present = {
            row.orig_filename
            for row in session.query(Call).filter(Call.orig_filename.in_(EDGE_FILENAMES))
        }
        assert present == set(EDGE_FILENAMES)
        for row in session.query(Call).filter(Call.orig_filename.in_(EDGE_FILENAMES)):
            session.delete(row)
        session.flush()
        seed_if_empty(session)
        session.flush()
        scores = {
            row.orig_filename: row.quality.overall
            for row in session.query(Call).filter(Call.orig_filename.in_(EDGE_FILENAMES))
        }
        assert scores["2026-11-17_score0.wav"] == 0
        assert scores["2026-11-17_score100.wav"] == 100
        session.rollback()


def test_edge_demos_score_zero_and_hundred():
    zero = analyze(_by_name("2026-11-17_score0.wav"))
    perfect = analyze(_by_name("2026-11-17_score100.wav"))
    assert zero.overall == 0
    assert perfect.overall == 100
    assert perfect.politeness == 100
    assert perfect.completeness == 100
    assert perfect.script_compliance == 100
    assert perfect.resolution == 100
    assert all(check.score == 100 and check.passed for check in perfect.checks)
