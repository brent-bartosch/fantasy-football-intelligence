# Architecture Decision Record — QB VORP Recalibration

**Date:** 2026-07-14
**Project:** fantasy_football
**Design doc:** [../specs/2026-07-14-qb-vorp-recalibration-design.md](../specs/2026-07-14-qb-vorp-recalibration-design.md)
**Risk register:** [2026-07-14-qb-vorp-recalibration-risks.md](2026-07-14-qb-vorp-recalibration-risks.md)
**Tier:** 2 (inherited from risk register)
**Domains covered:** 1, 2, 3, 4, 5, 6, 7, 8
**Domains skipped:** none (3 Secrets and 4 Auth are drafted as no-surface)

> **Naming note:** these are the generic-architecture ADR domains (1 Error Handling … 8 Deployment). They are NOT the project's own internal "ADR Domain" numbering (D1 mode machine, D7 valuation gate). The D7 gate referenced throughout is the project's valuation gate.

---

### Domain 1: Error Handling

**Decision:** Fail loud on every invariant that could yield a *silently-wrong* valuation; no fallback or silent degradation. The change adds explicit guards: (a) the sanity gate (`recompute_rank == 24 + extra`, VORP diff == 0) runs for every candidate scenario including new intermediate ranks; (b) before the search, assert all candidate scenarios share `config_version` and projection snapshot; (c) assert ≥2 distinct QB tiers survive at each candidate rank — if not, `qb_tier_targets` is inert and the harness surfaces it rather than silently measuring a different strategy; (d) a post-change grep assertion that no unintended `qb_hoard_12` pin remains.

**Rationale:** Addresses R1 (propagation drift), R4 (tier collapse), R6 (vintage mismatch), R13 (mis-built scenario) from the risk register. The dominant failure mode across the register is "code right, board silently wrong," so every guard is a loud stop, matching the repo's fail-loud convention (`baseline.py`/`priors.py` already raise on bad state; CLAUDE.md `fail-loud-error-handling`).

**Implementation:** extend the existing recompute-vs-stored sanity gate in `qb_vorp_sweep.py`; a SQL assertion on `config_version`/snapshot per scenario; a tier-count assert in the search harness; a `grep -rn qb_hoard_12` check in the change.

**Risk if skipped:** a split or vintage-mismatched valuation ships undetected and the draft-day board recommends off wrong numbers (R1/R2, L×I 45).

---

### Domain 2: Data Flow

**Decision:** Single source of truth = the materialized `valuation.player_value` snapshot the draft assistant reads. The default scenario is a **pointer** (scenario name) pinned at the 6 enumerated code sites (spec §3); the actual VORP lives in the DB snapshot. The change (a) switches the pointer at all 6 sites, (b) rebuilds/repoints the DB snapshot via `build_valuation.py`, (c) re-baselines the D7 reference so downstream consistency compares to the shipped valuation. `qb_hoard_12` is never mutated, so the old snapshot remains a valid, re-pointable source of truth.

**Rationale:** Addresses R1, R2 (live snapshot not rebuilt), R6, R9 (stale reference). The core risk is code pointing one way and the DB snapshot/backtest pool another (split valuation). The assistant's existing ADP/valuation snapshot-mismatch guard (`draft_assistant.py` refuses on mismatch) is the enforcing mechanism.

**Implementation:** spec §3 pin list; `build_valuation.py` rebuild; `run_backtests.py --reference`; verify the assistant's snapshot id advances past the change.

**Risk if skipped:** the draft-day assistant silently uses the old inflated valuation despite a "done" change (R2, L×I 45).

---

### Domain 3: Secrets

**Decision:** No new secrets surface. The change reuses existing DB credentials (already env-managed); no new API keys, no new external calls at change time.

**Rationale:** No risk-register entry maps here. A valuation recalibration touches only already-ingested data and local modules.

**Implementation:** none.

**Risk if skipped:** negligible — nothing to manage.

---

### Domain 4: Auth

**Decision:** No auth surface. This is a local, single-user analytical change; it adds no access paths, endpoints, or privilege boundaries.

**Rationale:** No risk-register entry maps here.

**Implementation:** none.

**Risk if skipped:** negligible.

---

### Domain 5: Logging

**Decision:** The search harness logs, per candidate rank: actual-points all-play % ± CI, QB count, injury-robustness score, QB tier distribution, and a per-season breakdown; the shipped change records the chosen rank plus before/after composite in the commit and ADR.

**Rationale:** Addresses R3 (per-season breakdown exposes single-season over-tuning), R4 (tier distribution catches collapse), R7 (log an upside metric beside all-play %), R10 (the cross-rank spread reveals whether deadlines are masking the effect). Logging here is the primary detective control — most register risks are caught by *reading the search output*, not by a runtime alarm.

**Implementation:** extend `qb_vorp_sweep.py` output columns; capture the decision in the commit message / spec addendum.

**Risk if skipped:** over-tuning, tier collapse, or wrong-objective optimization slip through unseen (R3/R4/R7).

---

### Domain 6: Dependency Management

**Decision:** No new external dependencies; reuses existing `ffi.sim` / `ffi.valuation` / `ffi.sim.backtest` modules. The one internal coupling: the change depends on `qb_hoard_0` (and any new intermediate scenario) being materialized at the *current* projection/config vintage.

**Rationale:** Addresses R6 (vintage). No third-party SLA or version surface.

**Implementation:** `build_valuation.py` for any new scenario; no new libraries.

**Risk if skipped:** the vintage coupling is already covered by Domains 1–2; no independent dependency risk.

---

### Domain 7: Testing & Validation

**Decision:** Validation is empirical-first: (a) the guarded search is the arbiter — 50-seed search, 100-seed winner confirmation, with the QB-depth and injury-robustness guardrails as hard disqualifiers; (b) update value-assertion tests — `qb_hoard_12`'s tests survive untouched (it is unmutated), add assertions for the new default scenario; (c) extend the sanity gate to new ranks; (d) re-run the D7 gate as the discipline check (not the goodness arbiter — see spec §4); (e) diagnostic spot-check (`draft_diagnostic.py --backtest`) at the chosen rank.

**Rationale:** Addresses R3 (over-tuning → 100-seed confirmation), R1/R13 (assertions/grep), R8 (injury-robustness disqualifier), R12 (backtest fidelity cross-checked qualitatively). Unit tests alone can't validate this — the question is "does this rank win on actual points with depth intact," which is empirical.

**Implementation:** `qb_vorp_sweep.py` (search + guardrails), pytest updates (`test_valuation.py` et al.), `run_backtests.py`, `draft_diagnostic.py`.

**Risk if skipped:** ship a rank that over-tunes to three seasons or fails the depth guardrail (R3/R8).

---

### Domain 8: Deployment & Rollback

**Decision:** Deploy as one atomic change on the `qb-vorp-recalibration` branch: pointer switch (6 sites) + valuation rebuild + D7 reference re-baseline. **Rollback = revert the pointer commit** (restoring `qb_hoard_12`, which is unmutated and still materialized) + restore the prior D7 reference via `run_backtests.py --reference`. Trivially reversible *because* we never mutate `qb_hoard_12` — that reversibility is the reason for pushback #1's pointer-switch decision.

**Rationale:** Addresses R2, R9, R11 (freeze timing). Rollback safety and the ~Aug 22 freeze deadline make clean reversibility a first-class requirement; the status quo (rank 36) is always the safe fallback.

**Implementation:** `git revert` of the pointer commit; `run_backtests.py --reference` to restore the old reference; snapshot rebuild. Land before the ~Aug 22 freeze; if it slips, keep rank 36.

**Risk if skipped:** a bad recalibration can't be cleanly backed out before draft day, or ships unvalidated under freeze pressure (R2/R11).
