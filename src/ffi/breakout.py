"""Curated breakout-potential notes, joined onto the cheat-sheet board.

ANNOTATIONS ONLY. Nothing here touches proj/VORP/tier -- the notes layer exists
to put researched *context* (situation change, post-injury discount, year-2/3
leap, role path) next to a name the board's numbers alone would not flag. If a
note ever convinces the operator to move a number, that goes through the
existing capped, human-confirmed path (`scripts/confirm_signals.py` ->
`ffi.signals_apply`), never through this module. See
`docs/superpowers/specs/2026-07-24-breakout-notes-design.md`.

Fail-loud per ADR Domain 1 (`docs/superpowers/risks/2026-07-08-draft-intelligence-adr.md`):
every way a curated note could silently fail to reach the board raises
`BreakoutNotesError` instead. The dangerous case is not a typo that renders
nothing obvious -- it is a note that matches a real player who sits *below* the
cheat sheet's per-position DEPTH cutoff, so the entry looks fine in the YAML,
loads without complaint, and simply never appears on the board the operator
reads on draft day. That is silent degradation, so it is an error here.
"""
import dataclasses
import pathlib

import yaml

NOTES_PATH = pathlib.Path("data/breakout-notes-2026.yaml")

# Badge letter + meaning. A category outside this set is an error: the HTML
# colors badges by category, so an unknown one would render an uncolored,
# meaningless badge rather than announcing itself.
CATEGORIES = {
    "situation": "S",  # new QB/OC/scheme, rebuilt line, vacated targets or carries
    "post-injury": "I",  # 2025 lost or dented, healthy now, ADP still discounted
    "year-n-leap": "Y",  # year 2-3 role + efficiency signals
    "role-path": "R",  # one depth-chart event from a starting job
}
POSITIONS = {"QB", "RB", "WR", "TE"}

REQUIRED_FIELDS = ("name", "pos", "category", "thesis", "kill")
# Draft-day scanning budget, not a storage limit: a thesis nobody can read under
# a 90-second clock is a note that does not work. (Design doc said 140 for the
# thesis; relaxed to 220 after writing real entries -- the mechanism plus its
# evidence does not compress to 140 without becoming assertion-without-reason.)
MAX_THESIS = 220
MAX_KILL = 180


class BreakoutNotesError(Exception):
    """A curated breakout note cannot be placed on the board: malformed entry,
    unknown category/position, duplicate, a name matching no pooled player, an
    ambiguous name, or a player who exists but falls below the cheat sheet's
    DEPTH cutoff and would therefore never render. Raised before any HTML is
    written -- the board is never built with notes silently missing."""


@dataclasses.dataclass(frozen=True)
class BreakoutNote:
    name: str
    pos: str
    category: str
    thesis: str
    kill: str
    sources: tuple[str, ...] = ()

    @property
    def badge(self) -> str:
        return CATEGORIES[self.category]


def load_notes(path: pathlib.Path | str = NOTES_PATH) -> list[BreakoutNote]:
    """Parse the curated YAML into `BreakoutNote`s, or raise.

    Validates shape only -- matching against the player pool is `attach`'s job,
    so the notes file can be linted without a database.
    """
    path = pathlib.Path(path)
    if not path.exists():
        raise BreakoutNotesError(
            f"breakout notes file not found: {path} (expected a curated YAML list; "
            "see docs/superpowers/specs/2026-07-24-breakout-notes-design.md)"
        )
    raw = yaml.safe_load(path.read_text())
    if raw is None:
        raise BreakoutNotesError(f"{path} is empty -- expected a YAML list of notes")
    if not isinstance(raw, list):
        raise BreakoutNotesError(
            f"{path} must be a YAML list of notes, got {type(raw).__name__}"
        )

    # Collect every problem before raising rather than stopping at the first.
    # Editing this file is a curation loop -- surfacing one error per run turns a
    # ten-second fix into ten builds, and `attach` below already works this way.
    notes: list[BreakoutNote] = []
    problems: list[str] = []
    for i, entry in enumerate(raw):
        where = f"entry {i + 1}"
        if not isinstance(entry, dict):
            problems.append(f"{where}: expected a mapping, got {type(entry).__name__}")
            continue
        who = f"{where} ({entry.get('name', '?')})"
        missing = [f for f in REQUIRED_FIELDS if not str(entry.get(f, "")).strip()]
        if missing:
            problems.append(
                f"{who}: missing/empty required field(s): {', '.join(missing)}"
                + (
                    ". Every note needs a kill condition -- a thesis with nothing "
                    "that would falsify it is a hunch, not research"
                    if "kill" in missing
                    else ""
                )
            )
            continue
        unknown = set(entry) - set(REQUIRED_FIELDS) - {"sources"}
        if unknown:
            problems.append(
                f"{who}: unknown field(s) {sorted(unknown)} -- typo, or the "
                "schema changed without this loader"
            )
        if entry["category"] not in CATEGORIES:
            problems.append(
                f"{who}: unknown category {entry['category']!r}; expected one of "
                f"{sorted(CATEGORIES)}"
            )
        if entry["pos"] not in POSITIONS:
            problems.append(
                f"{who}: position {entry['pos']!r} is not one of {sorted(POSITIONS)}"
            )
        for field, cap in (("thesis", MAX_THESIS), ("kill", MAX_KILL)):
            if len(entry[field]) > cap:
                problems.append(
                    f"{who}: {field} is {len(entry[field])} chars, cap is {cap} "
                    f"(trim {len(entry[field]) - cap}) -- this gets read under a "
                    "90-second draft clock"
                )
        sources = entry.get("sources") or []
        if not isinstance(sources, list):
            problems.append(f"{who}: sources must be a list of URLs")
            sources = []
        notes.append(
            BreakoutNote(
                name=str(entry["name"]).strip(),
                pos=entry["pos"],
                category=entry["category"],
                thesis=entry["thesis"].strip(),
                kill=entry["kill"].strip(),
                sources=tuple(str(s) for s in sources),
            )
        )

    dupes = _duplicates([(n.name.lower(), n.pos) for n in notes])
    if dupes:
        problems.append(
            f"duplicate note(s) for {dupes} -- one note per player; merge them"
        )
    if problems:
        raise BreakoutNotesError(
            f"{path}: {len(problems)} problem(s)\n  - " + "\n  - ".join(problems)
        )
    return notes


def attach(notes, pool, depth: dict[str, int]) -> dict[str, BreakoutNote]:
    """Map pool player `ref` -> note, or raise if any note cannot be placed.

    `depth` is the cheat sheet's per-position cutoff, passed in rather than
    imported so this module has no dependency on the rendering script. A note
    on a player ranked below their position's cutoff raises: the entry would
    otherwise load cleanly and never appear on the board.
    """
    rendered: dict[tuple[str, str], object] = {}
    in_pool: dict[tuple[str, str], list] = {}
    for pos, cutoff in depth.items():
        ranked = sorted(
            (p for p in pool if p.position == pos), key=lambda p: -p.proj_points
        )
        for rank, p in enumerate(ranked):
            key = (p.name.strip().lower(), pos)
            in_pool.setdefault(key, []).append(p)
            if rank < cutoff:
                rendered.setdefault(key, p)

    attached: dict[str, BreakoutNote] = {}
    unmatched: list[str] = []
    ambiguous: list[str] = []
    below_cutoff: list[str] = []
    for note in notes:
        key = (note.name.lower(), note.pos)
        candidates = in_pool.get(key, [])
        if not candidates:
            unmatched.append(f"{note.name} ({note.pos})")
        elif len(candidates) > 1:
            ambiguous.append(f"{note.name} ({note.pos}) x{len(candidates)}")
        elif key not in rendered:
            below_cutoff.append(
                f"{note.name} ({note.pos}, cutoff {depth.get(note.pos)})"
            )
        else:
            attached[rendered[key].ref] = note

    problems = []
    if unmatched:
        problems.append(
            f"no player in the pool matches: {unmatched} -- check spelling and "
            "position against the board (the pool uses e.g. 'Tre Harris', "
            "'Chigoziem Okonkwo')"
        )
    if ambiguous:
        problems.append(
            f"name+position matches more than one pooled player: {ambiguous} -- "
            "disambiguate before this note can be placed"
        )
    if below_cutoff:
        problems.append(
            f"player is in the pool but ranks below the cheat sheet's DEPTH "
            f"cutoff, so the note would never render: {below_cutoff} -- raise "
            "DEPTH for that position or drop the note"
        )
    if problems:
        raise BreakoutNotesError(
            "breakout notes cannot be placed: " + "; ".join(problems)
        )
    return attached


def _duplicates(keys):
    seen, dupes = set(), []
    for k in keys:
        if k in seen:
            dupes.append(k)
        seen.add(k)
    return dupes
