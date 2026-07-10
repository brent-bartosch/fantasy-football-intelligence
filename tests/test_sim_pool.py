import json

import pytest

from ffi.scoring.config import ensure_config_in_db, load_config_v1
from ffi.sim.pool import build_pool

CFG = load_config_v1()
SCENARIO = "qb_hoard_12"


def _insert_xwalk(db, name, position, sleeper_id, gsis_id=None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk (name, position, sleeper_id, gsis_id)"
            " VALUES (%s,%s,%s,%s) RETURNING xwalk_id",
            (name, position, sleeper_id, gsis_id),
        )
        return cur.fetchone()[0]


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


def _insert_snapshot(db, records, season=2026):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.sleeper_projections (season, week, payload) VALUES (%s, NULL, %s)",
            (season, json.dumps(records)),
        )


def _sleeper_rec(player_id, position, adp_2qb):
    return {
        "player_id": player_id,
        "player": {"position": position},
        "stats": {"adp_2qb": adp_2qb},
    }


def _seed_full_pool(db, real_adp_count=200):
    """Seed a pool that satisfies every validation gate: all six positions,
    >=200 real-ADP players, >=8 QBs in the ADP top 30, >=25 K and >=25 DEF."""
    ensure_config_in_db(db, CFG)
    records = []

    # 8 QBs inside the ADP top-30 (2QB sanity gate).
    for i in range(8):
        xid = _insert_xwalk(db, f"QB{i}", "QB", f"q{i}")
        _insert_value(db, xid, "QB", 300 - i, 100 - i)
        records.append(_sleeper_rec(f"q{i}", "QB", 1.0 + i))

    # Remaining real-ADP skill players to clear the >=200 floor and pad the
    # ADP top-30 with non-QBs so QBs are a minority share of the top 30.
    remaining = real_adp_count - 8
    for i in range(remaining):
        xid = _insert_xwalk(db, f"WR{i}", "WR", f"w{i}")
        _insert_value(db, xid, "WR", 200 - i * 0.1, 50 - i * 0.05)
        records.append(_sleeper_rec(f"w{i}", "WR", 9.0 + i))

    # RB and TE presence (no ADP requirement beyond overall floor).
    for i in range(5):
        xid = _insert_xwalk(db, f"RB{i}", "RB", f"r{i}")
        _insert_value(db, xid, "RB", 150 - i, 40 - i)
        records.append(_sleeper_rec(f"r{i}", "RB", 999))
    for i in range(5):
        xid = _insert_xwalk(db, f"TE{i}", "TE", f"t{i}")
        _insert_value(db, xid, "TE", 120 - i, 20 - i)
        records.append(_sleeper_rec(f"t{i}", "TE", 999))

    # 25 K, all undrafted (adp sentinel 999).
    for i in range(25):
        xid = _insert_xwalk(db, f"K{i}", "K", f"k{i}")
        _insert_value(db, xid, "K", 90 - i, 5 - i * 0.1)
        records.append(_sleeper_rec(f"k{i}", "K", 999))

    # 25 DEF, all undrafted, ref = team abbr.
    for i in range(25):
        abbr = f"D{i:02d}"
        xid = _insert_xwalk(db, f"Defense{i}", "DEF", abbr)
        _insert_value(db, xid, "DEF", 80 - i, 4 - i * 0.1)
        records.append(_sleeper_rec(abbr, "DEF", 999))

    _insert_snapshot(db, records)
    db.commit()


def test_pool_maps_sentinel_adp_to_none(db):
    _seed_full_pool(db)
    pool = build_pool(db, SCENARIO)
    kickers = [p for p in pool if p.position == "K"]
    assert kickers, "expected kickers in pool"
    assert all(p.adp is None for p in kickers)


def test_pool_orders_real_adp_before_none(db):
    _seed_full_pool(db)
    pool = build_pool(db, SCENARIO)
    real_adp = [p.adp is not None for p in pool]
    # Once we hit the first None, every subsequent entry must also be None.
    first_none = real_adp.index(False) if False in real_adp else len(real_adp)
    assert all(real_adp[:first_none])
    assert not any(real_adp[first_none:])
    # Real-ADP prefix itself must be ascending.
    real_vals = [p.adp for p in pool[:first_none]]
    assert real_vals == sorted(real_vals)
    # None-ADP suffix must be vorp-descending.
    none_vorps = [p.vorp for p in pool[first_none:]]
    assert none_vorps == sorted(none_vorps, reverse=True)


def test_pool_fails_loud_when_positions_missing(db):
    ensure_config_in_db(db, CFG)
    records = []
    for i in range(8):
        xid = _insert_xwalk(db, f"QB{i}", "QB", f"q{i}")
        _insert_value(db, xid, "QB", 300 - i, 100 - i)
        records.append(_sleeper_rec(f"q{i}", "QB", 1.0 + i))
    _insert_snapshot(db, records)
    db.commit()
    with pytest.raises(ValueError, match="pool missing positions"):
        build_pool(db, SCENARIO)


def test_pool_rejects_duplicate_refs(db):
    _seed_full_pool(db)
    # Insert a second xwalk row with a sleeper_id that collides with an
    # existing player's ref, and give it a player_value row too.
    xid = _insert_xwalk(db, "Duplicate Guy", "WR", "w0")
    _insert_value(db, xid, "WR", 999, 999)
    db.commit()
    with pytest.raises(ValueError, match="duplicate"):
        build_pool(db, SCENARIO)


def test_pool_fails_loud_on_null_tier(db):
    """Test fail-loud guard when player_value.tier is NULL.
    This is the reachable null-check path: the query's inner join on xwalk_id
    ensures xwalk rows exist, but tier can be NULL in valuation.player_value."""
    _seed_full_pool(db)
    # Insert a player with NULL tier by passing tier=None to _insert_value.
    xid = _insert_xwalk(db, "Null Tier Player", "WR", "null_tier")
    _insert_value(db, xid, "WR", 100.0, 50.0, tier=None)
    db.commit()
    with pytest.raises(ValueError, match="NULL tier.*Null Tier Player.*scenario"):
        build_pool(db, SCENARIO)
