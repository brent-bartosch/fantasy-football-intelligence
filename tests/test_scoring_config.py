import pytest

from ffi.scoring.config import ScoringConfig, ensure_config_in_db, load_config_v1


def test_load_config_v1():
    cfg = load_config_v1()
    assert cfg.version == 1
    assert cfg.offense.weights["receptions"] == 1
    assert cfg.offense.weights["pass_yards"] == 0.04
    assert cfg.offense.bonus_stacking == "cumulative"
    assert cfg.defense.points_allowed_tiers[0].max == 0
    assert cfg.defense.points_allowed_tiers[-1].max is None
    assert cfg.kicking.weights["fg_miss_30_39"] == -1


def test_config_rejects_unknown_top_level_field():
    raw = load_config_v1().model_dump()
    raw["made_up_section"] = {"foo": "bar"}
    with pytest.raises(Exception):  # pydantic ValidationError
        ScoringConfig.model_validate(raw)


def test_ensure_config_in_db_idempotent(db):
    cfg = load_config_v1()
    ensure_config_in_db(db, cfg)
    ensure_config_in_db(db, cfg)  # second call is a no-op
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM scoring.config WHERE version=1")
        assert cur.fetchone()[0] == 1


def test_ensure_config_in_db_immutability_guard(db):
    cfg = load_config_v1()
    ensure_config_in_db(db, cfg)
    mutated = cfg.model_copy(deep=True)
    mutated.offense.weights["receptions"] = 2
    with pytest.raises(ValueError, match="immutable"):
        ensure_config_in_db(db, mutated)
