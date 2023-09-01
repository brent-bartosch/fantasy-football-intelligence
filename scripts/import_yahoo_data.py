#!/usr/bin/env python3
"""
Import Yahoo Fantasy Football data into PostgreSQL database
"""

import os
import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from datetime import datetime
from dotenv import load_dotenv
import argparse

load_dotenv()

class YahooDataImporter:
    """Import Yahoo Fantasy data to PostgreSQL"""
    
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
        self.db_conn.autocommit = False
        self.cursor = self.db_conn.cursor(cursor_factory=RealDictCursor)
        
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
    
    def get_user_leagues(self, game_id=None):
        """Get all user's leagues for a game/season"""
        print(f"\n📊 Fetching user's leagues...")
        
        if game_id:
            url = f"https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games;game_keys={game_id}/leagues?format=json"
        else:
            # Get all NFL leagues
            url = "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games;game_codes=nfl/leagues?format=json"
        
        data = self.api_request(url)
        if not data:
            return []
        
        leagues = []
        try:
            users = data['fantasy_content']['users']['0']['user']
            
            for item in users:
                if isinstance(item, dict) and 'games' in item:
                    games = item['games']
                    
                    for game_key, game_value in games.items():
                        if game_key.isdigit() and isinstance(game_value, dict):
                            game_data = game_value.get('game', [])
                            
                            # Find leagues in game data
                            for game_item in game_data:
                                if isinstance(game_item, dict) and 'leagues' in game_item:
                                    leagues_data = game_item['leagues']
                                    
                                    for league_key, league_value in leagues_data.items():
                                        if league_key.isdigit():
                                            league_info = league_value['league']
                                            
                                            # Extract league details
                                            league = {
                                                'league_key': league_info[0]['league_key'],
                                                'league_id': league_info[0]['league_id'],
                                                'name': league_info[0]['name'],
                                                'season': league_info[0].get('season', ''),
                                                'num_teams': league_info[0].get('num_teams', 0),
                                                'draft_status': league_info[0].get('draft_status', ''),
                                                'is_finished': league_info[0].get('is_finished', 0)
                                            }
                                            leagues.append(league)
                                            print(f"   Found: {league['name']} ({league['season']})")
            
        except Exception as e:
            print(f"Error parsing leagues: {e}")
        
        return leagues
    
    def import_league(self, league_key):
        """Import complete league data"""
        print(f"\n🔄 Importing league: {league_key}")
        
        # Get league details
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}?format=json"
        data = self.api_request(url)
        
        if not data:
            return False
        
        try:
            league_data = data['fantasy_content']['league'][0]
            
            # Insert league
            self.cursor.execute("""
                INSERT INTO leagues (league_id, league_name, season_year, num_teams, draft_type, roster_positions)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (league_id) DO UPDATE
                SET league_name = EXCLUDED.league_name,
                    season_year = EXCLUDED.season_year,
                    num_teams = EXCLUDED.num_teams
            """, (
                league_data['league_key'],
                league_data['name'],
                int(league_data['season']),
                league_data.get('num_teams', 0),
                league_data.get('draft_type', 'snake'),
                json.dumps(league_data.get('roster_positions', {}))
            ))
            
            print(f"   ✅ League info imported")
            
            # Import settings
            self.import_league_settings(league_key)
            
            # Import teams
            self.import_teams(league_key)
            
            # Import draft
            self.import_draft_results(league_key)
            
            # Import transactions
            self.import_transactions(league_key)
            
            self.db_conn.commit()
            return True
            
        except Exception as e:
            print(f"   ❌ Error importing league: {e}")
            self.db_conn.rollback()
            return False
    
    def import_league_settings(self, league_key):
        """Import league scoring settings"""
        print(f"   📋 Importing scoring settings...")
        
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/settings?format=json"
        data = self.api_request(url)
        
        if not data:
            return
        
        try:
            settings = data['fantasy_content']['league'][1]['settings'][0]
            stat_modifiers = settings.get('stat_modifiers', {}).get('stats', [])
            
            for stat in stat_modifiers:
                if 'stat' in stat:
                    stat_info = stat['stat']
                    
                    # Map stat IDs to names
                    stat_names = {
                        '4': 'passing_yards', '5': 'passing_tds', '6': 'interceptions',
                        '8': 'rushing_yards', '9': 'rushing_tds',
                        '10': 'receptions', '11': 'receiving_yards', '12': 'receiving_tds',
                        '15': 'return_tds', '16': '2pt_conversions', '18': 'fumbles_lost'
                    }
                    
                    stat_id = stat_info['stat_id']
                    stat_name = stat_names.get(stat_id, f'stat_{stat_id}')
                    
                    self.cursor.execute("""
                        INSERT INTO scoring_settings (league_id, stat_name, points_value, stat_category)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (
                        league_key,
                        stat_name,
                        float(stat_info.get('value', 0)),
                        'offense'  # You can enhance this with proper categories
                    ))
            
            print(f"      ✅ {len(stat_modifiers)} scoring rules imported")
            
        except Exception as e:
            print(f"      ❌ Error importing settings: {e}")
    
    def import_teams(self, league_key):
        """Import all teams in league"""
        print(f"   👥 Importing teams...")
        
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/teams?format=json"
        data = self.api_request(url)
        
        if not data:
            return
        
        try:
            teams = data['fantasy_content']['league'][1]['teams']
            team_count = teams.get('count', 0)
            
            for key, value in teams.items():
                if key.isdigit() and isinstance(value, dict):
                    team_data = value['team'][0]
                    
                    # Get or create manager
                    managers = team_data.get('managers', [])
                    manager_guid = None
                    manager_name = 'Unknown'
                    
                    if managers:
                        manager_info = managers[0]['manager']
                        manager_guid = manager_info.get('guid')
                        manager_name = manager_info.get('nickname', 'Unknown')
                        
                        # Insert manager
                        self.cursor.execute("""
                            INSERT INTO managers (yahoo_guid, manager_name)
                            VALUES (%s, %s)
                            ON CONFLICT (yahoo_guid) DO UPDATE
                            SET manager_name = EXCLUDED.manager_name
                            RETURNING manager_id
                        """, (manager_guid, manager_name))
                        
                        manager_id = self.cursor.fetchone()['manager_id']
                    else:
                        manager_id = None
                    
                    # Insert team
                    self.cursor.execute("""
                        INSERT INTO teams (
                            league_id, manager_id, team_name, draft_position,
                            final_rank, total_points_scored, made_playoffs, won_championship
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        RETURNING team_id
                    """, (
                        league_key,
                        manager_id,
                        team_data['name'],
                        team_data.get('draft_position', 0),
                        team_data.get('team_standings', {}).get('rank', 0),
                        team_data.get('team_points', {}).get('total', 0),
                        team_data.get('clinched_playoffs', False),
                        team_data.get('team_standings', {}).get('rank') == 1
                    ))
                    
                    print(f"      ✅ {team_data['name']} - {manager_name}")
            
            print(f"      ✅ {team_count} teams imported")
            
        except Exception as e:
            print(f"      ❌ Error importing teams: {e}")
    
    def import_draft_results(self, league_key):
        """Import draft results"""
        print(f"   📝 Importing draft results...")
        
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/draftresults?format=json"
        data = self.api_request(url)
        
        if not data:
            return
        
        try:
            draft_results = data['fantasy_content']['league'][1]['draft_results']
            pick_count = draft_results.get('count', 0)
            
            imported = 0
            for key, value in draft_results.items():
                if key.isdigit() and isinstance(value, dict):
                    pick_data = value['draft_result']
                    
                    # Get player info
                    player_key = pick_data.get('player_key')
                    
                    if player_key:
                        # Insert player first
                        player_id = self.get_or_create_player(player_key)
                        
                        # Get team
                        team_key = pick_data.get('team_key')
                        self.cursor.execute("""
                            SELECT team_id FROM teams 
                            WHERE league_id = %s 
                            ORDER BY team_id 
                            LIMIT 1 OFFSET %s
                        """, (league_key, int(pick_data.get('team_key', '0').split('.')[-1]) - 1))
                        
                        result = self.cursor.fetchone()
                        team_id = result['team_id'] if result else None
                        
                        if team_id and player_id:
                            # Insert draft pick
                            self.cursor.execute("""
                                INSERT INTO draft_picks (
                                    league_id, team_id, player_id, round_number,
                                    pick_number, overall_pick, auction_cost
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT DO NOTHING
                            """, (
                                league_key,
                                team_id,
                                player_id,
                                pick_data.get('round', 0),
                                pick_data.get('pick', 0),
                                pick_data.get('pick', 0),
                                pick_data.get('cost', None)
                            ))
                            imported += 1
            
            print(f"      ✅ {imported}/{pick_count} draft picks imported")
            
        except Exception as e:
            print(f"      ❌ Error importing draft: {e}")
    
    def get_or_create_player(self, player_key):
        """Get or create a player record"""
        
        # Check if player exists
        self.cursor.execute("""
            SELECT player_id FROM players WHERE yahoo_player_id = %s
        """, (player_key,))
        
        result = self.cursor.fetchone()
        if result:
            return result['player_id']
        
        # Get player info from API
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/player/{player_key}?format=json"
        data = self.api_request(url)
        
        if data:
            try:
                player_data = data['fantasy_content']['player'][0]
                
                self.cursor.execute("""
                    INSERT INTO players (yahoo_player_id, player_name, position, nfl_team)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (yahoo_player_id) DO UPDATE
                    SET player_name = EXCLUDED.player_name
                    RETURNING player_id
                """, (
                    player_key,
                    player_data['name']['full'],
                    player_data.get('primary_position', 'UNKNOWN'),
                    player_data.get('editorial_team_abbr', 'FA')
                ))
                
                return self.cursor.fetchone()['player_id']
                
            except Exception as e:
                print(f"         ⚠️  Could not create player {player_key}: {e}")
        
        return None
    
    def import_transactions(self, league_key):
        """Import league transactions"""
        print(f"   💱 Importing transactions...")
        
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/transactions?format=json"
        data = self.api_request(url)
        
        if not data:
            return
        
        try:
            transactions = data['fantasy_content']['league'][1].get('transactions', {})
            trans_count = transactions.get('count', 0)
            
            print(f"      Found {trans_count} transactions")
            # Transaction import logic would go here
            # This is complex due to the variety of transaction types
            
        except Exception as e:
            print(f"      ❌ Error importing transactions: {e}")
    
    def close(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.db_conn:
            self.db_conn.close()

def main():
    parser = argparse.ArgumentParser(description='Import Yahoo Fantasy Football data')
    parser.add_argument('--league-key', help='Specific league key to import')
    parser.add_argument('--game-id', help='Game ID (e.g., 414 for 2022 NFL)')
    parser.add_argument('--all', action='store_true', help='Import all available leagues')
    
    args = parser.parse_args()
    
    importer = YahooDataImporter()
    
    try:
        if args.league_key:
            # Import specific league
            importer.import_league(args.league_key)
            
        elif args.all or args.game_id:
            # Import all leagues for a game/season
            leagues = importer.get_user_leagues(args.game_id)
            
            for league in leagues:
                importer.import_league(league['league_key'])
        
        else:
            # Interactive mode - show available leagues
            print("\n🏈 Available Fantasy Football Leagues:\n")
            leagues = importer.get_user_leagues()
            
            if leagues:
                for i, league in enumerate(leagues, 1):
                    print(f"{i}. {league['name']} ({league['season']}) - {league['league_key']}")
                
                choice = input("\nEnter league number to import (or 'all' for all): ").strip()
                
                if choice.lower() == 'all':
                    for league in leagues:
                        importer.import_league(league['league_key'])
                elif choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(leagues):
                        importer.import_league(leagues[idx]['league_key'])
            else:
                print("No leagues found!")
        
        print("\n✅ Import complete!")
        
    except Exception as e:
        print(f"\n❌ Import failed: {e}")
    
    finally:
        importer.close()

if __name__ == "__main__":
    main()