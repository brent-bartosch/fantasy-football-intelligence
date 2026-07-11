-- 006_draft.sql — draft-day event archive (imported from the JSONL log post-draft)
CREATE TABLE IF NOT EXISTS draft.events (
    draft_id    text NOT NULL,          -- e.g. '2026-real', '2026-rehearsal-2'
    seq         integer NOT NULL,
    ts          timestamptz NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('pick','undo','mode','note','meta')),
    payload     jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (draft_id, seq)
);
