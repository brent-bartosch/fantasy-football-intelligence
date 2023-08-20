-- Draft Analysis Tables (Simplified without vector embeddings)

-- Store expert analysis text without embeddings for now
CREATE TABLE IF NOT EXISTS draft_analysis (
    analysis_id SERIAL PRIMARY KEY,
    source VARCHAR(255),
    title TEXT,
    content TEXT,
    player_mentions TEXT[],  -- Array of player names mentioned
    key_insights TEXT[],
    date_published DATE,
    season_year INTEGER DEFAULT 2025
);