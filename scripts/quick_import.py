#!/usr/bin/env python3
"""
Quick import for specific leagues
"""

import sys
sys.path.append('.')
from import_yahoo_data import YahooDataImporter

def import_recent_leagues():
    """Import recent LMU leagues"""
    importer = YahooDataImporter()
    
    # Focus on recent years of LMU Still Undefeated
    recent_leagues = [
        ('449.l.427482', 'LMU Still Undefeated', 2024),
        ('423.l.22647', 'LMU Still Undefeated', 2023),
        ('414.l.427482', 'LMU Still Undefeated', 2022),
        ('406.l.22647', 'LMU Still Undefeated', 2021),
    ]
    
    print("\n🏈 Importing Recent LMU Still Undefeated Leagues\n")
    
    for league_key, name, year in recent_leagues:
        print(f"\n{'='*60}")
        print(f"Importing: {name} ({year})")
        print(f"League Key: {league_key}")
        print('='*60)
        
        try:
            # First verify the league exists
            leagues = importer.get_user_leagues()
            
            # Find matching league
            found = False
            for league in leagues:
                if str(year) in league['season'] and 'LMU' in league['name']:
                    actual_key = league['league_key']
                    print(f"✅ Found league: {actual_key}")
                    
                    # Import it
                    success = importer.import_league(actual_key)
                    if success:
                        print(f"✅ Successfully imported {name} ({year})")
                    else:
                        print(f"⚠️  Partial import for {name} ({year})")
                    
                    found = True
                    break
            
            if not found:
                print(f"⚠️  Could not find {name} ({year})")
                    
        except Exception as e:
            print(f"❌ Error importing {name} ({year}): {e}")
    
    importer.close()
    print("\n✅ Import process complete!")

if __name__ == "__main__":
    import_recent_leagues()