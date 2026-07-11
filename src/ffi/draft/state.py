"""Append-only, crash-safe draft event log (ADR Domain 1: R2 -- in-memory
draft state is a named SPOF; per-pick state is persisted to disk on every
pick and replayed on startup).

The JSONL file this module manages is the draft-day source of truth.
`migrations/006_draft.sql`'s `draft.events` table is a *post-draft* archival
copy, populated after the fact by `scripts/import_draft_log.py` for
after-action analysis -- it is never written to during a live draft.

Convention, not enforced here: the first event of every log is a `meta`
event (league_key, our_franchise_slot, our_position, board_vintage,
scoring_config). Enforcing that ordering is the session layer's job
(Task 13), not this module's.

Failure policy: `append` has no try/except around the write path at all --
an fsync failure must crash the assistant visibly (into MANUAL/PAPER), not
be silently absorbed. On `replay`, a torn FINAL line (the process crashed
mid-write) is the one expected corruption: it is dropped and `torn_tail`
comes back `True` so the caller can banner it. Any other corruption -- an
unparseable non-final line, or a seq value that isn't strictly increasing
from 1 -- raises `TornTailError`; that is real corruption and refusing to
run on it is the point.
"""
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class TornTailError(Exception):
    """The log has corruption beyond a single torn final line."""


@dataclass(frozen=True)
class DraftEvent:
    seq: int
    ts: str
    kind: str
    payload: dict


def _parse(path: Path) -> tuple[list[DraftEvent], bool, str]:
    """Parse `path` into (events, torn_tail, clean_text).

    `clean_text` is what the file's contents should be after this parse --
    identical to the original text unless a torn final line was dropped, in
    which case it has that trailing garbage removed.
    """
    if not path.exists():
        return [], False, ""

    text = path.read_text()
    lines = text.split("\n")
    ends_with_newline = lines[-1] == ""
    if ends_with_newline:
        lines.pop()

    if not lines:
        return [], False, text

    body, last = lines[:-1], lines[-1]

    events = []
    for line in body:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            raise TornTailError(f"unparseable line in {path}: {line!r}") from None
        events.append(_to_event(raw))

    torn_tail = False
    clean_lines = list(body)
    if ends_with_newline:
        try:
            raw = json.loads(last)
        except json.JSONDecodeError:
            torn_tail = True
        else:
            events.append(_to_event(raw))
            clean_lines.append(last)
    else:
        # No trailing newline at all -- crash mid-write. Drop the partial line.
        torn_tail = True

    for expected_seq, event in enumerate(events, start=1):
        if event.seq != expected_seq:
            raise TornTailError(
                f"seq out of sequence in {path}: expected {expected_seq}, got {event.seq}"
            )

    clean_text = "".join(line + "\n" for line in clean_lines)
    return events, torn_tail, clean_text


def _to_event(raw: dict) -> DraftEvent:
    return DraftEvent(
        seq=raw["seq"], ts=raw["ts"], kind=raw["kind"], payload=raw["payload"]
    )


class DraftLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        events, torn_tail, clean_text = _parse(self.path)
        if torn_tail:
            # Drop the torn tail from disk too -- otherwise the next real
            # append concatenates onto the un-terminated partial line.
            self.path.write_text(clean_text)

        self._replayed_events = events
        self._torn_tail = torn_tail
        self._next_seq = events[-1].seq + 1 if events else 1
        self._file = open(self.path, "a")

    def append(self, kind: str, payload: dict) -> DraftEvent:
        event = DraftEvent(
            seq=self._next_seq,
            ts=datetime.now().astimezone().isoformat(),
            kind=kind,
            payload=payload,
        )
        line = {
            "seq": event.seq,
            "ts": event.ts,
            "kind": event.kind,
            "payload": event.payload,
        }
        self._file.write(json.dumps(line) + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())
        self._next_seq += 1
        return event

    @classmethod
    def replay(cls, path: Path) -> tuple["DraftLog", list[DraftEvent], bool]:
        log = cls(path)
        return log, log._replayed_events, log._torn_tail
