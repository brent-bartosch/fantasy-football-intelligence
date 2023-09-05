#!/usr/bin/env python3
"""
Scoring Adjustment Calculator
Adjusts player rankings based on your league's specific scoring rules
compared to standard scoring.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Dict, List, Tuple
from dotenv import load_dotenv

load_dotenv()

class ScoringAdjuster:
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
        
        # Standard scoring baseline (typical half-PPR)
        self.standard_scoring = {
            'QB': {
                'passing_yards': 0.04,  # 1 point per 25 yards
                'passing_td': 4,
                'interception': -2,
                'rushing_yards': 0.1,  # 1 point per 10 yards
                'rushing_td': 6,
                'two_point': 2
            },
            'RB': {
                'rushing_yards': 0.1,
                'rushing_td': 6,
                'reception': 0.5,  # Half-PPR
                'receiving_yards': 0.1,
                'receiving_td': 6,
                'fumble_lost': -2
            },
            'WR': {
                'reception': 0.5,
                'receiving_yards': 0.1,
                'receiving_td': 6,
                'rushing_yards': 0.1,
                'rushing_td': 6,
                'fumble_lost': -2
            },
            'TE': {
                'reception': 0.5,
                'receiving_yards': 0.1,
                'receiving_td': 6,
                'fumble_lost': -2
            }
        }
        
        self.your_scoring = {}
        self.position_multipliers = {}
    
    def load_league_scoring(self, league_id: str = None):
        """Load your league's specific scoring settings"""
        print("\n📊 Loading league scoring settings...")
        
        # Try to get from most recent league if not specified
        if not league_id:
            self.cursor.execute("""
                SELECT league_id, roster_positions, league_name, season_year
                FROM leagues 
                WHERE league_name LIKE '%LMU%'
                ORDER BY season_year DESC
                LIMIT 1
            """)
            
            result = self.cursor.fetchone()
            if result:
                league_id = result['league_id']
                print(f"   Using {result['league_name']} ({result['season_year']})")
        
        # Get scoring settings if they exist
        self.cursor.execute("""
            SELECT stat_name, points_value, stat_category 
            FROM scoring_settings
            WHERE league_id = %s
        """, (league_id,))
        
        settings = self.cursor.fetchall()
        
        if settings:
            print(f"   ✅ Found {len(settings)} scoring rules")
            # Process settings into position-based scoring
            self._process_scoring_settings(settings)
        else:
            print("   ⚠️  No custom scoring found, using estimates")
            self._estimate_lmu_scoring()
    
    def _estimate_lmu_scoring(self):
        """
        Estimate LMU league scoring based on typical variations
        Your league seems to value certain positions differently
        """
        print("\n🎯 Estimating LMU scoring variations...")
        
        # Common variations from standard that affect rankings
        self.your_scoring = {
            'QB': {
                'passing_yards': 0.04,  # Standard
                'passing_td': 6,  # 6-point passing TDs (vs 4 standard)
                'interception': -2,
                'rushing_yards': 0.1,
                'rushing_td': 6,
                'two_point': 2
            },
            'RB': {
                'rushing_yards': 0.1,
                'rushing_td': 6,
                'reception': 1.0,  # Full PPR (vs 0.5 standard)
                'receiving_yards': 0.1,
                'receiving_td': 6,
                'fumble_lost': -2,
                'rushing_bonus_100': 3,  # Bonus points
                'receiving_bonus_100': 3
            },
            'WR': {
                'reception': 1.0,  # Full PPR
                'receiving_yards': 0.1,
                'receiving_td': 6,
                'rushing_yards': 0.1,
                'rushing_td': 6,
                'fumble_lost': -2,
                'receiving_bonus_100': 3
            },
            'TE': {
                'reception': 1.0,  # Full PPR
                'receiving_yards': 0.1,
                'receiving_td': 6,
                'fumble_lost': -2,
                'receiving_bonus_100': 3
            }
        }
        
        # Calculate position multipliers based on scoring differences
        self._calculate_position_multipliers()
    
    def _calculate_position_multipliers(self):
        """Calculate how much to adjust each position's value"""
        
        # Compare your scoring to standard for key stats
        position_adjustments = {}
        
        for position in ['QB', 'RB', 'WR', 'TE']:
            if position not in self.your_scoring:
                continue
            
            your_pos = self.your_scoring[position]
            std_pos = self.standard_scoring[position]
            
            # Calculate adjustment factors
            adjustments = []
            
            # Key stat comparisons
            if position == 'QB':
                # 6-pt passing TDs boost QB value significantly
                if your_pos.get('passing_td', 4) > std_pos.get('passing_td', 4):
                    adjustments.append(1.15)  # 15% boost for 6-pt TDs
            
            elif position in ['RB', 'WR', 'TE']:
                # PPR scoring boosts pass-catchers
                your_ppr = your_pos.get('reception', 0.5)
                std_ppr = std_pos.get('reception', 0.5)
                
                if your_ppr > std_ppr:
                    ppr_boost = 1 + (your_ppr - std_ppr) * 0.2
                    adjustments.append(ppr_boost)
                
                # Bonus points for 100-yard games
                if your_pos.get('receiving_bonus_100', 0) > 0:
                    adjustments.append(1.05)  # 5% boost for bonuses
            
            # Average all adjustments
            if adjustments:
                position_adjustments[position] = sum(adjustments) / len(adjustments)
            else:
                position_adjustments[position] = 1.0
        
        self.position_multipliers = position_adjustments
        
        print("\n📊 Position Value Adjustments:")
        for pos, mult in position_adjustments.items():
            change = (mult - 1) * 100
            symbol = "↑" if change > 0 else "↓" if change < 0 else "="
            print(f"   {pos}: {mult:.2f}x ({symbol}{abs(change):.0f}%)")
    
    def adjust_rankings(self):
        """Apply scoring adjustments to create custom rankings"""
        print("\n🔄 Adjusting rankings for your league...")
        
        # Get consensus rankings
        self.cursor.execute("""
            SELECT 
                player_name,
                position,
                AVG(overall_rank) as avg_rank,
                AVG(adp) as avg_adp,
                AVG(projected_points) as avg_points
            FROM expert_rankings
            WHERE season_year = 2025
                AND position IN ('QB', 'RB', 'WR', 'TE')
            GROUP BY player_name, position
            ORDER BY avg_rank
        """)
        
        players = self.cursor.fetchall()
        
        # Clear existing adjusted rankings
        self.cursor.execute("DELETE FROM adjusted_rankings")
        
        # Calculate adjusted values
        adjusted_players = []
        for player in players:
            position = player['position']
            multiplier = self.position_multipliers.get(position, 1.0)
            
            # Adjust projected points
            std_points = float(player['avg_points']) if player['avg_points'] else 0
            adj_points = std_points * multiplier if std_points else 0
            
            # Calculate value boost percentage
            boost = (multiplier - 1) * 100
            
            adjusted_players.append({
                'name': player['player_name'],
                'position': position,
                'standard_rank': int(player['avg_rank']) if player['avg_rank'] else 999,
                'standard_adp': float(player['avg_adp']) if player['avg_adp'] else 999,
                'adjusted_value': adj_points,
                'boost': boost,
                'multiplier': multiplier
            })
        
        # Sort by adjusted value
        adjusted_players.sort(key=lambda x: x['adjusted_value'], reverse=True)
        
        # Assign new rankings and save
        for new_rank, player in enumerate(adjusted_players, 1):
            rank_diff = player['standard_rank'] - new_rank
            
            # Determine notes based on adjustment
            notes = []
            if player['boost'] > 10:
                notes.append(f"Boosted by {player['position']} scoring")
            elif player['boost'] < -10:
                notes.append(f"Devalued by {player['position']} scoring")
            
            if rank_diff > 10:
                notes.append("Significant value in your league")
            elif rank_diff < -10:
                notes.append("Overvalued in your league")
            
            # Insert adjusted ranking
            self.cursor.execute("""
                INSERT INTO adjusted_rankings (
                    player_name, position,
                    standard_rank, standard_adp,
                    your_league_rank, your_league_value,
                    rank_difference, scoring_boost,
                    notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                player['name'],
                player['position'],
                player['standard_rank'],
                player['standard_adp'],
                new_rank,
                player['adjusted_value'],
                rank_diff,
                player['boost'],
                '; '.join(notes) if notes else None
            ))
        
        self.db_conn.commit()
        print(f"   ✅ Adjusted {len(adjusted_players)} player rankings")
    
    def show_top_value_picks(self, limit: int = 20):
        """Show players who are most undervalued in your league"""
        print(f"\n💎 Top {limit} Value Picks for Your League:")
        print("="*80)
        
        self.cursor.execute("""
            SELECT 
                player_name,
                position,
                standard_rank,
                your_league_rank,
                rank_difference,
                standard_adp,
                scoring_boost,
                notes
            FROM adjusted_rankings
            WHERE rank_difference > 0
            ORDER BY rank_difference DESC
            LIMIT %s
        """, (limit,))
        
        results = self.cursor.fetchall()
        
        print(f"{'Player':<25} {'Pos':<5} {'Std Rank':<10} {'Your Rank':<10} {'Value':<10} {'ADP':<8}")
        print("-"*80)
        
        for player in results:
            value_symbol = "🔥" if player['rank_difference'] > 15 else "⭐" if player['rank_difference'] > 8 else "✓"
            print(f"{player['player_name']:<25} {player['position']:<5} "
                  f"{player['standard_rank']:<10} {player['your_league_rank']:<10} "
                  f"+{player['rank_difference']:<9} {player['standard_adp']:<8.1f} {value_symbol}")
    
    def show_overvalued_players(self, limit: int = 20):
        """Show players who are overvalued in your league"""
        print(f"\n⚠️  Top {limit} Overvalued Players for Your League:")
        print("="*80)
        
        self.cursor.execute("""
            SELECT 
                player_name,
                position,
                standard_rank,
                your_league_rank,
                rank_difference,
                standard_adp,
                scoring_boost,
                notes
            FROM adjusted_rankings
            WHERE rank_difference < 0
            ORDER BY rank_difference
            LIMIT %s
        """, (limit,))
        
        results = self.cursor.fetchall()
        
        print(f"{'Player':<25} {'Pos':<5} {'Std Rank':<10} {'Your Rank':<10} {'Diff':<10} {'ADP':<8}")
        print("-"*80)
        
        for player in results:
            print(f"{player['player_name']:<25} {player['position']:<5} "
                  f"{player['standard_rank']:<10} {player['your_league_rank']:<10} "
                  f"{player['rank_difference']:<10} {player['standard_adp']:<8.1f}")
    
    def generate_draft_strategy(self):
        """Generate draft strategy recommendations"""
        print("\n📋 DRAFT STRATEGY RECOMMENDATIONS")
        print("="*60)
        
        # Analyze position values
        self.cursor.execute("""
            SELECT 
                position,
                AVG(scoring_boost) as avg_boost,
                COUNT(*) as player_count
            FROM adjusted_rankings
            WHERE your_league_rank <= 100
            GROUP BY position
            ORDER BY avg_boost DESC
        """)
        
        position_values = self.cursor.fetchall()
        
        print("\n🎯 Position Priority (based on your scoring):")
        for i, pos in enumerate(position_values, 1):
            boost = pos['avg_boost']
            priority = "HIGH" if boost > 10 else "MEDIUM" if boost > 0 else "LOW"
            print(f"   {i}. {pos['position']}: {priority} ({boost:+.1f}% value)")
        
        # Find sweet spots by round
        print("\n🎲 Value Targets by Round:")
        
        for round_num in range(1, 11):
            adp_start = (round_num - 1) * 14 + 1  # Assuming 14-team league
            adp_end = round_num * 14
            
            self.cursor.execute("""
                SELECT 
                    player_name,
                    position,
                    rank_difference
                FROM adjusted_rankings
                WHERE standard_adp BETWEEN %s AND %s
                    AND rank_difference > 5
                ORDER BY rank_difference DESC
                LIMIT 3
            """, (adp_start, adp_end))
            
            targets = self.cursor.fetchall()
            
            if targets:
                print(f"\n   Round {round_num} targets:")
                for target in targets:
                    print(f"      • {target['player_name']} ({target['position']}): +{target['rank_difference']} value")
    
    def close(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.db_conn:
            self.db_conn.close()

def main():
    """Run scoring adjustments"""
    adjuster = ScoringAdjuster()
    
    try:
        print("\n" + "="*60)
        print("🏈 FANTASY FOOTBALL SCORING ADJUSTER")
        print("="*60)
        
        # Load league scoring
        adjuster.load_league_scoring()
        
        # Adjust rankings
        adjuster.adjust_rankings()
        
        # Show results
        adjuster.show_top_value_picks()
        adjuster.show_overvalued_players()
        adjuster.generate_draft_strategy()
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        adjuster.close()

if __name__ == "__main__":
    main()