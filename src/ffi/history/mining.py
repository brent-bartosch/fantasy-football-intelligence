"""Historical mining primitives. Pure functions where possible (testable);
SQL readers thin. Attribution simplification (documented in the report):
a player's weekly points count for the team holding him that week — bench vs
started is unknowable without lineups (deliberately not imported)."""
from collections import defaultdict


def all_play_from_weeks(weeks: list[dict]) -> dict:
    """weeks: [{'week': N, 'scores': {key: points}}] -> {key: {'wins','losses'}}.
    All-play: each week, a team 'plays' every other team (ties count half—rare;
    rounded down to keep ints, noted in report)."""
    out: dict = defaultdict(lambda: {"wins": 0, "losses": 0})
    for w in weeks:
        scores = w["scores"]
        for k, s in scores.items():
            wins = sum(1 for o, os_ in scores.items() if o != k and s > os_)
            losses = sum(1 for o, os_ in scores.items() if o != k and s < os_)
            out[k]["wins"] += wins
            out[k]["losses"] += losses
    return dict(out)


def roster_intervals(events: list[dict], end_week: int) -> dict:
    """events per player: draft (week 0), add, drop, trade_in — chronological.
    Returns {player_ref: [(team_key, first_week, last_week, how)]}. A draft/add
    at week W covers weeks max(W,1)..(next departure or end_week)."""
    by_player: dict = defaultdict(list)
    for e in sorted(events, key=lambda e: e["week"]):
        by_player[e["player_ref"]].append(e)
    out: dict = {}
    for pref, evs in by_player.items():
        intervals, current = [], None  # current = (team_key, start_week, how)
        for e in evs:
            wk = max(int(e["week"]), 1) if e["type"] != "drop" else int(e["week"])
            if e["type"] in ("draft", "add", "trade_in"):
                if current is not None:
                    intervals.append((current[0], current[1], wk, current[2]))
                current = (e["team_key"], max(int(e["week"]), 1), e["type"])
            elif e["type"] == "drop":
                if current is not None:
                    intervals.append((current[0], current[1], wk, current[2]))
                    current = None
        if current is not None:
            intervals.append((current[0], current[1], end_week, current[2]))
        out[pref] = intervals
    return out


def all_play(conn) -> list[dict]:
    """Per team-season: actual record vs all-play record (regular season only)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT league_key, season, week, team_key, points
               FROM public.matchup_results WHERE NOT is_playoffs
               ORDER BY league_key, week"""
        )
        rows = cur.fetchall()
    by_lw: dict = defaultdict(dict)
    seasons: dict = {}
    for lk, season, week, tk, pts in rows:
        by_lw[(lk, week)][tk] = float(pts)
        seasons[tk] = (lk, season)
    weeks_by_league: dict = defaultdict(list)
    for (lk, week), scores in by_lw.items():
        weeks_by_league[lk].append({"week": week, "scores": scores})
    out = []
    for lk, weeks in weeks_by_league.items():
        ap = all_play_from_weeks(weeks)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT team_key, team_name, slot, final_rank FROM teams WHERE league_id=%s""",
                (lk,),
            )
            meta = {tk: (name, slot, rank) for tk, name, slot, rank in cur.fetchall()}
        # actual W/L comes straight from matchup_results head-to-head (points vs opp_points)
        actual: dict = defaultdict(lambda: [0, 0])
        with conn.cursor() as cur:
            cur.execute(
                """SELECT team_key,
                          count(*) FILTER (WHERE points > opp_points),
                          count(*) FILTER (WHERE points < opp_points)
                   FROM public.matchup_results
                   WHERE league_key=%s AND NOT is_playoffs GROUP BY team_key""",
                (lk,),
            )
            for tk, w_, l_ in cur.fetchall():
                actual[tk] = [w_, l_]
        for tk, rec in ap.items():
            name, slot, rank = meta[tk]
            aw, al = actual[tk]
            out.append(
                {
                    "league_key": lk,
                    "season": seasons[tk][1],
                    "slot": slot,
                    "team": name,
                    "final_rank": rank,
                    "actual_w": aw,
                    "actual_l": al,
                    "all_play_pct": rec["wins"] / max(rec["wins"] + rec["losses"], 1),
                    "actual_pct": aw / max(aw + al, 1),
                    "luck": aw / max(aw + al, 1)
                    - rec["wins"] / max(rec["wins"] + rec["losses"], 1),
                }
            )
    return out


def franchise_slot_outcomes(conn) -> list[dict]:
    """Franchise slot (1-12, the stable Yahoo team-seat, NOT draft order — see
    manager_slot_annotations for which human held each slot over time) -> avg
    final rank, championship count, all 16 seasons. This measures persistent
    manager-seat quality, not draft-position advantage (snake order varies by
    season — see draft_position_outcomes for that)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.slot, count(*) AS seasons,
                   avg(t.final_rank) AS avg_finish,
                   count(*) FILTER (WHERE t.won_championship) AS titles,
                   avg(t.total_points_scored) AS avg_pf
            FROM teams t
            JOIN raw.yahoo_league_settings s ON s.league_key = t.league_id
            GROUP BY t.slot ORDER BY t.slot
            """
        )
        return [
            dict(zip(("slot", "seasons", "avg_finish", "titles", "avg_pf"), r))
            for r in cur.fetchall()
        ]


def draft_position_outcomes(conn) -> list[dict]:
    """TRUE snake-draft position (1-12) -> avg final rank, championship count,
    avg PF, seasons count, across all 16 seasons. A team-season's draft
    position is its round-1 pick_number (round_number=1) — this is the actual
    slot the team drafted from that year, which varies season to season
    (unlike the stable franchise slot in franchise_slot_outcomes). Validates
    exactly one round-1 pick per team-season (192 total) and fails loud
    naming offenders otherwise."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT dp.league_id, dp.team_id, dp.pick_number
            FROM draft_picks dp
            JOIN teams t ON t.team_id = dp.team_id
            JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
            WHERE dp.round_number = 1
            """
        )
        rows = cur.fetchall()

    by_team_season: dict = defaultdict(list)
    for league_id, team_id, pick_number in rows:
        by_team_season[(league_id, team_id)].append(pick_number)

    offenders = {k: v for k, v in by_team_season.items() if len(v) != 1}
    if offenders:
        raise ValueError(
            f"draft_position_outcomes: expected exactly one round-1 pick per "
            f"team-season, found violations for {len(offenders)} team-season(s): "
            f"{offenders}"
        )
    if len(by_team_season) != 192:
        raise ValueError(
            f"draft_position_outcomes: expected 192 team-seasons with a round-1 "
            f"pick, found {len(by_team_season)}"
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT dp.pick_number, t.final_rank, t.won_championship, t.total_points_scored
            FROM draft_picks dp
            JOIN teams t ON t.team_id = dp.team_id
            JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
            WHERE dp.round_number = 1
            """
        )
        detail = cur.fetchall()

    agg: dict = defaultdict(
        lambda: {"seasons": 0, "finishes": [], "titles": 0, "pfs": []}
    )
    for pick_number, final_rank, won_championship, total_points_scored in detail:
        a = agg[pick_number]
        a["seasons"] += 1
        a["finishes"].append(final_rank)
        a["titles"] += 1 if won_championship else 0
        a["pfs"].append(total_points_scored)

    out = []
    for pos in sorted(agg):
        a = agg[pos]
        out.append(
            {
                "position": pos,
                "seasons": a["seasons"],
                "avg_finish": sum(a["finishes"]) / len(a["finishes"]),
                "titles": a["titles"],
                "avg_pf": sum(a["pfs"]) / len(a["pfs"]) if a["pfs"] else 0,
            }
        )
    return out


def position_round_tendencies(conn) -> list[dict]:
    """Per slot x round-band x position: pick share (the tendency fingerprint)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.slot,
                   CASE WHEN dp.round_number <= 3 THEN 'R1-3'
                        WHEN dp.round_number <= 8 THEN 'R4-8'
                        ELSE 'R9+' END AS band,
                   p.position, count(*) AS picks
            FROM draft_picks dp
            JOIN teams t ON t.team_id = dp.team_id
            JOIN players p ON p.player_id = dp.player_id
            JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
            GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC
            """
        )
        return [
            dict(zip(("slot", "band", "position", "picks"), r)) for r in cur.fetchall()
        ]


def qb_timing_by_slot(conn) -> list[dict]:
    """Rounds where each slot took its QB1/QB2 (the 2QB strategic fingerprint)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH qb_picks AS (
                SELECT t.slot, dp.league_id, dp.round_number,
                       row_number() OVER (PARTITION BY dp.league_id, t.slot
                                          ORDER BY dp.overall_pick) AS qb_n
                FROM draft_picks dp
                JOIN teams t ON t.team_id = dp.team_id
                JOIN players p ON p.player_id = dp.player_id
                JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
                WHERE p.position = 'QB'
            )
            SELECT slot, avg(round_number) FILTER (WHERE qb_n=1) AS qb1_round,
                   avg(round_number) FILTER (WHERE qb_n=2) AS qb2_round,
                   avg(round_number) FILTER (WHERE qb_n=3) AS qb3_round,
                   count(DISTINCT league_id) AS seasons
            FROM qb_picks GROUP BY slot ORDER BY slot
            """
        )
        return [
            dict(zip(("slot", "qb1_round", "qb2_round", "qb3_round", "seasons"), r))
            for r in cur.fetchall()
        ]


def transaction_timing(conn) -> list[dict]:
    """Adds/drops/trades by NFL-season week bucket across 16 seasons — tests the
    'weeks 10-14 championship-pickup cluster' hypothesis.

    Week is inferred from ts vs each season's real week-1 start date, pulled from
    the week=1 raw.yahoo_matchups payload's "week_start" field (confirmed present
    for all 16 seasons — see docs/research/<date>-historical-mining-report.md for
    the sanity-check). NOTE: an earlier draft anchored on the earliest transaction
    timestamp (draft day, ~2-3 weeks before week 1) was rejected after verification
    showed it shifted every bucket 2-3 weeks late; the real week_start date is used
    instead."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH season_start AS (
                SELECT league_key,
                       (payload::jsonb #> '{fantasy_content,league,1,scoreboard,0,matchups,0,matchup}'
                        ->> 'week_start')::date AS wk1_date
                FROM raw.yahoo_matchups WHERE week = 1
            )
            SELECT tr.season, tr.type,
                   least(greatest(1, 1 + floor(extract(epoch FROM tr.ts - ss.wk1_date::timestamptz) / 604800)::int), 17) AS approx_week,
                   count(*)
            FROM raw.yahoo_transactions tr
            JOIN season_start ss ON ss.league_key = tr.league_key
            WHERE tr.ts IS NOT NULL
            GROUP BY 1, 2, 3 ORDER BY 1, 3
            """
        )
        return [
            dict(zip(("season", "type", "approx_week", "n"), r)) for r in cur.fetchall()
        ]


def trade_stats(conn) -> dict:
    """Trade frequency per season + QB involvement share (hypothesis 5.4)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT tr.season, count(*) FROM raw.yahoo_transactions tr
               JOIN raw.yahoo_league_settings s ON s.league_key = tr.league_key
               WHERE tr.type='trade' GROUP BY tr.season ORDER BY tr.season"""
        )
        per_season = dict(cur.fetchall())
        cur.execute(
            """
            SELECT count(*) FROM raw.yahoo_transactions t
            JOIN raw.yahoo_league_settings s ON s.league_key = t.league_key
            WHERE t.type='trade' AND EXISTS (
                SELECT 1 FROM jsonb_each(t.payload->'players') kv
                WHERE kv.key ~ '^[0-9]+$'
                  AND kv.value->'player'->0 @> '[{"display_position": "QB"}]'::jsonb
            )
            """
        )
        qb_trades = cur.fetchone()[0]
        cur.execute(
            """SELECT count(*) FROM raw.yahoo_transactions t
               JOIN raw.yahoo_league_settings s ON s.league_key = t.league_key
               WHERE t.type='trade'"""
        )
        total = cur.fetchone()[0]
    return {"per_season": per_season, "total": total, "qb_involved": qb_trades}


def champion_value_split(conn) -> list[dict]:
    """2019-2025: champion's team-season points split by acquisition route
    (drafted vs added vs traded-in), using roster_intervals + weekly points.
    Attribution simplification documented in the report."""
    out = []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT t.league_id, t.team_key, t.team_name, s.season
               FROM teams t JOIN raw.yahoo_league_settings s ON s.league_key=t.league_id
               WHERE t.won_championship AND s.season >= 2019 ORDER BY s.season"""
        )
        champs = cur.fetchall()
    for league_id, team_key, name, season in champs:
        events = _acquisition_events(conn, league_id, team_key)
        intervals = roster_intervals(events, end_week=17)
        split = defaultdict(float)
        for pref, ivs in intervals.items():
            for tk, wk_from, wk_to, how in ivs:
                if tk != team_key:
                    continue
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT coalesce(sum(p.points), 0)
                           FROM scoring.player_week_points p
                           JOIN public.player_id_xwalk x ON x.gsis_id = p.player_ref
                           WHERE p.source='nflverse' AND p.season=%s
                             AND p.week BETWEEN %s AND %s AND x.yahoo_id = %s""",
                        (season, wk_from, wk_to, pref),
                    )
                    split[how] += float(cur.fetchone()[0])
        out.append({"season": season, "champion": name, **dict(split)})
    return out


def _acquisition_events(conn, league_id: str, team_key: str) -> list[dict]:
    """Draft + transaction events shaping the champion's roster timeline.
    player_ref = numeric yahoo id (matches crosswalk join)."""
    from ffi.ids import player_numeric_id

    events = []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT p.yahoo_player_id FROM draft_picks dp
               JOIN players p ON p.player_id = dp.player_id
               JOIN teams t ON t.team_id = dp.team_id
               WHERE dp.league_id=%s AND t.team_key=%s""",
            (league_id, team_key),
        )
        for (ykey,) in cur.fetchall():
            events.append(
                {
                    "player_ref": player_numeric_id(ykey),
                    "team_key": team_key,
                    "type": "draft",
                    "week": 0,
                }
            )
        cur.execute(
            """SELECT payload, ts FROM raw.yahoo_transactions
               WHERE league_key=%s AND type IN ('add','drop','add/drop','trade')
               ORDER BY ts""",
            (league_id,),
        )
        txns = cur.fetchall()
        cur.execute(
            """SELECT (payload::jsonb #> '{fantasy_content,league,1,scoreboard,0,matchups,0,matchup}'
                       ->> 'week_start')::date
               FROM raw.yahoo_matchups WHERE league_key=%s AND week=1""",
            (league_id,),
        )
        season_anchor = cur.fetchone()[0]
    for payload, ts in txns:
        week = (
            max(1, min(17, 1 + int((ts.date() - season_anchor).days // 7))) if ts else 1
        )
        players = payload.get("players") or {}
        for k, v in players.items():
            if not k.isdigit():
                continue
            plist = v["player"]
            pid = next(
                str(a["player_id"])
                for a in plist[0]
                if isinstance(a, dict) and "player_id" in a
            )
            tdata = plist[1]["transaction_data"]
            tdata = tdata[0] if isinstance(tdata, list) else tdata
            dest = tdata.get("destination_team_key")
            src = tdata.get("source_team_key")
            if dest == team_key:
                kind = "trade_in" if tdata["type"] == "trade" else "add"
                events.append(
                    {
                        "player_ref": pid,
                        "team_key": team_key,
                        "type": kind,
                        "week": week,
                    }
                )
            elif src == team_key and tdata["type"] in ("drop", "trade"):
                events.append(
                    {
                        "player_ref": pid,
                        "team_key": team_key,
                        "type": "drop",
                        "week": week,
                    }
                )
    return events
