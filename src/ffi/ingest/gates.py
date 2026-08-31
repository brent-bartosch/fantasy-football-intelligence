"""Semantic sanity gates at the ingest boundary (ADR Domain 1).

Pure functions over already-fetched data. Imports nothing internal — a gate
that imported the feed it validates could not be trusted to fail
independently of it (ARCHITECTURE §3 forbids gates -> ingest/usage/
league_state, in both directions).

Every check RAISES on failure. Nothing here returns a boolean, logs and
continues, or degrades: the CALLER picks the mode by catching or not
catching (BaseIngester.sanity_mode). This system already guards data
*absence* well and degraded *presence* not at all — R1 is one live proof and
the Aug-2026 Sleeper `adp_2qb` semantic drift is a second. The pattern is
lifted from src/ffi/sim/pool.py's 2QB gate, which raises ValueError rather
than emitting a plausible-looking degraded pool.
"""
from collections.abc import Mapping, Sequence

MIN_RANK_CORRELATION = 0.85
DEFAULT_MIN_OVERLAP = 20


class SanityGateError(Exception):
    """A payload passed schema validation but failed a semantic gate."""


_ID_KEYS = ("player_id", "id")


def _record_label(row: Mapping, index: int) -> str:
    """Name a row by its own id if it has one, else by position."""
    for key in _ID_KEYS:
        if key in row:
            return f"{key}={row[key]!r}"
    return f"record #{index}"


def _as_float(value, *, feed: str, field: str, where: str) -> float:
    """float() whose failure is a gate failure, not a bare ValueError.

    A count that arrives as `"n/a"` or a dict IS upstream drift — exactly what
    these gates exist to catch — so it must surface as SanityGateError, which
    the caller's sanity_mode routes, naming the offending record and value.
    An uncontextualised ValueError from inside a comprehension tells the
    operator nothing and bypasses the warn/fail decision entirely.
    """
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SanityGateError(
            f"{feed}: non-numeric {field}={value!r} on {where} — a value that "
            f"cannot be compared is upstream type drift, not a parse bug"
        ) from exc


def union_keys(records: Sequence[Mapping]) -> list[str]:
    """Every key seen across ALL records, not just the first one's.

    A field-set built from `records[0]` only catches drift that happens to
    land on record 0. Sleeper's payloads are ragged by design (deep-bench
    records carry ADP metadata only), so the sole comparable unit is the
    union — a key that vanishes from record 500 and survives on record 0 is
    still the adp_2qb failure mode.
    """
    keys: set[str] = set()
    for rec in records:
        keys.update(rec)
    return sorted(keys)


def check_fieldset(
    prev: Sequence[str] | None, curr: Sequence[str], *, feed: str
) -> None:
    """Field-set diff vs the prior snapshot. Any difference is drift.

    Added keys are as much a signal as removed ones: the adp_2qb incident
    was a cohort/semantic change, not a deletion. `prev=None` (no prior
    snapshot) passes — there is nothing to compare against on day one.
    """
    if prev is None:
        return
    prev_set, curr_set = set(prev), set(curr)
    removed = sorted(prev_set - curr_set)
    added = sorted(curr_set - prev_set)
    if removed or added:
        raise SanityGateError(
            f"{feed}: field-set drift vs prior snapshot — removed={removed} added={added}. "
            f"Schema drift is a hard signal (ADR D1; the Aug-2026 adp_2qb "
            f"semantic drift is the template for this check)."
        )


def _average_ranks(values: Sequence[float]) -> list[float]:
    """Ranks with ties averaged — the standard Spearman tie correction."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def check_rank_correlation(
    prev: Mapping[str, float],
    curr: Mapping[str, float],
    *,
    feed: str,
    min_rho: float = MIN_RANK_CORRELATION,
    min_overlap: int = DEFAULT_MIN_OVERLAP,
) -> float:
    """Spearman rank correlation over the key intersection. Raises below
    `min_rho`. Implemented here rather than via scipy so a degenerate input
    raises loudly instead of returning nan."""
    keys = sorted(set(prev) & set(curr))
    if len(keys) < min_overlap:
        raise SanityGateError(
            f"{feed}: only {len(keys)} keys overlap between snapshots "
            f"(need >= {min_overlap}) — a cohort this different is drift, not noise"
        )
    a = _average_ranks(
        [
            _as_float(prev[k], feed=feed, field="prior value", where=f"key {k!r}")
            for k in keys
        ]
    )
    b = _average_ranks(
        [
            _as_float(curr[k], feed=feed, field="current value", where=f"key {k!r}")
            for k in keys
        ]
    )
    n = len(keys)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a == 0 or var_b == 0:
        raise SanityGateError(
            f"{feed}: rank correlation undefined — all {n} values are tied in "
            f"one snapshot; a flat distribution is itself a failure"
        )
    rho = cov / ((var_a**0.5) * (var_b**0.5))
    if rho < min_rho:
        raise SanityGateError(
            f"{feed}: week-over-week rank correlation {rho:.3f} < {min_rho} over "
            f"{n} shared keys — the ordering changed more than a real feed can"
        )
    return rho


def check_nonzero_coverage(
    rows: Sequence[Mapping], *, feed: str, value_key: str, min_players: int
) -> int:
    """Count rows whose `value_key` is present and strictly positive.

    Guards the failure the ratio checks structurally cannot see: a collapsed
    population where numerator and denominator shrink together and the ratio
    still reads 100% (the R5 finding in ffi.ingest.sleeper).
    """
    count = 0
    for index, row in enumerate(rows):
        value = row.get(value_key)
        if value is None:
            continue
        coerced = _as_float(
            value, feed=feed, field=repr(value_key), where=_record_label(row, index)
        )
        if coerced > 0:
            count += 1
    if count < min_players:
        raise SanityGateError(
            f"{feed}: only {count} row(s) carry a positive {value_key!r} "
            f"(floor {min_players}) — population collapse, not a quiet day"
        )
    return count
