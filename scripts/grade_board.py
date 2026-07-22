#!/usr/bin/env python3
"""Grade a full external draft board under OUR league scoring.

Answers two questions:
  1. How does every team's OPTIMAL starting lineup project under our scoring
     (2QB/2RB/3WR/1TE/1FLEX/1K/1DEF) -> rank all 12.
  2. Did the actual draft order track OUR valuation, or a generic (FP national
     superflex ADP) order? Spearman(our-value-rank, draft-order) + the biggest
     divergences = where our-scoring edge was left on the board.

The board is a 19-row x 12-col transcription of an FP mock (col = team, in
round-1 pick order). Swap BOARD/TEAMS for a different draft, or pass
`--from-json <export>` to grade a draft_console.py export (its native format is
an ordered pick list with mine/other flags; `load_console_export` reconstructs
the 12-column snake grid this grader expects).
"""
import argparse
import json
from collections import defaultdict

from ffi.db import connect

TEAMS = [
    "BEAST MODE",
    "Admiral GAC",
    "BUSTOS",
    "Footloose",
    "HE HATE ME",
    "Kegs by Solis",
    "Marina del Rob",
    "Spoerts(US)",
    "Mr. E!",
    "Pat's Blue Rib",
    "Royer",
    "Monster",
]

# rows = rounds 1..19; each row is col1..col12 in the on-screen order.
BOARD = [
    [
        "Josh Allen",
        "Lamar Jackson",
        "Drake Maye",
        "Joe Burrow",
        "Jayden Daniels",
        "Ja'Marr Chase",
        "Jalen Hurts",
        "Justin Herbert",
        "Caleb Williams",
        "Jaxson Dart",
        "Puka Nacua",
        "Dak Prescott",
    ],
    [
        "Omarion Hampton",
        "Jonathan Taylor",
        "James Cook",
        "Ashton Jeanty",
        "Christian McCaffrey",
        "Jaxon Smith-Njigba",
        "Amon-Ra St. Brown",
        "Brock Purdy",
        "Trevor Lawrence",
        "Jahmyr Gibbs",
        "Bijan Robinson",
        "Patrick Mahomes",
    ],
    [
        "Saquon Barkley",
        "Justin Jefferson",
        "A.J. Brown",
        "CeeDee Lamb",
        "Drake London",
        "Rashee Rice",
        "Bo Nix",
        "Derrick Henry",
        "Chase Brown",
        "Matthew Stafford",
        "Nico Collins",
        "Zay Flowers",
    ],
    [
        "Breece Hall",
        "Trey McBride",
        "Kyler Murray",
        "Brock Bowers",
        "Jared Goff",
        "DeVonta Smith",
        "Malik Nabers",
        "Jeremiyah Love",
        "Kenneth Walker",
        "De'Von Achane",
        "George Pickens",
        "Chris Olave",
    ],
    [
        "Emeka Egbuka",
        "Tee Higgins",
        "Tetairoa McMillan",
        "Ladd McConkey",
        "Garrett Wilson",
        "Josh Jacobs",
        "Cam Skattebo",
        "Terry McLaurin",
        "Jaylen Waddle",
        "Luther Burden",
        "Jordan Love",
        "Quinshon Judkins",
    ],
    [
        "Jameson Williams",
        "Tyler Shough",
        "Javonte Williams",
        "Kyren Williams",
        "Travis Etienne",
        "Malik Willis",
        "Carnell Tate",
        "Colston Loveland",
        "Davante Adams",
        "Rome Odunze",
        "Baker Mayfield",
        "Tyler Warren",
    ],
    [
        "C.J. Stroud",
        "David Montgomery",
        "Mike Evans",
        "Cam Ward",
        "DJ Moore",
        "Sam Darnold",
        "TreVeyon Henderson",
        "Bucky Irving",
        "Christian Watson",
        "D'Andre Swift",
        "Marvin Harrison",
        "Courtland Sutton",
    ],
    [
        "Chris Godwin",
        "Jadarian Price",
        "Rachaad White",
        "Quentin Johnston",
        "Jaylen Warren",
        "Bhayshul Tuten",
        "Alec Pierce",
        "Parker Washington",
        "Brian Thomas",
        "Jordyn Tyson",
        "Tucker Kraft",
        "DK Metcalf",
    ],
    [
        "Michael Pittman",
        "Josh Downs",
        "Rico Dowdle",
        "Tony Pollard",
        "Rhamondre Stevenson",
        "Jonathon Brooks",
        "RJ Harvey",
        "Sam LaPorta",
        "Makai Lemon",
        "Chuba Hubbard",
        "Kyle Monangai",
        "Aaron Jones",
    ],
    [
        "Kyle Pitts",
        "Ricky Pearsall",
        "Dylan Sampson",
        "Michael Wilson",
        "Jacory Croskey-Merritt",
        "Tyrone Tracy",
        "Blake Corum",
        "Daniel Jones",
        "Jayden Reed",
        "J.K. Dobbins",
        "Kenny Gainwell",
        "Jordan Mason",
    ],
    [
        "Wan'Dale Robinson",
        "Chris Rodriguez",
        "Tyler Allgeier",
        "Keaton Mitchell",
        "Woody Marks",
        "Jonah Coleman",
        "Alvin Kamara",
        "Jakobi Meyers",
        "Jordan Addison",
        "Zach Charbonnet",
        "Tyjae Spears",
        "Isiah Pacheco",
    ],
    [
        "Emmett Johnson",
        "Romeo Doubs",
        "Emanuel Wilson",
        "Matthew Golden",
        "Brian Robinson",
        "Tank Bigsby",
        "Braelon Allen",
        "Xavier Worthy",
        "Harold Fannin",
        "James Conner",
        "Bryce Young",
        "Denver Broncos",
    ],
    [
        "Aaron Rodgers",
        "Fernando Mendoza",
        "Ray Davis",
        "Jacoby Brissett",
        "Kimani Vidal",
        "Tre Tucker",
        "Stefon Diggs",
        "Jayden Higgins",
        "George Kittle",
        "Khalil Shakir",
        "Travis Kelce",
        "Mike Washington",
    ],
    [
        "Dalton Kincaid",
        "Jalen McMillan",
        "Omar Cooper",
        "Rashid Shaheed",
        "Nicholas Singleton",
        "Sean Tucker",
        "Jauan Jennings",
        "Los Angeles Rams",
        "Geno Smith",
        "Jalen Coker",
        "KC Concepcion",
        "Jerry Jeudy",
    ],
    [
        "Jalen Nailor",
        "Isaiah Likely",
        "Antonio Williams",
        "Jake Ferguson",
        "Tre' Harris",
        "Kaytron Allen",
        "Adonai Mitchell",
        "Houston Texans",
        "Ty Johnson",
        "Travis Hunter",
        "Tua Tagovailoa",
        "Deshaun Watson",
    ],
    [
        "Demond Claiborne",
        "Jordan James",
        "Kenyon Sadiq",
        "MarShawn Lloyd",
        "Mark Andrews",
        "Dallas Goedert",
        "Chig Okonkwo",
        "Jaylen Wright",
        "Jaydon Blue",
        "Juwan Johnson",
        "Seattle Seahawks",
        "Justice Hill",
    ],
    [
        "Ryan Flournoy",
        "George Holani",
        "Greg Dulcich",
        "Kaelon Black",
        "Brenton Strange",
        "Michael Penix",
        "Oronde Gadsden",
        "Ollie Gordon",
        "Ja'Lynn Polk",
        "Philadelphia Eagles",
        "Alec Ingold",
        "Calvin Ridley",
    ],
    [
        "Cleveland Browns",
        "Baltimore Ravens",
        "Los Angeles Chargers",
        "Pittsburgh Steelers",
        "Jacksonville Jaguars",
        "New England Patriots",
        "Minnesota Vikings",
        "Brandon Aubrey",
        "Noah Whittington",
        "Dalton Schultz",
        "DJ Giddens",
        "Hunter Henry",
    ],
    [
        "Cameron Dicker",
        "Ka'imi Fairbairn",
        "Cam Little",
        "Jason Myers",
        "Eddy Pineiro",
        "Tyler Loop",
        "Andy Borregales",
        "Troy Franklin",
        "Cairo Santos",
        "Evan McPherson",
        "Jake Bates",
        "Tyler Bass",
    ],
]

DST_HINT = {  # board label -> nickname to match our DEF row name on
    "Denver Broncos": "Broncos",
    "Los Angeles Rams": "Rams",
    "Houston Texans": "Texans",
    "Seattle Seahawks": "Seahawks",
    "Philadelphia Eagles": "Eagles",
    "Cleveland Browns": "Browns",
    "Baltimore Ravens": "Ravens",
    "Los Angeles Chargers": "Chargers",
    "Pittsburgh Steelers": "Steelers",
    "Jacksonville Jaguars": "Jaguars",
    "New England Patriots": "Patriots",
    "Minnesota Vikings": "Vikings",
}


def overall(round_1indexed: int, col_1indexed: int) -> int:
    r, c = round_1indexed, col_1indexed
    return (r - 1) * 12 + (c if r % 2 == 1 else 13 - c)


def load_console_export(path: str):
    """Reconstruct (BOARD, TEAMS) from a draft_console.py export
    ({my_slot, teams, rounds, picks:[{overall, name, mine}]}). Each pick's
    overall maps to (round, col) via the same snake order this grader uses, so
    the ordered pick stream becomes the 12-column grid. Empty cells (short/
    partial drafts) are '' and skipped in main. The exporting seat gets a
    '(US)' team name so the ranking star lands on you."""
    d = json.loads(
        open(path).read_text() if hasattr(path, "read_text") else open(path).read()
    )
    n_teams, n_rounds, my_slot = d.get("teams", 12), d.get("rounds", 19), d["my_slot"]
    grid = [["" for _ in range(n_teams)] for _ in range(n_rounds)]
    for pk in d["picks"]:
        ov = pk["overall"]
        r = (ov - 1) // n_teams + 1
        idx = (ov - 1) % n_teams
        col = idx + 1 if r % 2 == 1 else n_teams - idx
        if 1 <= r <= n_rounds:
            grid[r - 1][col - 1] = pk["name"]
    teams = [f"Team {i}" for i in range(1, n_teams + 1)]
    if 1 <= my_slot <= n_teams:
        teams[my_slot - 1] = f"Team {my_slot}(US)"
    return grid, teams


def load_valuation(conn):
    cur = conn.cursor()
    cur.execute(
        """select x.name, pv.position, pv.proj_points, pv.vorp
                   from valuation.player_value pv
                   join public.player_id_xwalk x on x.xwalk_id = pv.xwalk_id
                   where pv.scenario='qb_hoard_12' and pv.config_version=1"""
    )
    rows = [(n, p, float(pr), float(v)) for (n, p, pr, v) in cur.fetchall()]
    by_name = {}
    for name, pos, proj, vorp in rows:
        by_name.setdefault(name.lower(), (name, pos, proj, vorp))
    return rows, by_name


def match(label, rows_by_name, all_rows):
    if label in DST_HINT:
        nick = DST_HINT[label].lower()
        for name, pos, proj, vorp in all_rows:
            if pos == "DEF" and nick in name.lower():
                return (name, pos, proj, vorp)
        return None
    lo = label.lower()
    if lo in rows_by_name:
        return rows_by_name[lo]
    first, last = lo.split(" ", 1)[0], lo.split(" ")[-1]
    cands = [
        (n, p, pr, v)
        for (n, p, pr, v) in all_rows
        if last in n.lower() and first[:4] in n.lower()
    ]
    if not cands:
        cands = [
            (n, p, pr, v) for (n, p, pr, v) in all_rows if last == n.lower().split()[-1]
        ]
    return max(cands, key=lambda t: t[2]) if cands else None


def optimal(players):  # players: list of (pos, proj)
    byp = defaultdict(list)
    for pos, proj in players:
        byp[pos].append(proj)
    for pos in byp:
        byp[pos].sort(reverse=True)
    req = {"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1}
    total, used = 0.0, defaultdict(int)
    for pos, n in req.items():
        take = byp[pos][:n]
        total += sum(take)
        used[pos] = len(take)
    flex = sorted(
        [p for pos in ("RB", "WR", "TE") for p in byp[pos][used[pos] :]], reverse=True
    )
    if flex:
        total += flex[0]
    return total


def spearman(xs, ys):
    n = len(xs)

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        for rank, i in enumerate(order):
            r[i] = rank
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    vy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


def main(board=None, teams=None):
    board = board if board is not None else BOARD
    teams = teams if teams is not None else TEAMS
    n_teams = len(teams)
    conn = connect()
    all_rows, by_name = load_valuation(conn)

    team_players = defaultdict(list)
    drafted = []
    unmatched = []
    for r_idx, row in enumerate(board, start=1):
        for c_idx, label in enumerate(row, start=1):
            if not label:
                continue  # empty cell (partial/short draft export)
            m = match(label, by_name, all_rows)
            ov = overall(r_idx, c_idx)
            if m is None:
                unmatched.append((ov, label))
                continue
            name, pos, proj, vorp = m
            team_players[c_idx].append((pos, proj))
            drafted.append((ov, label, name, pos, proj, vorp))

    print("=== TEAM RANKING — optimal starters, season proj pts (our scoring) ===")
    scored = sorted(
        ((optimal(team_players[c]), teams[c - 1]) for c in range(1, n_teams + 1)),
        reverse=True,
    )
    for rank, (tot, name) in enumerate(scored, 1):
        star = "  <== YOU" if name.endswith("(US)") else ""
        print(f"  {rank:>2}. {name:<18} {tot:>7.1f}{star}")

    skill = [d for d in drafted if d[3] in ("QB", "RB", "WR", "TE")]
    rho = spearman([d[0] for d in skill], [-d[5] for d in skill])
    print(
        f"\nSpearman(draft order, our value), n={len(skill)}: {rho:.3f}  "
        "(1.0 = board follows our scoring; lower = more edge left on board)"
    )
    if unmatched:
        print(f"\n(unmatched — excluded: {', '.join(l for _, l in unmatched)})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--from-json",
        help="grade a draft_console.py export instead of the hardcoded BOARD",
    )
    args = ap.parse_args()
    if args.from_json:
        b, t = load_console_export(args.from_json)
        main(board=b, teams=t)
    else:
        main()
