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


def test_unsafe_fields_raises_on_an_orphan_verification_marker():
    """A marker whose base field is absent guards nothing — a typo there would
    silently disable the gate, so it must fail loud, not be skipped."""
    doc = {"as_of": "2026-08-31", "playoff_tems_verified": False, "playoff_teams": 6}
    with pytest.raises(ValueError, match="playoff_tems_verified"):
        v.unsafe_fields(doc)


def test_find_consumers_detects_attribute_access(tmp_path):
    (tmp_path / "attr.py").write_text("x = 1\nhour = clock.waiver_processing_hour\n")
    hits = v.find_consumers(["waiver_processing_hour"], [tmp_path])
    assert [(f, ln) for f, _, ln in hits] == [("waiver_processing_hour", 2)]


def test_find_consumers_detects_a_bare_identifier_binding(tmp_path):
    (tmp_path / "bare.py").write_text("trade_deadline = date(2026, 11, 22)\n")
    hits = v.find_consumers(["trade_deadline"], [tmp_path])
    assert [(f, ln) for f, _, ln in hits] == [("trade_deadline", 1)]


def test_find_consumers_reports_both_fields_on_one_line(tmp_path):
    (tmp_path / "two.py").write_text(
        'f(clock["trade_deadline"], clock.playoff_teams)\n'
    )
    hits = v.find_consumers(["playoff_teams", "trade_deadline"], [tmp_path])
    assert sorted(f for f, _, _ in hits) == ["playoff_teams", "trade_deadline"]
    assert {ln for _, _, ln in hits} == {1}


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


def test_main_returns_1_when_an_unsafe_field_is_consumed(tmp_path, capsys):
    """The rc=1 path, exercised for real: a fixture module consuming one
    quoted and one attribute-style unsafe field must fail the gate and name
    the file and line."""
    config = tmp_path / "clock.yaml"
    config.write_text(
        "as_of: 2026-08-31\n"
        "waiver_processing_hour: UNSET\n"
        "trade_deadline: 2026-11-22\n"
        "trade_deadline_verified: false\n"
    )
    code = tmp_path / "code"
    code.mkdir()
    (code / "consumer.py").write_text(
        "def deadline(clock):\n"
        '    hour = clock["waiver_processing_hour"]\n'
        "    return clock.trade_deadline, hour\n"
    )

    assert v.main([], config=config, roots=[code]) == 1

    out = capsys.readouterr().out
    assert "FAIL: 2 consumer(s)" in out
    assert f"{code / 'consumer.py'}:2: consumes 'waiver_processing_hour'" in out
    assert f"{code / 'consumer.py'}:3: consumes 'trade_deadline'" in out


def test_main_returns_1_when_the_config_has_no_as_of(tmp_path, capsys):
    config = tmp_path / "clock.yaml"
    config.write_text("teams: 12\nwaiver_processing_hour: UNSET\n")
    assert v.main([], config=config, roots=[tmp_path]) == 1
    assert "no as_of stamp" in capsys.readouterr().out


def test_main_returns_0_when_no_unsafe_field_is_consumed(tmp_path, capsys):
    config = tmp_path / "clock.yaml"
    config.write_text("as_of: 2026-08-31\nwaiver_processing_hour: UNSET\n")
    code = tmp_path / "code"
    code.mkdir()
    (code / "fine.py").write_text('x = clock["teams"]\n')
    assert v.main([], config=config, roots=[code]) == 0
    assert "OK:" in capsys.readouterr().out
