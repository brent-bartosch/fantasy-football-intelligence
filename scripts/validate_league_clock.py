#!/usr/bin/env python3
"""Fail if any UNSET or UNVERIFIED league-clock field is consumed by code.

ADR Domain 1 fail-closed: a field whose value has not been observed must not
silently become a number in a deadline computation. R4/R16 are the concrete
cases — a `waiver_processing_hour` guessed from the settings page would
produce a confidently-wrong claim deadline every week of the season.

Detection is a textual scan of `src/` and `scripts/`, not an import graph. For
each unsafe field name, three shapes are matched:

  1. `['"]field['"]`   — dict access: `clock["waiver_processing_hour"]`,
                          `clock.get("clear_award_mechanism")`, and any other
                          quoted mention (including in a list of key names).
  2. `.field\\b`        — attribute access: `clock.waiver_processing_hour`,
                          i.e. the field arriving via a dataclass/SimpleNamespace.
  3. `\\bfield\\s*[:=]`  — bare-identifier binding or comparison: `trade_deadline =`,
                          `trade_deadline:` (kwarg, annotation, YAML-ish literal),
                          `trade_deadline ==`.

What is NOT caught, and cannot be by a textual scan:

  * Generic iteration that never names the field —
    `for k, v in doc.items(): setattr(cfg, k, v)` or `Clock(**doc)`. A consumer
    written that way is a false negative. The mitigation is social, not
    mechanical: `config/league_clock.yaml` documents the marker conventions,
    and `src/ffi/league_state/clock.py` is the only sanctioned reader.
  * Dynamically built keys — `clock[f"waiver_{part}"]`.
  * Consumers outside `src/` and `scripts/` (tests are intentionally exempt;
    they must be able to name unsafe fields to assert on them).

False positives are acceptable and are the safe direction — shape 3 in
particular will flag any unrelated local named `trade_deadline`. Fix a
collision by renaming the colliding symbol, not by weakening the pattern; a
false negative is the failure this script exists to prevent.

Exit 0 = safe. Exit 1 = an unsafe field is consumed (with file:line).
"""
import argparse
import pathlib
import re
import sys

import yaml

CONFIG = pathlib.Path("config/league_clock.yaml")
SCAN_ROOTS = [pathlib.Path("src"), pathlib.Path("scripts")]
SKIP_PARTS = {"__pycache__", ".venv", "archive"}
SELF = "validate_league_clock.py"
UNSET = "UNSET"
VERIFIED_SUFFIX = "_verified"


def load_doc(path: pathlib.Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(
            f"{path}: expected a top-level mapping, got {type(doc).__name__}"
        )
    return doc


def unsafe_fields(doc: dict) -> list[str]:
    """Field names that must not be consumed yet: value == 'UNSET', or a
    sibling `<field>_verified` that is false.

    A `<field>_verified` marker whose base field is absent is a config typo
    that would silently disable the gate for the field the author meant to
    protect, so it raises rather than being skipped (fail-loud)."""
    unsafe = {
        k for k, val in doc.items() if isinstance(val, str) and val.strip() == UNSET
    }
    for key, val in doc.items():
        if not key.endswith(VERIFIED_SUFFIX):
            continue
        base = key[: -len(VERIFIED_SUFFIX)]
        if base not in doc:
            raise ValueError(
                f"orphan verification marker {key!r}: no base field {base!r} in "
                f"the document. Rename the marker or add the field — a marker "
                f"that guards nothing silently disables the gate."
            )
        if val is False:
            unsafe.add(base)
    return sorted(unsafe)


def _consumption_pattern(fields: list[str]) -> re.Pattern:
    """One alternation with a named group per field, so a match knows which
    field it found. See the module docstring for the three shapes."""
    alts = []
    for i, field in enumerate(fields):
        f = re.escape(field)
        alts.append(rf"(?P<f{i}>['\"]{f}['\"]|\.{f}\b|\b{f}\s*[:=])")
    return re.compile("|".join(alts))


def find_consumers(
    fields: list[str], roots: list[pathlib.Path]
) -> list[tuple[str, str, int]]:
    if not fields:
        return []
    pattern = _consumption_pattern(fields)
    hits: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str, int]] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if SKIP_PARTS & set(path.parts) or path.name == SELF:
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                # finditer, not search: one line may consume two fields.
                for match in pattern.finditer(line):
                    field = fields[int(match.lastgroup[1:])]
                    hit = (field, str(path), lineno)
                    if hit not in seen:
                        seen.add(hit)
                        hits.append(hit)
    return hits


def main(
    argv: list[str] | None = None,
    config: pathlib.Path = CONFIG,
    roots: list[pathlib.Path] | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    roots = SCAN_ROOTS if roots is None else roots
    doc = load_doc(config)
    if "as_of" not in doc:
        print(f"FAIL: {config} has no as_of stamp.")
        return 1
    fields = unsafe_fields(doc)
    hits = find_consumers(fields, roots)
    if hits:
        print(f"FAIL: {len(hits)} consumer(s) of UNSET/UNVERIFIED league-clock fields:")
        for field, path, lineno in hits:
            print(f"  {path}:{lineno}: consumes {field!r}")
        print("\nRun the P3 observed-mechanics checks and populate the field, or")
        print("stop consuming it. Fail-closed is the contract (ADR Domain 1).")
        return 1
    if not args.quiet:
        print(
            f"OK: {config} as_of {doc['as_of']}; {len(fields)} field(s) still "
            f"UNSET/UNVERIFIED and none is consumed: {', '.join(fields) or '(none)'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
