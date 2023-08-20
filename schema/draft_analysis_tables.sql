-- Draft Analysis and RAG System Tables

-- Expert consensus rankings
CREATE TABLE IF NOT EXISTS expert_rankings (
    ranking_id SERIAL PRIMARY KEY,
    source VARCHAR(255),  -- ESPN, FantasyPros, etc.
    source_url TEXT,
    player_name VARCHAR(255),
    position VARCHAR(10),
    team VARCHAR(10),
    overall_rank INTEGER,
    position_rank INTEGER,
    adp DECIMAL(5,2),  -- Average Draft Position
    projected_points DECIMAL(10,2),
    tier INTEGER,
    notes TEXT,
    date_updated DATE DEFAULT CURRENT_DATE,
    season_year INTEGER DEFAULT 2025
);

-- Store expert analysis text with embeddings
CREATE TABLE IF NOT EXISTS draft_analysis (
    analysis_id SERIAL PRIMARY KEY,
    source VARCHAR(255),
    title TEXT,
    content TEXT,
    content_embedding VECTOR(1536),  -- For OpenAI embeddings
    player_mentions TEXT[],  -- Array of player names mentioned
    key_insights TEXT[],
    date_published DATE,
    season_year INTEGER DEFAULT 2025
);

-- Your league's custom rankings after adjustment
CREATE TABLE IF NOT EXISTS adjusted_rankings (
    adjusted_id SERIAL PRIMARY KEY,
    player_name VARCHAR(255),
    position VARCHAR(10),
    standard_rank INTEGER,
    standard_adp DECIMAL(5,2),
    your_league_rank INTEGER,
    your_league_value DECIMAL(10,2),
    rank_difference INTEGER,  -- How much higher/lower in your league
    scoring_boost DECIMAL(5,2),  -- Percentage boost from your scoring
    notes TEXT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Track which sources we've ingested
CREATE TABLE IF NOT EXISTS ingested_sources (
    source_id SERIAL PRIMARY KEY,
    source_name VARCHAR(255),
    source_type VARCHAR(50),  -- 'rankings', 'article', 'podcast_notes'
    url TEXT,
    date_ingested TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    content_hash VARCHAR(64),  -- To avoid re-ingesting
    metadata JSONB
);

-- Store position-specific scoring adjustments
CREATE TABLE IF NOT EXISTS scoring_adjustments (
    position VARCHAR(10) PRIMARY KEY,
    standard_scoring JSONB,  -- Standard scoring assumptions
    your_scoring JSONB,  -- Your league's scoring
    qb_adjustment DECIMAL(5,2) DEFAULT 1.0,
    rb_adjustment DECIMAL(5,2) DEFAULT 1.0,
    wr_adjustment DECIMAL(5,2) DEFAULT 1.0,
    te_adjustment DECIMAL(5,2) DEFAULT 1.0,
    def_adjustment DECIMAL(5,2) DEFAULT 1.0,
    k_adjustment DECIMAL(5,2) DEFAULT 1.0
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_expert_rankings_player ON expert_rankings(player_name);
CREATE INDEX IF NOT EXISTS idx_expert_rankings_position ON expert_rankings(position);
CREATE INDEX IF NOT EXISTS idx_expert_rankings_adp ON expert_rankings(adp);
CREATE INDEX IF NOT EXISTS idx_adjusted_rankings_player ON adjusted_rankings(player_name);
CREATE INDEX IF NOT EXISTS idx_adjusted_rankings_rank ON adjusted_rankings(your_league_rank);

-- Create vector extension if not exists (for embeddings)
CREATE EXTENSION IF NOT EXISTS vector;

-- View for consensus rankings across sources
CREATE OR REPLACE VIEW consensus_rankings AS
SELECT 
    player_name,
    position,
    team,
    COUNT(DISTINCT source) as num_sources,
    ROUND(AVG(overall_rank), 1) as avg_rank,
    ROUND(AVG(adp), 1) as avg_adp,
    MIN(overall_rank) as best_rank,
    MAX(overall_rank) as worst_rank,
    STDDEV(overall_rank) as rank_variance,
    ROUND(AVG(projected_points), 1) as avg_projected_points
FROM expert_rankings
WHERE season_year = 2025
GROUP BY player_name, position, team
HAVING COUNT(DISTINCT source) >= 2
ORDER BY avg_rank;

-- View for biggest risers/fallers in your league
CREATE OR REPLACE VIEW league_value_picks AS
SELECT 
    ar.player_name,
    ar.position,
    ar.standard_rank,
    ar.your_league_rank,
    ar.rank_difference,
    ar.scoring_boost,
    er.adp,
    ar.notes
FROM adjusted_rankings ar
LEFT JOIN (
    SELECT player_name, AVG(adp) as adp
    FROM expert_rankings
    WHERE season_year = 2025
    GROUP BY player_name
) er ON ar.player_name = er.player_name
WHERE ar.rank_difference != 0
ORDER BY ar.rank_difference DESC;