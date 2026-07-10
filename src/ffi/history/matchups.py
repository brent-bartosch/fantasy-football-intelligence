"""Parse raw.yahoo_matchups payloads (Yahoo's deeply nested scoreboard JSON)
into flat per-team rows. Load-bearing keys are accessed with [] — a missing
key is schema drift and must raise, not default (ADR Domain 1).

Shape confirmed against a live payload (2025 week 15, league 461.l.326814,
and 2010 week 14, league 242.l.8015) via:
    psql -d fantasy_football -t -c "SELECT jsonb_pretty(payload::jsonb #>
      '{fantasy_content,league,1,scoreboard,0,matchups,0,matchup}')
      FROM raw.yahoo_matchups WHERE week=15 LIMIT 1;"

fantasy_content.league[1].scoreboard['0'].matchups is a dict keyed '0','1',...
plus a 'count' int. Each entry is {"matchup": {...}} where the matchup dict
has "0" -> {"teams": {"0": {...}, "1": {...}, "count": 2}} as well as
top-level sibling keys ("week", "status", "is_playoffs", "is_consolation",
"winner_team_key", ...). is_playoffs is a STRING "0"/"1" sibling of "0",
not nested inside it — confirmed live, matches the brief's fixture as-is.

team = [attrs_list, points_dict]; attrs_list[0] holds {"team_key": ...};
points_dict has "team_points" (always present) and "team_projected_points"
(present in every payload checked across all 16 seasons — no fallback
needed, but treated as optional defensively since Yahoo has dropped fields
in the past for old seasons elsewhere in this project).
"""


def _iter_matchups(payload: dict):
    league = payload["fantasy_content"]["league"]
    scoreboard = league[1]["scoreboard"]
    matchups = scoreboard["0"]["matchups"]
    for k, v in matchups.items():
        if k == "count":
            continue
        yield v["matchup"]


def _team_row(team_node: dict) -> dict:
    team = team_node["team"]
    attrs, points = team[0], team[1]
    team_key = next(
        a["team_key"] for a in attrs if isinstance(a, dict) and "team_key" in a
    )
    proj = points.get(
        "team_projected_points"
    )  # absent in some very old seasons: allowed
    return {
        "team_key": team_key,
        "points": float(points["team_points"]["total"]),
        "proj_points": float(proj["total"]) if proj else None,
    }


def parse_matchup_payload(payload: dict) -> list[dict]:
    rows = []
    for matchup in _iter_matchups(payload):
        is_playoffs = str(matchup.get("is_playoffs", "0")) == "1"
        teams = matchup["0"]["teams"]
        sides = [_team_row(teams[k]) for k in ("0", "1")]
        if len(sides) != 2:
            raise ValueError(f"matchup without exactly 2 teams: {list(teams)[:5]}")
        for me, opp in ((0, 1), (1, 0)):
            rows.append(
                {
                    **sides[me],
                    "opp_team_key": sides[opp]["team_key"],
                    "opp_points": sides[opp]["points"],
                    "is_playoffs": is_playoffs,
                }
            )
    if not rows:
        raise ValueError("payload parsed to zero matchup rows — shape drift?")
    return rows
