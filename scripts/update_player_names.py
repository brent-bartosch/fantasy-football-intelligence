#!/usr/bin/env python3
"""
Update player names with real 2025 fantasy-relevant players
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# Top fantasy-relevant players for 2025
FANTASY_PLAYERS_2025 = [
    # QBs
    ('Josh Allen', 'QB', 'BUF'),
    ('Jalen Hurts', 'QB', 'PHI'),
    ('Lamar Jackson', 'QB', 'BAL'),
    ('Patrick Mahomes', 'QB', 'KC'),
    ('Dak Prescott', 'QB', 'DAL'),
    ('Joe Burrow', 'QB', 'CIN'),
    ('Justin Herbert', 'QB', 'LAC'),
    ('Trevor Lawrence', 'QB', 'JAX'),
    ('Tua Tagovailoa', 'QB', 'MIA'),
    ('Justin Fields', 'QB', 'CHI'),
    ('Deshaun Watson', 'QB', 'CLE'),
    ('Kirk Cousins', 'QB', 'MIN'),
    ('Geno Smith', 'QB', 'SEA'),
    ('Jared Goff', 'QB', 'DET'),
    ('Daniel Jones', 'QB', 'NYG'),
    
    # RBs
    ('Christian McCaffrey', 'RB', 'SF'),
    ('Breece Hall', 'RB', 'NYJ'),
    ('Jonathan Taylor', 'RB', 'IND'),
    ('Bijan Robinson', 'RB', 'ATL'),
    ('Saquon Barkley', 'RB', 'NYG'),
    ('Austin Ekeler', 'RB', 'LAC'),
    ('Derrick Henry', 'RB', 'TEN'),
    ('Nick Chubb', 'RB', 'CLE'),
    ('Josh Jacobs', 'RB', 'LV'),
    ('Tony Pollard', 'RB', 'DAL'),
    ('Travis Etienne', 'RB', 'JAX'),
    ('Najee Harris', 'RB', 'PIT'),
    ('Kenneth Walker III', 'RB', 'SEA'),
    ('Jahmyr Gibbs', 'RB', 'DET'),
    ('Rhamondre Stevenson', 'RB', 'NE'),
    ('Aaron Jones', 'RB', 'GB'),
    ('Dameon Pierce', 'RB', 'HOU'),
    ('Miles Sanders', 'RB', 'CAR'),
    ('James Conner', 'RB', 'ARI'),
    ('Joe Mixon', 'RB', 'CIN'),
    ('Alvin Kamara', 'RB', 'NO'),
    ('David Montgomery', 'RB', 'DET'),
    ('Javonte Williams', 'RB', 'DEN'),
    ('Rachaad White', 'RB', 'TB'),
    ('Isiah Pacheco', 'RB', 'KC'),
    
    # WRs
    ('CeeDee Lamb', 'WR', 'DAL'),
    ('Tyreek Hill', 'WR', 'MIA'),
    ('Justin Jefferson', 'WR', 'MIN'),
    ('Ja\'Marr Chase', 'WR', 'CIN'),
    ('Amon-Ra St. Brown', 'WR', 'DET'),
    ('Stefon Diggs', 'WR', 'BUF'),
    ('A.J. Brown', 'WR', 'PHI'),
    ('Davante Adams', 'WR', 'LV'),
    ('Cooper Kupp', 'WR', 'LAR'),
    ('Puka Nacua', 'WR', 'LAR'),
    ('Garrett Wilson', 'WR', 'NYJ'),
    ('Chris Olave', 'WR', 'NO'),
    ('DK Metcalf', 'WR', 'SEA'),
    ('Mike Evans', 'WR', 'TB'),
    ('Amari Cooper', 'WR', 'CLE'),
    ('DeVonta Smith', 'WR', 'PHI'),
    ('Keenan Allen', 'WR', 'LAC'),
    ('Calvin Ridley', 'WR', 'JAX'),
    ('Terry McLaurin', 'WR', 'WAS'),
    ('DJ Moore', 'WR', 'CHI'),
    ('Chris Godwin', 'WR', 'TB'),
    ('Tee Higgins', 'WR', 'CIN'),
    ('Jaylen Waddle', 'WR', 'MIA'),
    ('DeAndre Hopkins', 'WR', 'TEN'),
    ('Michael Pittman Jr.', 'WR', 'IND'),
    ('Tyler Lockett', 'WR', 'SEA'),
    ('Mike Williams', 'WR', 'LAC'),
    ('Christian Watson', 'WR', 'GB'),
    ('Marquise Brown', 'WR', 'ARI'),
    ('Diontae Johnson', 'WR', 'PIT'),
    
    # TEs
    ('Travis Kelce', 'TE', 'KC'),
    ('Mark Andrews', 'TE', 'BAL'),
    ('T.J. Hockenson', 'TE', 'MIN'),
    ('George Kittle', 'TE', 'SF'),
    ('Sam LaPorta', 'TE', 'DET'),
    ('Dalton Kincaid', 'TE', 'BUF'),
    ('Kyle Pitts', 'TE', 'ATL'),
    ('Dallas Goedert', 'TE', 'PHI'),
    ('Darren Waller', 'TE', 'NYG'),
    ('Evan Engram', 'TE', 'JAX'),
    ('Cole Kmet', 'TE', 'CHI'),
    ('David Njoku', 'TE', 'CLE'),
    ('Tyler Higbee', 'TE', 'LAR'),
    ('Pat Freiermuth', 'TE', 'PIT'),
    ('Dalton Schultz', 'TE', 'HOU'),
]

def update_players():
    """Add/update player names in database"""
    
    # Database connection
    db_conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME', 'fantasy_football'),
        user=os.getenv('DB_USER', 'brentbartosch'),
        password=os.getenv('DB_PASSWORD', ''),
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432')
    )
    cursor = db_conn.cursor(cursor_factory=RealDictCursor)
    
    print("\n🏈 UPDATING PLAYER DATABASE")
    print("="*40)
    
    added = 0
    updated = 0
    
    for player_name, position, team in FANTASY_PLAYERS_2025:
        # Create a unique player ID based on name
        clean_name = player_name.replace(' ', '_').replace("'", '').lower()
        yahoo_id = f"nfl.p.{clean_name}"
        
        cursor.execute("""
            INSERT INTO players (yahoo_player_id, player_name, position, nfl_team)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (yahoo_player_id) DO UPDATE
            SET player_name = EXCLUDED.player_name,
                position = EXCLUDED.position,
                nfl_team = EXCLUDED.nfl_team
            RETURNING player_id, 
                     CASE WHEN xmax = 0 THEN 'INSERT' ELSE 'UPDATE' END as operation
        """, (yahoo_id, player_name, position, team))
        
        result = cursor.fetchone()
        if result['operation'] == 'INSERT':
            added += 1
        else:
            updated += 1
    
    db_conn.commit()
    
    print(f"✅ Added {added} new players")
    print(f"✅ Updated {updated} existing players")
    
    # Show position breakdown
    cursor.execute("""
        SELECT position, COUNT(*) as count
        FROM players
        WHERE player_name NOT LIKE 'Player %'
        GROUP BY position
        ORDER BY position
    """)
    
    print("\n📊 Position Breakdown:")
    for row in cursor.fetchall():
        print(f"   {row['position']}: {row['count']} players")
    
    cursor.close()
    db_conn.close()

if __name__ == "__main__":
    update_players()