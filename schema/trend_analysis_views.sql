-- Trend Analysis Views for RSS/News Ingestion
-- These views help track player sentiment, buzz, and emerging narratives

-- 1. Player Buzz Trending (last 30 days)
CREATE OR REPLACE VIEW player_buzz_trend AS
SELECT 
    unnest(player_mentions) as player_name,
    DATE(date_published) as mention_date,
    COUNT(*) as daily_mentions,
    COUNT(*) - LAG(COUNT(*), 1, 0) OVER (
        PARTITION BY unnest(player_mentions) 
        ORDER BY DATE(date_published)
    ) as mention_change
FROM draft_analysis
WHERE date_published > CURRENT_DATE - INTERVAL '30 days'
    AND array_length(player_mentions, 1) > 0
GROUP BY player_name, DATE(date_published);

-- 2. Weekly Player Momentum
CREATE OR REPLACE VIEW weekly_player_momentum AS
SELECT 
    player_name,
    DATE_TRUNC('week', mention_date) as week,
    SUM(daily_mentions) as weekly_mentions,
    AVG(mention_change) as avg_daily_change,
    CASE 
        WHEN AVG(mention_change) > 2 THEN '🔥 HOT'
        WHEN AVG(mention_change) > 0 THEN '📈 RISING'
        WHEN AVG(mention_change) < -2 THEN '❄️ COOLING'
        WHEN AVG(mention_change) < 0 THEN '📉 FALLING'
        ELSE '➖ STABLE'
    END as momentum
FROM player_buzz_trend
GROUP BY player_name, DATE_TRUNC('week', mention_date)
ORDER BY week DESC, weekly_mentions DESC;

-- 3. Source Coverage Analysis
CREATE OR REPLACE VIEW source_coverage AS
WITH player_coverage AS (
    SELECT 
        source,
        unnest(player_mentions) as player
    FROM draft_analysis
    WHERE date_published > CURRENT_DATE - INTERVAL '7 days'
)
SELECT 
    da.source,
    COUNT(DISTINCT da.analysis_id) as total_articles,
    COUNT(DISTINCT DATE(da.date_published)) as days_active,
    COUNT(DISTINCT pc.player) as unique_players_covered,
    MAX(da.date_published) as last_update
FROM draft_analysis da
LEFT JOIN player_coverage pc ON da.source = pc.source
WHERE da.date_published > CURRENT_DATE - INTERVAL '7 days'
GROUP BY da.source
ORDER BY total_articles DESC;

-- 4. Top Players by Source
CREATE OR REPLACE VIEW top_players_by_source AS
WITH player_source_mentions AS (
    SELECT 
        source,
        unnest(player_mentions) as player_name,
        COUNT(*) as mention_count
    FROM draft_analysis
    WHERE date_published > CURRENT_DATE - INTERVAL '7 days'
    GROUP BY source, player_name
),
ranked_players AS (
    SELECT 
        source,
        player_name,
        mention_count,
        ROW_NUMBER() OVER (PARTITION BY source ORDER BY mention_count DESC) as rank
    FROM player_source_mentions
)
SELECT 
    source,
    player_name,
    mention_count,
    rank
FROM ranked_players
WHERE rank <= 5
ORDER BY source, rank;

-- 5. Player Sentiment Tracking (from metadata)
CREATE OR REPLACE VIEW player_sentiment AS
WITH sentiment_data AS (
    SELECT 
        unnest(da.player_mentions) as player_name,
        (is2.metadata->>'sentiment')::jsonb as sentiment,
        da.date_published
    FROM draft_analysis da
    JOIN ingested_sources is2 ON is2.metadata->>'analysis_id' = da.analysis_id::text
    WHERE da.date_published > CURRENT_DATE - INTERVAL '14 days'
        AND is2.metadata->>'sentiment' IS NOT NULL
)
SELECT 
    player_name,
    COUNT(*) as total_mentions,
    SUM((sentiment->>'positive')::float) as positive_score,
    SUM((sentiment->>'negative')::float) as negative_score,
    SUM((sentiment->>'neutral')::float) as neutral_score,
    CASE 
        WHEN SUM((sentiment->>'positive')::float) > SUM((sentiment->>'negative')::float) * 2 THEN '😊 VERY POSITIVE'
        WHEN SUM((sentiment->>'positive')::float) > SUM((sentiment->>'negative')::float) THEN '🙂 POSITIVE'
        WHEN SUM((sentiment->>'negative')::float) > SUM((sentiment->>'positive')::float) * 2 THEN '😟 VERY NEGATIVE'
        WHEN SUM((sentiment->>'negative')::float) > SUM((sentiment->>'positive')::float) THEN '😐 NEGATIVE'
        ELSE '😶 NEUTRAL'
    END as overall_sentiment
FROM sentiment_data
GROUP BY player_name
HAVING COUNT(*) >= 2
ORDER BY total_mentions DESC;

-- 6. Key Insights Summary
CREATE OR REPLACE VIEW recent_key_insights AS
SELECT 
    date_published::date as insight_date,
    source,
    title,
    key_insights,
    player_mentions,
    array_length(player_mentions, 1) as players_mentioned_count
FROM draft_analysis
WHERE array_length(key_insights, 1) > 0
    AND date_published > CURRENT_DATE - INTERVAL '3 days'
ORDER BY date_published DESC
LIMIT 50;

-- 7. Player Association Network (who's mentioned together)
CREATE OR REPLACE VIEW player_associations AS
WITH player_pairs AS (
    SELECT 
        p1.player as player1,
        p2.player as player2,
        COUNT(*) as co_mentions
    FROM 
        (SELECT analysis_id, unnest(player_mentions) as player FROM draft_analysis) p1
    JOIN 
        (SELECT analysis_id, unnest(player_mentions) as player FROM draft_analysis) p2
        ON p1.analysis_id = p2.analysis_id AND p1.player < p2.player
    WHERE p1.player != p2.player
    GROUP BY p1.player, p2.player
)
SELECT 
    player1,
    player2,
    co_mentions
FROM player_pairs
WHERE co_mentions >= 2
ORDER BY co_mentions DESC;

-- 8. Draft Strategy Trends (from key insights)
CREATE OR REPLACE VIEW strategy_trends AS
WITH expanded_insights AS (
    SELECT 
        unnest(key_insights) as insight,
        date_published
    FROM draft_analysis
    WHERE array_length(key_insights, 1) > 0
),
strategy_keywords AS (
    SELECT 
        CASE 
            WHEN lower(insight) LIKE '%zero rb%' THEN 'Zero RB'
            WHEN lower(insight) LIKE '%hero rb%' THEN 'Hero RB'
            WHEN lower(insight) LIKE '%robust rb%' THEN 'Robust RB'
            WHEN lower(insight) LIKE '%late round qb%' THEN 'Late Round QB'
            WHEN lower(insight) LIKE '%elite te%' THEN 'Elite TE'
            WHEN lower(insight) LIKE '%best player available%' THEN 'BPA'
            WHEN lower(insight) LIKE '%upside%' THEN 'Upside Hunting'
            WHEN lower(insight) LIKE '%floor%' THEN 'Safe Floor'
            ELSE 'Other'
        END as strategy,
        date_published
    FROM expanded_insights
)
SELECT 
    strategy,
    COUNT(*) as mention_count,
    MIN(date_published) as first_mentioned,
    MAX(date_published) as last_mentioned
FROM strategy_keywords
WHERE strategy != 'Other'
GROUP BY strategy
ORDER BY mention_count DESC;

-- 9. Position Group Buzz
CREATE OR REPLACE VIEW position_buzz AS
WITH position_mentions AS (
    SELECT 
        DATE(date_published) as mention_date,
        CASE 
            WHEN p.position IN ('QB') THEN 'QB'
            WHEN p.position IN ('RB') THEN 'RB'
            WHEN p.position IN ('WR') THEN 'WR'
            WHEN p.position IN ('TE') THEN 'TE'
            ELSE 'Other'
        END as position_group,
        COUNT(*) as mentions
    FROM draft_analysis da
    CROSS JOIN LATERAL unnest(da.player_mentions) AS pm(player_name)
    LEFT JOIN players p ON p.player_name = pm.player_name
    WHERE da.date_published > CURRENT_DATE - INTERVAL '14 days'
    GROUP BY DATE(date_published), position_group
)
SELECT 
    position_group,
    mention_date,
    mentions,
    SUM(mentions) OVER (
        PARTITION BY position_group 
        ORDER BY mention_date 
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) as seven_day_rolling_avg
FROM position_mentions
WHERE position_group != 'Other'
ORDER BY mention_date DESC, position_group;

-- 10. Content Freshness Monitor
CREATE OR REPLACE VIEW content_freshness AS
SELECT 
    source,
    COUNT(*) as articles_last_24h,
    COUNT(CASE WHEN date_published > NOW() - INTERVAL '3 days' THEN 1 END) as articles_last_3d,
    COUNT(CASE WHEN date_published > NOW() - INTERVAL '7 days' THEN 1 END) as articles_last_7d,
    MAX(date_published) as latest_article,
    CASE 
        WHEN MAX(date_published) > NOW() - INTERVAL '1 day' THEN '🟢 ACTIVE'
        WHEN MAX(date_published) > NOW() - INTERVAL '3 days' THEN '🟡 RECENT'
        WHEN MAX(date_published) > NOW() - INTERVAL '7 days' THEN '🟠 STALE'
        ELSE '🔴 INACTIVE'
    END as status
FROM draft_analysis
GROUP BY source
ORDER BY latest_article DESC;