#!/usr/bin/env python3
"""Walk the target league's renew chain; record per-season settings; detect 2QB era;
compare against the legacy-imported LMU chain. Risks R4/R9."""
import json
import time

# 2025 target league (league_rules.md). If the 2026 league exists by run time,
# start from its key instead and the chain will include 2025 automatically.
NAJEE_2025 = "461.l.326814"

# Legacy chain actually imported into Postgres (scripts/import_all_lmu.py)
LEGACY_LMU_KEYS = {
    "461.l.863132",
    "449.l.389359",
    "423.l.323988",
    "414.l.254390",
    "406.l.205166",
    "399.l.130335",
    "390.l.523677",
    "380.l.212373",
    "371.l.22647",
    "359.l.427482",
    "348.l.82093",
    "331.l.534456",
    "314.l.364382",
    "273.l.11353",
    "257.l.117805",
    "242.l.42939",
    "222.l.231759",
}


def renew_to_league_key(renew: str | None) -> str | None:
    if not renew:
        return None
    game_id, league_id = renew.split("_")
    return f"{game_id}.l.{league_id}"


def parse_settings(league_key: str, settings: dict) -> dict:
    # Load-bearing keys accessed directly: KeyError here = schema drift = stop (fail loud).
    roster = settings["roster_positions"]
    qb_slots = 0
    for slot in roster:
        pos = slot["roster_position"] if "roster_position" in slot else slot
        if pos["position"] == "QB":
            qb_slots += int(pos.get("count", 1))
    return {
        "league_key": league_key,
        "season": int(settings["season"]),
        "league_name": settings["name"],
        "num_teams": int(settings["num_teams"]),
        "renew": settings.get("renew", ""),
        "renewed": settings.get("renewed", ""),
        "qb_slots": qb_slots,
        "roster_positions": roster,
    }


def extract_managers(teams: dict) -> dict:
    """{manager_guid: nickname} from lg.teams(). Handles Yahoo's list-or-dict manager shapes.

    DEVIATION from brief: as of this run (2026), Yahoo's API returns the literal
    sentinel string "--hidden--" for EVERY manager's guid field — including the
    authenticated user's own team (verified via is_current_login="1" still carrying
    guid="--hidden--"). This is a real, observed Yahoo privacy change, not a shape
    mismatch: the field is present and non-empty, so the brief's original
    `mm.get("guid") or f"no-guid:{...}"` fallback (which only triggers on falsy/
    missing guid) never fires and all teams collapse onto one dict key. We now treat
    "--hidden--" the same as a missing guid and fall back to the per-league
    manager_id (stable 1-12 across all 16 renewed seasons in the observed chain).
    """
    out = {}
    for _, team in teams.items():
        mgrs = team["managers"]  # KeyError = schema drift = stop (fail loud)
        if isinstance(mgrs, dict):
            mgrs = [mgrs]
        for m in mgrs:
            mm = m["manager"] if "manager" in m else m
            guid = mm.get("guid")
            if not guid or guid == "--hidden--":
                guid = f"no-guid:{mm.get('manager_id')}"
            out[guid] = mm.get("nickname", "?")
    return out


def positions_to_roster_positions(positions: dict) -> list[dict]:
    """Convert yahoo_fantasy_api's lg.positions() shape into the
    [{"roster_position": {"position": ..., "count": ...}}, ...] shape parse_settings expects.

    DEVIATION from brief: yahoo_fantasy_api's lg.settings() deliberately strips
    'roster_positions' from the settings dict (library comment: "can be found in
    other APIs") — it is never present as a top-level key there. The real per-position
    data lives in lg.positions(), keyed by position code with count/position_type/
    is_starting_position, e.g. {"QB": {"position_type": "O", "count": 2,
    "is_starting_position": 1}, ...}. We fetch it separately and splice it into the
    settings dict under "roster_positions" before calling parse_settings, so
    parse_settings's contract (and its tests) are unchanged.
    """
    return [
        {"roster_position": {"position": pos, **info}}
        for pos, info in positions.items()
    ]


def walk_renew_chain(session, start_key: str) -> list[dict]:
    from ffi.yahoo_client import get_league

    rows, key = [], start_key
    while key:
        lg = get_league(session, key)
        settings = dict(lg.settings())
        settings["roster_positions"] = positions_to_roster_positions(lg.positions())
        row = parse_settings(key, settings)
        row["settings_payload"] = settings
        row["managers"] = extract_managers(lg.teams())
        rows.append(row)
        print(
            f"  {row['season']}: {row['league_name']!r} teams={row['num_teams']} "
            f"QB={row['qb_slots']} managers={len(row['managers'])} key={key}"
        )
        key = renew_to_league_key(row["renew"])
        time.sleep(2)  # Yahoo throttle (R15) — two calls per season (settings + teams)
    return rows


def main():
    from ffi.db import connect
    from ffi.yahoo_client import get_session

    session = get_session()
    print(f"Walking renew chain from {NAJEE_2025} ...")
    rows = walk_renew_chain(session, NAJEE_2025)

    conn = connect()
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                """INSERT INTO raw.yahoo_league_settings
                   (league_key, season, league_name, num_teams, renew, renewed, qb_slots,
                    roster_positions, managers, settings_payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (league_key) DO UPDATE SET
                     settings_payload=EXCLUDED.settings_payload, qb_slots=EXCLUDED.qb_slots,
                     num_teams=EXCLUDED.num_teams, managers=EXCLUDED.managers, fetched_at=now()""",
                (
                    r["league_key"],
                    r["season"],
                    r["league_name"],
                    r["num_teams"],
                    r["renew"],
                    r["renewed"],
                    r["qb_slots"],
                    json.dumps(r["roster_positions"]),
                    json.dumps(r["managers"]),
                    json.dumps(r["settings_payload"]),
                ),
            )
    conn.commit()

    chain_keys = {r["league_key"] for r in rows}
    print("\n=== AUDIT REPORT ===")
    print(
        f"Chain length: {len(rows)} seasons ({min(r['season'] for r in rows)}–{max(r['season'] for r in rows)})"
    )
    two_qb_since = [r["season"] for r in rows if r["qb_slots"] >= 2]
    print(f"2QB seasons: {sorted(two_qb_since)}")
    overlap = chain_keys & LEGACY_LMU_KEYS
    print(
        f"Overlap with legacy-imported LMU chain: {len(overlap)}/{len(LEGACY_LMU_KEYS)}"
    )
    if len(overlap) == 0:
        print(
            "!! DIVERGENCE: the imported 17-year history is a DIFFERENT league than the NAJEE chain."
        )
        print(
            "!! STOP: report both chains to the user before any tendency mining (risks R4/R9)."
        )
    elif chain_keys != LEGACY_LMU_KEYS:
        print(
            f"!! PARTIAL overlap. In chain but not imported: {sorted(chain_keys - LEGACY_LMU_KEYS)}"
        )
        print(f"!! Imported but not in chain: {sorted(LEGACY_LMU_KEYS - chain_keys)}")

    # R9: manager-continuity verification — GUIDs are the anchor (nicknames change annually)
    #
    # DEVIATION: rows are walked newest-season-first (chain descends from the renew
    # pointer), so a naive "last processed wins" would let the OLDEST season's
    # nickname clobber the display name. That matters because Yahoo also hides
    # nicknames (literal "--hidden--") for seasons older than ~5 years in this
    # chain (2010-2020 observed hidden; 2021-2025 observed visible). We keep the
    # first real (non-hidden) nickname seen — i.e. the most recent one — and only
    # fall back to "--hidden--" if no season ever exposed a real nickname for that
    # manager slot.
    guid_names, guid_seasons = {}, {}
    for r in rows:
        for g, n in r["managers"].items():
            guid_seasons.setdefault(g, set()).add(r["season"])
            if n != "--hidden--" or g not in guid_names:
                guid_names[g] = n
    seasons_all = {r["season"] for r in rows}
    print("\nManager continuity (R9):")
    for g, seasons in sorted(guid_seasons.items(), key=lambda kv: -len(kv[1])):
        missing = sorted(seasons_all - seasons)
        gap = f", MISSING {missing}" if missing else ""
        print(
            f"  {guid_names[g]!r} ({g[:12]}…): {len(seasons)} seasons "
            f"{min(seasons)}–{max(seasons)}{gap}"
        )
    sports = [g for g, n in guid_names.items() if n.lower() == "sports"]
    if sports:
        for g in sports:
            print(f"  -> user 'Sports' GUID {g}: seasons {sorted(guid_seasons[g])}")
    else:
        print(
            "!! 'Sports' nickname not found in any season — identify the user's GUID manually (R9)."
        )
    core = sum(1 for s in guid_seasons.values() if len(s) >= 10)
    print(
        f"  GUIDs spanning >=10 seasons: {core} "
        f"(expect ~10 if the 'same core managers' premise holds; 0 means GUIDs broke — STOP, R9)"
    )


if __name__ == "__main__":
    main()
