#!/usr/bin/env python3
"""Advisory agent lane (Phase 4 / Task 16) -- EXPENDABLE per risk R3.

A standalone, read-only follower of the draft log: tails the same JSONL file
`ffi.draft.state.DraftLog` treats as the draft-day source of truth, and after
each opponent pick composes a compact board context and asks a model
(`claude -p <prompt> --max-turns 1`) for a short advisory annotation, written
atomically to `reports/draft-annotations-live.md`.

Bright line (non-negotiable, per the Phase 4 risk register's R3): this
process NEVER touches the pick path. It can be killed at any point in a
draft with zero effect on the assistant or the board -- the annotations file
simply goes stale, which `DraftSession.status_lines` already discloses
("[AGENT LANE] stale/absent" once its mtime exceeds 300s). There is
deliberately no try/except around the model call inside the assistant: the
isolation here is structural (a separate OS process), not a caught exception.

This module does NOT construct `ffi.draft.state.DraftLog` -- that opens an
append file handle on the same log a live session/poller owns (a second
handle races the session's `_next_seq` counter and is built for exactly one
owning writer, not a read-only tail). `_read_events` instead mirrors
`DraftLog`'s tolerant torn-tail parse (`state._parse`, a private module
function) directly for a read-only follower: splitting on "\n" always drops
exactly one trailing element -- the empty string after a clean final
newline, or a partial in-flight write if the log is caught mid-append. Either
way that's correct: a clean file loses nothing but the split artifact, and a
torn write is excluded until the next poll picks it up complete.

`build_annotation_context` is the only pure, tested surface (Step 1 of the
brief). The tail loop, the model subprocess call, and the atomic annotation
write are I/O/process-boundary code exercised by manual `--dry-run` smoke
only (Step 3) -- `--dry-run` prints the composed prompt instead of shelling
out, so the test suite and rehearsals never invoke a model.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ffi.db import connect
from ffi.draft.recommend import recommend
from ffi.draft.replay import derive_state
from ffi.draft.session import AGENT_LANE_PATH
from ffi.sim.draft import (
    ROUNDS,
    TOTAL_PICKS,
    _avail_view,
    _build_sorted_pool,
    snake_position,
)
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors
from ffi.sim.strategy import StrategyParams

DEFAULT_OUT = AGENT_LANE_PATH  # single-sourced from session.py -- no desync possible
DEFAULT_POLL_INTERVAL_S = 5.0


def _read_events(path: Path) -> list[tuple[str, dict]]:
    """Read-only tail of the log -- see module docstring for why this
    doesn't construct `DraftLog`. Mirrors `ffi.draft.state._parse`'s
    tolerance exactly: only the FINAL content line may be torn (an
    unparseable last line, or a missing trailing newline entirely, both mean
    the writer is mid-append -- dropped, picked up complete on the next
    poll). Any earlier unparseable line is real corruption (a single writer
    means a coincidentally-truncated-looking EARLIER line can't happen from
    a normal crash) and raises `json.JSONDecodeError` uncaught, crashing this
    process -- by design isolated from the pick path, never caught here."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").split("\n")
    ends_with_newline = lines[-1] == ""
    if ends_with_newline:
        lines.pop()
    if not lines:
        return []
    body, last = lines[:-1], lines[-1]

    events = []
    for line in body:
        raw = json.loads(line)
        events.append((raw["kind"], raw["payload"]))

    if ends_with_newline:
        try:
            raw = json.loads(last)
        except json.JSONDecodeError:
            pass  # torn final line despite the trailing newline -- drop it
        else:
            events.append((raw["kind"], raw["payload"]))
    # else: no trailing newline at all -- crash mid-write; `last` is already
    # excluded (it was popped into `body`/`last` split, never appended above)

    return events


def build_annotation_context(
    events, pool, priors, params: StrategyParams | None = None
) -> str:
    """Pure: compact context for the model prompt -- our roster, the
    on-clock window (current overall + how far our next turn is), and the
    top rule-4 board with tiers/vorp via `ffi.draft.recommend.recommend`,
    the exact board the live assistant shows (never recomputed here).

    `events` is the full parsed log (meta + pick/undo + any mode events);
    only the `meta` event and `pick`/`undo` events are consumed. No VONA
    forecast: that requires rebuilding `DraftSession._maybe_forecast`'s
    upcoming-seat window, which is out of scope for an EXPENDABLE advisory
    lane (~100 lines, per the brief) that can die with zero effect on the
    board. `priors` is accepted per the brief's fixed signature but unused
    for that reason -- kept so a forecast can be added later without an
    interface change.
    """
    meta = next(payload for kind, payload in events if kind == "meta")
    our_slot = meta["our_franchise_slot"]
    our_position = meta["our_position"]
    params = params or StrategyParams()

    pick_undo = [(k, p) for k, p in events if k in ("pick", "undo")]
    st = derive_state(pick_undo, our_slot, our_position)

    overall = st.next_overall
    rnd, position = snake_position(overall)
    our_next = overall
    while our_next <= TOTAL_PICKS and snake_position(our_next)[1] != our_position:
        our_next += 1

    sorted_pool = _build_sorted_pool(pool)
    avail_by_pos = _avail_view(sorted_pool, set(st.taken_refs))
    counts = dict(st.counts_by_position[our_position])
    picks_left_after = ROUNDS - rnd
    rec = recommend(avail_by_pos, rnd, counts, picks_left_after, params)

    lines = ["# Draft agent lane context", ""]
    lines.append("## Our roster")
    roster = ", ".join(f"{p}:{c}" for p, c in sorted(counts.items())) or "(empty)"
    lines.append(roster)
    lines.append("")
    lines.append("## On the clock")
    whose = "US" if position == our_position else f"draft position {position}"
    lines.append(f"overall {overall} (round {rnd}, position {position}) — {whose}")
    if position == our_position:
        lines.append("we are on the clock now")
    else:
        lines.append(
            f"our next pick: overall {our_next} ({our_next - overall} picks away)"
        )
    lines.append("")
    lines.append("## Top board")
    for score, cand in rec.top[:10]:
        lines.append(
            f"  {cand.name} ({cand.position}) tier {cand.tier} "
            f"vorp {cand.vorp:.1f} score {score:.1f}"
        )
    if rec.notes:
        lines.append("")
        lines.append("## Notes")
        for n in rec.notes:
            lines.append(f"- {n}")
    return "\n".join(lines) + "\n"


def _build_prompt(context: str) -> str:
    return (
        "You are an advisory fantasy-football draft co-pilot running in a "
        "background lane alongside the live draft assistant. You do NOT "
        "make picks -- the assistant and operator do that. Given the "
        "deterministic board context below, write a SHORT (3-6 bullet) "
        "advisory note: notable value gaps, positional runs to watch, or "
        "risks at the operator's next pick. No preamble, no pick "
        "recommendation.\n\n" + context
    )


def _call_model(prompt: str) -> str | None:
    """Any failure (timeout, nonzero exit, missing CLI) is printed to this
    process's own stderr and returns None -- the caller writes nothing, so
    the annotations file simply goes stale (the assistant already discloses
    that). No retry: a stale annotation is harmless: the whole point of the
    advisory lane is that it may die."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--max-turns", "1"],
            timeout=60,
            capture_output=True,
            text=True,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"draft_agent_lane: model call failed: {exc!r}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(
            f"draft_agent_lane: model call exited {result.returncode}: "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    return result.stdout


def _atomic_write(path: Path, text: str) -> None:
    """Write-temp-then-`os.replace`, mirroring `ffi.draft.state._atomic_write`
    (fsync the temp file, atomic rename, fsync the directory entry) -- the
    repo's one atomic-write pattern, reused here rather than reinvented."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _on_clock_position(events, meta) -> int:
    pick_undo = [(k, p) for k, p in events if k in ("pick", "undo")]
    st = derive_state(pick_undo, meta["our_franchise_slot"], meta["our_position"])
    return snake_position(st.next_overall)[1]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Advisory agent lane -- read-only draft-log follower (EXPENDABLE)"
    )
    ap.add_argument("--log", type=Path, required=True, help="draft log JSONL to tail")
    ap.add_argument("--scenario", default="qb_hoard_12")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_S)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the composed prompt instead of calling the model "
        "(tests/rehearsals never invoke a model)",
    )
    args = ap.parse_args()

    conn = connect()
    pool = build_pool(conn, args.scenario)
    priors = build_slot_priors(conn)
    conn.close()

    print(
        f"draft_agent_lane: following {args.log} -> {args.out} "
        f"(dry_run={args.dry_run})"
    )
    seen_picks = -1
    while True:
        events = _read_events(args.log)
        meta = next((p for k, p in events if k == "meta"), None)
        pick_count = sum(1 for k, _ in events if k == "pick")
        if meta is not None and pick_count != seen_picks:
            seen_picks = pick_count
            if _on_clock_position(events, meta) != meta["our_position"]:
                prompt = _build_prompt(build_annotation_context(events, pool, priors))
                if args.dry_run:
                    print(prompt)
                else:
                    output = _call_model(prompt)
                    if output is not None:
                        _atomic_write(args.out, output)
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
