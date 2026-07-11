# Draft-day runbook (2026 — Aug 29–30)

The single sheet you drive the live draft from. It assumes the rehearsal ladder
(see `rehearsal-ladder.md`) has already passed and a `draft-day` git tag exists.
Everything here is boring on purpose: draft day is not the time to improvise.

**Step 1 is `git checkout draft-day`.** The tag is the last passed FULL
rehearsal (ADR D8); the working tree may have drifted since the freeze. Draft
from the tag, not from `main`.

---

## T-1 (the day/evening before)

- [ ] **Fresh DB backup + external copy.** `bash scripts/backup_db.sh`, then
      copy the resulting `backups/fantasy_football_<ts>.sql.gz` somewhere OFF
      this laptop (cloud drive or a USB stick). A backup that only exists on
      the machine you're drafting from is not a backup.
- [ ] **pg_restore drill (ADR D8) — and the PG_BIN version-alignment check.**
      Run the full `docs/runbooks/pg-restore-drill.md` (fresh backup → restore
      into a scratch DB → verify row counts → drop). This is where the
      Phase 3 `PG_BIN` gotcha lives: PATH resolves `psql`/`pg_restore` to
      Postgres 14.x (`/opt/homebrew/bin`) while the server runs 15.x. Before
      restoring, confirm the restore binary matches the server major version:
      ```bash
      /opt/homebrew/opt/postgresql@15/bin/psql --version   # must be 15.x
      psql -d fantasy_football -tAc "show server_version"    # must match major
      ```
      A 14-client restore of a 15-server dump can fail or subtly mis-restore
      under draft-day pressure — catch it here, a day early, not live.
- [ ] **Apply pending migrations to the live DB.** `ls migrations/*.sql` and
      confirm every one has been applied (Task 15 lesson: a live run against an
      un-migrated DB fails loud on the first signals/draft query). If unsure,
      re-apply — they are written to be idempotent.
- [ ] **Laptop power** — charger packed and tested; do not draft on battery.
- [ ] **Phone hotspot tested** — actually tether the laptop to the phone and
      load a page. The house wifi failing is the expected failure; the hotspot
      is the fallback, so prove it works cold, tonight.
- [ ] **Printed paper board.** The assistant writes
      `reports/paper-board-<date>.md` on every start (top-60 overall + top-15
      per position, with tiers). Print it. If every online path dies you draft
      from paper in PAPER mode — the board on the desk is the true floor.
- [ ] **Board freshness.** Re-run `uv run python scripts/ingest_sleeper.py
      --season 2026` and `uv run python scripts/build_valuation.py` so the ADP
      snapshot is < 36h old at draft time (the preflight refuses stale ADP
      unless you pass `--override-stale`; don't rely on the override).

## T-0 (draft room open)

1. **`git checkout draft-day`** — draft from the frozen, last-passed rehearsal
   tag. Confirm with `git describe --tags`.
2. **Launch the assistant:**
   ```bash
   uv run python scripts/draft_assistant.py \
     --league-key <2026 league key> --our-slot <S> --position <P>
   ```
   - `<2026 league key>` is the renewed NAJEE league key for the 2026 season
     (the 2025 key was `461.l.326814`; the game prefix changes on renewal —
     confirm it in `scripts/list_my_leagues.py` once the league is renewed).
   - `--our-slot` is our stable franchise slot; `--position` is our draft
     position once the draft order is posted. If the order is not yet known,
     start in `--no-poll` (pure MANUAL) and restart with `--position` once it
     is, using `--resume` on the same log.
3. **Verify the preflight banner is green:**
   - board vintage line shows an ADP snapshot < 36h old and a matching
     valuation snapshot (no mismatch/stale SystemExit);
   - `paper board written: reports/paper-board-<date>.md` printed;
   - `log: data/draft-logs/<date>-<key>.jsonl` printed (this is the source of
     truth — every pick is fsync'd to it before it's shown);
   - **first live poll:** confirm the `lg.draft_results` payload shape is what
     the poller expects — each made pick carries a bare numeric `player_id`
     (not a `461.p.*` key) and a `team_key` in your `team_slots`. This is the
     one seam that could not be exercised without the live endpoint (flagged in
     `poller.py` / Task 13); eyeball the first one or two applied picks against
     the Yahoo draft board before trusting counts/forecast.

## Mode cheat-sheet

| Mode | Meaning | What you do |
|---|---|---|
| **LIVE** | Polling Yahoo every ~5–7s; picks auto-applied. | Nothing — draft normally. `<enter>` for the recommendation on our clock. |
| **POLL-DEGRADED** | One poll failed; still trying, serving last-known board. | Keep going; it auto-recovers to LIVE on the next good poll. Watch the banner. |
| **MANUAL** | Auto-polling stopped (two failures, or a 999, or you set it). Board is last-known; YOU enter every pick. | Type `p <name>` for each pick as it happens on Yahoo. Sticky — it will NOT auto-return to LIVE. Flip back only when you trust the connection: `m live`. |
| **PAPER** | Everything online is dead; draft from the printed sheet. | Set `m paper`. Cross names off the paper board by hand; keep logging with `p <name>` so the log stays complete for resume. |

Keystrokes: `<enter>`/`r` recommend · `p <name>` manual pick · `u` undo last
manual pick · `b [pos]` best available · `s` status · `m live|manual|paper` ·
`q` quit. Undo only works on MANUAL picks (undoing a polled pick would desync
from Yahoo and is refused).

When to flip to PAPER: any time the assistant and Yahoo disagree about the board
and you can't reconcile in ~30s, or the machine itself is unresponsive. PAPER is
never wrong — it's just slower.

## The 999 rule (read before draft day)

If you see `error 999 (rate-limit lockout) -> MANUAL, no retry`: **do NOT
retry, do NOT restart the assistant to "reconnect."** Yahoo has locked this IP
for ~10–15 minutes; every additional call extends the lockout. You are in
MANUAL for **10–15 minutes minimum**. Enter picks by hand with `p <name>`; the
board is still correct (last-known), you're just the one moving it. After the
cooldown, `m live` to resume polling. The 999 drill (`drill_draft.py --drill
999`) proves the switch to MANUAL is immediate and logged exactly once — trust
it and keep drafting.

## Post-draft

1. **Import the log** into `draft.events` for after-action analysis:
   ```bash
   uv run python scripts/import_draft_log.py \
     --log data/draft-logs/<date>-<key>.jsonl --draft-id 2026-real
   ```
   (Re-run with `--replace` only if you need to overwrite an earlier import of
   the same `--draft-id`.)
2. **Archive the log.** Copy the JSONL to the same external location as the T-1
   backup — it is the durable record of exactly what happened and the input to
   every post-draft review.
