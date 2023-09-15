#!/usr/bin/env python3
"""
LMU Fantasy Football Draft Assistant
Combines expert rankings, historical data, and scoring adjustments
to provide real-time draft recommendations.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from typing import List, Dict, Set
from dotenv import load_dotenv

load_dotenv()

class DraftAssistant:
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
        
        self.drafted_players = set()
        self.my_team = []
        self.current_round = 1
        self.draft_position = 1
        self.num_teams = 14
    
    def initialize_draft(self):
        """Set up draft parameters"""
        print("\n🏈 LMU FANTASY FOOTBALL DRAFT ASSISTANT")
        print("="*60)
        
        # Get draft position
        while True:
            try:
                self.draft_position = int(input("\nWhat's your draft position? (1-14): "))
                if 1 <= self.draft_position <= 14:
                    break
                print("Please enter a number between 1 and 14")
            except ValueError:
                print("Please enter a valid number")
        
        print(f"\n✅ Draft position: #{self.draft_position}")
        print(f"📊 League: 14 teams, PPR with bonuses")
        print(f"🎯 Strategy: Value-based drafting with LMU scoring adjustments\n")
    
    def get_best_available(self, position: str = None, limit: int = 10) -> List[Dict]:
        """Get best available players"""
        
        # Build the drafted players filter
        drafted_filter = ""
        if self.drafted_players:
            drafted_names = "', '".join(self.drafted_players)
            drafted_filter = f"AND ar.player_name NOT IN ('{drafted_names}')"
        
        position_filter = f"AND ar.position = '{position}'" if position else ""
        
        query = f"""
            SELECT 
                ar.player_name,
                ar.position,
                ar.your_league_rank as adj_rank,
                ar.standard_rank as std_rank,
                ar.rank_difference as value,
                ar.standard_adp as adp,
                ar.scoring_boost,
                ar.notes
            FROM adjusted_rankings ar
            WHERE 1=1
                {drafted_filter}
                {position_filter}
            ORDER BY ar.your_league_rank
            LIMIT %s
        """
        
        self.cursor.execute(query, (limit,))
        return self.cursor.fetchall()
    
    def get_pick_recommendation(self) -> Dict:
        """Get recommendation for current pick"""
        
        # Calculate current overall pick
        if self.current_round % 2 == 1:  # Odd round (snake left)
            current_pick = (self.current_round - 1) * self.num_teams + self.draft_position
        else:  # Even round (snake right)
            current_pick = self.current_round * self.num_teams - self.draft_position + 1
        
        print(f"\n📍 Round {self.current_round}, Pick {current_pick}")
        print("-" * 40)
        
        # Get best available by position
        recommendations = []
        
        for position in ['QB', 'RB', 'WR', 'TE']:
            best = self.get_best_available(position, 3)
            if best:
                top_player = best[0]
                
                # Calculate reach/value
                expected_pick = float(top_player['adp']) if top_player['adp'] else 999
                reach = current_pick - expected_pick
                
                # Score the pick
                value_score = top_player['value'] if top_player['value'] else 0
                adp_score = -abs(reach) / 10  # Penalize reaches
                position_need = self._calculate_position_need(position)
                
                total_score = value_score + adp_score + position_need
                
                recommendations.append({
                    'player': top_player,
                    'reach': reach,
                    'total_score': total_score,
                    'position_need': position_need
                })
        
        # Sort by total score
        recommendations.sort(key=lambda x: x['total_score'], reverse=True)
        
        return recommendations[0] if recommendations else None
    
    def _calculate_position_need(self, position: str) -> float:
        """Calculate need for a position based on roster construction"""
        
        # Count current roster
        position_counts = {'QB': 0, 'RB': 0, 'WR': 0, 'TE': 0}
        for player in self.my_team:
            if player['position'] in position_counts:
                position_counts[player['position']] += 1
        
        # Target roster construction (adjust based on league)
        targets = {
            'QB': 2,   # 1 starter + 1 backup
            'RB': 5,   # 2 starters + 2 flex + 1 bench
            'WR': 5,   # 2 starters + 2 flex + 1 bench
            'TE': 2    # 1 starter + 1 backup
        }
        
        # Calculate need score
        current = position_counts[position]
        target = targets[position]
        
        if current >= target:
            return -10  # Penalize if we have enough
        elif current == 0 and position in ['RB', 'WR']:
            return 15  # Bonus for first RB/WR
        elif current == 0 and position == 'QB' and self.current_round > 5:
            return 10  # Need a QB eventually
        else:
            return 5 * (target - current) / target
    
    def show_best_available(self):
        """Display best available players"""
        print("\n🎯 BEST AVAILABLE PLAYERS")
        print("="*60)
        
        # Overall best
        print("\n📊 Top 10 Overall:")
        best = self.get_best_available(None, 10)
        
        print(f"{'Rank':<6} {'Player':<25} {'Pos':<5} {'ADP':<8} {'Value'}")
        print("-"*55)
        
        for player in best:
            value_symbol = "🔥" if player['value'] > 10 else "⭐" if player['value'] > 5 else ""
            adp_str = f"{player['adp']:.1f}" if player['adp'] else "N/A"
            value_str = f"+{player['value']}" if player['value'] > 0 else str(player['value'])
            
            print(f"{player['adj_rank']:<6} {player['player_name']:<25} "
                  f"{player['position']:<5} {adp_str:<8} {value_str:<6} {value_symbol}")
    
    def mark_player_drafted(self, player_name: str, by_me: bool = False):
        """Mark a player as drafted"""
        
        # Get player details
        self.cursor.execute("""
            SELECT player_name, position
            FROM adjusted_rankings
            WHERE LOWER(player_name) LIKE LOWER(%s)
            LIMIT 1
        """, (f"%{player_name}%",))
        
        player = self.cursor.fetchone()
        
        if player:
            self.drafted_players.add(player['player_name'])
            
            if by_me:
                self.my_team.append(player)
                print(f"✅ Added {player['player_name']} ({player['position']}) to your team")
            else:
                print(f"❌ {player['player_name']} ({player['position']}) off the board")
            
            return True
        else:
            print(f"⚠️  Player '{player_name}' not found")
            return False
    
    def show_my_team(self):
        """Display current roster"""
        print("\n👥 YOUR TEAM")
        print("="*40)
        
        if not self.my_team:
            print("No players drafted yet")
            return
        
        # Group by position
        by_position = {}
        for player in self.my_team:
            pos = player['position']
            if pos not in by_position:
                by_position[pos] = []
            by_position[pos].append(player['player_name'])
        
        for position in ['QB', 'RB', 'WR', 'TE']:
            if position in by_position:
                print(f"\n{position}:")
                for name in by_position[position]:
                    print(f"  • {name}")
    
    def analyze_historical_picks(self):
        """Show what players were typically drafted at this position historically"""
        
        if self.current_round % 2 == 1:
            current_pick = (self.current_round - 1) * self.num_teams + self.draft_position
        else:
            current_pick = self.current_round * self.num_teams - self.draft_position + 1
        
        print(f"\n📚 HISTORICAL ANALYSIS FOR PICK #{current_pick}")
        print("="*50)
        
        # Get historical picks in this range
        pick_range = 3
        
        self.cursor.execute("""
            SELECT 
                p.player_name,
                dp.overall_pick,
                l.season_year,
                COUNT(*) OVER (PARTITION BY p.player_name) as times_drafted
            FROM draft_picks dp
            JOIN players p ON dp.player_id = p.player_id
            JOIN leagues l ON dp.league_id = l.league_id
            WHERE l.league_name LIKE '%LMU%'
                AND dp.overall_pick BETWEEN %s AND %s
            ORDER BY l.season_year DESC, dp.overall_pick
            LIMIT 20
        """, (current_pick - pick_range, current_pick + pick_range))
        
        results = self.cursor.fetchall()
        
        if results:
            print(f"\nPlayers drafted at picks {current_pick-pick_range} to {current_pick+pick_range}:")
            print(f"{'Year':<6} {'Pick':<6} {'Player'}")
            print("-"*40)
            
            for result in results:
                print(f"{result['season_year']:<6} {result['overall_pick']:<6} {result['player_name']}")
    
    def run_draft(self):
        """Main draft loop"""
        self.initialize_draft()
        
        print("\n📝 DRAFT COMMANDS:")
        print("  'best' - Show best available")
        print("  'rec' - Get pick recommendation")
        print("  'draft [player]' - Draft a player (you)")
        print("  'taken [player]' - Mark player as drafted (other)")
        print("  'team' - Show your team")
        print("  'history' - Show historical picks")
        print("  'next' - Move to next round")
        print("  'quit' - Exit draft assistant")
        
        while True:
            print(f"\n[Round {self.current_round}] > ", end="")
            command = input().strip().lower()
            
            if command == 'quit':
                break
            
            elif command == 'best':
                self.show_best_available()
            
            elif command == 'rec':
                rec = self.get_pick_recommendation()
                if rec:
                    player = rec['player']
                    reach = rec['reach']
                    
                    print(f"\n🎯 RECOMMENDATION: {player['player_name']} ({player['position']})")
                    print(f"   Adjusted Rank: #{player['adj_rank']}")
                    print(f"   ADP: {player['adp']:.1f}" if player['adp'] else "   ADP: N/A")
                    
                    if reach < -10:
                        print(f"   ⚠️  Reach by {-reach:.0f} picks")
                    elif reach > 10:
                        print(f"   💎 Value! Expected {reach:.0f} picks later")
                    else:
                        print(f"   ✅ Good value at current position")
                    
                    if player['notes']:
                        print(f"   📝 {player['notes']}")
            
            elif command.startswith('draft '):
                player_name = command[6:]
                if self.mark_player_drafted(player_name, by_me=True):
                    # Auto-advance if it's your pick
                    if self.current_round % 2 == 1:
                        if self.draft_position == 14:
                            self.current_round += 1
                    else:
                        if self.draft_position == 1:
                            self.current_round += 1
            
            elif command.startswith('taken '):
                player_name = command[6:]
                self.mark_player_drafted(player_name, by_me=False)
            
            elif command == 'team':
                self.show_my_team()
            
            elif command == 'history':
                self.analyze_historical_picks()
            
            elif command == 'next':
                self.current_round += 1
                print(f"➡️  Moving to Round {self.current_round}")
            
            else:
                print("Unknown command. Type 'best', 'rec', 'draft [player]', 'taken [player]', 'team', 'history', 'next', or 'quit'")
    
    def close(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.db_conn:
            self.db_conn.close()

def main():
    """Run the draft assistant"""
    assistant = DraftAssistant()
    
    try:
        assistant.run_draft()
        
        print("\n" + "="*60)
        print("📊 DRAFT COMPLETE!")
        print("="*60)
        
        assistant.show_my_team()
        
    except KeyboardInterrupt:
        print("\n\n👋 Draft assistant closed")
    
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        assistant.close()

if __name__ == "__main__":
    main()