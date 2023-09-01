#!/usr/bin/env python3
"""
Import all LMU Still Undefeated leagues (2009-2025)
"""

import os
import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from datetime import datetime
from dotenv import load_dotenv
import time

load_dotenv()

# LMU Still Undefeated league keys
LMU_LEAGUES = [
    ('461.l.863132', 2025),
    ('449.l.389359', 2024),
    ('423.l.323988', 2023),
    ('414.l.254390', 2022),
    ('406.l.205166', 2021),
    ('399.l.130335', 2020),
    ('390.l.523677', 2019),
    ('380.l.212373', 2018),
    ('371.l.22647', 2017),
    ('359.l.427482', 2016),
    ('348.l.82093', 2015),
    ('331.l.534456', 2014),
    ('314.l.364382', 2013),
    ('273.l.11353', 2012),
    ('257.l.117805', 2011),
    ('242.l.42939', 2010),
    ('222.l.231759', 2009),
]

class LMUImporter:
    def __init__(self):
        # Load token
        self.token = self.load_token()
        if not self.token:
            raise ValueError("No valid token found. Run yahoo_manual_auth.py first")
        
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
            'leagues': 0,
            'teams': 0,
            'managers': 0,
            'draft_picks': 0,
            'players': 0
        }
    
    def load_token(self):
        """Load OAuth token"""
        token_file = 'config/yahoo_token.json'
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                return json.load(f)
        return None
    
    def api_request(self, url, timeout=10):
        """Make authenticated API request with timeout"""
        headers = {
            'Authorization': f"Bearer {self.token['access_token']}",
            'Accept': 'application/json'
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"   API Error {response.status_code}")
                return None
        except requests.exceptions.Timeout:
            print(f"   Timeout on request")
            return None
        except Exception as e:
            print(f"   Request error: {e}")
            return None
    
    def import_league(self, league_key, year):
        """Import a single league"""
        print(f"\n{'='*60}")
        print(f"📅 Importing {year} - {league_key}")
        print(f"{'='*60}")
        
        # Import league info
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}?format=json"
        data = self.api_request(url)
        
        if not data:
            print(f"   ❌ Could not fetch league data")
            return False
        
        try:
            league_data = data['fantasy_content']['league'][0]
            
            # Insert league
            self.cursor.execute("""
                INSERT INTO leagues (
                    league_id, league_name, season_year, 
                    num_teams, draft_type, roster_positions
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (league_id) DO UPDATE
                SET league_name = EXCLUDED.league_name,
                    num_teams = EXCLUDED.num_teams
            """, (
                league_key,
                league_data.get('name', 'LMU Still Undefeated'),
                year,
                league_data.get('num_teams', 0),
                league_data.get('scoring_type', 'head'),
                json.dumps({})
            ))
            
            self.stats['leagues'] += 1
            print(f"   ✅ League imported: {league_data.get('name')}")
            
            # Import teams
            self.import_teams(league_key, year)
            
            # Import draft (with delay to avoid rate limiting)
            time.sleep(1)
            self.import_draft(league_key, year)
            
            # Commit this league
            self.db_conn.commit()
            return True
            
        except Exception as e:
            print(f"   ❌ Error: {e}")
            self.db_conn.rollback()
            return False
    
    def import_teams(self, league_key, year):
        """Import teams for a league"""
        print(f"   👥 Importing teams...")
        
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/teams?format=json"
        data = self.api_request(url)
        
        if not data:
            return
        
        try:
            teams_data = data['fantasy_content']['league'][1]['teams']
            
            team_count = 0
            for key in teams_data:
                if key.isdigit():
                    team_info = teams_data[key]['team']
                    
                    # Handle both list and dict formats
                    if isinstance(team_info, list):
                        team_data = {}
                        for item in team_info:
                            if isinstance(item, dict):
                                team_data.update(item)
                    else:
                        team_data = team_info
                    
                    # Get manager info
                    manager_guid = None
                    manager_name = 'Unknown'
                    
                    if 'managers' in team_data:
                        managers = team_data['managers']
                        if isinstance(managers, list) and len(managers) > 0:
                            manager_info = managers[0].get('manager', {})
                            manager_guid = manager_info.get('guid')
                            manager_name = manager_info.get('nickname', 'Unknown')
                    
                    # Insert manager
                    manager_id = None
                    if manager_guid:
                        self.cursor.execute("""
                            INSERT INTO managers (yahoo_guid, manager_name)
                            VALUES (%s, %s)
                            ON CONFLICT (yahoo_guid) DO UPDATE
                            SET manager_name = EXCLUDED.manager_name
                            RETURNING manager_id
                        """, (manager_guid, manager_name))
                        
                        result = self.cursor.fetchone()
                        if result:
                            manager_id = result['manager_id']
                    
                    # Insert team
                    self.cursor.execute("""
                        INSERT INTO teams (
                            league_id, manager_id, team_name, 
                            draft_position
                        )
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        RETURNING team_id
                    """, (
                        league_key,
                        manager_id,
                        team_data.get('name', 'Unknown'),
                        team_data.get('draft_position', team_count + 1)
                    ))
                    
                    if self.cursor.fetchone():
                        team_count += 1
                        self.stats['teams'] += 1
            
            print(f"      ✅ {team_count} teams imported")
            
        except Exception as e:
            print(f"      ❌ Error: {e}")
    
    def import_draft(self, league_key, year):
        """Import draft results"""
        print(f"   📝 Importing draft...")
        
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/draftresults?format=json"
        data = self.api_request(url, timeout=15)
        
        if not data:
            print(f"      ⚠️  No draft data available")
            return
        
        try:
            draft_data = data['fantasy_content']['league'][1]['draft_results']
            
            picks = 0
            for key in draft_data:
                if key.isdigit():
                    pick_info = draft_data[key]['draft_result']
                    
                    player_key = pick_info.get('player_key')
                    if player_key:
                        # Simple player creation
                        self.cursor.execute("""
                            INSERT INTO players (yahoo_player_id, player_name, position, nfl_team)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (yahoo_player_id) DO NOTHING
                            RETURNING player_id
                        """, (
                            player_key,
                            f"Player {player_key}",
                            'TBD',
                            'TBD'
                        ))
                        
                        result = self.cursor.fetchone()
                        if result:
                            player_id = result['player_id']
                            self.stats['players'] += 1
                            
                            # Get team (simplified)
                            team_position = int(pick_info.get('team_key', '0').split('.')[-1])
                            
                            self.cursor.execute("""
                                SELECT team_id FROM teams 
                                WHERE league_id = %s 
                                ORDER BY draft_position, team_id 
                                LIMIT 1 OFFSET %s
                            """, (league_key, team_position - 1))
                            
                            team_result = self.cursor.fetchone()
                            if team_result:
                                # Insert draft pick
                                self.cursor.execute("""
                                    INSERT INTO draft_picks (
                                        league_id, team_id, player_id,
                                        round_number, overall_pick
                                    )
                                    VALUES (%s, %s, %s, %s, %s)
                                    ON CONFLICT DO NOTHING
                                """, (
                                    league_key,
                                    team_result['team_id'],
                                    player_id,
                                    pick_info.get('round', 0),
                                    pick_info.get('pick', 0)
                                ))
                                
                                picks += 1
                                self.stats['draft_picks'] += 1
            
            print(f"      ✅ {picks} draft picks imported")
            
        except Exception as e:
            print(f"      ❌ Error: {e}")
    
    def import_all(self):
        """Import all LMU leagues"""
        print("\n" + "="*60)
        print("🏈 IMPORTING ALL LMU STILL UNDEFEATED LEAGUES")
        print("="*60)
        
        successful = []
        failed = []
        
        for league_key, year in LMU_LEAGUES:
            if self.import_league(league_key, year):
                successful.append(year)
            else:
                failed.append(year)
            
            # Small delay between leagues
            time.sleep(2)
        
        # Print summary
        print("\n" + "="*60)
        print("📊 IMPORT COMPLETE")
        print("="*60)
        print(f"\n✅ Successful: {successful}")
        print(f"❌ Failed: {failed}")
        print(f"\n📈 Total Stats:")
        print(f"   Leagues: {self.stats['leagues']}")
        print(f"   Teams: {self.stats['teams']}")
        print(f"   Managers: {self.stats['managers']}")
        print(f"   Players: {self.stats['players']}")
        print(f"   Draft Picks: {self.stats['draft_picks']}")
    
    def close(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.db_conn:
            self.db_conn.close()

def main():
    importer = LMUImporter()
    
    try:
        importer.import_all()
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
    finally:
        importer.close()

if __name__ == "__main__":
    main()