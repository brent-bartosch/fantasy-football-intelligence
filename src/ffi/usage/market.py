"""Market overlay: `market_trends` normalization over `raw.sleeper_trending`
plus a fail-closed urgency score (ADR Domain 2/6).

The urgency overlay answers "is the market already on this player?" A role
change (ASCENDING) that nobody has noticed is worth more than one the whole
league is already chasing. It fails CLOSED: if the market input's sanity gate
failed, `urgency` returns a `Refusal` instead of a score — a suspicious market
snapshot must never be laundered into a confident number.

Pure functions over already-fetched data. Imports `ffi.usage` value types and
nothing else internal, so the trend engine and the market overlay stay acyclic.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from ffi.usage import ASCENDING, FALLING, WATCH, TrendSignal

# The net-add scale: a player with >= NET_ADD_FULL net adds saturates the heat
# term. Deliberately small — the add/drop lists cap at ~100 rows each, and a
# league's whole attention rarely exceeds a net of ~10 on one player.
NET_ADD_FULL = 10.0


@dataclass(frozen=True)
class Refusal:
    """Why a composite refused to render (fail-closed, never a silent zero)."""

    reason: str


@dataclass(frozen=True)
class MarketTrend:
    """One normalized row of `public.market_trends`."""

    player_id: str
    trend_type: str  # 'add' | 'drop'
    count: int
    player_name: str | None = None
    position: str | None = None

    def __post_init__(self) -> None:
        if self.trend_type not in ("add", "drop"):
            raise ValueError(
                f"MarketTrend.trend_type {self.trend_type!r} not in ('add','drop')"
            )
        if self.count < 0:
            raise ValueError(f"MarketTrend.count must be >= 0, got {self.count}")


@dataclass(frozen=True)
class MarketState:
    """A player's aggregated market position, ready for `urgency`."""

    add_count: int
    drop_count: int
    add_rank: int | None = None  # 1 = most-added
    gate_failed: bool = False


def normalize(
    snapshot_id: int,
    archive_date,
    trend_type: str,
    rows: Sequence[dict],
) -> list[MarketTrend]:
    """Normalize one `raw.sleeper_trending.payload` list into `MarketTrend`s.

    `snapshot_id` and `archive_date` are carried on the normalized row so a
    reader can trace a trend back to the exact archived snapshot it came from
    (the archive is unrecoverable — provenance must be explicit).
    """
    out: list[MarketTrend] = []
    for rec in rows:
        if not isinstance(rec, dict) or "player_id" not in rec or "count" not in rec:
            raise ValueError(
                f"market_trends: record missing player_id/count — schema drift: {rec!r}"
            )
        out.append(
            MarketTrend(
                player_id=str(rec["player_id"]),
                trend_type=trend_type,
                count=int(rec["count"]),
                player_name=rec.get("player_name"),
                position=rec.get("position"),
            )
        )
    return out


def aggregate(player_id: str, trends: Sequence[MarketTrend]) -> MarketState:
    """Aggregate a player's add/drop counts and add-rank from normalized trends."""
    adds = [t for t in trends if t.trend_type == "add"]
    drops = [t for t in trends if t.trend_type == "drop"]
    add_count = sum(t.count for t in adds if t.player_id == player_id)
    drop_count = sum(t.count for t in drops if t.player_id == player_id)
    ranked = sorted(adds, key=lambda t: (-t.count, t.player_id))
    add_rank = None
    for i, t in enumerate(ranked, start=1):
        if t.player_id == player_id:
            add_rank = i
            break
    return MarketState(add_count=add_count, drop_count=drop_count, add_rank=add_rank)


def urgency(signal: TrendSignal, market: MarketState) -> float | Refusal:
    """Score how urgent it is to act on `signal`, 0..1, or refuse.

    Fail-closed: a market snapshot whose sanity gate failed returns `Refusal`.
    A FALLING signal (route collapse — a droppable player) has no acquisition
    urgency and scores 0.0, never a refusal: the signal itself is valid.
    """
    if market.gate_failed:
        return Refusal("market input gate failed — refusing to score urgency")
    if signal.direction not in (ASCENDING, WATCH, FALLING):
        return Refusal(f"unknown signal direction {signal.direction!r}")
    if signal.direction == FALLING:
        return 0.0
    dir_weight = 1.0 if signal.direction == ASCENDING else 0.5
    net = market.add_count - market.drop_count
    heat = max(0.0, min(1.0, net / NET_ADD_FULL))
    if market.add_rank is not None:
        rank_heat = max(0.0, min(1.0, 1.0 - (market.add_rank - 1) / 100.0))
        heat = 0.5 * heat + 0.5 * rank_heat
    return round(dir_weight * heat, 3)
