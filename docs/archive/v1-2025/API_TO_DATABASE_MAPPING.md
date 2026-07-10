# Yahoo API to Database Mapping

This document maps Yahoo Fantasy Sports API responses to our PostgreSQL database schema.

## League Resource → `leagues` table

| Yahoo API Field | Database Column | Notes |
|-----------------|-----------------|-------|
| league_key | league_id | Extract league_id from key format `{game_id}.l.{league_id}` |
| name | league_name | |
| season | season_year | |
| num_teams | num_teams | |
| draft_type | draft_type | live, auction, autopick |
| roster_positions | roster_positions | Store as JSONB |
| current_week | - | Use for determining active week |
| start_date | created_at | League start date |

## League Settings → `scoring_settings` table

| Yahoo API Field | Database Column | Notes |
|-----------------|-----------------|-------|
| stat_modifiers.stat_id | stat_name | Map stat_id to readable name |
| stat_modifiers.value | points_value | Points per unit |
| - | stat_category | Derive from stat_id mapping |
| - | league_id | From parent league |

## Manager Resource → `managers` table

| Yahoo API Field | Database Column | Notes |
|-----------------|-----------------|-------|
| guid | yahoo_guid | Unique Yahoo identifier |
| nickname | manager_name | Display name |
| email | email | If available |
| - | first_season | Calculate from historical data |
| - | total_seasons | Count from teams table |

## Team Resource → `teams` table

| Yahoo API Field | Database Column | Notes |
|-----------------|-----------------|-------|
| team_key | - | Parse to get team_id |
| name | team_name | |
| managers[0].guid | - | Link to managers.yahoo_guid |
| draft_position | draft_position | |
| team_standings.rank | final_rank | End of season rank |
| team_standings.points_for | total_points_scored | |
| team_standings.playoff_seed | playoff_seed | 0 if missed |
| clinched_playoffs | made_playoffs | Boolean |
| - | won_championship | Check if final_rank = 1 |

## Player Resource → `players` table

| Yahoo API Field | Database Column | Notes |
|-----------------|-----------------|-------|
| player_key | yahoo_player_id | Format: `{game_id}.p.{player_id}` |
| name.full | player_name | |
| primary_position | position | QB, RB, WR, TE, etc |
| editorial_team_abbr | nfl_team | 3-letter team code |
| - | birth_date | Not available from API |
| - | height | Not available from API |
| - | weight | Not available from API |

## Draft Results → `draft_picks` table

| Yahoo API Field | Database Column | Notes |
|-----------------|-----------------|-------|
| pick | overall_pick | Overall draft position |
| round | round_number | |
| - | pick_number | Calculate from round |
| team_key | team_id | Parse and link to teams |
| player_key | player_id | Parse and link to players |
| cost | auction_cost | For auction drafts only |
| - | keeper_pick | Determine from draft type |
| - | draft_timestamp | Use league start_date |

## Player Stats → `player_stats` table

| Yahoo API Field | Database Column | Notes |
|-----------------|-----------------|-------|
| player_key | player_id | Link to players table |
| - | season_year | From league context |
| week | week_number | |
| player_points.total | actual_points | Based on league scoring |
| - | projected_points | From projections if available |
| stats[4] | passing_yards | Stat ID 4 |
| stats[5] | passing_tds | Stat ID 5 |
| stats[6] | interceptions | Stat ID 6 |
| stats[8] | rushing_yards | Stat ID 8 |
| stats[9] | rushing_tds | Stat ID 9 |
| stats[10] | receptions | Stat ID 10 |
| stats[11] | receiving_yards | Stat ID 11 |
| stats[12] | receiving_tds | Stat ID 12 |
| stats[18] | fumbles | Stat ID 18 (fumbles lost) |
| stats[16] | two_pt_conversions | Stat ID 16 |

## Transactions → `trades` and `transactions` tables

### For Trades → `trades` table
| Yahoo API Field | Database Column | Notes |
|-----------------|-----------------|-------|
| transaction_key | - | Generate trade_id |
| timestamp | trade_date | Unix timestamp to datetime |
| status | status | completed, pending, vetoed |
| teams involved | team1_id, team2_id | Parse from transaction |

### For Trade Details → `trade_details` table
| Yahoo API Field | Database Column | Notes |
|-----------------|-----------------|-------|
| - | trade_id | Link to parent trade |
| source_team_key | from_team_id | |
| destination_team_key | to_team_id | |
| player_key | player_id | Link to players |
| - | faab_amount | If FAAB involved |

### For Add/Drop → `transactions` table
| Yahoo API Field | Database Column | Notes |
|-----------------|-----------------|-------|
| transaction_key | transaction_id | |
| type | transaction_type | add, drop, add/drop |
| timestamp | transaction_date | |
| team_key | team_id | Link to teams |
| player_key | player_id | Link to players |
| waiver_priority | waiver_priority | If waiver claim |
| faab_bid | faab_bid | If FAAB league |

## Manager Tendencies → `manager_tendencies` table

This table is calculated/derived from other data:

| Source Data | Database Column | Calculation |
|------------|-----------------|-------------|
| draft_picks + managers | manager_id | Group by manager |
| draft_picks | season_year | Group by year |
| draft_picks + players | position | Group by position |
| draft_picks | avg_draft_round | AVG(round_number) by position |
| draft_picks | total_drafted | COUNT by position |
| players.nfl_team | favorite_teams | COUNT by team as JSONB |
| draft_picks vs ADP | reach_percentage | % picked before ADP |
| draft analysis | handcuff_rate | % RBs with handcuff drafted |

## API Call Sequence for Data Import

### Initial Historical Import
1. Get league metadata: `/fantasy/v2/league/{league_key}`
2. Get league settings: `/fantasy/v2/league/{league_key}/settings`
3. Get all teams: `/fantasy/v2/league/{league_key}/teams`
4. Get draft results: `/fantasy/v2/league/{league_key}/draftresults`
5. Get all transactions: `/fantasy/v2/league/{league_key}/transactions`
6. For each team:
   - Get roster by week: `/fantasy/v2/team/{team_key}/roster;week={week}`
   - Get matchups: `/fantasy/v2/team/{team_key}/matchups`
7. Get player stats by week for rostered players

### Weekly Updates (Current Season)
1. Get current week: `/fantasy/v2/league/{league_key}`
2. Get scoreboard: `/fantasy/v2/league/{league_key}/scoreboard;week={current_week}`
3. Get new transactions: `/fantasy/v2/league/{league_key}/transactions` (filter by date)
4. Update player stats for rostered players
5. Update team standings

## Data Processing Notes

### Player ID Management
- Yahoo player IDs format: `{game_id}.p.{player_id}`
- Extract numeric player_id for storage
- Maintain game_id for API calls

### Team Key Parsing
- Format: `{game_id}.l.{league_id}.t.{team_id}`
- Parse to extract team_id for database
- Maintain full key for API calls

### Handling Missing Data
- Not all players have complete stats every week
- Use NULL for missing statistical categories
- Calculate actual_points based on available stats and scoring settings

### Season Identification
- Use game_id to identify season (see game ID table)
- Store season_year for easier querying

### Transaction Processing
- Trades may involve multiple players
- Create one trades record, multiple trade_details records
- Add/drops create single transaction records

## SQL Import Functions

```sql
-- Function to parse Yahoo keys
CREATE OR REPLACE FUNCTION parse_yahoo_key(key TEXT, part TEXT)
RETURNS TEXT AS $$
BEGIN
    CASE part
        WHEN 'game_id' THEN
            RETURN split_part(key, '.', 1);
        WHEN 'league_id' THEN
            RETURN split_part(key, '.', 3);
        WHEN 'team_id' THEN
            RETURN split_part(key, '.', 5);
        WHEN 'player_id' THEN
            RETURN split_part(key, '.', 3);
        ELSE
            RETURN NULL;
    END CASE;
END;
$$ LANGUAGE plpgsql;

-- Function to calculate fantasy points
CREATE OR REPLACE FUNCTION calculate_fantasy_points(
    stats JSONB,
    scoring_settings JSONB
) RETURNS DECIMAL AS $$
DECLARE
    total_points DECIMAL := 0;
    stat_record RECORD;
BEGIN
    FOR stat_record IN SELECT * FROM jsonb_each(stats)
    LOOP
        IF scoring_settings ? stat_record.key THEN
            total_points := total_points + 
                (stat_record.value::DECIMAL * (scoring_settings->>stat_record.key)::DECIMAL);
        END IF;
    END LOOP;
    RETURN total_points;
END;
$$ LANGUAGE plpgsql;
```

## Import Priority Order

1. **Leagues** - Foundation for all other data
2. **Scoring Settings** - Needed to calculate points
3. **Managers** - Required for teams
4. **Teams** - Required for drafts and transactions
5. **Players** - Required for all player-related data
6. **Draft Picks** - Historical draft data
7. **Player Stats** - Performance data
8. **Transactions** - Trade and waiver data
9. **Manager Tendencies** - Calculated after all data imported