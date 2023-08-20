-- Fantasy Football Analytics Database Schema
-- PostgreSQL 15

-- Drop existing tables if they exist (be careful in production!)
DROP TABLE IF EXISTS trade_details CASCADE;
DROP TABLE IF EXISTS trades CASCADE;
DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS manager_tendencies CASCADE;
DROP TABLE IF EXISTS player_stats CASCADE;
DROP TABLE IF EXISTS draft_picks CASCADE;
DROP TABLE IF EXISTS players CASCADE;
DROP TABLE IF EXISTS teams CASCADE;
DROP TABLE IF EXISTS managers CASCADE;
DROP TABLE IF EXISTS scoring_settings CASCADE;
DROP TABLE IF EXISTS leagues CASCADE;

-- Core League Information
CREATE TABLE leagues (
    league_id VARCHAR(50) PRIMARY KEY,
    league_name VARCHAR(255),
    season_year INTEGER,
    num_teams INTEGER,
    draft_type VARCHAR(50), -- snake, auction, etc
    roster_positions JSONB, -- {"QB": 1, "RB": 2, "WR": 3, etc}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- League Scoring Settings (customizable per league/year)
CREATE TABLE scoring_settings (
    setting_id SERIAL PRIMARY KEY,
    league_id VARCHAR(50) REFERENCES leagues(league_id),
    stat_name VARCHAR(100), -- passing_yards, rushing_td, etc
    points_value DECIMAL(5,2), -- points per unit
    stat_category VARCHAR(50) -- passing, rushing, receiving, etc
);

-- Fantasy Team Managers/Participants
CREATE TABLE managers (
    manager_id SERIAL PRIMARY KEY,
    yahoo_guid VARCHAR(100) UNIQUE,
    manager_name VARCHAR(255),
    email VARCHAR(255),
    first_season INTEGER,
    total_seasons INTEGER DEFAULT 0
);

-- Teams per season
CREATE TABLE teams (
    team_id SERIAL PRIMARY KEY,
    league_id VARCHAR(50) REFERENCES leagues(league_id),
    manager_id INTEGER REFERENCES managers(manager_id),
    team_name VARCHAR(255),
    draft_position INTEGER,
    final_rank INTEGER,
    total_points_scored DECIMAL(10,2),
    playoff_seed INTEGER,
    made_playoffs BOOLEAN,
    won_championship BOOLEAN
);

-- NFL Players Master List
CREATE TABLE players (
    player_id SERIAL PRIMARY KEY,
    yahoo_player_id VARCHAR(50) UNIQUE,
    player_name VARCHAR(255),
    position VARCHAR(10),
    nfl_team VARCHAR(50),
    birth_date DATE,
    height VARCHAR(10),
    weight INTEGER
);

-- Draft History
CREATE TABLE draft_picks (
    pick_id SERIAL PRIMARY KEY,
    league_id VARCHAR(50) REFERENCES leagues(league_id),
    team_id INTEGER REFERENCES teams(team_id),
    player_id INTEGER REFERENCES players(player_id),
    round_number INTEGER,
    pick_number INTEGER,
    overall_pick INTEGER,
    keeper_pick BOOLEAN DEFAULT FALSE,
    auction_cost DECIMAL(10,2), -- for auction drafts
    draft_timestamp TIMESTAMP
);

-- Weekly Player Performance
CREATE TABLE player_stats (
    stat_id SERIAL PRIMARY KEY,
    player_id INTEGER REFERENCES players(player_id),
    season_year INTEGER,
    week_number INTEGER,
    actual_points DECIMAL(8,2),
    projected_points DECIMAL(8,2),
    passing_yards INTEGER,
    passing_tds INTEGER,
    interceptions INTEGER,
    rushing_yards INTEGER,
    rushing_tds INTEGER,
    receptions INTEGER,
    receiving_yards INTEGER,
    receiving_tds INTEGER,
    fumbles INTEGER,
    two_pt_conversions INTEGER
);

-- Manager Draft Tendencies
CREATE TABLE manager_tendencies (
    tendency_id SERIAL PRIMARY KEY,
    manager_id INTEGER REFERENCES managers(manager_id),
    season_year INTEGER,
    position VARCHAR(10),
    avg_draft_round DECIMAL(4,2),
    total_drafted INTEGER,
    favorite_teams JSONB, -- {"GB": 5, "KC": 3}
    reach_percentage DECIMAL(5,2), -- % picked before ADP
    handcuff_rate DECIMAL(5,2) -- % of RBs with handcuff
);

-- Trade History
CREATE TABLE trades (
    trade_id SERIAL PRIMARY KEY,
    league_id VARCHAR(50) REFERENCES leagues(league_id),
    trade_date TIMESTAMP,
    team1_id INTEGER REFERENCES teams(team_id),
    team2_id INTEGER REFERENCES teams(team_id),
    status VARCHAR(50) -- accepted, rejected, pending
);

CREATE TABLE trade_details (
    detail_id SERIAL PRIMARY KEY,
    trade_id INTEGER REFERENCES trades(trade_id),
    from_team_id INTEGER REFERENCES teams(team_id),
    to_team_id INTEGER REFERENCES teams(team_id),
    player_id INTEGER REFERENCES players(player_id),
    faab_amount DECIMAL(10,2) -- if FAAB involved
);

-- Waiver/Free Agent Activity
CREATE TABLE transactions (
    transaction_id SERIAL PRIMARY KEY,
    league_id VARCHAR(50) REFERENCES leagues(league_id),
    team_id INTEGER REFERENCES teams(team_id),
    transaction_type VARCHAR(50), -- add, drop, trade
    player_id INTEGER REFERENCES players(player_id),
    transaction_date TIMESTAMP,
    waiver_priority INTEGER,
    faab_bid DECIMAL(10,2)
);

-- Create indexes for performance
CREATE INDEX idx_draft_picks_league ON draft_picks(league_id);
CREATE INDEX idx_draft_picks_player ON draft_picks(player_id);
CREATE INDEX idx_draft_picks_team ON draft_picks(team_id);
CREATE INDEX idx_player_stats_player_season ON player_stats(player_id, season_year);
CREATE INDEX idx_player_stats_week ON player_stats(season_year, week_number);
CREATE INDEX idx_transactions_date ON transactions(transaction_date);
CREATE INDEX idx_transactions_team ON transactions(team_id);
CREATE INDEX idx_teams_manager ON teams(manager_id);
CREATE INDEX idx_teams_league ON teams(league_id);
CREATE INDEX idx_trades_league ON trades(league_id);
CREATE INDEX idx_trades_date ON trades(trade_date);

-- Add comments to tables for documentation
COMMENT ON TABLE leagues IS 'Fantasy football leagues and their configuration';
COMMENT ON TABLE managers IS 'Fantasy team managers/participants across all seasons';
COMMENT ON TABLE teams IS 'Individual teams within a league season';
COMMENT ON TABLE players IS 'NFL players master list';
COMMENT ON TABLE draft_picks IS 'Complete draft history for all leagues';
COMMENT ON TABLE player_stats IS 'Weekly NFL player performance statistics';
COMMENT ON TABLE scoring_settings IS 'League-specific scoring configurations';
COMMENT ON TABLE manager_tendencies IS 'Calculated draft and management tendencies per manager';
COMMENT ON TABLE trades IS 'Trade transactions between teams';
COMMENT ON TABLE transactions IS 'Waiver wire and free agent transactions';

-- Grant permissions (adjust as needed)
GRANT ALL ON ALL TABLES IN SCHEMA public TO CURRENT_USER;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO CURRENT_USER;