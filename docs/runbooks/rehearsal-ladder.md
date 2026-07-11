# Rehearsal ladder (ADR D7 / D8)

The ladder that converts "the assistant should work" into "the assistant is
*measured* working" before the 2026 draft (Aug 29–30). Three levels, each
gating the next; a level passes only when **all its drills pass twice
consecutively**. The four written acceptance criteria (ADR D7) are the same at
every level — what changes is the realism of the plumbing under them.

## The four written pass criteria (verbatim)

1. **Poll lag p95 < 15s** (measured pick-visible-to-applied).
2. **Token refresh mid-session without pick loss.**
3. **Forced-999 → MANUAL switchover < 30s** (from the injected 999 to the
   operator completing a manual pick — human-timed).
4. **Crash → resume with full state** (kill mid-draft; `--resume` reproduces
   taken / counts / overall / mode exactly).

The headless harness (`scripts/drill_draft.py`) measures 1, 2, 4 and the
*machine side* of 3 (injection → MANUAL banner, logged exactly once) with zero
Yahoo calls. The **human half of 3** — an operator actually completing a manual
pick within 30s of the banner — can only be timed with a person at the keyboard;
it runs at Level 1 with the user. Every run appends a row to `rehearsal-log.md`
(committed — drill history is draft-day evidence).

```bash
uv run python scripts/drill_draft.py --drill lag     --season 2024
uv run python scripts/drill_draft.py --drill 999     --season 2024
uv run python scripts/drill_draft.py --drill refresh --season 2024
uv run python scripts/drill_draft.py --drill crash   --season 2024
```

## The ladder

| Level | Venue | What it exercises | Entry criterion | Exit criterion |
|---|---|---|---|---|
| **1** | FP Draft Wizard browser mocks + the headless drills | The assistant core, human-in-the-loop pace, and the operator's own reflexes (typing `p <name>`, reading modes, the 999 discipline). Automation is kept **OFF** the API-key account (R13). | Headless drills 1/2/4 + machine-side 3 all PASS once. | All four criteria pass **twice consecutively**, incl. the human-timed <30s 999 switchover, in ≥2 separate mock drafts. |
| **2** | A private Yahoo test league (the ONLY live-plumbing venue) | The real `lg.draft_results` payload shape, real token refresh against Yahoo, real 999 behavior, real team_slot mapping from `lg.teams()`. Mocks are not API-visible and Yahoo has **no draft-submission API**, so this league is the only place the live poll path can be exercised end-to-end. | Level 1 exit met. | All four criteria pass **twice consecutively** against the live test league, including a real (not injected) mid-draft token refresh and a crash/resume on live data. |
| **3** | Full dress rehearsal, user-in-the-loop | The whole thing under realistic conditions: real board, real clock, operator making real decisions. Every time the operator overrides a recommendation, that decision is logged as a `note` event and **reviewed afterward** (was the model wrong, or the operator?). | Level 2 exit met. | A full-length rehearsal completes with all four criteria holding and no unexplained state divergence. This passing rehearsal is what gets tagged `draft-day`. |

### Per-level entry/exit detail

- **Each level gates the next.** Do not start Level 2 plumbing until Level 1
  has passed twice consecutively; do not schedule the Level 3 dress rehearsal
  until Level 2 has.
- **"Twice consecutively"** means two clean passes with no failure in between.
  A single failure resets that level's counter — investigate, fix, and start the
  two-pass count over. This is deliberate: draft day is one shot, so we want the
  green state to be reproducible, not a fluke.
- **Level 3 override logging.** During the dress rehearsal, any operator
  deviation from the recommendation is recorded as a `note` event in the draft
  log and reviewed after the rehearsal. The point is calibration: if the
  operator keeps overriding the model in the same spot, either the model needs a
  fix before draft day or the operator needs to trust it there.

## Level-2 league setup — PENDING USER INPUTS

Level 2 needs a live Yahoo venue that only the user can create. This runbook is
the request. **Before the freeze (~Aug 22), the user must:**

1. **Create a private Yahoo fantasy football league** (season 2026) on the same
   Yahoo account whose API credentials the assistant uses.
2. **Set it to 12 teams** — the assistant assumes exactly 12 slots
   (`load_team_slots` / `_live_team_slots` both refuse any other count).
3. **Fill the other 11 seats with autodraft bots** so a draft can run
   unattended end-to-end. (Yahoo has no draft-submission API, so the assistant
   cannot make picks for us; the bots draft, the assistant *observes*.)
4. **Schedule a draft** (a few of them — Level 2 needs at least two clean
   consecutive passes, and each is a full bot draft). Give each a start time you
   can be at the keyboard for.
5. **Confirm the renewed 2026 league key** for the *real* NAJEE league via
   `scripts/list_my_leagues.py` once the league renews (the 2025 key was
   `461.l.326814`; the game prefix changes on renewal). The draft-day runbook
   needs it.

Once the test league exists and a bot draft is scheduled, Level 2 runs the same
four criteria against it — the drills' fake transport is replaced by the real
poller, but the pass/fail bars are identical.

## Schedule (relative to the Aug 29–30 draft)

| When | What |
|---|---|
| Now → early Aug | **Level 1**: 5–10 FP Draft Wizard mock drafts per day, human-paced, automation OFF the API-key account (R13). Run the headless drills alongside; log each. |
| Once user creates the test league (target: by mid-Aug) | **Level 2**: bot drafts in the private Yahoo league; validate the live `lg.draft_results` payload on the FIRST poll before trusting anything. |
| ~1 week before draft | **Level 3**: full dress rehearsal, user-in-the-loop, override logging on. |
| **~Aug 22 — FREEZE** | Stop changing code. The last passed FULL rehearsal at/after the freeze is tagged `draft-day`. Only doc/board-data refreshes after this. |
| Aug 29–30 | **Draft.** Runbook step 1 = `git checkout draft-day`. |

## Tag protocol (ADR D8)

- **`git tag rehearsal-N`** (annotated) after each passed drill *session* — the
  message lists which drills passed and their metrics. `rehearsal-1` is the
  first: the headless drills 1 (lag), 2 (999 machine-side), 4 (crash), plus the
  refresh drill (criterion 2), all PASS. N increments per subsequent passed
  session (Level 2 and Level 3 sessions get their own tags).
- **`git tag draft-day`** = the **last passed FULL rehearsal** (a complete
  Level 3 dress rehearsal with all four criteria holding). This is the commit
  draft day runs from.
- **Freeze ≈ Aug 22.** After the freeze, `draft-day` should only ever move
  forward onto a later *passed* full rehearsal, never onto un-rehearsed code.
- **The draft-day runbook's step 1 is `git checkout draft-day`** — you draft
  from the tag, not from `main`.

## Carry-forward notes for the live runs (Level 2+)

These are the seams the fake-transport drills could not exercise; verify each on
the first live run:

- **(a) Validate the live `lg.draft_results` payload shape** at the first
  Level-2 poll before trusting the board: each made pick must carry a bare
  numeric `player_id` (not a `461.p.*` key) and a `team_key` present in
  `team_slots` (Task 13 seam, flagged in `poller.py`).
- **(b) `team_slots` comes from `yahoo_call(lg.teams)` at session start** for
  the live season (the current season's `teams` rows don't exist yet, so
  `load_team_slots` — which the rehearsal drills use against historical seasons
  — does not apply live).
- **(c) Apply pending migrations before any live run** (Task 15 lesson: a live
  run against an un-migrated DB fails loud on the first query).
- **(d) One log per draft.** A fresh session refuses to open onto a non-empty
  log by design; use `--resume` to continue an interrupted draft, `--log-path`
  to start a genuinely new one.
