-- Fantasy Football Analytics Views
-- These views provide pre-built analytics queries

-- Drop existing views if they exist
DROP VIEW IF EXISTS draft_value_analysis CASCADE;
DROP VIEW IF EXISTS manager_performance CASCADE;
DROP VIEW IF EXISTS position_draft_trends CASCADE;
DROP VIEW IF EXISTS trade_activity_summary CASCADE;
DROP VIEW IF EXISTS waiver_wire_efficiency CASCADE;
DROP VIEW IF EXISTS weekly_performance_trends CASCADE;
DROP VIEW IF EXISTS championship_correlations CASCADE;

-- 1. Draft Value Analysis
-- Shows which players provided best/worst value relative to draft position
CREATE VIEW draft_value_analysis AS
SELECT 
    dp.league_id,
    l.season_year,
    p.player_name,
    p.position,
    p.nfl_team,
    dp.overall_pick,
    dp.round_number,
    dp.auction_cost,
    COALESCE(SUM(ps.actual_points), 0) as total_season_points,
    RANK() OVER (PARTITION BY p.position, l.season_year ORDER BY SUM(ps.actual_points) DESC) as position_rank,
    dp.overall_pick - RANK() OVER (PARTITION BY p.position, l.season_year ORDER BY SUM(ps.actual_points) DESC) as value_differential
FROM draft_picks dp
JOIN leagues l ON dp.league_id = l.league_id
JOIN players p ON dp.player_id = p.player_id
LEFT JOIN player_stats ps ON p.player_id = ps.player_id 
    AND ps.season_year = l.season_year
GROUP BY dp.league_id, l.season_year, p.player_name, p.position, p.nfl_team, 
         dp.overall_pick, dp.round_number, dp.auction_cost;

-- 2. Manager Performance Summary
-- Overall manager performance across all seasons
CREATE VIEW manager_performance AS
SELECT 
    m.manager_id,
    m.manager_name,
    COUNT(DISTINCT t.team_id) as seasons_played,
    ROUND(AVG(t.final_rank), 2) as avg_finish,
    MIN(t.final_rank) as best_finish,
    MAX(t.final_rank) as worst_finish,
    SUM(CASE WHEN t.won_championship THEN 1 ELSE 0 END) as championships,
    SUM(CASE WHEN t.made_playoffs THEN 1 ELSE 0 END) as playoff_appearances,
    ROUND(AVG(t.total_points_scored), 2) as avg_points_per_season,
    ROUND(100.0 * SUM(CASE WHEN t.made_playoffs THEN 1 ELSE 0 END) / 
          NULLIF(COUNT(DISTINCT t.team_id), 0), 1) as playoff_percentage,
    ROUND(100.0 * SUM(CASE WHEN t.won_championship THEN 1 ELSE 0 END) / 
          NULLIF(COUNT(DISTINCT t.team_id), 0), 1) as championship_percentage
FROM managers m
LEFT JOIN teams t ON m.manager_id = t.manager_id
GROUP BY m.manager_id, m.manager_name
ORDER BY championships DESC, playoff_percentage DESC;

-- 3. Position Draft Trends
-- When each position is typically drafted
CREATE VIEW position_draft_trends AS
SELECT 
    l.season_year,
    p.position,
    COUNT(*) as total_drafted,
    ROUND(AVG(dp.overall_pick), 1) as avg_draft_position,
    MIN(dp.overall_pick) as earliest_pick,
    MAX(dp.overall_pick) as latest_pick,
    ROUND(AVG(dp.round_number), 1) as avg_round,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY dp.overall_pick) as median_pick
FROM draft_picks dp
JOIN leagues l ON dp.league_id = l.league_id
JOIN players p ON dp.player_id = p.player_id
GROUP BY l.season_year, p.position
ORDER BY l.season_year DESC, avg_draft_position;

-- 4. Trade Activity Summary
-- Who trades the most and when
CREATE VIEW trade_activity_summary AS
SELECT 
    m.manager_name,
    l.season_year,
    COUNT(DISTINCT tr.trade_id) as total_trades,
    SUM(CASE WHEN tr.status = 'accepted' THEN 1 ELSE 0 END) as accepted_trades,
    SUM(CASE WHEN tr.status = 'rejected' THEN 1 ELSE 0 END) as rejected_trades,
    ROUND(100.0 * SUM(CASE WHEN tr.status = 'accepted' THEN 1 ELSE 0 END) / 
          NULLIF(COUNT(DISTINCT tr.trade_id), 0), 1) as acceptance_rate,
    EXTRACT(WEEK FROM AVG(tr.trade_date - l.created_at)) as avg_trade_week
FROM managers m
JOIN teams t ON m.manager_id = t.manager_id
JOIN leagues l ON t.league_id = l.league_id
LEFT JOIN trades tr ON (tr.team1_id = t.team_id OR tr.team2_id = t.team_id)
GROUP BY m.manager_name, l.season_year
ORDER BY total_trades DESC;

-- 5. Waiver Wire Efficiency
-- How efficiently managers use waiver claims/FAAB
CREATE VIEW waiver_wire_efficiency AS
SELECT 
    m.manager_name,
    l.season_year,
    COUNT(DISTINCT trans.transaction_id) as total_transactions,
    SUM(CASE WHEN trans.transaction_type = 'add' THEN 1 ELSE 0 END) as adds,
    SUM(CASE WHEN trans.transaction_type = 'drop' THEN 1 ELSE 0 END) as drops,
    AVG(trans.faab_bid) as avg_faab_bid,
    SUM(trans.faab_bid) as total_faab_spent,
    COUNT(DISTINCT CASE WHEN ps.actual_points > 10 THEN trans.player_id END) as impactful_adds
FROM managers m
JOIN teams t ON m.manager_id = t.manager_id
JOIN leagues l ON t.league_id = l.league_id
LEFT JOIN transactions trans ON trans.team_id = t.team_id
LEFT JOIN player_stats ps ON trans.player_id = ps.player_id 
    AND ps.season_year = l.season_year
    AND ps.week_number > EXTRACT(WEEK FROM trans.transaction_date)
WHERE trans.transaction_type IN ('add', 'drop')
GROUP BY m.manager_name, l.season_year
ORDER BY l.season_year DESC, impactful_adds DESC;

-- 6. Weekly Performance Trends
-- Track team performance throughout the season
CREATE VIEW weekly_performance_trends AS
WITH weekly_scores AS (
    SELECT 
        t.team_id,
        t.team_name,
        m.manager_name,
        l.season_year,
        ps.week_number,
        SUM(ps.actual_points) as weekly_points
    FROM teams t
    JOIN managers m ON t.manager_id = m.manager_id
    JOIN leagues l ON t.league_id = l.league_id
    JOIN draft_picks dp ON dp.team_id = t.team_id
    JOIN player_stats ps ON ps.player_id = dp.player_id 
        AND ps.season_year = l.season_year
    GROUP BY t.team_id, t.team_name, m.manager_name, l.season_year, ps.week_number
)
SELECT 
    *,
    AVG(weekly_points) OVER (PARTITION BY team_id ORDER BY week_number 
                             ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as three_week_avg,
    RANK() OVER (PARTITION BY season_year, week_number ORDER BY weekly_points DESC) as weekly_rank
FROM weekly_scores
ORDER BY season_year DESC, week_number, weekly_points DESC;

-- 7. Championship Correlation Analysis
-- What factors correlate with winning championships
CREATE VIEW championship_correlations AS
SELECT 
    l.season_year,
    t.won_championship,
    t.draft_position,
    COUNT(CASE WHEN p.position = 'RB' AND dp.round_number <= 3 THEN 1 END) as early_rbs,
    COUNT(CASE WHEN p.position = 'WR' AND dp.round_number <= 3 THEN 1 END) as early_wrs,
    MIN(CASE WHEN p.position = 'QB' THEN dp.round_number END) as qb_draft_round,
    COUNT(DISTINCT tr.trade_id) as total_trades,
    COUNT(DISTINCT trans.transaction_id) as total_transactions,
    t.total_points_scored,
    t.final_rank
FROM teams t
JOIN leagues l ON t.league_id = l.league_id
JOIN draft_picks dp ON dp.team_id = t.team_id
JOIN players p ON dp.player_id = p.player_id
LEFT JOIN trades tr ON (tr.team1_id = t.team_id OR tr.team2_id = t.team_id)
LEFT JOIN transactions trans ON trans.team_id = t.team_id
GROUP BY l.season_year, t.team_id, t.won_championship, t.draft_position, 
         t.total_points_scored, t.final_rank
ORDER BY l.season_year DESC, t.won_championship DESC;

-- Grant permissions on views
GRANT SELECT ON ALL TABLES IN SCHEMA public TO CURRENT_USER;