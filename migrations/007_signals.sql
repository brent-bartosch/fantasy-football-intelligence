-- 007_signals.sql — typed signal intake (Task 14 FP news) + capped adjustments
-- (Task 15 consumes signals.signals; adjustments applied at human confirm gate).
CREATE TABLE IF NOT EXISTS signals.signals (
    signal_id    bigserial PRIMARY KEY,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    source       text NOT NULL,                        -- 'fp_news'
    external_id  text NOT NULL,                        -- stable dedupe key (fp link)
    xwalk_id     integer,                              -- resolved via fp player_id; NULL = unmatched
    player_name  text,
    signal_type  text NOT NULL CHECK (signal_type IN ('injury','role_change','depth_chart','hype','news')),
    title        text NOT NULL,
    summary      text,
    impact       text,                                 -- FP's impact string, verbatim
    evidence_url text NOT NULL,
    payload      jsonb NOT NULL,                       -- full item, provenance
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','denied')),
    decided_at   timestamptz,
    UNIQUE (source, external_id)
);
CREATE TABLE IF NOT EXISTS signals.adjustments (
    adjustment_id bigserial PRIMARY KEY,
    signal_id     bigint NOT NULL REFERENCES signals.signals(signal_id),
    xwalk_id      integer NOT NULL,
    pct           real NOT NULL CHECK (pct >= -0.10 AND pct <= 0.10),  -- ±10%/adjustment (per-day cap: one adjustment per signal, signals dedupe daily)
    applied_at    timestamptz NOT NULL DEFAULT now(),
    note          text
);
