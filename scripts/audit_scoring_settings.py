#!/usr/bin/env python3
"""R8 scoring-bonus audit: diff the LIVE 2026 league's scoring rules against the
documented `league_rules.md` tables (and the stored 2025 payload as a second
witness). Completes the R8 re-audit — structural settings already confirmed; this
is the scoring half.

One raw Yahoo call (`get_settings_raw`) carries BOTH the stat_id->name map
(`stat_categories`, which yfa's `stat_categories()` strips the ids from) and the
`stat_modifiers`. Everything else is offline.

Usage: uv run python scripts/audit_scoring_settings.py [LEAGUE_KEY]
       default LEAGUE_KEY = 470.l.152123 (2026 "MIKE VRABEL'S HOT TUB").
"""
import sys

import objectpath

LIVE_2026 = "470.l.152123"
PRIOR_2025 = "461.l.326814"  # stored in raw.yahoo_league_settings

# Documented rules from league_rules.md (source of truth), keyed by Yahoo stat_id.
# value = per-unit points (str, as Yahoo returns); bonuses = {target: points}.
# Yardage stats store the per-yard rate (1/25 = 0.04 passing, 1/10 = 0.1 rush/rec).
EXPECTED = {
    # Passing
    2: {"name": "Completions", "value": 0.5},
    3: {"name": "Incomplete Passes", "value": -0.5},
    4: {"name": "Passing Yards", "value": 0.04, "bonuses": {300: 3, 400: 4, 500: 5}},
    5: {"name": "Passing TDs", "value": 6},
    6: {"name": "Interceptions", "value": -2},
    58: {"name": "Pick Sixes Thrown", "value": -4},
    # Rushing
    8: {"name": "Rushing Attempts", "value": 0.33},
    9: {"name": "Rushing Yards", "value": 0.1, "bonuses": {100: 3, 150: 4, 200: 5}},
    10: {"name": "Rushing TDs", "value": 6},
    81: {"name": "Rushing 1st Downs", "value": 1},
    # Receiving
    11: {"name": "Receptions", "value": 1},
    12: {"name": "Receiving Yards", "value": 0.1, "bonuses": {100: 3, 150: 4, 200: 5}},
    13: {"name": "Receiving TDs", "value": 6},
    80: {"name": "Receiving 1st Downs", "value": 1},
    # Returns & misc
    14: {"name": "Return Yards", "value": 0.1, "bonuses": {200: 3, 250: 4, 300: 5}},
    15: {"name": "Return TDs", "value": 6},
    16: {"name": "2-Point Conversions", "value": 2},
    17: {"name": "Fumbles", "value": -1},
    18: {"name": "Fumbles Lost", "value": -2},
    57: {"name": "Off Fumble Return TD", "value": 6},
    82: {"name": "Extra Point Returned", "value": 2},
    # Kicking
    19: {"name": "FG 0-19", "value": 3},
    20: {"name": "FG 20-29", "value": 3},
    21: {"name": "FG 30-39", "value": 3},
    22: {"name": "FG 40-49", "value": 4},
    23: {"name": "FG 50+", "value": 5},
    24: {"name": "Missed FG 0-19", "value": -3},
    25: {"name": "Missed FG 20-29", "value": -2},
    26: {"name": "Missed FG 30-39", "value": -1},
    29: {"name": "PAT Made", "value": 1},
    30: {"name": "PAT Missed", "value": -1},
    # Defense / special teams
    32: {"name": "Sacks", "value": 1},
    33: {"name": "Interceptions (D)", "value": 2},
    34: {"name": "Fumble Recovery", "value": 2},
    35: {"name": "Defensive TD", "value": 6},
    36: {"name": "Safety", "value": 2},
    37: {"name": "Blocked Kick", "value": 2},
    67: {"name": "4th Down Stops", "value": 2},
    68: {"name": "Tackles for Loss", "value": 1},
    77: {"name": "Three and Outs", "value": 1},
    # Points allowed tiers
    50: {"name": "PA 0", "value": 10},
    51: {"name": "PA 1-6", "value": 7},
    52: {"name": "PA 7-13", "value": 4},
    53: {"name": "PA 14-20", "value": 1},
    54: {"name": "PA 21-27", "value": 0},
    55: {"name": "PA 28-34", "value": -1},
    56: {"name": "PA 35+", "value": -4},
    # Yards allowed tiers
    70: {"name": "YA negative", "value": 20},
    71: {"name": "YA 0-99", "value": 10},
    72: {"name": "YA 100-199", "value": 7},
    73: {"name": "YA 200-299", "value": 4},
    74: {"name": "YA 300-399", "value": 0},
    75: {"name": "YA 400-499", "value": -4},
    76: {"name": "YA 500+", "value": -7},
}


def num(v):
    """Yahoo returns points as strings like '.5' / '-.5' / '6'. Normalize to float."""
    return float(v)


def parse_raw(raw: dict) -> tuple[dict, dict]:
    """From a raw settings tree return (id->name, id->rule) where rule =
    {'value': float, 'bonuses': {target:int -> points:float}}."""
    tree = objectpath.Tree(raw)
    id_name = {}
    for s in tree.execute("$..stat_categories..stat"):
        if isinstance(s, dict) and "stat_id" in s:
            id_name[int(s["stat_id"])] = s.get("name", s.get("display_name", "?"))
    rules = {}
    for s in tree.execute("$..stat_modifiers..stat"):
        if not isinstance(s, dict) or "stat_id" not in s:
            continue
        sid = int(s["stat_id"])
        rule = {"value": num(s["value"])}
        if "bonuses" in s:
            bl = s["bonuses"]
            bl = bl if isinstance(bl, list) else [bl]
            rule["bonuses"] = {
                int(b["bonus"]["target"]): num(b["bonus"]["points"]) for b in bl
            }
        rules[sid] = rule
    return id_name, rules


def fmt_rule(rule: dict | None) -> str:
    if rule is None:
        return "—(absent)"
    s = f"{rule['value']:g}"
    if rule.get("bonuses"):
        s += (
            " +bonus{"
            + ", ".join(f"{t}:{p:g}" for t, p in sorted(rule["bonuses"].items()))
            + "}"
        )
    return s


def rules_equal(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return a is b
    if a["value"] != b["value"]:
        return False
    return a.get("bonuses", {}) == b.get("bonuses", {})


def expected_rule(sid: int) -> dict | None:
    e = EXPECTED.get(sid)
    if e is None:
        return None
    r = {"value": float(e["value"])}
    if "bonuses" in e:
        r["bonuses"] = {t: float(p) for t, p in e["bonuses"].items()}
    return r


def main():
    live_key = sys.argv[1] if len(sys.argv) > 1 else LIVE_2026
    from ffi.db import connect
    from ffi.yahoo_client import get_league, get_session, yahoo_call

    # --- prior season (offline, from DB): a second witness for the diff ---
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "select settings_payload from raw.yahoo_league_settings where league_key=%s",
        (PRIOR_2025,),
    )
    row = cur.fetchone()
    prior_rules = {}
    if row:
        sm = row[0].get("stat_modifiers", {}).get("stats", [])
        for s in sm:
            st = s["stat"]
            sid = int(st["stat_id"])
            rule = {"value": num(st["value"])}
            if "bonuses" in st:
                rule["bonuses"] = {
                    int(b["bonus"]["target"]): num(b["bonus"]["points"])
                    for b in st["bonuses"]
                }
            prior_rules[sid] = rule
    else:
        print(f"(no stored {PRIOR_2025} payload — 2025 column will be blank)")

    # --- live season: one raw Yahoo call carries names + modifiers ---
    print(f"Fetching live scoring settings for {live_key} ...")
    session = get_session()
    lg = get_league(session, live_key)
    # get_settings_raw builds "league/{}/settings" and needs the FULL league key
    # (e.g. 470.l.152123), not the bare numeric id.
    raw = yahoo_call(lg.yhandler.get_settings_raw, live_key)
    id_name, live_rules = parse_raw(raw)

    all_ids = sorted(set(EXPECTED) | set(live_rules) | set(prior_rules))
    print(
        f"\n{'id':>3}  {'stat':<22} {'2026 LIVE':<24} {'2025':<24} {'league_rules.md':<24} verdict"
    )
    print("-" * 108)
    mismatches = []
    for sid in all_ids:
        name = id_name.get(sid) or EXPECTED.get(sid, {}).get("name", "?")
        live = live_rules.get(sid)
        prior = prior_rules.get(sid)
        exp = expected_rule(sid)
        exp_known = sid in EXPECTED
        ok_doc = (not exp_known) or rules_equal(live, exp)
        ok_yoy = (sid not in prior_rules) or rules_equal(live, prior)
        if exp_known and not ok_doc:
            verdict = "MISMATCH vs rules"
            mismatches.append((sid, name, "doc", fmt_rule(live), fmt_rule(exp)))
        elif not ok_yoy:
            verdict = "CHANGED vs 2025"
            mismatches.append((sid, name, "yoy", fmt_rule(live), fmt_rule(prior)))
        elif not exp_known:
            verdict = "extra (not in rules doc)"
        else:
            verdict = "ok"
        print(
            f"{sid:>3}  {name:<22} {fmt_rule(live):<24} {fmt_rule(prior):<24} {fmt_rule(exp):<24} {verdict}"
        )

    print("\n=== SUMMARY ===")
    if not mismatches:
        print(
            "PASS — every documented stat matches live 2026, and no stat changed vs 2025."
        )
    else:
        print(f"{len(mismatches)} discrepancy(ies):")
        for sid, name, kind, got, want in mismatches:
            ref = "league_rules.md" if kind == "doc" else "2025 payload"
            print(f"  stat {sid} ({name}): live={got}  vs {ref}={want}")
    # stats documented in rules but entirely absent live = silent scoring loss
    missing = [sid for sid in EXPECTED if sid not in live_rules]
    if missing:
        print(
            f"NOTE: documented ids absent from live modifiers: {missing} "
            "(may be default-scored, not custom — verify names above)."
        )


if __name__ == "__main__":
    main()
