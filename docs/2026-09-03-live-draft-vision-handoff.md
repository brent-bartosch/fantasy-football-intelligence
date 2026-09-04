# Live-Draft Vision Handoff — LMU (14-team, 1-QB)

**For:** a vision-capable model (reads images) assisting a live Yahoo fantasy draft.
**Date:** 2026-09-03
**Your job:** read a screenshot of the Yahoo draft results, extract every pick, then
cross those players off the local draft board and tell the human to refresh.

---

## 1. The setup in one paragraph

This repo (`fantasy_football/`) has a self-contained offline draft board for two leagues:

- **NAJEE** — 12 teams, 2-QB (already drafted; ignore).
- **LMU Still Undefeated** — **14 teams, 1-QB**, full PPR, 6-pt pass TD, **no first-down
  scoring**. This is the league drafting now. League ID 878667.

The board is a single HTML file built by a script. The human is drafting live on Yahoo
(~1 min/pick) and needs the board re-aligned after each round. You cross players off by
**rebuilding the HTML with a "marks" file** — the projections/rankings are already baked,
so this is a sub-second operation, not a reprocessing.

## 2. The workflow (do this every time)

1. **Read the screenshot.** It shows the Yahoo draft results: a list of picks, each with
   an overall pick number, a round, a team, a **player name**, and a **position**.
2. **Extract the picks** — every player drafted so far, in draft order, with position.
3. **Decide mine vs gone** (see §5).
4. **Write a marks file** (JSON, §3).
5. **Run the build command** (§4).
6. **Respond** with just: `done — refreshed, <N> players crossed off` plus a one-line
   flag **only** if something is wrong (ambiguous name, player not on the board).

Never do the huge reprocessing. You only update who is crossed off.

## 3. The marks file

Write `data/lmu-marks.json` (any path works; pass it to the script):

```json
{
  "slot": 14,
  "taken": ["Bijan Robinson", "Jahmyr Gibbs", "Jonathan Taylor"],
  "mine": ["Jahmyr Gibbs"]
}
```

- `slot` — the human's draft position (1–14). They will tell you once Yahoo assigns it.
- `taken` — every drafted player's **full name**, in draft order (all 12/13 other teams'
  picks AND the human's picks). Only players *already off the board*, not future picks.
- `mine` — the subset of `taken` that are the human's own picks.

## 4. The build command

```bash
cd /Users/brentbartosch/Development/fantasy_football
uv run python scripts/draft_console.py --profile lmu --marks data/lmu-marks.json
```

Output: `reports/draft-console-lmu.html`. The human hard-refreshes that file
(`Cmd+Shift+R`). It must show the top-right badge **"✓ self-test 18/18"** — if it shows
**"✗ ENGINE DRIFT"**, stop and flag it, do not trust the board.

To rebuild **without** any pre-marks (fresh board): omit `--marks`.

## 5. Determining "mine" vs "gone"

The human's own team must be marked `mine` (green); every other drafted player `gone`
(struck through). You have two ways to know which is which:

1. **From the slot.** Snake order: round 1 runs pick 1→14, round 2 runs 14→1, and so on.
   If the human is slot 14, their picks are the last of every odd round and the first of
   every even round. When in doubt, ask the human or use method 2.
2. **From the human.** They may just say "I have: [names]" — trust that and put those in
   `mine`, everything else in `taken` as `gone`.

If you can't determine it, **ask** rather than guessing — a green/gone mix-up corrupts the
board (the "mine" rows drive the roster/round state).

## 6. Name-matching rules (important — fail loud)

Names are matched **case-insensitively** against the board's player list, but must be
**unambiguous and reasonably exact**. Rules:

- Use the **full name** as it appears on the board (e.g. "Bijan Robinson", "Ja'Marr Chase",
  "Kenneth Walker III"). Yahoo sometimes abbreviates — expand "B. Robinson" → "Bijan Robinson".
- **Watch out for the Jr./III/II suffix** ("Kenneth Walker III" ≠ "Kenneth Walker").
- **Apostrophes matter** ("Ja'Marr", "De'Von").
- If a name matches **more than one** board player (e.g. two "J. Williams"), the script
  refuses loudly. Use the position from the screenshot to disambiguate, or ask.
- If a name matches **zero** board players, the script raises. Either you misread the name,
  or the player is outside the board's depth (rare for the first ~250 picks). Flag it.

The script raises a `ValueError` on a bad name — that is correct fail-loud behavior, not a bug.

## 7. League facts you must keep straight

| Fact | LMU (this league) |
|---|---|
| Teams | 14 |
| QB starters | **1** (single-QB — do NOT treat QBs as scarce) |
| Roster | 1QB/2RB/3WR/1TE/1FLEX/1K/1DEF, 6 BN, 2 IR = 18 picks |
| Scoring | full PPR, 6-pt pass TD, +0.5/cmp −0.5/inc, +0.33/rush att, **no first-down points** |
| ADP column | standard 1-QB ADP (`adp_std`) — QBs sit low on the board, correctly |

Because it's **1-QB**, the human's first QB should come ~round 10–12, NOT early. If you
notice the human taking QBs in the first few rounds, flag it.

## 8. Validation checklist before you say "done"

- [ ] Marks file written with correct `slot`, `taken` (ordered), `mine`.
- [ ] Build command ran with no traceback (a `ValueError` = fix the name and re-run).
- [ ] `reports/draft-console-lmu.html` was written.
- [ ] (Optional, if you can run JS) the self-test badge is green — the build's Python
      golden trace already guarantees this; a `ValueError` is the only realistic failure.

## 9. Full command reference

```bash
cd /Users/brentbartosch/Development/fantasy_football

# Cross off players from a snapshot (the live workflow)
uv run python scripts/draft_console.py --profile lmu --marks data/lmu-marks.json

# Rebuild a clean board (no pre-marks)
uv run python scripts/draft_console.py --profile lmu

# The NAJEE (2-QB) board, if ever needed
uv run python scripts/draft_console.py

# Health checks (run only if you suspect a broken build)
uv run pytest -q
bash scripts/check_file_size.sh
python3 scripts/check_import_boundaries.py
```

Files:
- Board output: `reports/draft-console-lmu.html`
- Marks file: `data/lmu-marks.json` (gitignored, you create it)
- Board builder: `scripts/draft_console.py`
- League profile: `src/ffi/league_profile.py` (`LMU` = 14-team/1-QB)
