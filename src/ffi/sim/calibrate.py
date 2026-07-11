"""Opponent QB-timing measurement harness (Phase 4 Task 2).

Measures how early the simulator's opponents (all franchise slots except our
own, fixed at slot 12) take their 1st/2nd/3rd QB under a given pool +
`SlotPriors`, by running `n_drafts` full seeded drafts and aggregating
`run_draft`'s pick log. This is a pure measurement pass -- no model change:
`opponent_pick`/`SlotPriors` are exactly what Task 1-9 already shipped. Task 3
adds an `OpponentParams` mechanism and threads `opponent_params` through
`run_draft` for real; Task 4 fits it using these functions as the objective.

`historical_qb_timing` wraps `ffi.history.mining.qb_timing_by_slot` (the
existing, already-verified SQL -- never reimplemented here) into the same
per-slot shape so `timing_gap_report` can diff measured-vs-historical
directly.
"""
import statistics
from collections import defaultdict
from dataclasses import dataclass

from ffi.sim.draft import run_draft, snake_position
from ffi.sim.priors import SlotPriors, _band
from ffi.sim.strategy import StrategyParams, make_strategy_fn

OUR_FRANCHISE_SLOT = 12


@dataclass(frozen=True)
class QbTimingMeasurement:
    n_drafts: int
    league_means: tuple  # (qb1_mean, qb2_mean, qb3_mean) round, opponents only
    per_slot: dict  # slot(1-11) -> {"qb1":.., "qb2":.., "qb3":.., "n":..}
    pos_share_by_band: dict  # (band, pos) -> share, opponents only


def _mean_or_nan(values: list) -> float:
    return statistics.mean(values) if values else float("nan")


def measure_qb_timing(
    pool, priors: SlotPriors, n_drafts: int, base_seed: int, opponent_params=None
) -> QbTimingMeasurement:
    """Run `n_drafts` seeded drafts (seeds `base_seed .. base_seed+n_drafts-1`)
    and aggregate opponent (franchise_slot != 12) QB-round timing.

    `opponent_params` is accepted and ignored when None -- Task 3 threads it
    through `run_draft` for real; the kwarg exists now so Task 4's fit loop
    doesn't need a signature change here.
    """
    our_pick_fn = make_strategy_fn(StrategyParams())

    # Pooled samples across every opponent seat x draft, per QB ordinal --
    # separate denominators, since a seat with no 2nd/3rd QB in a given draft
    # contributes nothing to that ordinal's mean.
    league_qb_rounds: tuple = ([], [], [])
    # slot -> ([qb1 rounds], [qb2 rounds], [qb3 rounds])
    per_slot_qb_rounds: dict = defaultdict(lambda: ([], [], []))
    per_slot_n: dict = defaultdict(int)
    band_pos_counts: dict = defaultdict(int)
    band_totals: dict = defaultdict(int)

    for i in range(n_drafts):
        seed = base_seed + i
        result = run_draft(
            pool,
            priors,
            our_pick_fn,
            seed,
            our_franchise_slot=OUR_FRANCHISE_SLOT,
            our_position=None,
        )

        qb_n_by_slot: dict = defaultdict(int)
        seats_seen_this_draft: set = set()
        for pick in result.picks:
            fslot = pick["franchise_slot"]
            if fslot == OUR_FRANCHISE_SLOT:
                continue
            seats_seen_this_draft.add(fslot)

            round_ = snake_position(pick["overall"])[0]
            band = _band(round_)
            band_totals[band] += 1
            band_pos_counts[(band, pick["pos"])] += 1

            if pick["pos"] == "QB":
                qb_n_by_slot[fslot] += 1
                ordinal = qb_n_by_slot[fslot]
                if ordinal <= 3:
                    league_qb_rounds[ordinal - 1].append(round_)
                    per_slot_qb_rounds[fslot][ordinal - 1].append(round_)

        for fslot in seats_seen_this_draft:
            per_slot_n[fslot] += 1

    league_means = (
        _mean_or_nan(league_qb_rounds[0]),
        _mean_or_nan(league_qb_rounds[1]),
        _mean_or_nan(league_qb_rounds[2]),
    )

    per_slot = {
        slot: {
            "qb1": _mean_or_nan(rounds[0]),
            "qb2": _mean_or_nan(rounds[1]),
            "qb3": _mean_or_nan(rounds[2]),
            "n": per_slot_n[slot],
        }
        for slot, rounds in per_slot_qb_rounds.items()
    }

    pos_share_by_band = {}
    for band, total in band_totals.items():
        for (b, pos), count in band_pos_counts.items():
            if b == band:
                pos_share_by_band[(band, pos)] = count / total

    return QbTimingMeasurement(
        n_drafts=n_drafts,
        league_means=league_means,
        per_slot=per_slot,
        pos_share_by_band=pos_share_by_band,
    )


def historical_qb_timing(conn) -> dict:
    """slot -> {"qb1", "qb2", "qb3", "seasons"}, from
    `ffi.history.mining.qb_timing_by_slot` (reused verbatim, never
    reimplemented). Fail-loud: raises `ValueError` if the query returns no
    rows -- no empty-dict default."""
    from ffi.history.mining import qb_timing_by_slot

    rows = qb_timing_by_slot(conn)
    if not rows:
        raise ValueError(
            "historical_qb_timing: qb_timing_by_slot(conn) returned zero rows"
        )

    def _to_float(v):
        return None if v is None else float(v)

    return {
        row["slot"]: {
            "qb1": _to_float(row["qb1_round"]),
            "qb2": _to_float(row["qb2_round"]),
            "qb3": _to_float(row["qb3_round"]),
            # psycopg2 returns count(...) as int, but normalize defensively
            # since it feeds arithmetic in `_seasons_weighted_mean`.
            "seasons": _to_float(row["seasons"]),
        }
        for row in rows
    }


def _seasons_weighted_mean(historical: dict, key: str) -> float:
    weighted_sum = 0.0
    weight_total = 0.0
    for slot_data in historical.values():
        value = slot_data.get(key)
        seasons = slot_data.get("seasons")
        if value is None or not seasons:
            continue
        weighted_sum += value * seasons
        weight_total += seasons
    return weighted_sum / weight_total if weight_total > 0 else float("nan")


def timing_gap_report(measured: QbTimingMeasurement, historical: dict) -> str:
    """Markdown report: league-mean table (measured vs historical vs delta
    for QB1/2/3, historical league means seasons-weighted over slots),
    per-slot table sorted by slot, and the top-10 `pos_share_by_band`
    deviations from a uniform 1/6 baseline (the audit signal for how far the
    realized position mix drifted from an unconcentrated prior -- no
    historical position-share baseline is available to diff against, since
    `historical_qb_timing`'s shape carries only QB-timing data)."""
    lines = ["# QB-timing gap report", ""]

    lines.append("## League means (round, opponents only)")
    lines.append("")
    lines.append("| ordinal | measured | historical | delta |")
    lines.append("|---|---|---|---|")
    hist_means = (
        _seasons_weighted_mean(historical, "qb1"),
        _seasons_weighted_mean(historical, "qb2"),
        _seasons_weighted_mean(historical, "qb3"),
    )
    for label, m_val, h_val in zip(
        ("QB1", "QB2", "QB3"), measured.league_means, hist_means
    ):
        delta = m_val - h_val
        lines.append(f"| {label} | {m_val:.2f} | {h_val:.2f} | {delta:+.2f} |")
    lines.append("")

    lines.append("## Per-slot (round, opponents only where measured)")
    lines.append("")
    lines.append(
        "| slot | m.qb1 | m.qb2 | m.qb3 | m.n | h.qb1 | h.qb2 | h.qb3 | h.seasons |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    all_slots = sorted(set(measured.per_slot) | set(historical))
    for slot in all_slots:
        m = measured.per_slot.get(slot)
        h = historical.get(slot)

        def fmt(v):
            return "-" if v is None else f"{v:.2f}"

        m_qb1 = fmt(m["qb1"]) if m else "-"
        m_qb2 = fmt(m["qb2"]) if m else "-"
        m_qb3 = fmt(m["qb3"]) if m else "-"
        m_n = str(m["n"]) if m else "-"
        h_qb1 = fmt(h["qb1"]) if h else "-"
        h_qb2 = fmt(h["qb2"]) if h else "-"
        h_qb3 = fmt(h["qb3"]) if h else "-"
        h_seasons = str(int(h["seasons"])) if h else "-"
        lines.append(
            f"| {slot} | {m_qb1} | {m_qb2} | {m_qb3} | {m_n} | "
            f"{h_qb1} | {h_qb2} | {h_qb3} | {h_seasons} |"
        )
    lines.append("")

    lines.append("## Top-10 pos-share deviations from uniform (1/6), opponents only")
    lines.append("")
    lines.append("| band | pos | share | deviation |")
    lines.append("|---|---|---|---|")
    uniform = 1.0 / 6.0
    deviations = sorted(
        measured.pos_share_by_band.items(),
        key=lambda kv: abs(kv[1] - uniform),
        reverse=True,
    )
    for (band, pos), share in deviations[:10]:
        lines.append(f"| {band} | {pos} | {share:.3f} | {share - uniform:+.3f} |")
    lines.append("")

    return "\n".join(lines)
