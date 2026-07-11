"""Yahoo `draftresults` live poller: diff-by-pick-number, crosswalk pick
resolution, team_key -> franchise-slot mapping.

Yahoo's draft-results endpoint always returns every pick slot for the
league, made or not; unmade slots simply lack a `player_id` (Fact 11).
`DraftPoller.poll` filters to made picks, diffs against the pick numbers
it has already seen, resolves each new pick against the player crosswalk,
appends it to the DraftLog, and only then returns it -- durable before
visible.

Failure policy: `poll` contains no try/except at all. `fetch_fn` calls
into `ffi.yahoo_client.yahoo_call`, whose typed errors (`YahooRateLimitError`,
`YahooAuthError`) must propagate to the caller's `ModeMachine` (Task 13) --
the poller is not the place failure policy lives. An unrecognized
`team_key` (a team not in `team_slots`) raises `ValueError`: mid-draft that
means a 13th team appeared, which is corruption, not a case to paper over.
"""
import time
from dataclasses import dataclass
from typing import Callable

from ffi.draft.state import DraftLog


@dataclass(frozen=True)
class ResolvedPick:
    overall: int
    round: int
    team_key: str
    franchise_slot: int
    yahoo_player_id: str
    ref: str | None
    name: str | None
    pos: str | None
    # ref None => crosswalk miss: the assistant MUST queue it for manual
    # resolution and show a banner -- never guess, never drop (fail-loud).


@dataclass(frozen=True)
class PollResult:
    new_picks: tuple[ResolvedPick, ...]
    latency_s: float  # wall time of the fetch_fn call only (rehearsal metric)
    total_made: int  # picks with a player_id in this fetch


class DraftPoller:
    def __init__(
        self,
        fetch_fn: Callable[[], list[dict]],
        resolve: Callable[[str], tuple[str, str, str] | None],
        team_slots: dict[str, int],
        log: DraftLog,
    ):
        self._fetch_fn = fetch_fn
        self._resolve = resolve
        self._team_slots = team_slots
        self._log = log
        self._seen: set[int] = set()

    def poll(self) -> PollResult:
        start = time.monotonic()
        picks = self._fetch_fn()
        latency_s = time.monotonic() - start

        made = [p for p in picks if "player_id" in p]
        new = sorted(
            (p for p in made if int(p["pick"]) not in self._seen),
            key=lambda p: int(p["pick"]),
        )

        resolved = []
        for p in new:
            overall = int(p["pick"])
            team_key = p["team_key"]
            if team_key not in self._team_slots:
                raise ValueError(
                    f"pick {overall}: unknown team_key {team_key!r} -- not in "
                    "team_slots (a 13th team mid-draft would mean corruption)"
                )
            franchise_slot = self._team_slots[team_key]
            yahoo_player_id = str(p["player_id"])
            match = self._resolve(yahoo_player_id)
            ref, name, pos = match if match is not None else (None, None, None)

            rp = ResolvedPick(
                overall=overall,
                round=int(p["round"]),
                team_key=team_key,
                franchise_slot=franchise_slot,
                yahoo_player_id=yahoo_player_id,
                ref=ref,
                name=name,
                pos=pos,
            )
            self._log.append(
                "pick",
                {
                    "overall": rp.overall,
                    "round": rp.round,
                    "franchise_slot": rp.franchise_slot,
                    "team_key": rp.team_key,
                    "ref": rp.ref,
                    "yahoo_player_id": rp.yahoo_player_id,
                    "name": rp.name,
                    "pos": rp.pos,
                    "source": "poll",
                },
            )
            self._seen.add(overall)
            resolved.append(rp)

        return PollResult(
            new_picks=tuple(resolved), latency_s=latency_s, total_made=len(made)
        )


def load_team_slots(conn, league_key: str) -> dict[str, int]:
    """team_key -> franchise slot (1-12) for a league already backfilled into
    `teams` (Phase 1's slot-identity convention -- see
    scripts/backfill_draft_teams.py). Rehearsals/tests only: the live 2026
    session (Task 13) sources this from `yahoo_call(lg.teams)` at startup
    instead, since the current season's `teams` rows don't exist yet."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT team_key, slot FROM teams WHERE league_id=%s AND slot IS NOT NULL",
            (league_key,),
        )
        mapping = {team_key: slot for team_key, slot in cur.fetchall()}
    if len(mapping) != 12:
        raise ValueError(
            f"{league_key}: expected exactly 12 teams with a slot in `teams`, "
            f"got {len(mapping)}"
        )
    return mapping


def build_resolver(conn) -> Callable[[str], tuple[str, str, str] | None]:
    """One upfront query over the crosswalk: yahoo_id -> (sleeper ref, name,
    position). DEF rows are included automatically -- Phase 3 established
    sleeper_id = team abbreviation (e.g. Rams = 'LAR') for defenses, stored
    in the same `sleeper_id` column as skill players. Only rows with both a
    yahoo_id and a completed sleeper match are indexed; anything else is
    indistinguishable from "not in the crosswalk" and must come back as a
    miss (None), never a partial guess."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT yahoo_id, sleeper_id, name, position
               FROM public.player_id_xwalk
               WHERE yahoo_id IS NOT NULL AND sleeper_id IS NOT NULL"""
        )
        table = {
            yahoo_id: (sleeper_id, name, position)
            for yahoo_id, sleeper_id, name, position in cur.fetchall()
        }
    return table.get
