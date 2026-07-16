# Session Handoff — 2026-07-15

Pick-up doc for a fresh session. Full durable state is in auto-memory
(`fantasy-2026-project-state.md`); this is the "what just happened + what's next".

## TL;DR
This session found and deployed a real draft-strategy improvement, and correctly
*rejected* a valuation change that looked good but wasn't. Everything is committed
and **merged to `main`** (working tree clean). The live draft assistant now runs a
better strategy.

## What shipped (on `main`)
- **Live strategy deploy** (`scripts/draft_assistant.py`): the assistant built its
  `SessionConfig` with no `params`, silently using the WORST default
  (`qb_not_before=(1,1,1)` front-loads QB3 ~R3, TE cap 3). Now injects the
  backtest-validated config: **QB3 late** (`qb_not_before=(1,1,10)`,
  `qb_by_round=(2,5,14)`) + **TE cap 2**. 62 draft/strategy tests pass.
- **Tools:** `scripts/draft_diagnostic.py` (single-draft transcript + all-12 grade +
  `--backtest SEASON` actual-points grading — reviewed clean) and 6 stress-test
  harnesses: `qb_vorp_sweep`, `qb_timing_search`, `qb_bye_aware`, `qb_bye_h2h`,
  `qb_timing_h2h`, `positional_depth`. Plus `demo_single_draft.py` (light quick view).
- **Full paper trail** (a *no-change* outcome, done right): `docs/superpowers/
  {specs,risks,plans}/2026-07-14-qb-vorp-recalibration*` + `.../risks/*-adr.md`.

## Key findings (all graded on ACTUAL nflverse points, backtest 2023–25)
1. **The core principle:** VORP is blind to *P(a player ever starts)*, so it
   over-hoards depth at **single-start positions**. QB3 and "3 TEs" are the same bug.
   Fix now = tighter caps + QB timing. Cleaner future fix = **value = VORP × P(starts)**.
2. **Delay QB3:** front-load (~R3) → ~60% playoff; QB3 at ~R9–13 → ~75%. **+15pp.**
   It's a **plateau (R9–R13 flat), not a magic number** — the sim shows trends.
3. **Cap TE at 2:** TE3 → 73% playoff, **TE2 → 79%** (+6pp), TE1 → 73% (no backup →
   TE-hole weeks). Single-start positions want **starters + exactly ONE insurance**.
4. **Stacked:** deployed default behavior (front-QB3 + TE3) ≈ 60% → (delay QB3 +
   TE2) = **79% playoff. +19pp.**
5. **Rejected:** QB VORP replacement-baseline recalibration = **dead knob** (the QB
   deadline dominates; 1.4pp, overlapping CIs). Also proven: **QB tiers are
   rank-invariant** (gmm on proj_points; 0/249 production mismatch) — QB-value
   experiments never need per-rank materialization.
6. **Rejected:** bye-aware swapping of a LATE QB3 = inert (H2H wins 7.93=7.93) — a
   round-13 QB3 is a backup who doesn't play, so his bye is moot.
7. **Metric lesson:** season **all-play % ≠ H2H playoff %**; they diverge exactly at
   single-start depth (all-play likes raw skill, playoff wants the insurance). **Use
   playoff-make.** `draft_diagnostic.py --backtest` grades on actuals; the projected
   metric flatters QB-hoarding (that's the "dominant draft" mirage).

## Open / next steps
1. **R8 re-audit — finish it.** Structural settings CONFIRMED vs `league_rules.md`
   for renewed league `470.l.152123` ("MIKE VRABEL'S HOT TUB"): roster 2QB/3WR/2RB/
   1TE/1FLEX/1K/1DEF/8BN, H2H, 6 playoff teams wks 15–17, fractional+negative — all
   match. **CHANGE: IR 1→2** (minor). **REMAINING: diff the scoring bonuses** — parse
   `lg.settings()['stat_modifiers']` (map stat_id→name via `lg.stat_categories()`)
   against the `league_rules.md` scoring tables (6-pt pass TD, full PPR, first downs,
   yardage bonuses). Auth works via `ffi.yahoo_client.get_session()` (refresh token
   valid; no browser needed).
2. **Level-2 test league (user action):** set *Brent's Bold League* `470.l.151274`
   (currently **1 team**) to **12 teams + 11 autodraft bots** so the live-plumbing
   rehearsal (poll/OAuth/999/crash-resume) can run. Assistant refuses any non-12 count.
3. **Optional strategy polish:** the deployed config uses `qb_tier_targets=()`; the
   Phase-4 tier-target knob `(1,2,99)` was validated earlier but never deployed —
   decide whether to add it (small, confounded +1.3pp).
4. **Future:** implement value = VORP × P(starts) as the principled replacement for
   the cap patch.

## Carry-forward facts
- **Draft: Saturday Aug 29 2026.** Feature freeze ~Aug 22.
- **Leagues:** real `470.l.152123`; test `470.l.151274`. 2026 NFL game id = 470.
- **Deploy is NOT gated by D7:** the D7 gate tests the *valuation* (fixed REF_STRATEGIES);
  this was a *strategy-config* change, validated by the actual-points backtests, not the
  gate (valuation untouched → gate composite unchanged).
- `main` tip after this session: strategy deploy + tooling + R8 partial + this doc.
