"""Versioned scoring config: committed JSON is the source, scoring.config the
DB mirror. Configs are IMMUTABLE — any rules change is a new version (ADR D2/D8)."""
import json
import pathlib

from pydantic import BaseModel, ConfigDict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
V1_PATH = REPO_ROOT / "config" / "scoring" / "v1.json"


class BonusTier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    threshold: float
    points: float


class RangeTier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max: float | None  # None = +infinity (last tier)
    points: float


class OffenseRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weights: dict[str, float]
    yardage_bonuses: dict[str, list[BonusTier]]
    bonus_stacking: str  # 'cumulative' is the verified semantic


class KickingRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weights: dict[str, float]


class DefenseRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weights: dict[str, float]
    points_allowed_tiers: list[RangeTier]
    yards_allowed_tiers: list[RangeTier]


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int
    description: str
    offense: OffenseRules
    kicking: KickingRules
    defense: DefenseRules


def load_config(path: str | pathlib.Path) -> ScoringConfig:
    return ScoringConfig.model_validate(json.loads(pathlib.Path(path).read_text()))


def load_config_v1() -> ScoringConfig:
    return load_config(V1_PATH)


def ensure_config_in_db(conn, cfg: ScoringConfig) -> None:
    """Insert if absent. If the version exists with DIFFERENT rules, fail loud:
    configs are immutable — bump the version instead."""
    rules = cfg.model_dump()
    with conn.cursor() as cur:
        cur.execute("SELECT rules FROM scoring.config WHERE version=%s", (cfg.version,))
        row = cur.fetchone()
        if row is not None:
            if row[0] != rules:
                raise ValueError(
                    f"scoring.config version {cfg.version} exists with different rules — "
                    "configs are immutable; create a new version file instead."
                )
            return
        cur.execute(
            "INSERT INTO scoring.config (version, description, rules) VALUES (%s,%s,%s)",
            (cfg.version, cfg.description, json.dumps(rules)),
        )
    conn.commit()
