# Yahoo Fantasy Sports API Reference

## Overview

The Yahoo Fantasy Sports API provides comprehensive access to fantasy sports data including NFL, MLB, NBA, and NHL. This document outlines all available data and capabilities for building our fantasy football analytics platform.

## Authentication

- **Method**: OAuth 1.0a (3-legged for private data, 2-legged for public)
- **Requirements**: 
  - Register app at https://developer.yahoo.com
  - Consumer Key and Secret required
  - User authorization needed for private league data

## Base URL

```
https://fantasysports.yahooapis.com/fantasy/v2/
```

## NFL Game IDs (Historical)

| Season | Game ID | Game Key |
|--------|---------|----------|
| 2024   | 449     | 449      |
| 2023   | 423     | 423      |
| 2022   | 414     | 414      |
| 2021   | 406     | 406      |
| 2020   | 399     | 399      |
| 2019   | 390     | 390      |
| 2018   | 380     | 380      |
| 2017   | 371     | 371      |
| 2016   | 359     | 359      |
| 2015   | 348     | 348      |
| 2014   | 331     | 331      |
| 2013   | 314     | 314      |
| 2012   | 273     | 273      |
| 2011   | 257     | 257      |
| 2010   | 242     | 242      |

## Core Resources

### 1. Game Resource
**Purpose**: Get metadata about a fantasy game/sport

**Data Available**:
- Game key/ID
- Game code (e.g., "nfl")
- Name (e.g., "Football")
- Season year
- Registration status
- Game type
- URL

**Example Endpoint**:
```
/fantasy/v2/game/nfl
/fantasy/v2/game/449  # 2024 NFL season
```

### 2. League Resource
**Purpose**: Access specific fantasy league information

**Data Available**:
- League key/ID
- Name
- URL
- Logo URL
- Draft status
- Number of teams
- Current week
- Start/end weeks
- Start/end dates
- Game code
- Season

**Sub-resources**:
- `/settings` - Scoring settings, roster positions, stat modifiers
- `/standings` - Current standings with records
- `/scoreboard` - Weekly matchup scores
- `/teams` - All teams in league
- `/players` - All players
- `/draftresults` - Complete draft results
- `/transactions` - All transactions (trades, adds, drops)

**Example Endpoints**:
```
/fantasy/v2/league/449.l.12345
/fantasy/v2/league/449.l.12345/settings
/fantasy/v2/league/449.l.12345/standings
/fantasy/v2/league/449.l.12345/draftresults
/fantasy/v2/league/449.l.12345/transactions
```

### 3. Team Resource
**Purpose**: Individual fantasy team information

**Data Available**:
- Team key/ID
- Name
- Logo URL
- Manager info (name, email, guid)
- Waiver priority
- FAAB balance (if applicable)
- Number of moves
- Number of trades
- Clinched playoffs
- Draft position
- Draft grade

**Sub-resources**:
- `/roster` - Current roster with all players
- `/matchups` - All matchups for the season
- `/stats` - Team stats by week or season
- `/standings` - Team's standing info
- `/draftresults` - Team's draft picks

**Example Endpoints**:
```
/fantasy/v2/team/449.l.12345.t.1
/fantasy/v2/team/449.l.12345.t.1/roster;week=5
/fantasy/v2/team/449.l.12345.t.1/matchups
/fantasy/v2/team/449.l.12345.t.1/stats;type=week;week=1,2,3
```

### 4. Player Resource
**Purpose**: NFL player information and stats

**Data Available**:
- Player key/ID
- Name (full, first, last)
- Editorial team abbreviation
- Bye week
- Position
- Status (e.g., "Q", "O", "IR")
- Image URL
- Eligible positions
- Selected position (on roster)

**Player Stats Available**:
- Passing: yards, TDs, INTs, attempts, completions
- Rushing: attempts, yards, TDs
- Receiving: targets, receptions, yards, TDs
- Return: yards, TDs
- Miscellaneous: 2PT conversions, fumbles, fumbles lost
- Kicking: FG attempts/made by distance, PAT attempts/made
- Defense: sacks, INTs, fumble recoveries, TDs, safeties, blocks

**Sub-resources**:
- `/stats` - Player statistics
- `/ownership` - Ownership percentage across Yahoo
- `/percent_owned` - Simpler ownership data
- `/draft_analysis` - ADP, average pick, average round

**Example Endpoints**:
```
/fantasy/v2/player/449.p.7200  # Specific player
/fantasy/v2/league/449.l.12345/players;status=A  # Available players
/fantasy/v2/player/449.p.7200/stats;type=season
```

### 5. Transaction Resource
**Purpose**: League transaction history

**Transaction Types**:
- `add` - Free agent/waiver additions
- `drop` - Dropped players
- `add/drop` - Waiver claims with drops
- `trade` - Completed trades
- `pending_trade` - Proposed trades

**Data Available**:
- Transaction key/ID
- Type
- Status
- Timestamp
- FAAB bid (if applicable)
- Players involved
- Teams involved
- Trade notes
- Voter info (for vetoes)

**Example Endpoints**:
```
/fantasy/v2/league/449.l.12345/transactions
/fantasy/v2/league/449.l.12345/transactions;types=trade
/fantasy/v2/league/449.l.12345/transactions;team_key=449.l.12345.t.1
```

### 6. Roster Resource
**Purpose**: Team roster composition

**Data Available**:
- Coverage type (week, date)
- Week/date
- Players array with:
  - Player details
  - Selected position
  - Is flex position

**Example Endpoints**:
```
/fantasy/v2/team/449.l.12345.t.1/roster
/fantasy/v2/team/449.l.12345.t.1/roster;week=5
```

### 7. Draft Results Resource
**Purpose**: Complete draft history

**Data Available**:
- Pick number
- Round
- Team key
- Player key
- Cost (for auction drafts)
- Time taken

**Example Endpoints**:
```
/fantasy/v2/league/449.l.12345/draftresults
/fantasy/v2/team/449.l.12345.t.1/draftresults
```

### 8. Matchup/Scoreboard Resource
**Purpose**: Weekly matchup data

**Data Available**:
- Week
- Team scores
- Projected scores
- Win probability
- Status (in progress, completed)
- Is playoffs
- Is championship
- Is tied

**Example Endpoints**:
```
/fantasy/v2/league/449.l.12345/scoreboard;week=5
/fantasy/v2/team/449.l.12345.t.1/matchups;weeks=1,2,3
```

## League Settings Structure

### Roster Positions
```json
{
  "roster_positions": [
    {"position": "QB", "count": 1},
    {"position": "WR", "count": 3},
    {"position": "RB", "count": 2},
    {"position": "TE", "count": 1},
    {"position": "W/R/T", "count": 1},  // Flex
    {"position": "K", "count": 1},
    {"position": "DEF", "count": 1},
    {"position": "BN", "count": 6}  // Bench
  ]
}
```

### Stat Categories (Sample)
| Stat ID | Name | Display Name | Position Type |
|---------|------|--------------|---------------|
| 4 | Passing Yards | Pass Yds | Offense |
| 5 | Passing TDs | Pass TD | Offense |
| 6 | Interceptions | Int | Offense |
| 8 | Rushing Yards | Rush Yds | Offense |
| 9 | Rushing TDs | Rush TD | Offense |
| 10 | Receptions | Rec | Offense |
| 11 | Reception Yards | Rec Yds | Offense |
| 12 | Reception TDs | Rec TD | Offense |
| 15 | Return TDs | Ret TD | Offense |
| 16 | 2-Pt Conversions | 2-Pt | Offense |
| 18 | Fumbles Lost | Fum Lost | Offense |
| 57 | Fumble Rec TD | Fum Rec TD | Offense |

### Stat Modifiers (Scoring)
```json
{
  "stat_modifiers": [
    {"stat_id": 4, "value": 0.04},  // 0.04 pts per passing yard
    {"stat_id": 5, "value": 4},     // 4 pts per passing TD
    {"stat_id": 6, "value": -1},    // -1 per INT
    {"stat_id": 8, "value": 0.1},   // 0.1 pts per rushing yard
    {"stat_id": 9, "value": 6},     // 6 pts per rushing TD
    {"stat_id": 10, "value": 0.5},  // 0.5 PPR
    {"stat_id": 11, "value": 0.1},  // 0.1 pts per receiving yard
    {"stat_id": 12, "value": 6}     // 6 pts per receiving TD
  ]
}
```

## Collection Filters

### Players Collection
- `position` - Filter by position (QB, RB, WR, etc.)
- `status` - A (available), T (taken), K (keepers), FA (free agents)
- `search` - Search by name
- `sort` - Sort by various stats or projections
- `sort_type` - season, week, date
- `sort_week` - Specific week for stats
- `start` - Pagination start
- `count` - Number of results

### Transactions Collection
- `types` - add, drop, trade, pending_trade
- `team_key` - Filter by team
- `type` - Filter by single type
- `count` - Number of results

### Games Collection
- `is_available` - Currently available games
- `game_types` - full, pickem-team, pickem-group
- `game_codes` - nfl, mlb, nba, nhl
- `seasons` - Specific year

## API Response Format

Responses can be in XML (default) or JSON:
```
/fantasy/v2/league/449.l.12345?format=json
```

## Rate Limits

- **Per Hour**: 3,000 requests (authenticated)
- **Per Day**: 20,000 requests (authenticated)
- **Concurrent**: 10 requests
- Public data has stricter limits

## Useful Query Patterns

### Get Everything for Analysis
```bash
# League overview with all settings
/fantasy/v2/league/449.l.12345;out=metadata,settings,standings,teams,players,draftresults,transactions

# Team season data
/fantasy/v2/team/449.l.12345.t.1;out=metadata,stats,roster,matchups

# Player performance
/fantasy/v2/player/449.p.7200;out=metadata,stats,ownership
```

### Multi-week Data
```bash
# Get multiple weeks of matchups
/fantasy/v2/team/449.l.12345.t.1/matchups;weeks=1,2,3,4,5

# Get multiple weeks of stats
/fantasy/v2/team/449.l.12345.t.1/stats;type=week;week=1,2,3,4,5
```

### Historical Analysis
```bash
# Access previous seasons by using appropriate game_id
/fantasy/v2/league/423.l.12345  # 2023 season
/fantasy/v2/league/414.l.12345  # 2022 season
```

## Data Limitations

1. **Private Leagues**: Require OAuth authentication and user must be league member
2. **Historical Data**: Limited to leagues user participated in
3. **Live Scoring**: Updates may be delayed during games
4. **Player News**: Returned as HTML, needs parsing
5. **IDP Stats**: Limited IDP (Individual Defensive Player) statistics

## Integration Recommendations

1. **Authentication**: 
   - Use established OAuth libraries
   - Store refresh tokens securely
   - Handle token expiration gracefully

2. **Data Collection**:
   - Cache responses to minimize API calls
   - Use batch endpoints with `;out=` parameter
   - Implement exponential backoff for rate limits

3. **Best Practices**:
   - Request only needed fields
   - Use filters to reduce response size
   - Batch similar requests
   - Respect rate limits

## Key Insights for Our Analytics Platform

### Data We Can Capture

1. **Complete Draft History**: Every pick, position, cost (auction)
2. **Weekly Performance**: Player stats, team scores, matchup results  
3. **Transaction Log**: Every add, drop, trade with timestamps
4. **League Settings**: Exact scoring system, roster requirements
5. **Manager Behavior**: Draft picks, trade patterns, waiver activity
6. **Player Metrics**: Weekly stats, ownership trends, projections

### Analytics Possibilities

1. **Draft Analysis**:
   - Compare actual performance vs draft position
   - Identify reach/value picks by manager
   - Position scarcity analysis

2. **Manager Profiling**:
   - Draft tendencies (position preferences, team biases)
   - Trade frequency and timing
   - Waiver wire aggressiveness (FAAB spending patterns)

3. **Performance Tracking**:
   - Week-over-week consistency
   - Matchup difficulty analysis
   - Playoff performance vs regular season

4. **Optimal Strategy Discovery**:
   - Correlate draft strategies with final standings
   - Identify optimal trade windows
   - Determine waiver wire value adds

5. **Scoring System Impact**:
   - Player value differences in PPR vs standard
   - Position value by scoring system
   - Optimal roster construction

## Next Steps

1. Build OAuth authentication flow
2. Create data import scripts for each resource
3. Map API data to our PostgreSQL schema
4. Implement incremental updates for current season
5. Build historical data backfill process
6. Create automated weekly data pulls