#!/usr/bin/env python3
"""
Manual Yahoo OAuth authentication - no local server required
Just copy/paste the authorization code
"""

import os
import json
import requests
import webbrowser
from urllib.parse import urlencode
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

class YahooManualAuth:
    """Yahoo OAuth with manual code entry"""
    
    def __init__(self):
        self.client_id = os.getenv('YAHOO_CLIENT_ID')
        self.client_secret = os.getenv('YAHOO_CLIENT_SECRET')
        self.redirect_uri = os.getenv('YAHOO_REDIRECT_URI', 'https://localhost:8000/callback')
        
        if not all([self.client_id, self.client_secret]):
            raise ValueError("Missing Yahoo OAuth credentials in .env file")
        
        print(f"✅ Loaded credentials")
        print(f"   Client ID: {self.client_id[:30]}...")
        
    def authenticate(self):
        """Manual OAuth flow"""
        
        # Build authorization URL
        auth_params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'fspt-r',  # Fantasy Sports Read
            'access_type': 'offline',  # Get refresh token
            'language': 'en-us'
        }
        
        auth_url = f"https://api.login.yahoo.com/oauth2/request_auth?{urlencode(auth_params)}"
        
        print("\n" + "="*70)
        print("🏈 YAHOO FANTASY FOOTBALL AUTHENTICATION")
        print("="*70)
        
        print("\nSTEP 1: Open this URL in your browser:")
        print("-" * 70)
        print(auth_url)
        print("-" * 70)
        
        # Try to open browser
        try:
            webbrowser.open(auth_url)
            print("\n(Browser should open automatically)")
        except:
            pass
        
        print("\nSTEP 2: Sign in to Yahoo and click 'Agree' to authorize the app")
        
        print("\nSTEP 3: After authorizing, you'll see an error page (this is normal!)")
        print("        The URL will look like:")
        print("        https://localhost:8000/callback?code=xxxxx")
        
        print("\nSTEP 4: Copy the ENTIRE URL from your browser's address bar")
        print("        (Including the https://localhost... part)")
        
        print("\n" + "="*70)
        
        # Get the URL from user
        callback_url = input("\nPaste the complete URL here and press Enter:\n> ").strip()
        
        # Extract the code
        if 'code=' in callback_url:
            code = callback_url.split('code=')[1].split('&')[0]
            print(f"\n✅ Got authorization code: {code[:10]}...")
        else:
            print("\n❌ No authorization code found in URL")
            print("   Make sure you copied the entire URL including 'code='")
            return None
        
        # Exchange code for token
        print("\n🔄 Exchanging code for access token...")
        
        token_data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'code': code,
            'grant_type': 'authorization_code'
        }
        
        response = requests.post(
            'https://api.login.yahoo.com/oauth2/get_token',
            data=token_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        if response.status_code == 200:
            token = response.json()
            
            # Add expiry timestamp
            token['expires_at'] = (datetime.now() + timedelta(seconds=token.get('expires_in', 3600))).timestamp()
            
            # Save token
            self.save_token(token)
            
            print("\n" + "="*70)
            print("🎉 SUCCESS! Authentication complete!")
            print("="*70)
            print(f"\n✅ Access token obtained (expires in {token.get('expires_in', 0)} seconds)")
            print(f"✅ Refresh token: {'Yes' if 'refresh_token' in token else 'No'}")
            print(f"✅ Token saved to: config/yahoo_token.json")
            
            return token
        else:
            print(f"\n❌ Failed to get token: {response.status_code}")
            print(f"Response: {response.text}")
            
            if response.status_code == 401:
                print("\nPossible issues:")
                print("- The authorization code may have expired (try again)")
                print("- Client ID/Secret may be incorrect")
                print("- Redirect URI doesn't match what's registered")
            
            return None
    
    def save_token(self, token):
        """Save token to file"""
        os.makedirs('config', exist_ok=True)
        token_file = 'config/yahoo_token.json'
        
        with open(token_file, 'w') as f:
            json.dump(token, f, indent=2)
        
        print(f"💾 Token saved to {token_file}")
    
    def load_token(self):
        """Load existing token"""
        token_file = 'config/yahoo_token.json'
        
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                token = json.load(f)
            
            # Check if expired
            expires_at = datetime.fromtimestamp(token.get('expires_at', 0))
            
            if datetime.now() < expires_at:
                time_left = expires_at - datetime.now()
                print(f"✅ Valid token loaded (expires in {time_left})")
                return token
            else:
                print("⚠️  Token expired")
                
                # Try to refresh
                if 'refresh_token' in token:
                    return self.refresh_token(token['refresh_token'])
        
        return None
    
    def refresh_token(self, refresh_token):
        """Refresh an expired token"""
        print("🔄 Refreshing token...")
        
        refresh_data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token'
        }
        
        response = requests.post(
            'https://api.login.yahoo.com/oauth2/get_token',
            data=refresh_data
        )
        
        if response.status_code == 200:
            token = response.json()
            token['expires_at'] = (datetime.now() + timedelta(seconds=token.get('expires_in', 3600))).timestamp()
            self.save_token(token)
            print("✅ Token refreshed successfully")
            return token
        else:
            print(f"❌ Failed to refresh token: {response.status_code}")
            return None
    
    def test_api(self):
        """Test the API connection"""
        
        # Try to load existing token
        token = self.load_token()
        
        if not token:
            print("No valid token found. Authenticating...")
            token = self.authenticate()
        
        if not token:
            print("❌ No valid token available")
            return False
        
        print("\n🧪 Testing Yahoo Fantasy API connection...")
        
        headers = {
            'Authorization': f"Bearer {token['access_token']}",
            'Accept': 'application/json'
        }
        
        # Get user's fantasy games
        url = "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games?format=json"
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            print("✅ API connection successful!\n")
            
            try:
                data = response.json()
                users = data.get('fantasy_content', {}).get('users', {})
                
                if '0' in users:
                    user_data = users['0']['user']
                    
                    # Find games in user data
                    for item in user_data:
                        if isinstance(item, dict) and 'games' in item:
                            games = item['games']
                            count = games.get('count', 0)
                            
                            print(f"📊 Found {count} fantasy games:")
                            
                            for key, value in games.items():
                                if key.isdigit() and isinstance(value, dict):
                                    game_info = value.get('game', [{}])[0]
                                    
                                    name = game_info.get('name', 'Unknown')
                                    season = game_info.get('season', '')
                                    code = game_info.get('code', '')
                                    game_id = game_info.get('game_id', '')
                                    
                                    print(f"   • {name} {season}")
                                    print(f"     Game ID: {game_id}, Code: {code}")
                            
                            break
                            
            except Exception as e:
                print(f"Response received but couldn't parse: {e}")
                print(f"Raw response: {response.text[:500]}")
            
            return True
        else:
            print(f"❌ API request failed: {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False

def main():
    print("\n🏈 Yahoo Fantasy Football Manual Authentication\n")
    
    auth = YahooManualAuth()
    
    # Test the API (will authenticate if needed)
    success = auth.test_api()
    
    if success:
        print("\n✨ Setup complete! Your token is saved and the API is working.")
        print("   You can now run data import scripts.")
    else:
        print("\n❌ Setup failed. Please check your credentials and try again.")

if __name__ == "__main__":
    main()