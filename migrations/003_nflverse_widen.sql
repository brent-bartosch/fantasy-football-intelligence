-- Phase 2 Task 6: columns the league scores that Phase 1 didn't store.
ALTER TABLE raw.nflverse_player_week
    ADD COLUMN IF NOT EXISTS fumbles           INTEGER,  -- all fumbles (league: -1 each)
    ADD COLUMN IF NOT EXISTS two_point_conversions INTEGER,
    ADD COLUMN IF NOT EXISTS special_teams_tds INTEGER;  -- return-TD proxy
