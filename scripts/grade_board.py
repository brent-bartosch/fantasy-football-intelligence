#!/usr/bin/env python3
"""Grade a full external draft board under OUR league scoring.

Answers two questions:
  1. How does every team's OPTIMAL starting lineup project under our scoring
     (2QB/2RB/3WR/1TE/1FLEX/1K/1DEF) -> rank all 12.
  2. Did the actual draft order track OUR valuation, or a generic (FP national
     superflex ADP) order? Spearman(our-value-rank, draft-order) + the biggest
     divergences = where our-scoring edge was left on the board.

The board is a 19-row x 12-col transcription of an FP mock (col = team, in
round-1 pick order). Swap BOARD/TEAMS for a different draft.
"""
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
        "Matthew Stafford",
        "Caleb Williams",
        "Jalen Hurts",
        "Ja'Marr Chase",
        "Jahmyr Gibbs",
        "Justin Herbert",
        "Bijan Robinson",
    ],
    [
        "Jared Goff",
        "Bo Nix",
        "Jaxson Dart",
        "James Cook",
        "Jonathan Taylor",
        "Patrick Mahomes",
        "Christian McCaffrey",
        "Dak Prescott",
        "Amon-Ra St. Brown",
        "Jaxon Smith-Njigba",
        "Trevor Lawrence",
        "Puka Nacua",
    ],
    [
        "Justin Jefferson",
        "CeeDee Lamb",
        "Drake London",
        "Brock Purdy",
        "Ashton Jeanty",
        "Nico Collins",
        "Saquon Barkley",
        "A.J. Brown",
        "George Pickens",
        "Omarion Hampton",
        "Chris Olave",
        "Kenneth Walker",
    ],
    [
        "Zay Flowers",
        "Malik Nabers",
        "Garrett Wilson",
        "DeVonta Smith",
        "Jeremiyah Love",
        "Jaylen Waddle",
        "Derrick Henry",
        "Rashee Rice",
        "Brock Bowers",
        "De'Von Achane",
        "Trey McBride",
        "Chase Brown",
    ],
    [
        "Kyren Williams",
        "Travis Etienne",
        "Breece Hall",
        "Tee Higgins",
        "Emeka Egbuka",
        "Quinshon Judkins",
        "Ladd McConkey",
        "Josh Jacobs",
        "Javonte Williams",
        "Kyler Murray",
        "Bucky Irving",
        "Jordan Love",
    ],
    [
        "Tucker Kraft",
        "Rome Odunze",
        "DJ Moore",
        "Davante Adams",
        "Jameson Williams",
        "Tyler Warren",
        "Mike Evans",
        "Colston Loveland",
        "Baker Mayfield",
        "Luther Burden",
        "Terry McLaurin",
        "Tetairoa McMillan",
    ],
    [
        "TreVeyon Henderson",
        "Cam Skattebo",
        "Christian Watson",
        "David Montgomery",
        "Tyler Shough",
        "Carnell Tate",
        "Malik Willis",
        "D'Andre Swift",
        "C.J. Stroud",
        "Cam Ward",
        "Marvin Harrison",
        "Sam Darnold",
    ],
    [
        "Michael Pittman",
        "Chuba Hubbard",
        "Bhayshul Tuten",
        "Courtland Sutton",
        "Makai Lemon",
        "Wan'Dale Robinson",
        "Parker Washington",
        "DK Metcalf",
        "Jaylen Warren",
        "Jordyn Tyson",
        "Alec Pierce",
        "Brian Thomas",
    ],
    [
        "Kyle Pitts",
        "Tony Pollard",
        "Jadarian Price",
        "Kenny Gainwell",
        "Rhamondre Stevenson",
        "RJ Harvey",
        "Rico Dowdle",
        "Chris Godwin",
        "Harold Fannin",
        "Blake Corum",
        "Rachaad White",
        "Kyle Monangai",
    ],
    [
        "Alvin Kamara",
        "Tyler Allgeier",
        "Jordan Mason",
        "Tyrone Tracy",
        "Jordan Addison",
        "Isiah Pacheco",
        "Jayden Reed",
        "Jacory Croskey-Merritt",
        "Aaron Jones",
        "Quentin Johnston",
        "Jonathon Brooks",
        "J.K. Dobbins",
    ],
    [
        "Chris Rodriguez",
        "Woody Marks",
        "Zach Charbonnet",
        "Keaton Mitchell",
        "Josh Downs",
        "Dylan Sampson",
        "Xavier Worthy",
        "Tyjae Spears",
        "Houston Texans",
        "Ricky Pearsall",
        "Jonah Coleman",
        "Michael Wilson",
    ],
    [
        "Jayden Higgins",
        "Emanuel Wilson",
        "Omar Cooper",
        "Braelon Allen",
        "Romeo Doubs",
        "KC Concepcion",
        "Sam LaPorta",
        "Khalil Shakir",
        "Brian Robinson",
        "Matthew Golden",
        "Tank Bigsby",
        "Jakobi Meyers",
    ],
    [
        "Los Angeles Rams",
        "Emmett Johnson",
        "Adonai Mitchell",
        "James Conner",
        "Jalen Coker",
        "Jacoby Brissett",
        "Daniel Jones",
        "Bryce Young",
        "Geno Smith",
        "Rashid Shaheed",
        "Denzel Boston",
        "Stefon Diggs",
    ],
    [
        "Ryan Flournoy",
        "Nicholas Singleton",
        "Sean Tucker",
        "Mike Washington",
        "Jalen Nailor",
        "Malik Washington",
        "George Kittle",
        "Ray Davis",
        "Kimani Vidal",
        "Jalen McMillan",
        "Fernando Mendoza",
        "Travis Hunter",
    ],
    [
        "Aaron Rodgers",
        "Jerry Jeudy",
        "Jauan Jennings",
        "Tre' Harris",
        "Tre Tucker",
        "Oronde Gadsden",
        "Kaytron Allen",
        "Troy Franklin",
        "Kayshon Boutte",
        "Travis Kelce",
        "Antonio Williams",
        "Deebo Samuel",
    ],
    [
        "Demond Claiborne",
        "Hunter Henry",
        "Isaiah Likely",
        "Dallas Goedert",
        "Dalton Kincaid",
        "Denver Broncos",
        "Isaac TeSlaa",
        "Jake Ferguson",
        "Michael Penix",
        "Tua Tagovailoa",
        "Philadelphia Eagles",
        "Mark Andrews",
    ],
    [
        "Kaelon Black",
        "Brenton Strange",
        "Juwan Johnson",
        "Chig Okonkwo",
        "MarShawn Lloyd",
        "George Holani",
        "Jaylen Wright",
        "Justice Hill",
        "Ollie Gordon",
        "Jaydon Blue",
        "LeQuint Allen",
        "Malik Davis",
    ],
    [
        "Tyreek Hill",
        "Los Angeles Chargers",
        "Minnesota Vikings",
        "Baltimore Ravens",
        "Jacksonville Jaguars",
        "Malachi Fields",
        "Pittsburgh Steelers",
        "New England Patriots",
        "Jaylin Noel",
        "Brandon Aubrey",
        "Greg Dulcich",
        "Seattle Seahawks",
    ],
    [
        "Cameron Dicker",
        "Ka'imi Fairbairn",
        "Cam Little",
        "Jason Myers",
        "Eddy Pineiro",
        "Wil Lutz",
        "Tyler Loop",
        "Evan McPherson",
        "Cairo Santos",
        "Green Bay Packers",
        "Harrison Mevis",
        "Andy Borregales",
    ],
]

DST_HINT = {  # board label -> nickname to match our DEF row name on
    "Houston Texans": "Texans",
    "Los Angeles Rams": "Rams",
    "Denver Broncos": "Broncos",
    "Philadelphia Eagles": "Eagles",
    "Los Angeles Chargers": "Chargers",
    "Minnesota Vikings": "Vikings",
    "Baltimore Ravens": "Ravens",
    "Jacksonville Jaguars": "Jaguars",
    "Pittsburgh Steelers": "Steelers",
    "New England Patriots": "Patriots",
    "Seattle Seahawks": "Seahawks",
    "Green Bay Packers": "Packers",
}


def overall(round_1indexed: int, col_1indexed: int) -> int:
    r, c = round_1indexed, col_1indexed
    return (r - 1) * 12 + (c if r % 2 == 1 else 13 - c)


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
                return (name, pos, float(proj), float(vorp))
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


def main():
    conn = connect()
    all_rows, by_name = load_valuation(conn)

    # per-team optimal lineup
    team_players = defaultdict(list)  # col -> [(pos, proj)]
    drafted = []  # (overall, label, name, pos, proj, vorp)
    unmatched = []
    for r_idx, row in enumerate(BOARD, start=1):
        for c_idx, label in enumerate(row, start=1):
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
        ((optimal(team_players[c]), TEAMS[c - 1]) for c in range(1, 13)), reverse=True
    )
    for rank, (tot, name) in enumerate(scored, 1):
        star = "  <== YOU" if name.endswith("(US)") else ""
        print(f"  {rank:>2}. {name:<18} {tot:>7.1f}{star}")

    # edge: did draft order track our value? (skill only; K/DEF drafted late by all)
    skill = [d for d in drafted if d[3] in ("QB", "RB", "WR", "TE")]
    ov_list = [d[0] for d in skill]
    vorp_list = [d[5] for d in skill]
    rho = spearman(ov_list, [-v for v in vorp_list])  # our value desc vs draft order
    print(f"\n=== Did the board follow OUR scoring? ===")
    print(
        f"  Spearman(draft order, our VORP rank), n={len(skill)} skill picks: {rho:.3f}"
    )
    print(
        "  (1.0 = board perfectly follows our value; lower = more edge left on board)"
    )

    # biggest steals: high our-VORP, but fell late relative to value rank
    by_value = sorted(skill, key=lambda d: -d[5])
    value_rank = {d[2]: i + 1 for i, d in enumerate(by_value)}
    steals = sorted(
        skill, key=lambda d: (value_rank[d[2]] - d[0])
    )  # value_rank much < overall
    print("\n=== Biggest our-scoring STEALS (our value rank << where drafted) ===")
    for d in steals[:12]:
        ov, label, name, pos, proj, vorp = d
        print(
            f"  {name:<22} {pos}  our#{value_rank[name]:>3}  drafted #{ov:<3}  (+{ov-value_rank[name]:>3})  vorp {vorp:.0f}"
        )

    if unmatched:
        print(
            f"\n(unmatched labels — rookies/typos, excluded: "
            f"{', '.join(l for _,l in unmatched)})"
        )


if __name__ == "__main__":
    main()
