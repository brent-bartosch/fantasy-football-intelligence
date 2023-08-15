# 🏈 LMU Fantasy Football Intelligence Platform

A comprehensive AI-powered draft analysis system combining 17 years of league history, real-time RSS feeds, expert rankings, and custom scoring adjustments to dominate your fantasy football draft.

## 🎯 Overview

This platform ingests data from multiple sources to create a personalized draft strategy:
- **17 years** of LMU Still Undefeated league history (2009-2025)
- **Real-time RSS feeds** from 10+ fantasy football sources
- **Expert consensus rankings** with your league's scoring adjustments
- **AI-powered sentiment analysis** and player buzz tracking
- **Interactive draft assistant** with live recommendations

## ✅ Current Features

### 📊 Data Collection & Storage
- **Yahoo API Integration**: OAuth 2.0 authenticated data import
- **PostgreSQL Database**: 11 core tables + 7 analytical views
- **RSS Feed Ingestion**: Automated pundit analysis collection
- **Player Database**: 85+ fantasy-relevant players with entity resolution

### 🤖 Intelligence Systems
- **Scoring Adjuster**: Accounts for 6-point passing TDs, full PPR, bonuses
- **Trend Analysis**: 10 SQL views tracking player momentum and sentiment
- **Draft Assistant**: Real-time pick recommendations with historical context
- **RAG System**: Retrieval-augmented generation for draft insights

### 📈 Analytics Available
- **Historical Draft Patterns**: 3,782 picks across 244 teams analyzed
- **Player Buzz Tracking**: Daily momentum indicators (🔥 HOT / ❄️ COOLING)
- **Consensus Rankings**: Multi-source expert aggregation
- **Value Identification**: Players over/undervalued in your scoring system
- **Strategy Detection**: Zero RB, Hero RB, Late Round QB trend tracking

## 🚀 Quick Start

### Prerequisites
- macOS with Homebrew
- PostgreSQL 15
- Python 3.8+
- Yahoo Developer API credentials

### Installation

1. **Database Setup**:
```bash
brew install postgresql@15
brew services start postgresql@15
createdb fantasy_football
```

2. **Schema Creation**:
```bash
psql fantasy_football < schema/create_tables.sql
psql fantasy_football < schema/create_views.sql
psql fantasy_football < schema/draft_analysis_simple.sql
psql fantasy_football < schema/trend_analysis_views.sql
```

3. **Install Dependencies**:
```bash
pip install psycopg2-binary python-dotenv requests requests-oauthlib feedparser
```

4. **Configure Environment**:
```bash
cp .env.example .env
# Edit .env with your Yahoo API credentials
```

## 📁 Project Structure

```
fantasy_football/
├── README.md
├── schema/
│   ├── create_tables.sql         # Core database schema
│   ├── create_views.sql          # Analytical views
│   ├── draft_analysis_simple.sql # RAG system tables
│   └── trend_analysis_views.sql  # RSS trend tracking
├── scripts/
│   ├── yahoo_manual_auth.py      # Yahoo OAuth authentication
│   ├── import_all_lmu.py         # Import historical leagues
│   ├── ingest_expert_rankings.py # Import expert consensus
│   ├── scoring_adjuster.py       # Adjust for league scoring
│   ├── draft_assistant.py        # Interactive draft tool
│   ├── rss_ingester.py          # RSS feed processor
│   ├── update_player_names.py    # Player database updater
│   └── daily_ingest.sh          # Automation script
├── config/
│   └── yahoo_token.json         # OAuth token (gitignored)
└── logs/
    └── rss_ingestion_*.log      # Daily ingestion logs
```

## 💻 Usage Guide

### 1. Initial Data Import
```bash
# Authenticate with Yahoo
python scripts/yahoo_manual_auth.py

# Import historical leagues
python scripts/import_all_lmu.py

# Add current player names
python scripts/update_player_names.py
```

### 2. Pre-Draft Preparation
```bash
# Ingest expert rankings
python scripts/ingest_expert_rankings.py

# Pull latest RSS feeds
python scripts/rss_ingester.py

# Adjust rankings for your scoring
python scripts/scoring_adjuster.py
```

### 3. Draft Day
```bash
# Launch interactive assistant
python scripts/draft_assistant.py
```

Commands in draft assistant:
- `best` - Show best available players
- `rec` - Get AI recommendation
- `draft [player]` - Mark as your pick
- `taken [player]` - Mark as drafted by others
- `team` - View your roster
- `history` - See historical patterns

### 4. Automation (Optional)
```bash
# Add to crontab for daily updates
crontab -e
# Add: 0 8 * * * /path/to/fantasy_football/scripts/daily_ingest.sh
```

## 📊 Key SQL Queries

```sql
-- Connect to database
psql fantasy_football

-- Check player buzz trends
SELECT * FROM weekly_player_momentum 
WHERE week = DATE_TRUNC('week', CURRENT_DATE)
ORDER BY weekly_mentions DESC;

-- View adjusted rankings
SELECT * FROM adjusted_rankings 
ORDER BY your_league_rank LIMIT 50;

-- See draft value picks
SELECT * FROM league_value_picks 
WHERE rank_difference > 10;

-- Track content freshness
SELECT * FROM content_freshness;

-- Historical draft analysis
SELECT * FROM draft_value_analysis 
WHERE season_year >= 2020;
```

## 🔄 Data Pipeline

```
Yahoo API → Historical Data (17 years)
     ↓
RSS Feeds → Real-time Analysis
     ↓
Expert Rankings → Consensus Building
     ↓
Scoring Adjustments → Personalized Rankings
     ↓
Draft Assistant → Live Recommendations
```

## 📈 Current Stats

- **Leagues Imported**: 17 seasons (2009-2025)
- **Draft Picks Analyzed**: 3,782
- **Players Tracked**: 85+ with 158 name variations
- **RSS Sources**: 10 configured, 4 actively working
- **Articles Ingested**: 119+
- **Trend Views**: 10 analytical views

## 🚧 TODO / Improvements

### High Priority
- [ ] Fix broken RSS feed URLs (FantasyPros, Rotoworld, NFL.com)
- [ ] Add OpenAI embeddings for semantic search
- [ ] Import weekly performance data from Yahoo
- [ ] Add injury tracking and alerts
- [ ] Create web UI for draft assistant

### Medium Priority
- [ ] Add more RSS sources (Beat reporters, Reddit)
- [ ] Implement machine learning for draft prediction
- [ ] Add trade analyzer using historical data
- [ ] Create Slack/Discord bot for alerts
- [ ] Add weather data for game day decisions

### Nice to Have
- [ ] Mobile app for draft day
- [ ] Voice commands for hands-free drafting
- [ ] Export to CSV/Excel for offline analysis
- [ ] Integration with other platforms (Sleeper, ESPN)
- [ ] Advanced keeper league analysis

## 🎯 Competitive Advantages

Your league-mates use:
- Static rankings from one source
- Standard scoring assumptions
- No historical context
- Yesterday's news

You have:
- **Dynamic rankings** from multiple sources
- **LMU-specific scoring** (6-pt TDs, PPR, bonuses)
- **17 years of league patterns**
- **Real-time sentiment tracking**
- **AI-powered recommendations**

## 🛠️ Troubleshooting

### PostgreSQL Connection Issues
```bash
brew services restart postgresql@15
```

### RSS Feed Errors
- Check feed URLs are still valid
- Some sites require user agent headers
- Rate limiting may apply - add delays

### Yahoo API Issues
- Token expires after 1 hour
- Re-run `yahoo_manual_auth.py` if needed
- Check API rate limits (500 requests/hour)

## 📚 Documentation

- [Yahoo Fantasy Sports API Guide](https://developer.yahoo.com/fantasysports/guide/)
- [PostgreSQL 15 Documentation](https://www.postgresql.org/docs/15/)
- [RSS Feed Best Practices](https://www.rssboard.org/rss-specification)

## 🏆 Results

With this system, you'll have:
- **Better draft preparation** through data-driven insights
- **Real-time intelligence** during your draft
- **Scoring-specific advantages** others will miss
- **Historical context** for every pick

## 📝 License

MIT

## 🙏 Acknowledgments

- Yahoo Fantasy Sports API
- PostgreSQL community
- Fantasy football content creators
- LMU Still Undefeated league members (17 years strong!)

---

*Built with 🏈 for the LMU Still Undefeated league*
*May your adjusted rankings be ever in your favor!*