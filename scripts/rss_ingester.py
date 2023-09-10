#!/usr/bin/env python3
"""
RSS Feed Ingester for Fantasy Football Pundit Analysis
Automatically pulls and processes content from multiple fantasy football sources
"""

import os
import feedparser
import psycopg2
from psycopg2.extras import RealDictCursor
import hashlib
import re
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple
import time
import json
from dotenv import load_dotenv

load_dotenv()

# RSS Feed Sources
FANTASY_RSS_FEEDS = {
    'FantasyPros News': 'https://www.fantasypros.com/nfl/rss/news.xml',
    'FantasyPros Player News': 'https://www.fantasypros.com/nfl/rss/player-news.xml', 
    'Rotoworld Football': 'https://www.rotoworld.com/rss/feed.aspx?sport=nfl&ftype=news',
    'ESPN Fantasy Football': 'https://www.espn.com/espn/rss/fantasy/football/news',
    'NFL.com Fantasy': 'http://www.nfl.com/rss/rsslanding?searchString=fantasy',
    'CBS Sports Fantasy': 'https://www.cbssports.com/rss/headlines/fantasy/football/',
    'Yahoo Fantasy': 'https://sports.yahoo.com/fantasy/rss/',
    'The Fantasy Footballers': 'https://www.thefantasyfootballers.com/feed/',
    'PFF Fantasy': 'https://www.pff.com/feed',
    '4for4 Fantasy': 'https://www.4for4.com/fantasy-football/feed'
}

# Keywords for entity extraction
POSITION_KEYWORDS = {
    'QB': ['quarterback', 'qb', 'passer', 'signal-caller'],
    'RB': ['running back', 'rb', 'rusher', 'halfback', 'tailback'],
    'WR': ['wide receiver', 'wr', 'wideout', 'receiver', 'pass-catcher'],
    'TE': ['tight end', 'te'],
    'K': ['kicker', 'placekicker'],
    'DEF': ['defense', 'dst', 'd/st', 'defensive unit']
}

SENTIMENT_KEYWORDS = {
    'positive': [
        'breakout', 'sleeper', 'value', 'upside', 'improving', 'ascending',
        'buy-low', 'league-winner', 'must-start', 'locked-in', 'elite',
        'dominant', 'explosive', 'efficient', 'consistent', 'undervalued'
    ],
    'negative': [
        'bust', 'avoid', 'concern', 'injury', 'declining', 'regression',
        'overvalued', 'risky', 'fade', 'sit', 'benched', 'struggling',
        'inefficient', 'touchdown-dependent', 'game-script', 'committee'
    ],
    'neutral': [
        'monitor', 'wait-and-see', 'matchup-dependent', 'streaming',
        'game-time decision', 'questionable', 'doubtful', 'probable'
    ]
}

class RSSIngester:
    def __init__(self):
        # Database connection
        self.db_conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME', 'fantasy_football'),
            user=os.getenv('DB_USER', 'brentbartosch'),
            password=os.getenv('DB_PASSWORD', ''),
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432')
        )
        self.cursor = self.db_conn.cursor(cursor_factory=RealDictCursor)
        
        # Cache player names for faster extraction
        self.player_cache = self._load_player_cache()
        self.team_cache = self._load_team_cache()
        
        self.stats = {
            'articles_processed': 0,
            'players_mentioned': 0,
            'duplicates_skipped': 0,
            'errors': []
        }
    
    def _load_player_cache(self) -> Dict[str, str]:
        """Load all player names for entity extraction"""
        self.cursor.execute("""
            SELECT DISTINCT player_name, position, nfl_team 
            FROM players 
            WHERE player_name NOT LIKE 'Player %'
        """)
        
        players = {}
        for row in self.cursor.fetchall():
            if row['player_name']:
                # Store variations of the name
                full_name = row['player_name']
                players[full_name.lower()] = full_name
                
                # Add last name only for common references
                parts = full_name.split()
                if len(parts) > 1:
                    last_name = parts[-1].lower()
                    # Only add if unique enough (avoid common names)
                    if last_name not in ['smith', 'johnson', 'williams', 'jones', 'brown']:
                        players[last_name] = full_name
        
        print(f"📚 Loaded {len(players)} player name variations")
        return players
    
    def _load_team_cache(self) -> Dict[str, str]:
        """Load NFL team names and abbreviations"""
        teams = {
            'arizona': 'ARI', 'cardinals': 'ARI', 'ari': 'ARI',
            'atlanta': 'ATL', 'falcons': 'ATL', 'atl': 'ATL',
            'baltimore': 'BAL', 'ravens': 'BAL', 'bal': 'BAL',
            'buffalo': 'BUF', 'bills': 'BUF', 'buf': 'BUF',
            'carolina': 'CAR', 'panthers': 'CAR', 'car': 'CAR',
            'chicago': 'CHI', 'bears': 'CHI', 'chi': 'CHI',
            'cincinnati': 'CIN', 'bengals': 'CIN', 'cin': 'CIN',
            'cleveland': 'CLE', 'browns': 'CLE', 'cle': 'CLE',
            'dallas': 'DAL', 'cowboys': 'DAL', 'dal': 'DAL',
            'denver': 'DEN', 'broncos': 'DEN', 'den': 'DEN',
            'detroit': 'DET', 'lions': 'DET', 'det': 'DET',
            'green bay': 'GB', 'packers': 'GB', 'gb': 'GB',
            'houston': 'HOU', 'texans': 'HOU', 'hou': 'HOU',
            'indianapolis': 'IND', 'colts': 'IND', 'ind': 'IND',
            'jacksonville': 'JAX', 'jaguars': 'JAX', 'jax': 'JAX',
            'kansas city': 'KC', 'chiefs': 'KC', 'kc': 'KC',
            'las vegas': 'LV', 'raiders': 'LV', 'lv': 'LV',
            'los angeles rams': 'LAR', 'rams': 'LAR', 'lar': 'LAR',
            'los angeles chargers': 'LAC', 'chargers': 'LAC', 'lac': 'LAC',
            'miami': 'MIA', 'dolphins': 'MIA', 'mia': 'MIA',
            'minnesota': 'MIN', 'vikings': 'MIN', 'min': 'MIN',
            'new england': 'NE', 'patriots': 'NE', 'ne': 'NE',
            'new orleans': 'NO', 'saints': 'NO', 'no': 'NO',
            'new york giants': 'NYG', 'giants': 'NYG', 'nyg': 'NYG',
            'new york jets': 'NYJ', 'jets': 'NYJ', 'nyj': 'NYJ',
            'philadelphia': 'PHI', 'eagles': 'PHI', 'phi': 'PHI',
            'pittsburgh': 'PIT', 'steelers': 'PIT', 'pit': 'PIT',
            'san francisco': 'SF', '49ers': 'SF', 'niners': 'SF', 'sf': 'SF',
            'seattle': 'SEA', 'seahawks': 'SEA', 'sea': 'SEA',
            'tampa bay': 'TB', 'buccaneers': 'TB', 'bucs': 'TB', 'tb': 'TB',
            'tennessee': 'TEN', 'titans': 'TEN', 'ten': 'TEN',
            'washington': 'WAS', 'commanders': 'WAS', 'was': 'WAS'
        }
        return teams
    
    def extract_entities(self, text: str) -> Tuple[List[str], List[str], List[str]]:
        """Extract player names, positions, and teams from text"""
        text_lower = text.lower()
        
        # Extract players
        found_players = []
        for name_variant, full_name in self.player_cache.items():
            # Use word boundaries for more accurate matching
            pattern = r'\b' + re.escape(name_variant) + r'\b'
            if re.search(pattern, text_lower):
                if full_name not in found_players:
                    found_players.append(full_name)
        
        # Extract positions
        found_positions = []
        for position, keywords in POSITION_KEYWORDS.items():
            for keyword in keywords:
                pattern = r'\b' + re.escape(keyword) + r'\b'
                if re.search(pattern, text_lower):
                    if position not in found_positions:
                        found_positions.append(position)
        
        # Extract teams
        found_teams = []
        for team_variant, team_code in self.team_cache.items():
            pattern = r'\b' + re.escape(team_variant) + r'\b'
            if re.search(pattern, text_lower):
                if team_code not in found_teams:
                    found_teams.append(team_code)
        
        return found_players, found_positions, found_teams
    
    def analyze_sentiment(self, text: str) -> Dict[str, float]:
        """Analyze sentiment of the text"""
        text_lower = text.lower()
        
        sentiment_scores = {
            'positive': 0,
            'negative': 0,
            'neutral': 0
        }
        
        # Count sentiment keywords
        for sentiment, keywords in SENTIMENT_KEYWORDS.items():
            for keyword in keywords:
                pattern = r'\b' + re.escape(keyword) + r'\b'
                matches = len(re.findall(pattern, text_lower))
                sentiment_scores[sentiment] += matches
        
        # Normalize scores
        total = sum(sentiment_scores.values())
        if total > 0:
            for sentiment in sentiment_scores:
                sentiment_scores[sentiment] = sentiment_scores[sentiment] / total
        
        return sentiment_scores
    
    def extract_key_insights(self, text: str) -> List[str]:
        """Extract key insights from text"""
        insights = []
        
        # Look for patterns that indicate key points
        patterns = [
            r'(?:should|must|will likely|expect|look for|watch for|keep an eye on)[^.]*\.',
            r'(?:buy|sell|start|sit|draft|avoid|target)[^.]*\.',
            r'(?:breakout|sleeper|bust|value|concern)[^.]*\.',
            r'(?:upgrade|downgrade|trending)[^.]*\.'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text.lower())
            insights.extend(matches[:2])  # Limit to avoid too many
        
        # Clean and deduplicate
        insights = list(set([s.strip() for s in insights if len(s.strip()) > 20]))[:5]
        
        return insights
    
    def check_duplicate(self, url: str, title: str) -> bool:
        """Check if article already exists"""
        # Create hash of URL + title
        content_hash = hashlib.sha256(f"{url}{title}".encode()).hexdigest()
        
        self.cursor.execute("""
            SELECT source_id FROM ingested_sources
            WHERE content_hash = %s
        """, (content_hash,))
        
        return self.cursor.fetchone() is not None
    
    def ingest_feed(self, feed_url: str, source_name: str):
        """Ingest a single RSS feed"""
        print(f"\n📡 Fetching {source_name}...")
        
        try:
            feed = feedparser.parse(feed_url)
            
            if feed.bozo:
                print(f"   ⚠️  Feed parsing issue: {feed.bozo_exception}")
                self.stats['errors'].append(f"{source_name}: {feed.bozo_exception}")
                return
            
            articles_added = 0
            
            for entry in feed.entries[:50]:  # Limit to recent 50 entries
                # Extract basic info
                title = entry.get('title', 'No Title')
                link = entry.get('link', '')
                
                # Skip if duplicate
                if self.check_duplicate(link, title):
                    self.stats['duplicates_skipped'] += 1
                    continue
                
                # Get content (try different fields)
                content = entry.get('summary', '')
                if not content:
                    content = entry.get('description', '')
                if not content:
                    content = entry.get('content', [{}])[0].get('value', '') if 'content' in entry else ''
                
                if not content:
                    continue
                
                # Clean HTML if present
                content = re.sub('<[^<]+?>', '', content)
                
                # Extract entities
                players, positions, teams = self.extract_entities(title + ' ' + content)
                
                # Analyze sentiment
                sentiment = self.analyze_sentiment(content)
                
                # Extract insights
                insights = self.extract_key_insights(content)
                
                # Get publish date
                published = None
                if hasattr(entry, 'published_parsed'):
                    published = datetime(*entry.published_parsed[:6])
                elif hasattr(entry, 'updated_parsed'):
                    published = datetime(*entry.updated_parsed[:6])
                else:
                    published = datetime.now()
                
                # Store in database
                try:
                    # Insert into draft_analysis
                    self.cursor.execute("""
                        INSERT INTO draft_analysis (
                            source, title, content,
                            player_mentions, key_insights,
                            date_published, season_year
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING analysis_id
                    """, (
                        source_name,
                        title[:500],  # Limit title length
                        content[:5000],  # Limit content length
                        players[:20],  # Limit to 20 players
                        insights,
                        published,
                        2025
                    ))
                    
                    analysis_id = self.cursor.fetchone()['analysis_id']
                    
                    # Record in ingested_sources
                    content_hash = hashlib.sha256(f"{link}{title}".encode()).hexdigest()
                    
                    self.cursor.execute("""
                        INSERT INTO ingested_sources (
                            source_name, source_type, url,
                            content_hash, metadata
                        )
                        VALUES (%s, %s, %s, %s, %s)
                    """, (
                        source_name,
                        'rss',
                        link,
                        content_hash,
                        json.dumps({
                            'sentiment': sentiment,
                            'positions': positions,
                            'teams': teams,
                            'analysis_id': analysis_id
                        })
                    ))
                    
                    articles_added += 1
                    self.stats['articles_processed'] += 1
                    self.stats['players_mentioned'] += len(players)
                    
                except psycopg2.Error as e:
                    print(f"   ❌ Database error: {e}")
                    self.db_conn.rollback()
                    continue
            
            self.db_conn.commit()
            print(f"   ✅ Added {articles_added} new articles")
            
        except Exception as e:
            print(f"   ❌ Feed error: {e}")
            self.stats['errors'].append(f"{source_name}: {e}")
    
    def ingest_all_feeds(self):
        """Ingest all configured RSS feeds"""
        print("\n" + "="*60)
        print("🏈 FANTASY FOOTBALL RSS INGESTION")
        print("="*60)
        
        for source_name, feed_url in FANTASY_RSS_FEEDS.items():
            self.ingest_feed(feed_url, source_name)
            time.sleep(1)  # Be polite to servers
        
        self._print_summary()
    
    def _print_summary(self):
        """Print ingestion summary"""
        print("\n" + "="*60)
        print("📊 INGESTION SUMMARY")
        print("="*60)
        print(f"Articles processed: {self.stats['articles_processed']}")
        print(f"Players mentioned: {self.stats['players_mentioned']}")
        print(f"Duplicates skipped: {self.stats['duplicates_skipped']}")
        
        if self.stats['errors']:
            print(f"\n⚠️  Errors encountered: {len(self.stats['errors'])}")
            for error in self.stats['errors'][:5]:
                print(f"   - {error}")
    
    def show_recent_insights(self, hours: int = 24):
        """Show recent insights from ingested content"""
        print(f"\n🔍 INSIGHTS FROM LAST {hours} HOURS")
        print("="*60)
        
        # Most mentioned players
        self.cursor.execute("""
            SELECT 
                UNNEST(player_mentions) as player,
                COUNT(*) as mentions
            FROM draft_analysis
            WHERE date_published > NOW() - INTERVAL '%s hours'
            GROUP BY player
            ORDER BY mentions DESC
            LIMIT 10
        """, (hours,))
        
        print("\n📈 Most Mentioned Players:")
        for row in self.cursor.fetchall():
            if row['player']:
                print(f"   {row['player']}: {row['mentions']} mentions")
        
        # Key insights
        self.cursor.execute("""
            SELECT 
                title,
                key_insights,
                player_mentions
            FROM draft_analysis
            WHERE date_published > NOW() - INTERVAL '%s hours'
                AND array_length(key_insights, 1) > 0
            ORDER BY date_published DESC
            LIMIT 5
        """, (hours,))
        
        print("\n💡 Recent Key Insights:")
        for row in self.cursor.fetchall():
            print(f"\n   📰 {row['title'][:60]}...")
            if row['key_insights']:
                for insight in row['key_insights'][:2]:
                    print(f"      • {insight[:80]}...")
    
    def close(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.db_conn:
            self.db_conn.close()

def main():
    """Main function to run RSS ingestion"""
    ingester = RSSIngester()
    
    try:
        # Run ingestion
        ingester.ingest_all_feeds()
        
        # Show recent insights
        ingester.show_recent_insights(24)
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Ingestion interrupted")
    
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        ingester.close()

if __name__ == "__main__":
    main()