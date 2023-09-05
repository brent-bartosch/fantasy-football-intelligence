#!/usr/bin/env python3
"""
Ingest expert rankings from various fantasy football sources.
This script can parse rankings from CSVs, APIs, or scraped data.
"""

import os
import sys
import json
import csv
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, date
from typing import List, Dict, Any
import requests
from dotenv import load_dotenv

load_dotenv()

class RankingsIngester:
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
        
        self.stats = {
            'rankings_imported': 0,
            'sources_added': 0,
            'duplicates_skipped': 0
        }
    
    def check_source_exists(self, url: str, source_type: str) -> bool:
        """Check if we've already ingested this source"""
        content_hash = hashlib.sha256(f"{url}{source_type}".encode()).hexdigest()
        
        self.cursor.execute("""
            SELECT source_id FROM ingested_sources 
            WHERE content_hash = %s
        """, (content_hash,))
        
        return self.cursor.fetchone() is not None
    
    def add_source(self, source_name: str, source_type: str, url: str, metadata: Dict = None):
        """Record that we've ingested this source"""
        content_hash = hashlib.sha256(f"{url}{source_type}".encode()).hexdigest()
        
        self.cursor.execute("""
            INSERT INTO ingested_sources (
                source_name, source_type, url, 
                content_hash, metadata
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source_id) DO NOTHING
        """, (
            source_name, source_type, url,
            content_hash, json.dumps(metadata or {})
        ))
        
        self.stats['sources_added'] += 1
    
    def ingest_csv_rankings(self, file_path: str, source_name: str):
        """
        Ingest rankings from a CSV file.
        Expected columns: Player, Position, Team, Rank, ADP, Tier, Projected_Points
        """
        print(f"\n📊 Ingesting {source_name} from CSV...")
        
        if not os.path.exists(file_path):
            print(f"   ❌ File not found: {file_path}")
            return
        
        # Check if already ingested
        if self.check_source_exists(file_path, 'csv'):
            print(f"   ⚠️  Already ingested this file")
            self.stats['duplicates_skipped'] += 1
            return
        
        with open(file_path, 'r') as f:
            reader = csv.DictReader(f)
            
            count = 0
            for row in reader:
                self.cursor.execute("""
                    INSERT INTO expert_rankings (
                        source, player_name, position, team,
                        overall_rank, position_rank, adp, 
                        projected_points, tier
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    source_name,
                    row.get('Player', '').strip(),
                    row.get('Position', '').strip(),
                    row.get('Team', '').strip(),
                    int(row.get('Rank', 0)) if row.get('Rank') else None,
                    int(row.get('Position_Rank', 0)) if row.get('Position_Rank') else None,
                    float(row.get('ADP', 0)) if row.get('ADP') else None,
                    float(row.get('Projected_Points', 0)) if row.get('Projected_Points') else None,
                    int(row.get('Tier', 0)) if row.get('Tier') else None
                ))
                count += 1
            
            self.stats['rankings_imported'] += count
            self.add_source(source_name, 'csv', file_path)
            self.db_conn.commit()
            print(f"   ✅ Imported {count} rankings")
    
    def ingest_fantasypros_ecr(self):
        """
        Ingest Expert Consensus Rankings from FantasyPros
        Note: This is a placeholder - actual implementation would need
        either API access or web scraping
        """
        print("\n📊 Ingesting FantasyPros ECR...")
        
        # Sample data structure - in production, this would come from API/scraping
        sample_rankings = [
            {"player": "Josh Allen", "position": "QB", "team": "BUF", "rank": 1, "adp": 24.5, "tier": 1},
            {"player": "Jalen Hurts", "position": "QB", "team": "PHI", "rank": 2, "adp": 28.3, "tier": 1},
            {"player": "Lamar Jackson", "position": "QB", "team": "BAL", "rank": 3, "adp": 32.1, "tier": 1},
            {"player": "Patrick Mahomes", "position": "QB", "team": "KC", "rank": 4, "adp": 35.2, "tier": 2},
            {"player": "Dak Prescott", "position": "QB", "team": "DAL", "rank": 5, "adp": 42.8, "tier": 2},
            {"player": "Christian McCaffrey", "position": "RB", "team": "SF", "rank": 1, "adp": 1.1, "tier": 1},
            {"player": "Breece Hall", "position": "RB", "team": "NYJ", "rank": 2, "adp": 2.8, "tier": 1},
            {"player": "Jonathan Taylor", "position": "RB", "team": "IND", "rank": 3, "adp": 4.2, "tier": 1},
            {"player": "CeeDee Lamb", "position": "WR", "team": "DAL", "rank": 1, "adp": 3.5, "tier": 1},
            {"player": "Tyreek Hill", "position": "WR", "team": "MIA", "rank": 2, "adp": 5.1, "tier": 1},
        ]
        
        count = 0
        for player in sample_rankings:
            self.cursor.execute("""
                INSERT INTO expert_rankings (
                    source, player_name, position, team,
                    overall_rank, adp, tier,
                    date_updated
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                'FantasyPros ECR',
                player['player'],
                player['position'],
                player['team'],
                player['rank'],
                player['adp'],
                player['tier'],
                date.today()
            ))
            count += 1
        
        self.stats['rankings_imported'] += count
        self.add_source('FantasyPros ECR', 'api', 'https://www.fantasypros.com/ecr')
        self.db_conn.commit()
        print(f"   ✅ Imported {count} consensus rankings")
    
    def ingest_espn_rankings(self):
        """
        Ingest ESPN Fantasy Football rankings
        Note: Placeholder for ESPN data ingestion
        """
        print("\n📊 Ingesting ESPN Rankings...")
        
        # Sample ESPN-style rankings
        sample_rankings = [
            {"player": "Christian McCaffrey", "position": "RB", "team": "SF", "rank": 1, "adp": 1.0, "points": 385.2},
            {"player": "CeeDee Lamb", "position": "WR", "team": "DAL", "rank": 2, "adp": 2.3, "points": 342.1},
            {"player": "Tyreek Hill", "position": "WR", "team": "MIA", "rank": 3, "adp": 3.8, "points": 335.5},
            {"player": "Breece Hall", "position": "RB", "team": "NYJ", "rank": 4, "adp": 4.2, "points": 328.9},
            {"player": "Amon-Ra St. Brown", "position": "WR", "team": "DET", "rank": 5, "adp": 5.5, "points": 318.2},
        ]
        
        count = 0
        for player in sample_rankings:
            self.cursor.execute("""
                INSERT INTO expert_rankings (
                    source, player_name, position, team,
                    overall_rank, adp, projected_points,
                    date_updated
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                'ESPN',
                player['player'],
                player['position'],
                player['team'],
                player['rank'],
                player['adp'],
                player['points'],
                date.today()
            ))
            count += 1
        
        self.stats['rankings_imported'] += count
        self.add_source('ESPN', 'api', 'https://fantasy.espn.com')
        self.db_conn.commit()
        print(f"   ✅ Imported {count} ESPN rankings")
    
    def calculate_consensus(self):
        """Calculate consensus rankings from all sources"""
        print("\n🔮 Calculating consensus rankings...")
        
        self.cursor.execute("""
            SELECT 
                player_name,
                position,
                COUNT(DISTINCT source) as num_sources,
                ROUND(AVG(overall_rank), 1) as avg_rank,
                ROUND(AVG(adp), 1) as avg_adp,
                MIN(overall_rank) as best_rank,
                MAX(overall_rank) as worst_rank
            FROM expert_rankings
            WHERE season_year = 2025
            GROUP BY player_name, position
            HAVING COUNT(DISTINCT source) >= 2
            ORDER BY avg_rank
            LIMIT 20
        """)
        
        results = self.cursor.fetchall()
        
        print("\n📊 Top 20 Consensus Rankings:")
        print(f"{'Rank':<6} {'Player':<25} {'Pos':<5} {'Sources':<8} {'Avg ADP':<10} {'Range'}")
        print("-" * 70)
        
        for i, player in enumerate(results, 1):
            range_str = f"{player['best_rank']}-{player['worst_rank']}"
            print(f"{i:<6} {player['player_name']:<25} {player['position']:<5} "
                  f"{player['num_sources']:<8} {player['avg_adp']:<10} {range_str}")
    
    def ingest_draft_analysis(self, title: str, content: str, source: str,
                            player_mentions: List[str] = None, 
                            key_insights: List[str] = None):
        """Ingest draft analysis articles or notes"""
        self.cursor.execute("""
            INSERT INTO draft_analysis (
                source, title, content,
                player_mentions, key_insights,
                date_published
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            source,
            title,
            content,
            player_mentions or [],
            key_insights or [],
            date.today()
        ))
        
        self.db_conn.commit()
        print(f"   ✅ Added analysis: {title}")
    
    def close(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.db_conn:
            self.db_conn.close()

def main():
    """Main function to run ingestion"""
    ingester = RankingsIngester()
    
    try:
        print("\n" + "="*60)
        print("🏈 FANTASY FOOTBALL EXPERT RANKINGS INGESTION")
        print("="*60)
        
        # Ingest from various sources
        ingester.ingest_fantasypros_ecr()
        ingester.ingest_espn_rankings()
        
        # If you have CSV files, uncomment and update path:
        # ingester.ingest_csv_rankings('data/rankings.csv', 'Custom Rankings')
        
        # Add sample draft analysis
        ingester.ingest_draft_analysis(
            title="2025 Draft Strategy: Zero RB Approach",
            content="""In 2025, the Zero RB strategy continues to gain traction. 
                     Focus on elite WRs like CeeDee Lamb and Tyreek Hill in early rounds,
                     then target high-upside RBs in the middle rounds.""",
            source="Draft Analysis",
            player_mentions=["CeeDee Lamb", "Tyreek Hill"],
            key_insights=["Zero RB viable", "Elite WRs early", "RB depth in middle rounds"]
        )
        
        # Calculate and display consensus
        ingester.calculate_consensus()
        
        # Print summary
        print("\n" + "="*60)
        print("📊 IMPORT SUMMARY")
        print("="*60)
        print(f"Rankings imported: {ingester.stats['rankings_imported']}")
        print(f"Sources added: {ingester.stats['sources_added']}")
        print(f"Duplicates skipped: {ingester.stats['duplicates_skipped']}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        ingester.close()

if __name__ == "__main__":
    main()