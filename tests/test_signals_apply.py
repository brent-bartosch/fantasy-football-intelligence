"""Task 15: capped human-confirmed adjustments (ffi.signals_apply).

Cap semantics under test: ±10% per signal, ±20% cumulative per player,
at most one adjustment per (xwalk_id, day). Every violation must raise
AdjustmentCapError and leave zero new rows in signals.adjustments (fail-loud,
no partial writes).
"""
import dataclasses
import datetime
import json

import pytest

from ffi.scoring.config import ensure_config_in_db, load_config_v1
from ffi.sim.pool import build_pool
from ffi.signals_apply import (
    AdjustmentCapError,
    adjusted_pool,
    apply_adjustment,
    cumulative_pct,
)

CFG = load_config_v1()
SCENARIO = "qb_hoard_12"


def _seed_xwalk(db, name, fantasypros_id=None, sleeper_id=None, position=None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk (name, fantasypros_id, sleeper_id, position) "
            "VALUES (%s, %s, %s, %s) RETURNING xwalk_id",
            (
                name,
                str(fantasypros_id) if fantasypros_id is not None else None,
                sleeper_id,
                position,
            ),
        )
        return cur.fetchone()[0]


def _seed_signal(db, xwalk_id=None, status="confirmed", signal_type="news", title="t"):
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO signals.signals
               (source, external_id, xwalk_id, signal_type, title, evidence_url, payload, status)
               VALUES ('fp_news', %s, %s, %s, %s, 'http://x', %s, %s)
               RETURNING signal_id""",
            (
                f"ext-{title}-{xwalk_id}-{status}",
                xwalk_id,
                signal_type,
                title,
                json.dumps({}),
                status,
            ),
        )
        return cur.fetchone()[0]


def _seed_adjustment(db, signal_id, xwalk_id, pct, applied_at):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO signals.adjustments (signal_id, xwalk_id, pct, applied_at) "
            "VALUES (%s, %s, %s, %s)",
            (signal_id, xwalk_id, pct, applied_at),
        )


def _count_adjustments(db):
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM signals.adjustments")
        return cur.fetchone()[0]


class TestApplyAdjustmentHappyPath:
    def test_returns_adjustment_id_and_inserts_row(self, db):
        xwalk_id = _seed_xwalk(db, "Player A", fantasypros_id=1)
        signal_id = _seed_signal(db, xwalk_id=xwalk_id)
        db.commit()

        adjustment_id = apply_adjustment(db, signal_id, 0.05, note="test")
        db.commit()

        assert isinstance(adjustment_id, int)
        with db.cursor() as cur:
            cur.execute(
                "SELECT signal_id, xwalk_id, pct, note FROM signals.adjustments WHERE adjustment_id = %s",
                (adjustment_id,),
            )
            row = cur.fetchone()
        assert row == (signal_id, xwalk_id, 0.05, "test")


class TestApplyAdjustmentRefusals:
    def test_pct_over_per_signal_cap_raises_and_writes_nothing(self, db):
        xwalk_id = _seed_xwalk(db, "Player B", fantasypros_id=2)
        signal_id = _seed_signal(db, xwalk_id=xwalk_id)
        db.commit()

        with pytest.raises(AdjustmentCapError):
            apply_adjustment(db, signal_id, 0.11)
        assert _count_adjustments(db) == 0

    def test_negative_pct_over_per_signal_cap_raises(self, db):
        xwalk_id = _seed_xwalk(db, "Player B2", fantasypros_id=22)
        signal_id = _seed_signal(db, xwalk_id=xwalk_id)
        db.commit()

        with pytest.raises(AdjustmentCapError):
            apply_adjustment(db, signal_id, -0.15)
        assert _count_adjustments(db) == 0

    def test_cumulative_cap_across_two_signals_raises(self, db):
        """Two existing adjustments on different days (0.10 + 0.05 = 0.15,
        each within the per-signal cap on its own) plus a new one today
        (0.10) would sum to 0.25, over the ±20% cumulative cap -- even though
        the new adjustment is on yet another day (so the per-day cap doesn't
        trip)."""
        xwalk_id = _seed_xwalk(db, "Player C", fantasypros_id=3)
        older_signal_id = _seed_signal(db, xwalk_id=xwalk_id, title="older")
        old_signal_id = _seed_signal(db, xwalk_id=xwalk_id, title="old")
        two_days_ago = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(days=2)
        yesterday = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=1
        )
        _seed_adjustment(db, older_signal_id, xwalk_id, 0.10, two_days_ago)
        _seed_adjustment(db, old_signal_id, xwalk_id, 0.05, yesterday)
        new_signal_id = _seed_signal(db, xwalk_id=xwalk_id, title="new")
        db.commit()

        with pytest.raises(AdjustmentCapError):
            apply_adjustment(db, new_signal_id, 0.10)
        assert _count_adjustments(db) == 2  # only the two seeded ones

    def test_two_adjustments_same_day_raises_per_day_cap(self, db):
        xwalk_id = _seed_xwalk(db, "Player D", fantasypros_id=4)
        signal_1 = _seed_signal(db, xwalk_id=xwalk_id, title="first")
        signal_2 = _seed_signal(db, xwalk_id=xwalk_id, title="second")
        db.commit()

        apply_adjustment(db, signal_1, 0.05)
        db.commit()

        with pytest.raises(AdjustmentCapError):
            apply_adjustment(db, signal_2, 0.05)
        assert _count_adjustments(db) == 1

    def test_unconfirmed_signal_refuses(self, db):
        xwalk_id = _seed_xwalk(db, "Player E", fantasypros_id=5)
        signal_id = _seed_signal(db, xwalk_id=xwalk_id, status="pending")
        db.commit()

        with pytest.raises(AdjustmentCapError):
            apply_adjustment(db, signal_id, 0.05)
        assert _count_adjustments(db) == 0

    def test_denied_signal_refuses(self, db):
        xwalk_id = _seed_xwalk(db, "Player E2", fantasypros_id=52)
        signal_id = _seed_signal(db, xwalk_id=xwalk_id, status="denied")
        db.commit()

        with pytest.raises(AdjustmentCapError):
            apply_adjustment(db, signal_id, 0.05)
        assert _count_adjustments(db) == 0

    def test_unmatched_signal_refuses(self, db):
        signal_id = _seed_signal(db, xwalk_id=None)
        db.commit()

        with pytest.raises(AdjustmentCapError):
            apply_adjustment(db, signal_id, 0.05)
        assert _count_adjustments(db) == 0

    def test_nonexistent_signal_refuses(self, db):
        with pytest.raises(AdjustmentCapError):
            apply_adjustment(db, 999999, 0.05)
        assert _count_adjustments(db) == 0

    def test_exactly_ten_percent_is_allowed(self, db):
        xwalk_id = _seed_xwalk(db, "Player F", fantasypros_id=6)
        signal_id = _seed_signal(db, xwalk_id=xwalk_id)
        db.commit()

        adjustment_id = apply_adjustment(db, signal_id, 0.10)
        db.commit()
        assert isinstance(adjustment_id, int)

    def test_exactly_twenty_percent_cumulative_is_allowed(self, db):
        xwalk_id = _seed_xwalk(db, "Player G", fantasypros_id=7)
        old_signal_id = _seed_signal(db, xwalk_id=xwalk_id, title="old")
        yesterday = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=1
        )
        _seed_adjustment(db, old_signal_id, xwalk_id, 0.10, yesterday)
        new_signal_id = _seed_signal(db, xwalk_id=xwalk_id, title="new")
        db.commit()

        adjustment_id = apply_adjustment(db, new_signal_id, 0.10)
        db.commit()
        assert isinstance(adjustment_id, int)


class TestCumulativePct:
    def test_sums_per_player(self, db):
        xwalk_1 = _seed_xwalk(db, "Player H", fantasypros_id=8)
        xwalk_2 = _seed_xwalk(db, "Player I", fantasypros_id=9)
        s1 = _seed_signal(db, xwalk_id=xwalk_1, title="s1")
        s2 = _seed_signal(db, xwalk_id=xwalk_1, title="s2")
        s3 = _seed_signal(db, xwalk_id=xwalk_2, title="s3")
        yesterday = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=1
        )
        _seed_adjustment(db, s1, xwalk_1, 0.05, yesterday)
        db.commit()
        apply_adjustment(db, s2, 0.05)
        apply_adjustment(db, s3, -0.03)
        db.commit()

        cum = cumulative_pct(db)
        assert cum[xwalk_1] == pytest.approx(0.10)
        assert cum[xwalk_2] == pytest.approx(-0.03)

    def test_empty_when_no_adjustments(self, db):
        assert cumulative_pct(db) == {}


def _seed_full_pool_with_adjustable_player(db, real_adp_count=200):
    """Same shape as test_sim_pool's seeding helper (all six positions,
    >=200 real-ADP players, QB/K/DEF gates satisfied), plus one extra WR
    ('Adjustable WR') the adjusted_pool tests shift via a confirmed signal."""
    ensure_config_in_db(db, CFG)
    records = []

    for i in range(8):
        xid = _seed_xwalk(db, f"QB{i}", sleeper_id=f"q{i}", position="QB")
        _insert_value(db, xid, "QB", 300 - i, 100 - i)
        records.append(_sleeper_rec(f"q{i}", "QB", 1.0 + i))

    remaining = real_adp_count - 8
    adjustable_xwalk_id = None
    for i in range(remaining):
        xid = _seed_xwalk(db, f"WR{i}", sleeper_id=f"w{i}", position="WR")
        _insert_value(db, xid, "WR", 200 - i * 0.1, 50 - i * 0.05)
        records.append(_sleeper_rec(f"w{i}", "WR", 9.0 + i))
        if i == 5:
            adjustable_xwalk_id = xid

    for i in range(5):
        xid = _seed_xwalk(db, f"RB{i}", sleeper_id=f"r{i}", position="RB")
        _insert_value(db, xid, "RB", 150 - i, 40 - i)
        records.append(_sleeper_rec(f"r{i}", "RB", 999))
    for i in range(5):
        xid = _seed_xwalk(db, f"TE{i}", sleeper_id=f"t{i}", position="TE")
        _insert_value(db, xid, "TE", 120 - i, 20 - i)
        records.append(_sleeper_rec(f"t{i}", "TE", 999))
    for i in range(25):
        xid = _seed_xwalk(db, f"K{i}", sleeper_id=f"k{i}", position="K")
        _insert_value(db, xid, "K", 90 - i, 5 - i * 0.1)
        records.append(_sleeper_rec(f"k{i}", "K", 999))
    for i in range(25):
        abbr = f"D{i:02d}"
        xid = _seed_xwalk(db, f"Defense{i}", sleeper_id=abbr, position="DEF")
        _insert_value(db, xid, "DEF", 80 - i, 4 - i * 0.1)
        records.append(_sleeper_rec(abbr, "DEF", 999))

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.sleeper_projections (season, week, payload) VALUES (%s, NULL, %s)",
            (2026, json.dumps(records)),
        )
    db.commit()
    return adjustable_xwalk_id


def _insert_value(db, xwalk_id, position, proj_points, vorp, tier=1, scenario=SCENARIO):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO valuation.player_value"
            " (config_version, scenario, xwalk_id, position, proj_points, vorp, tier, params)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                CFG.version,
                scenario,
                xwalk_id,
                position,
                proj_points,
                vorp,
                tier,
                json.dumps({}),
            ),
        )


def _sleeper_rec(player_id, position, adp_2qb):
    return {
        "player_id": player_id,
        "player": {"position": position},
        "stats": {"adp_2qb": adp_2qb},
    }


class TestAdjustedPool:
    def test_no_adjustments_matches_build_pool(self, db):
        _seed_full_pool_with_adjustable_player(db)
        expected = build_pool(db, SCENARIO)
        actual = adjusted_pool(db, SCENARIO)
        assert actual == expected

    def test_shifts_exactly_the_adjusted_player_and_preserves_ordering(self, db):
        xwalk_id = _seed_full_pool_with_adjustable_player(db)
        signal_id = _seed_signal(db, xwalk_id=xwalk_id, title="hype")
        db.commit()
        apply_adjustment(db, signal_id, 0.10)
        db.commit()

        baseline = {p.ref: p for p in build_pool(db, SCENARIO)}
        shifted = adjusted_pool(db, SCENARIO)
        shifted_by_ref = {p.ref: p for p in shifted}

        assert set(shifted_by_ref) == set(baseline)

        target_ref = None
        for ref, p in baseline.items():
            with db.cursor() as cur:
                cur.execute(
                    "SELECT xwalk_id FROM public.player_id_xwalk WHERE sleeper_id = %s",
                    (ref,),
                )
                if cur.fetchone()[0] == xwalk_id:
                    target_ref = ref
                    break
        assert target_ref is not None

        base_p = baseline[target_ref]
        adj_p = shifted_by_ref[target_ref]
        assert adj_p.proj_points == pytest.approx(base_p.proj_points * 1.10)
        assert adj_p.vorp == pytest.approx(base_p.vorp + base_p.proj_points * 0.10)

        # Every other player is untouched.
        for ref, base_p in baseline.items():
            if ref == target_ref:
                continue
            other = shifted_by_ref[ref]
            assert other.proj_points == pytest.approx(base_p.proj_points)
            assert other.vorp == pytest.approx(base_p.vorp)

        # Ordering convention preserved: real-ADP prefix ascending, then
        # None-ADP suffix vorp-descending.
        real_adp = [p.adp is not None for p in shifted]
        first_none = real_adp.index(False) if False in real_adp else len(real_adp)
        assert all(real_adp[:first_none])
        assert not any(real_adp[first_none:])
        real_vals = [p.adp for p in shifted[:first_none]]
        assert real_vals == sorted(real_vals)
