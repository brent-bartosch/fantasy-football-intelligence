-- Sample Analytics Queries for Fantasy Football Database
-- These queries demonstrate the types of insights you can extract

-- ============================================
-- 1. DRAFT ANALYSIS
-- ============================================

-- Find the best value picks (players who outperformed their draft position)
SELECT 
    season_year,
    player_name,
    position,
    overall_pick,
    total_season_points,
    position_rank,
    value_differential
FROM draft_value_analysis
WHERE value_differential > 20  -- Drafted 20+ spots later than their performance rank
ORDER BY value_differential DESC
LIMIT 20;

-- Identify draft busts (high picks who underperformed)
SELECT 
    season_year,
    player_name,
    position,
    overall_pick,
    total_season_points,
    position_rank,
    value_differential
FROM draft_value_analysis
WHERE overall_pick <= 30  -- First 3 rounds
  AND value_differential < -20  -- Performed 20+ spots worse than draft position
ORDER BY value_differential
LIMIT 20;

-- ============================================
-- 2. MANAGER TENDENCIES
-- ============================================

-- Manager performance ranking
SELECT 
    manager_name,
    seasons_played,
    avg_finish,
    championships,
    playoff_appearances,
    playoff_percentage,
    championship_percentage,
    avg_points_per_season
FROM manager_performance
WHERE seasons_played >= 3  -- Only managers with 3+ seasons
ORDER BY championship_percentage DESC, playoff_percentage DESC;

-- Manager draft patterns by position
SELECT 
    m.manager_name,
    mt.season_year,
    mt.position,
    mt.avg_draft_round,
    mt.total_drafted,
    mt.reach_percentage,
    mt.handcuff_rate
FROM manager_tendencies mt
JOIN managers m ON mt.manager_id = m.manager_id
WHERE mt.position IN ('RB', 'WR', 'QB')
ORDER BY m.manager_name, mt.season_year, mt.position;

-- ============================================
-- 3. OPTIMAL DRAFT STRATEGY
-- ============================================

-- Analyze championship team draft patterns
SELECT 
    season_year,
    CASE WHEN won_championship THEN 'Champion' ELSE 'Non-Champion' END as team_type,
    AVG(early_rbs) as avg_early_rbs,
    AVG(early_wrs) as avg_early_wrs,
    AVG(qb_draft_round) as avg_qb_round,
    AVG(total_trades) as avg_trades,
    AVG(total_transactions) as avg_transactions
FROM championship_correlations
GROUP BY season_year, won_championship
ORDER BY season_year DESC, won_championship DESC;

-- Position value by round
SELECT 
    p.position,
    dp.round_number,
    COUNT(*) as picks_made,
    ROUND(AVG(ps_total.total_points), 1) as avg_points,
    ROUND(STDDEV(ps_total.total_points), 1) as points_stddev
FROM draft_picks dp
JOIN players p ON dp.player_id = p.player_id
JOIN leagues l ON dp.league_id = l.league_id
LEFT JOIN (
    SELECT player_id, season_year, SUM(actual_points) as total_points
    FROM player_stats
    GROUP BY player_id, season_year
) ps_total ON p.player_id = ps_total.player_id 
           AND ps_total.season_year = l.season_year
WHERE dp.round_number <= 10
GROUP BY p.position, dp.round_number
ORDER BY dp.round_number, avg_points DESC;

-- ============================================
-- 4. TRADE ANALYSIS
-- ============================================

-- Most active traders
SELECT 
    manager_name,
    season_year,
    total_trades,
    accepted_trades,
    acceptance_rate,
    avg_trade_week
FROM trade_activity_summary
WHERE total_trades > 0
ORDER BY season_year DESC, total_trades DESC;

-- Trade timing analysis
SELECT 
    EXTRACT(WEEK FROM trade_date - l.created_at) as weeks_into_season,
    COUNT(*) as trade_count,
    SUM(CASE WHEN t.status = 'accepted' THEN 1 ELSE 0 END) as accepted_count
FROM trades t
JOIN leagues l ON t.league_id = l.league_id
GROUP BY weeks_into_season
ORDER BY weeks_into_season;

-- ============================================
-- 5. WAIVER WIRE ANALYSIS
-- ============================================

-- Most efficient waiver wire users
SELECT 
    manager_name,
    season_year,
    total_transactions,
    adds,
    drops,
    avg_faab_bid,
    total_faab_spent,
    impactful_adds,
    ROUND(100.0 * impactful_adds / NULLIF(adds, 0), 1) as impact_rate
FROM waiver_wire_efficiency
WHERE adds > 5  -- Minimum activity threshold
ORDER BY season_year DESC, impact_rate DESC;

-- ============================================
-- 6. SCORING SYSTEM IMPACT
-- ============================================

-- Player value changes based on scoring settings
WITH standard_scoring AS (
    SELECT 
        p.player_id,
        p.player_name,
        p.position,
        SUM(
            ps.passing_yards * 0.04 +
            ps.passing_tds * 4 +
            ps.interceptions * -2 +
            ps.rushing_yards * 0.1 +
            ps.rushing_tds * 6 +
            ps.receptions * 0 +  -- Standard scoring
            ps.receiving_yards * 0.1 +
            ps.receiving_tds * 6 +
            ps.fumbles * -2
        ) as standard_points
    FROM players p
    JOIN player_stats ps ON p.player_id = ps.player_id
    GROUP BY p.player_id, p.player_name, p.position
),
ppr_scoring AS (
    SELECT 
        p.player_id,
        p.player_name,
        p.position,
        SUM(
            ps.passing_yards * 0.04 +
            ps.passing_tds * 4 +
            ps.interceptions * -2 +
            ps.rushing_yards * 0.1 +
            ps.rushing_tds * 6 +
            ps.receptions * 1 +  -- PPR scoring
            ps.receiving_yards * 0.1 +
            ps.receiving_tds * 6 +
            ps.fumbles * -2
        ) as ppr_points
    FROM players p
    JOIN player_stats ps ON p.player_id = ps.player_id
    GROUP BY p.player_id, p.player_name, p.position
)
SELECT 
    s.player_name,
    s.position,
    ROUND(s.standard_points, 1) as standard_points,
    ROUND(p.ppr_points, 1) as ppr_points,
    ROUND(p.ppr_points - s.standard_points, 1) as ppr_bonus,
    RANK() OVER (PARTITION BY s.position ORDER BY s.standard_points DESC) as standard_rank,
    RANK() OVER (PARTITION BY s.position ORDER BY p.ppr_points DESC) as ppr_rank
FROM standard_scoring s
JOIN ppr_scoring p ON s.player_id = p.player_id
WHERE s.standard_points > 50  -- Filter out low-usage players
ORDER BY ppr_bonus DESC
LIMIT 30;

-- ============================================
-- 7. WEEKLY CONSISTENCY ANALYSIS
-- ============================================

-- Find most consistent performers
WITH player_consistency AS (
    SELECT 
        p.player_id,
        p.player_name,
        p.position,
        ps.season_year,
        AVG(ps.actual_points) as avg_points,
        STDDEV(ps.actual_points) as stddev_points,
        COUNT(*) as games_played,
        ROUND(AVG(ps.actual_points) / NULLIF(STDDEV(ps.actual_points), 0), 2) as consistency_score
    FROM players p
    JOIN player_stats ps ON p.player_id = ps.player_id
    WHERE ps.actual_points IS NOT NULL
    GROUP BY p.player_id, p.player_name, p.position, ps.season_year
    HAVING COUNT(*) >= 10  -- Minimum games played
)
SELECT 
    player_name,
    position,
    season_year,
    ROUND(avg_points, 1) as avg_points,
    ROUND(stddev_points, 1) as stddev,
    consistency_score,
    games_played
FROM player_consistency
WHERE avg_points >= 10  -- Minimum average to be relevant
ORDER BY consistency_score DESC
LIMIT 25;