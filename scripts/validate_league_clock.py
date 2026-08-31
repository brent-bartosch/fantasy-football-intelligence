#!/usr/bin/env python3
"""Fail if any UNSET or UNVERIFIED league-clock field is consumed by code.

ADR Domain 1 fail-closed: a field whose value has not been observed must not
silently become a number in a deadline computation. R4/R16 are the concrete
cases — a `waiver_processing_hour` guessed from the settings page would
produce a confidently-wrong claim deadline every week of the season.

Detection is a string-literal scan, not an import graph: the YAML is read
through dict lookups (`clock["waiver_processing_hour"]`,
`clock.get("clear_award_mechanism")`), so the field name appears verbatim at
every real call site. False positives are acceptable and are the safe
direction; a false negative is the failure this script exists to prevent.

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
    sibling `<field>_verified` that is false."""
    unsafe = {
        k for k, val in doc.items() if isinstance(val, str) and val.strip() == UNSET
    }
    for key, val in doc.items():
        if key.endswith(VERIFIED_SUFFIX) and val is False:
            base = key[: -len(VERIFIED_SUFFIX)]
            if base in doc:
                unsafe.add(base)
    return sorted(unsafe)


def find_consumers(
    fields: list[str], roots: list[pathlib.Path]
) -> list[tuple[str, str, int]]:
    if not fields:
        return []
    pattern = re.compile("|".join(rf"['\"]{re.escape(f)}['\"]" for f in fields))
    hits: list[tuple[str, str, int]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if SKIP_PARTS & set(path.parts) or path.name == SELF:
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                match = pattern.search(line)
                if match:
                    hits.append((match.group(0).strip("'\""), str(path), lineno))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    doc = load_doc(CONFIG)
    if "as_of" not in doc:
        print(f"FAIL: {CONFIG} has no as_of stamp.")
        return 1
    fields = unsafe_fields(doc)
    hits = find_consumers(fields, SCAN_ROOTS)
    if hits:
        print(f"FAIL: {len(hits)} consumer(s) of UNSET/UNVERIFIED league-clock fields:")
        for field, path, lineno in hits:
            print(f"  {path}:{lineno}: consumes {field!r}")
        print("\nRun the P3 observed-mechanics checks and populate the field, or")
        print("stop consuming it. Fail-closed is the contract (ADR Domain 1).")
        return 1
    if not args.quiet:
        print(
            f"OK: {CONFIG} as_of {doc['as_of']}; {len(fields)} field(s) still "
            f"UNSET/UNVERIFIED and none is consumed: {', '.join(fields) or '(none)'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
