#!/usr/bin/env python3
"""
Import all seasons for league ID 326814
This appears to be your LMU Still Undefeated league
"""

import os
import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# NFL Game IDs by year
GAME_IDS = {
    2025: 449,
    2024: 449,
    2023: 423,
    2022: 414,
    2021: 406,
    2020: 399,
    2019: 390,
    2018: 380,
    2017: 371,
    2016: 359,
    2015: 348,
    2014: 331,
    2013: 314,
    2012: 273,
    2011: 257,
    2010: 242,
    2009: 222,
}

class LeagueImporter:
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
        
        # Track stats
        self.stats = {
            'leagues': 0,
            'teams': 0,
            'managers': 0,
            'draft_picks': 0,
            'players': 0,
            'errors': []
        }
    
    def load_token(self):
        """Load OAuth token"""
        token_file = 'config/yahoo_token.json'
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                return json.load(f)
        return None
    
    def api_request(self, url):
        """Make authenticated API request"""
        headers = {
            'Authorization': f"Bearer {self.token['access_token']}",
            'Accept': 'application/json'
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"API Error {response.status_code}: {response.text[:200]}")
            return None
    
    def find_league_by_id(self, league_id, year):
        """Find league key for a specific league ID and year"""
        game_id = GAME_IDS.get(year)
        if not game_id:
            print(f"   ⚠️  No game ID for year {year}")
            return None
        
        # Try different league key patterns
        possible_keys = [
            f"{game_id}.l.{league_id}",
            f"{game_id}.l.{league_id:06d}",  # Padded with zeros
        ]
        
        for league_key in possible_keys:
            url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}?format=json"
            data = self.api_request(url)
            
            if data and 'fantasy_content' in data:
                try:
                    league_data = data['fantasy_content']['league'][0]
                    if 'league_key' in league_data:
                        return league_key
                except:
                    continue
        
        return None
    
    def import_league_basic(self, league_key, year):
        """Import basic league information"""
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}?format=json"
        data = self.api_request(url)
        
        if not data:
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
                    season_year = EXCLUDED.season_year,
                    num_teams = EXCLUDED.num_teams
                RETURNING league_id
            """, (
                league_key,
                league_data.get('name', 'Unknown'),
                year,
                league_data.get('num_teams', 0),
                league_data.get('scoring_type', 'head'),
                json.dumps({})  # Will update with settings later
            ))
            
            self.stats['leagues'] += 1
            print(f"      ✅ League: {league_data.get('name')} imported")
            return True
            
        except Exception as e:
            print(f"      ❌ Error: {e}")
            self.stats['errors'].append(f"League {league_key}: {e}")
            return False
    
    def import_teams_and_managers(self, league_key, year):
        """Import teams and managers for a league"""
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/teams?format=json"
        data = self.api_request(url)
        
        if not data:
            return
        
        try:
            teams_data = data['fantasy_content']['league'][1]['teams']
            
            # Parse teams
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
                    
                    # Extract manager info
                    manager_guid = None
                    manager_name = 'Unknown'
                    
                    if 'managers' in team_data:
                        managers = team_data['managers']
                        if isinstance(managers, list) and len(managers) > 0:
                            manager_info = managers[0].get('manager', {})
                            manager_guid = manager_info.get('guid')
                            manager_name = manager_info.get('nickname', 'Unknown')
                    
                    # Insert manager if we have one
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
                            self.stats['managers'] += 1
                    
                    # Insert team
                    self.cursor.execute("""
                        INSERT INTO teams (
                            league_id, manager_id, team_name, 
                            draft_position, final_rank, total_points_scored
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (
                        league_key,
                        manager_id,
                        team_data.get('name', 'Unknown Team'),
                        team_data.get('draft_position', 0),
                        None,  # Will update later with standings
                        0      # Will update later
                    ))
                    
                    self.stats['teams'] += 1
                    print(f"         {team_data.get('name', 'Unknown')} - {manager_name}")
            
        except Exception as e:
            print(f"      ❌ Error importing teams: {e}")
            self.stats['errors'].append(f"Teams {league_key}: {e}")
    
    def import_draft_results(self, league_key, year):
        """Import draft results for a league"""
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/draftresults?format=json"
        data = self.api_request(url)
        
        if not data:
            return
        
        try:
            draft_data = data['fantasy_content']['league'][1]['draft_results']
            
            picks_imported = 0
            for key in draft_data:
                if key.isdigit():
                    pick_info = draft_data[key]['draft_result']
                    
                    # Get or create player
                    player_key = pick_info.get('player_key')
                    if player_key:
                        player_id = self.get_or_create_simple_player(player_key)
                        
                        if player_id:
                            # Simple team lookup by position
                            team_position = int(pick_info.get('team_key', '0').split('.')[-1])
                            
                            self.cursor.execute("""
                                SELECT team_id FROM teams 
                                WHERE league_id = %s 
                                ORDER BY team_id 
                                LIMIT 1 OFFSET %s
                            """, (league_key, team_position - 1))
                            
                            result = self.cursor.fetchone()
                            if result:
                                team_id = result['team_id']
                                
                                # Insert draft pick
                                self.cursor.execute("""
                                    INSERT INTO draft_picks (
                                        league_id, team_id, player_id,
                                        round_number, pick_number, overall_pick
                                    )
                                    VALUES (%s, %s, %s, %s, %s, %s)
                                    ON CONFLICT DO NOTHING
                                """, (
                                    league_key,
                                    team_id,
                                    player_id,
                                    pick_info.get('round', 0),
                                    pick_info.get('pick', 0),
                                    pick_info.get('pick', 0)
                                ))
                                
                                picks_imported += 1
                                self.stats['draft_picks'] += 1
            
            print(f"         {picks_imported} picks imported")
            
        except Exception as e:
            print(f"      ❌ Error importing draft: {e}")
            self.stats['errors'].append(f"Draft {league_key}: {e}")
    
    def get_or_create_simple_player(self, player_key):
        """Simplified player creation"""
        # Check if exists
        self.cursor.execute(
            "SELECT player_id FROM players WHERE yahoo_player_id = %s",
            (player_key,)
        )
        
        result = self.cursor.fetchone()
        if result:
            return result['player_id']
        
        # Create with minimal info
        self.cursor.execute("""
            INSERT INTO players (yahoo_player_id, player_name, position, nfl_team)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (yahoo_player_id) DO NOTHING
            RETURNING player_id
        """, (
            player_key,
            f"Player {player_key}",  # Placeholder name
            'TBD',
            'TBD'
        ))
        
        result = self.cursor.fetchone()
        if result:
            self.stats['players'] += 1
            return result['player_id']
        
        return None
    
    def import_all_seasons(self, league_id):
        """Import all available seasons for a league ID"""
        print(f"\n{'='*60}")
        print(f"🏈 Importing All Seasons for League ID: {league_id}")
        print(f"{'='*60}\n")
        
        successful_years = []
        failed_years = []
        
        # Try each year
        for year in sorted(GAME_IDS.keys(), reverse=True):
            print(f"\n📅 Checking {year} season...")
            
            # Find the league key for this year
            league_key = self.find_league_by_id(league_id, year)
            
            if league_key:
                print(f"   ✅ Found league: {league_key}")
                
                try:
                    # Import league info
                    if self.import_league_basic(league_key, year):
                        
                        # Import teams and managers
                        print(f"      👥 Importing teams...")
                        self.import_teams_and_managers(league_key, year)
                        
                        # Import draft
                        print(f"      📝 Importing draft...")
                        self.import_draft_results(league_key, year)
                        
                        # Commit this season
                        self.db_conn.commit()
                        successful_years.append(year)
                        print(f"   ✅ {year} complete!")
                    
                except Exception as e:
                    self.db_conn.rollback()
                    print(f"   ❌ Error in {year}: {e}")
                    failed_years.append(year)
                    self.stats['errors'].append(f"Year {year}: {e}")
            else:
                print(f"   ⚠️  No league found for {year}")
        
        # Print summary
        print(f"\n{'='*60}")
        print(f"📊 IMPORT SUMMARY")
        print(f"{'='*60}")
        print(f"\n✅ Successful Years: {successful_years}")
        print(f"❌ Failed Years: {failed_years}")
        print(f"\n📈 Statistics:")
        print(f"   Leagues: {self.stats['leagues']}")
        print(f"   Teams: {self.stats['teams']}")
        print(f"   Managers: {self.stats['managers']}")
        print(f"   Draft Picks: {self.stats['draft_picks']}")
        print(f"   Players: {self.stats['players']}")
        
        if self.stats['errors']:
            print(f"\n⚠️  Errors encountered: {len(self.stats['errors'])}")
            for error in self.stats['errors'][:5]:  # Show first 5 errors
                print(f"   - {error}")
    
    def close(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.db_conn:
            self.db_conn.close()

def main():
    importer = LeagueImporter()
    
    try:
        # Import all seasons for league 326814
        importer.import_all_seasons(326814)
        
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
    
    finally:
        importer.close()

if __name__ == "__main__":
    main()