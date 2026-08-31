"""Three-state source health (ADR Domain 1 + 5). Layer-0 leaf: imports
nothing internal, so the module that reports on every other module's state
can never be made self-referential (ARCHITECTURE §3).

R1 is the bug this exists to kill: scripts/morning_briefing.py rendered
nflverse [OK] for 50 days because the health loop tested `status` and never
`age_h` — on the same line that computed and printed `age_h`.

OK / KNOWN-LAGGING / BROKEN, not OK / DEGRADED: two of three sources lag
structurally most weeks, so a binary banner is on every report by week 2 and
a real outage hides inside it (R12). Only BROKEN is alarming.
"""

from __future__ import annotations

import datetime
import enum
import pathlib
from dataclasses import dataclass

import yaml

DEFAULT_CLOCK_PATH = pathlib.Path("config/source_clock.yaml")

_OK_STATUSES = frozenset({"success"})
_SUSPECT_STATUSES = frozenset({"sanity_warned"})
_REQUIRED_SOURCE_FIELDS = ("expected_interval_h", "lag_window_h", "deadline_h", "owner")
_REQUIRED_ARTIFACT_FIELDS = ("glob", "due_weekday", "active_from")


class SourceState(enum.Enum):
    OK = "OK"
    KNOWN_LAGGING = "KNOWN-LAGGING"
    BROKEN = "BROKEN"


class UnknownSourceError(KeyError):
    """A source has no contract in config/source_clock.yaml.

    Deliberately NOT a default contract: an unregistered source is an
    unmonitored source, and silently monitoring it against invented numbers
    is the failure this module exists to prevent.
    """


@dataclass(frozen=True)
class SourceContract:
    expected_interval_h: float
    lag_window_h: float
    deadline_h: float
    owner: str
    expected_season: int | None = None

    @property
    def fresh_within_h(self) -> float:
        return self.expected_interval_h + self.lag_window_h


@dataclass(frozen=True)
class ArtifactContract:
    name: str
    glob: str
    due_weekday: str
    active_from: datetime.date


@dataclass(frozen=True)
class SourceClock:
    as_of: datetime.date
    contracts: dict[str, SourceContract]
    artifacts: dict[str, ArtifactContract]

    def contract(self, source: str) -> SourceContract:
        try:
            return self.contracts[source]
        except KeyError:
            raise UnknownSourceError(
                f"{source!r} has no contract in config/source_clock.yaml — add one "
                f"(expected_interval_h, lag_window_h, deadline_h, owner). Known: "
                f"{sorted(self.contracts)}"
            ) from None


def _require(block: dict, fields: tuple[str, ...], where: str) -> None:
    missing = [f for f in fields if f not in block]
    if missing:
        raise ValueError(f"source_clock.yaml: {where} missing {missing}")


def load_clock(path: pathlib.Path | None = None) -> SourceClock:
    p = path or DEFAULT_CLOCK_PATH
    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict) or "as_of" not in raw or "sources" not in raw:
        raise ValueError(f"{p}: expected a mapping with 'as_of' and 'sources' keys")
    contracts = {}
    for name, block in (raw["sources"] or {}).items():
        _require(block, _REQUIRED_SOURCE_FIELDS, f"source {name!r}")
        contracts[name] = SourceContract(
            expected_interval_h=float(block["expected_interval_h"]),
            lag_window_h=float(block["lag_window_h"]),
            deadline_h=float(block["deadline_h"]),
            owner=str(block["owner"]),
            expected_season=(
                int(block["expected_season"])
                if block.get("expected_season") is not None
                else None
            ),
        )
    artifacts = {}
    for name, block in (raw.get("artifacts") or {}).items():
        _require(block, _REQUIRED_ARTIFACT_FIELDS, f"artifact {name!r}")
        artifacts[name] = ArtifactContract(
            name=name,
            glob=str(block["glob"]),
            due_weekday=str(block["due_weekday"]).lower(),
            active_from=block["active_from"],
        )
    return SourceClock(as_of=raw["as_of"], contracts=contracts, artifacts=artifacts)


def state(
    source: str, status: str, age_h: float, clock: SourceClock | None = None
) -> SourceState:
    """Three-state health for one source, from (status, age) TOGETHER.

    `status` is a raw.ingest_runs status ('success', 'failed', 'running',
    'sanity_warned', 'sanity_failed') or the string 'success' for
    file-derived sources like `backup`.
    """
    contract = (clock or load_clock()).contract(source)
    if age_h is None or age_h < 0:
        raise ValueError(
            f"{source}: age_h must be a non-negative number, got {age_h!r}"
        )
    if status not in _OK_STATUSES and status not in _SUSPECT_STATUSES:
        return SourceState.BROKEN
    if age_h > contract.deadline_h:
        return SourceState.BROKEN
    if status in _SUSPECT_STATUSES or age_h > contract.fresh_within_h:
        return SourceState.KNOWN_LAGGING
    return SourceState.OK


def is_alarming(s: SourceState) -> bool:
    """Only BROKEN gets a banner. Structural lag gets a quiet line (R12)."""
    return s is SourceState.BROKEN
