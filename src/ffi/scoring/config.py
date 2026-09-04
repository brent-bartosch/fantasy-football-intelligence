"""Versioned scoring config: committed JSON is the source, scoring.config the
DB mirror. Configs are IMMUTABLE — any rules change is a new version (ADR D2/D8)."""
import json
import pathlib

from pydantic import BaseModel, ConfigDict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
V1_PATH = REPO_ROOT / "config" / "scoring" / "v1.json"
V2_PATH = REPO_ROOT / "config" / "scoring" / "v2.json"

# Version -> committed JSON path. Configs are immutable; a new league/rules
# change is a new version (ADR D2/D8). Kept as a single registry so callers
# select a league by version instead of hardcoding a path.
CONFIG_PATHS = {
    1: V1_PATH,
    2: V2_PATH,
}


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


def load_config_v2() -> ScoringConfig:
    return load_config(V2_PATH)


def load_config_by_version(version: int) -> ScoringConfig:
    """Load a scoring config by version number (fail-loud on unknown version)."""
    path = CONFIG_PATHS.get(version)
    if path is None:
        raise ValueError(f"unknown scoring config version {version!r}")
    return load_config(path)


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
