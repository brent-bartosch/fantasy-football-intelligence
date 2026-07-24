# Breakout-Notes Layer — Design

Date: 2026-07-24 · Status: approved (implementation deferred until candidate list is reviewed)

## Problem

The cheat sheet ranks players by our-scoring projections, VORP, tier, and ADP.
That surface is blind to *trajectory*: a late-round RB whose situation just
flipped (new QB, rebuilt O-line, vacated touches), a receiver returning from
an injury-discounted season, a year-2/3 player positioned for a leap. These
are exactly the players a scan of proj/ADP won't flag. We want a research
layer that puts that context on the board without touching any number.

## Decisions (user-confirmed 2026-07-24)

1. **Annotations only.** Notes never move proj/VORP/tier. If a note convinces
   the operator, the existing capped path (`confirm_signals.py` →
   `signals.adjustments`) remains the only way a number moves.
2. **Deep research now + one refresh before the ~Aug 22 freeze.** No
   automation, no launchd changes.
3. **Surface: HTML cheat sheet only** (`reports/cheat-sheet.html`). Markdown
   sheet and draft console unchanged.
4. **Gate:** operator reviews the researched candidate list before the
   cheat-sheet integration is built.

## Components

### 1. Research report (per pass)

`docs/research/YYYY-MM-DD-breakout-candidates.md` — multi-source, cited web
research on non-obvious 2026 breakout candidates. Emphasis:

- **situation** — new QB/OC/scheme, rebuilt O-line, vacated targets/carries
- **post-injury** — 2025 season lost/dented, healthy now, ADP still discounted
- **year-n-leap** — year 2–3 players with role + efficiency signals
- **role-path** — late-round players one depth-chart event from a starting role

Skew toward mid/late-ADP RB/WR (the stated goal), but include QB/TE where the
2QB / full-PPR / +1-first-down scoring makes the case unusual vs. national ADP.

### 2. Curated data file

`data/breakout-notes-2026.yaml` — hand-curated distillation of the report,
edited freely by the operator. One entry per player:

```yaml
- name: <player name as it appears in the pool>
  pos: RB            # QB | RB | WR | TE
  category: situation   # situation | post-injury | year-n-leap | role-path
  thesis: "<one line, <=140 chars>"
  sources: [<url>, ...]
```

### 3. Cheat-sheet join (`cheat_sheet_html.py`)

- Load the YAML at build time; match each entry to the pool by
  (name, position). **Fail loud** on any unmatched entry (typo, renamed
  player, not in pool at current DEPTH) — build aborts listing the offenders;
  a note must never silently vanish.
- Matched players render a small category-colored badge next to the name;
  tap/hover reveals the thesis. A "breakouts" filter toggle shows only
  flagged players.
- No changes to ordering, cross-off, persistence, or any number.

## Out of scope

Score/VORP/tier effects, `signals.*` writes, draft console, markdown cheat
sheet, launchd/morning-chain automation, any recurring ingest.

## Testing

- Unit: YAML load + pool matching (exact match, unmatched → loud error with
  entry names, position-mismatch counts as unmatched).
- Golden-eyeball: rebuild `cheat-sheet.html`, verify badges/filter manually
  (the HTML layer has no test harness today; keep it that way — YAGNI).
