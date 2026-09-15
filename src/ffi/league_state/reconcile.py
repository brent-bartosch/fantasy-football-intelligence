"""API-vs-manual reconciliation (ADR Domain 1/6).

Manual capture is the PRIMARY backend; Yahoo is a backfill optimization. This
module diff-pass compares the two and logs every difference — it NEVER writes,
never merges, never silently prefers one side. A reader must see that the two
backends disagree before either one is trusted.

A reconciliation key is backend-agnostic: `(league_id, season, week, kind,
team_id, player_id)`. Backend-specific ids (`source_transaction_id`) are
deliberately NOT part of the key, because the same real-world move has
different ids in the manual file and the Yahoo feed.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from ffi.league_state import RosterRow, Transaction


@dataclass(frozen=True)
class Diff:
    outcome: str  # 'manual_only' | 'api_only' | 'mismatch'
    key: tuple
    manual: Transaction | None
    api: Transaction | None

    def describe(self) -> str:
        if self.outcome == "manual_only":
            return f"manual_only: {self.key} (no Yahoo counterpart)"
        if self.outcome == "api_only":
            return f"api_only: {self.key} (no manual capture)"
        # `mismatch` is only ever constructed with both sides present.
        assert self.manual is not None and self.api is not None
        return (
            f"mismatch: {self.key} — manual ts={self.manual.ts} "
            f"({self.manual.ts_precision}) vs api ts={self.api.ts} "
            f"({self.api.ts_precision})"
        )


@dataclass(frozen=True)
class ReconcileResult:
    matched: int
    diffs: tuple[Diff, ...]


@dataclass(frozen=True)
class RosterDiff:
    """Per-team ownership diff between a manual capture and an API fetch."""

    team_id: int
    matched: int
    manual_only: tuple[str, ...]  # player_ids the capture has that the API lacks
    api_only: tuple[str, ...]  # player_ids the API has that the capture lacks

    @property
    def clean(self) -> bool:
        return not self.manual_only and not self.api_only


def _key(t: Transaction) -> tuple:
    player = (t.payload or {}).get("player_id")
    return (t.league_id, t.season, t.week, t.kind, t.team_id, player)


def _ts_equal(a: Transaction, b: Transaction) -> bool:
    # ts_precision 'date' means "sometime that day"; compare the calendar day,
    # not the instant, so a midnight convention on one side does not false-mismatch.
    if a.ts_precision == "date" or b.ts_precision == "date":
        return a.ts is not None and b.ts is not None and a.ts.date() == b.ts.date()
    return a.ts == b.ts


def reconcile(
    manual: Sequence[Transaction], api: Sequence[Transaction]
) -> ReconcileResult:
    """Diff two backend views of the same week. Order-independent, no writes.

    A `mismatch` is emitted when the same real-world move appears on both sides
    with a different timestamp. `manual_only` is EXPECTED (manual is primary),
    but still listed — the operator must be able to see what the backfill would
    have added before deciding to record it.
    """
    api_by_key = {_key(t): t for t in api}
    manual_by_key = {_key(t): t for t in manual}
    diffs: list[Diff] = []
    matched = 0

    for key, m in manual_by_key.items():
        a = api_by_key.get(key)
        if a is None:
            diffs.append(Diff("manual_only", key, m, None))
        elif _ts_equal(m, a):
            matched += 1
        else:
            diffs.append(Diff("mismatch", key, m, a))

    for key, a in api_by_key.items():
        if key not in manual_by_key:
            diffs.append(Diff("api_only", key, None, a))

    diffs.sort(key=lambda d: (d.outcome, d.key))
    return ReconcileResult(matched=matched, diffs=tuple(diffs))


def reconcile_rosters(
    manual: Sequence[RosterRow], api: Sequence[RosterRow]
) -> list[RosterDiff]:
    """Per-team ownership diff between the manual capture and the API backfill.

    Callers normalize ids to ONE namespace first (the crosswalk maps the
    sleeper/gsis fallback ids to yahoo ids) — this function compares
    (team_id, player_id) sets and writes nothing, ever.
    """
    teams = sorted({r.team_id for r in manual} | {r.team_id for r in api})
    out: list[RosterDiff] = []
    for t in teams:
        m = {r.player_id for r in manual if r.team_id == t}
        a = {r.player_id for r in api if r.team_id == t}
        out.append(
            RosterDiff(
                team_id=t,
                matched=len(m & a),
                manual_only=tuple(sorted(m - a)),
                api_only=tuple(sorted(a - m)),
            )
        )
    return out
