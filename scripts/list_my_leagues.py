#!/usr/bin/env python3
"""
List all accessible leagues to find correct league IDs
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

def load_token():
    """Load OAuth token"""
    token_file = 'config/yahoo_token.json'
    if os.path.exists(token_file):
        with open(token_file, 'r') as f:
            return json.load(f)
    return None

def list_all_leagues():
    """List all accessible leagues"""
    token = load_token()
    if not token:
        print("No token found!")
        return
    
    headers = {
        'Authorization': f"Bearer {token['access_token']}",
        'Accept': 'application/json'
    }
    
    # Get all NFL leagues
    url = "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games;game_codes=nfl/leagues?format=json"
    
    response = requests.get(url, headers=headers, timeout=30)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        return
    
    data = response.json()
    
    leagues_by_year = {}
    
    try:
        users = data['fantasy_content']['users']['0']['user']
        
        for item in users:
            if isinstance(item, dict) and 'games' in item:
                games = item['games']
                
                for game_key, game_value in games.items():
                    if game_key.isdigit() and isinstance(game_value, dict):
                        game_data = game_value.get('game', [])
                        
                        # Get game info
                        game_info = game_data[0] if game_data else {}
                        season = game_info.get('season', 'Unknown')
                        
                        # Find leagues in game data
                        for game_item in game_data:
                            if isinstance(game_item, dict) and 'leagues' in game_item:
                                leagues_data = game_item['leagues']
                                
                                for league_key, league_value in leagues_data.items():
                                    if league_key.isdigit():
                                        league_info = league_value['league']
                                        
                                        league = {
                                            'league_key': league_info[0]['league_key'],
                                            'league_id': league_info[0]['league_id'],
                                            'name': league_info[0]['name'],
                                            'season': season,
                                            'num_teams': league_info[0].get('num_teams', 0),
                                        }
                                        
                                        if season not in leagues_by_year:
                                            leagues_by_year[season] = []
                                        
                                        leagues_by_year[season].append(league)
    
    except Exception as e:
        print(f"Error parsing: {e}")
        import traceback
        traceback.print_exc()
    
    # Print organized by year
    print("\n" + "="*70)
    print("YOUR FANTASY FOOTBALL LEAGUES BY YEAR")
    print("="*70)
    
    for year in sorted(leagues_by_year.keys(), reverse=True):
        print(f"\n📅 {year} Season:")
        print("-" * 40)
        
        for league in leagues_by_year[year]:
            # Extract just the league ID number from the key
            league_id_num = league['league_key'].split('.')[-1]
            print(f"  • {league['name']}")
            print(f"    League ID: {league_id_num}")
            print(f"    Full Key: {league['league_key']}")
            print(f"    Teams: {league['num_teams']}")
    
    # Find LMU leagues specifically
    print("\n" + "="*70)
    print("LMU STILL UNDEFEATED LEAGUES")
    print("="*70)
    
    lmu_leagues = []
    for year, leagues in leagues_by_year.items():
        for league in leagues:
            if 'LMU' in league['name'].upper():
                lmu_leagues.append((year, league))
    
    for year, league in sorted(lmu_leagues, key=lambda x: x[0], reverse=True):
        league_id_num = league['league_key'].split('.')[-1]
        print(f"{year}: League ID {league_id_num} (Key: {league['league_key']})")

if __name__ == "__main__":
    list_all_leagues()