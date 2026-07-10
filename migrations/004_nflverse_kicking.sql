-- Task 6 follow-up: kicking stats were never mapped from nflverse, so every
-- K player-week scored 0 under source='nflverse'. Add distance-binned FG
-- makes/misses and PAT made/missed so kickers can be scored per league rules.
-- fg_made_50_plus is the ingest-side sum of nflverse's fg_made_50_59 +
-- fg_made_60_ (league bins 50+ together; no distinct 50-59/60+ split).
ALTER TABLE raw.nflverse_player_week
    ADD COLUMN IF NOT EXISTS fg_made_0_19    INTEGER,
    ADD COLUMN IF NOT EXISTS fg_made_20_29   INTEGER,
    ADD COLUMN IF NOT EXISTS fg_made_30_39   INTEGER,
    ADD COLUMN IF NOT EXISTS fg_made_40_49   INTEGER,
    ADD COLUMN IF NOT EXISTS fg_made_50_plus INTEGER,  -- 50_59 + 60_ summed at ingest
    ADD COLUMN IF NOT EXISTS fg_missed_0_19  INTEGER,  -- league only penalizes misses 0-39
    ADD COLUMN IF NOT EXISTS fg_missed_20_29 INTEGER,
    ADD COLUMN IF NOT EXISTS fg_missed_30_39 INTEGER,
    ADD COLUMN IF NOT EXISTS pat_made        INTEGER,
    ADD COLUMN IF NOT EXISTS pat_missed      INTEGER;
