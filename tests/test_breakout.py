"""Breakout-notes layer: YAML validation + pool matching.

Synthetic pool on purpose -- these gates must hold regardless of what the live
board looks like on any given morning.
"""
import dataclasses

import pytest

from ffi.breakout import BreakoutNotesError, attach, load_notes

DEPTH = {"RB": 3, "WR": 3, "TE": 2, "QB": 2}


@dataclasses.dataclass(frozen=True)
class FakePlayer:
    ref: str
    name: str
    position: str
    proj_points: float


POOL = [
    FakePlayer("r1", "Alpha Back", "RB", 300),
    FakePlayer("r2", "Bravo Back", "RB", 250),
    FakePlayer("r3", "Charlie Back", "RB", 200),
    FakePlayer("r4", "Delta Back", "RB", 100),  # below RB cutoff of 3
    FakePlayer("w1", "Alpha Wide", "WR", 280),
    FakePlayer("t1", "Alpha End", "TE", 220),
]

NOTE = {
    "name": "Alpha Back",
    "pos": "RB",
    "category": "situation",
    "thesis": "Vacated 300 touches.",
    "kill": "Camp battle turns.",
}


def write(tmp_path, entries):
    import yaml

    p = tmp_path / "notes.yaml"
    p.write_text(yaml.safe_dump(entries))
    return p


def test_loads_valid_notes(tmp_path):
    notes = load_notes(write(tmp_path, [NOTE]))
    assert len(notes) == 1
    assert notes[0].name == "Alpha Back"
    assert notes[0].badge == "S"


def test_attaches_to_pool_by_ref(tmp_path):
    attached = attach(load_notes(write(tmp_path, [NOTE])), POOL, DEPTH)
    assert list(attached) == ["r1"]
    assert attached["r1"].thesis == "Vacated 300 touches."


def test_missing_file_is_loud(tmp_path):
    with pytest.raises(BreakoutNotesError, match="not found"):
        load_notes(tmp_path / "nope.yaml")


def test_missing_kill_field_is_loud(tmp_path):
    bad = {k: v for k, v in NOTE.items() if k != "kill"}
    with pytest.raises(BreakoutNotesError, match="kill"):
        load_notes(write(tmp_path, [bad]))


def test_unknown_category_is_loud(tmp_path):
    with pytest.raises(BreakoutNotesError, match="unknown category"):
        load_notes(write(tmp_path, [{**NOTE, "category": "vibes"}]))


def test_unknown_field_is_loud(tmp_path):
    with pytest.raises(BreakoutNotesError, match="unknown field"):
        load_notes(write(tmp_path, [{**NOTE, "confidence": "high"}]))


def test_overlong_thesis_is_loud(tmp_path):
    with pytest.raises(BreakoutNotesError, match="thesis is"):
        load_notes(write(tmp_path, [{**NOTE, "thesis": "x" * 400}]))


def test_duplicate_player_is_loud(tmp_path):
    with pytest.raises(BreakoutNotesError, match="duplicate"):
        load_notes(write(tmp_path, [NOTE, NOTE]))


def test_all_problems_reported_at_once(tmp_path):
    """One build surfaces every bad entry -- curation is a loop, and one error
    per run turns a ten-second fix into ten builds."""
    entries = [
        {**NOTE, "name": "One", "category": "vibes"},
        {**NOTE, "name": "Two", "pos": "XX"},
        {**NOTE, "name": "Three", "kill": "y" * 400},
    ]
    with pytest.raises(BreakoutNotesError) as e:
        load_notes(write(tmp_path, entries))
    msg = str(e.value)
    assert "3 problem(s)" in msg
    for name in ("One", "Two", "Three"):
        assert name in msg


def test_unmatched_name_is_loud(tmp_path):
    note = {**NOTE, "name": "Nobody Here"}
    with pytest.raises(BreakoutNotesError, match="no player in the pool matches"):
        attach(load_notes(write(tmp_path, [note])), POOL, DEPTH)


def test_wrong_position_is_unmatched(tmp_path):
    """Name+position is the key, so a note filed under the wrong position is a
    miss rather than a silent attach to the right player."""
    note = {**NOTE, "pos": "WR"}
    with pytest.raises(BreakoutNotesError, match="no player in the pool matches"):
        attach(load_notes(write(tmp_path, [note])), POOL, DEPTH)


def test_player_below_depth_cutoff_is_loud(tmp_path):
    """The dangerous case: the player exists, the YAML looks fine, and the note
    would simply never render because the cheat sheet truncates the column."""
    note = {**NOTE, "name": "Delta Back"}
    with pytest.raises(
        BreakoutNotesError, match="below the cheat sheet's DEPTH cutoff"
    ):
        attach(load_notes(write(tmp_path, [note])), POOL, DEPTH)


def test_shipped_notes_file_is_valid():
    """The committed curation file must always parse -- this is the guard that
    catches a bad hand-edit during the pre-freeze refresh."""
    notes = load_notes()
    assert notes, "shipped breakout notes file is empty"
