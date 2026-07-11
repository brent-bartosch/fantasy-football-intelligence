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
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass

from ffi.sim.draft import run_draft, snake_position
from ffi.sim.opponent import OpponentParams
from ffi.sim.priors import SlotPriors, _band
from ffi.sim.strategy import StrategyParams, make_strategy_fn

OUR_FRANCHISE_SLOT = 12

# Default fit grid (Task 4 brief): 6 x 4 x 3 = 72 candidates. s0 scales a
# slot's QB prior share while it holds 0 QBs, s1 while it holds 1, s2 while it
# holds >=2 (the last entry extends past the tuple's end -- see OpponentParams).
DEFAULT_FIT_GRID: dict = {
    "s0": (1.0, 1.5, 2.0, 3.0, 4.0, 6.0),
    "s1": (0.75, 1.0, 1.5, 2.0),
    "s2": (0.5, 0.75, 1.0),
}


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

    `opponent_params`, when given, is threaded through to every `run_draft`
    call (and from there to every `opponent_pick`); `None` uses
    `ffi.sim.opponent.DEFAULT_OPPONENT_PARAMS` (bit-identical legacy
    behavior).
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
            opponent_params=opponent_params,
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


def _per_slot_qb1_mae(measured: QbTimingMeasurement, historical: dict) -> float:
    """Mean absolute error between measured and historical QB1 round, over the
    opponent slots present in BOTH (slot 12, our seat, is absent from
    `measured` by construction). NaN measured/None historical entries are
    skipped -- a slot that never took a QB in the sample carries no signal."""
    errs = []
    for slot, m in measured.per_slot.items():
        h = historical.get(slot)
        if h is None:
            continue
        mq, hq = m.get("qb1"), h.get("qb1")
        if mq is None or hq is None or math.isnan(mq):
            continue
        errs.append(abs(mq - hq))
    return statistics.mean(errs) if errs else float("nan")


def fit_qb_need_scale(
    pool,
    priors: SlotPriors,
    historical: dict,
    n_drafts: int,
    base_seed: int,
    grid: dict | None = None,
) -> tuple:
    """Grid-search the QB `pos_need_scale` tuple `(s0, s1, s2)` that best
    reproduces `historical` QB-timing under `pool`/`priors`.

    Every candidate runs `measure_qb_timing` with the SAME `base_seed`
    (common random numbers -- the seat-permutation and opponent draws are
    paired across candidates, so objective differences reflect the scale
    change, not sampling noise). Objective (pinned in the Task 4 brief):

        3*|m1-h1| + 2*|m2-h2| + 1*|m3-h3| + 0.5 * per_slot_qb1_MAE

    with `h1,h2,h3` the seasons-weighted historical league means and
    `m1,m2,m3` the measured opponent league means. Returns
    `(best_params, trials)` where `trials` is every candidate's
    `{"scale", "qb1", "qb2", "qb3", "per_slot_qb1_mae", "objective"}` dict,
    sorted by objective ascending; `best_params` wraps `trials[0]["scale"]`.
    """
    grid = grid or DEFAULT_FIT_GRID
    h1 = _seasons_weighted_mean(historical, "qb1")
    h2 = _seasons_weighted_mean(historical, "qb2")
    h3 = _seasons_weighted_mean(historical, "qb3")

    trials = []
    for s0 in grid["s0"]:
        for s1 in grid["s1"]:
            for s2 in grid["s2"]:
                scale = (s0, s1, s2)
                params = OpponentParams(pos_need_scale=(("QB", scale),))
                m = measure_qb_timing(
                    pool, priors, n_drafts, base_seed, opponent_params=params
                )
                m1, m2, m3 = m.league_means
                per_slot_mae = _per_slot_qb1_mae(m, historical)
                objective = (
                    3 * abs(m1 - h1)
                    + 2 * abs(m2 - h2)
                    + 1 * abs(m3 - h3)
                    + 0.5 * per_slot_mae
                )
                trials.append(
                    {
                        "scale": scale,
                        "qb1": m1,
                        "qb2": m2,
                        "qb3": m3,
                        "per_slot_qb1_mae": per_slot_mae,
                        "objective": objective,
                    }
                )

    trials.sort(key=lambda t: t["objective"])
    best_params = OpponentParams(pos_need_scale=(("QB", trials[0]["scale"]),))
    return best_params, trials
