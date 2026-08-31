import pathlib

import pytest

import validate_league_clock as v  # scripts/ is on sys.path via conftest


def test_unsafe_fields_finds_unset_and_unverified():
    doc = {
        "as_of": "2026-08-31",
        "teams": 12,
        "waiver_processing_hour": "UNSET",
        "trade_deadline": "2026-11-22",
        "trade_deadline_verified": False,
        "playoff_teams": 6,
        "playoff_teams_verified": True,
    }
    assert v.unsafe_fields(doc) == ["trade_deadline", "waiver_processing_hour"]


def test_unsafe_fields_ignores_populated_and_verified():
    doc = {"teams": 12, "playoff_teams": 6, "playoff_teams_verified": True}
    assert v.unsafe_fields(doc) == []


def test_find_consumers_detects_a_string_literal_use(tmp_path):
    (tmp_path / "bad.py").write_text(
        'clock = load()\nhour = clock["waiver_processing_hour"]\n'
    )
    hits = v.find_consumers(["waiver_processing_hour"], [tmp_path])
    assert len(hits) == 1
    field, path, lineno = hits[0]
    assert field == "waiver_processing_hour"
    assert path.endswith("bad.py")
    assert lineno == 2


def test_find_consumers_ignores_a_field_that_is_not_used(tmp_path):
    (tmp_path / "fine.py").write_text('x = clock["teams"]\n')
    assert v.find_consumers(["waiver_processing_hour"], [tmp_path]) == []


def test_the_real_config_has_no_unsafe_consumers():
    """The gate itself: as long as no module consumes an UNSET/UNVERIFIED
    field, the repo is safe to ship with the skeleton in place."""
    assert v.main(["--quiet"]) == 0


def test_the_real_config_still_has_unset_fields():
    """If this fails, P3 has been done — delete this test and update the
    plan's completion notes with the observed values."""
    doc = v.load_doc(pathlib.Path("config/league_clock.yaml"))
    assert "waiver_processing_hour" in v.unsafe_fields(doc)
