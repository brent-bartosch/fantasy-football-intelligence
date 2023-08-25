#!/usr/bin/env python3
"""
Simplified Yahoo OAuth using oob (out-of-band) flow
This avoids the need for a local HTTPS server
"""

import os
import json
import requests
from urllib.parse import urlencode
from datetime import datetime, timedelta
from dotenv import load_dotenv
import webbrowser
import base64

load_dotenv()

class YahooAuthSimple:
    """Simplified Yahoo OAuth without local server"""
    
    def __init__(self):
        self.client_id = os.getenv('YAHOO_CLIENT_ID')
        self.client_secret = os.getenv('YAHOO_CLIENT_SECRET')
        
        if not all([self.client_id, self.client_secret]):
            raise ValueError("Missing Yahoo OAuth credentials in .env file")
        
        self.token = None
        self.load_token()
    
    def authenticate(self):
        """OAuth flow using out-of-band method"""
        
        # Step 1: Get authorization code
        auth_params = {
            'client_id': self.client_id,
            'redirect_uri': 'oob',  # Out-of-band
            'response_type': 'code',
            'scope': 'fspt-r',  # Fantasy Sports Read
            'access_type': 'offline'
        }
        
        auth_url = f"https://api.login.yahoo.com/oauth2/request_auth?{urlencode(auth_params)}"
        
        print("\n" + "="*60)
        print("Yahoo Fantasy OAuth Setup")
        print("="*60)
        print("\n1. Open this URL in your browser:")
        print(f"\n{auth_url}\n")
        
        # Try to open browser
        try:
            webbrowser.open(auth_url)
            print("(Browser should open automatically)")
        except:
            pass
        
        print("\n2. Sign in to Yahoo and authorize the app")
        print("\n3. You'll see an authorization code. Copy it.")
        print("\n" + "="*60)
        
        auth_code = input("\nPaste the authorization code here: ").strip()
        
        # Step 2: Exchange code for token
        token_data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': 'oob',
            'code': auth_code,
            'grant_type': 'authorization_code'
        }
        
        response = requests.post(
            'https://api.login.yahoo.com/oauth2/get_token',
            data=token_data
        )
        
        if response.status_code == 200:
            token = response.json()
            # Add expiry timestamp
            token['expires_at'] = (datetime.now() + timedelta(seconds=token['expires_in'])).timestamp()
            
            self.save_token(token)
            print("\n✅ Authentication successful!")
            print(f"Access token: {token['access_token'][:20]}...")
            print(f"Token expires in: {token['expires_in']} seconds")
            return token
        else:
            print(f"\n❌ Authentication failed: {response.status_code}")
            print(response.text)
            return None
    
    def save_token(self, token):
        """Save token to file"""
        os.makedirs('config', exist_ok=True)
        with open('config/yahoo_token.json', 'w') as f:
            json.dump(token, f, indent=2)
        self.token = token
    
    def load_token(self):
        """Load token from file"""
        token_file = 'config/yahoo_token.json'
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                self.token = json.load(f)
                print("✅ Loaded existing token")
                
                # Check expiry
                expires_at = datetime.fromtimestamp(self.token.get('expires_at', 0))
                if datetime.now() >= expires_at:
                    print("⚠️  Token expired, need to re-authenticate")
                    self.token = None
                    return False
                else:
                    time_left = expires_at - datetime.now()
                    print(f"Token valid for: {time_left}")
                return True
        return False
    
    def refresh_token(self):
        """Refresh expired token"""
        if not self.token or 'refresh_token' not in self.token:
            print("No refresh token available, need full authentication")
            return self.authenticate()
        
        refresh_data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': self.token['refresh_token'],
            'grant_type': 'refresh_token'
        }
        
        response = requests.post(
            'https://api.login.yahoo.com/oauth2/get_token',
            data=refresh_data
        )
        
        if response.status_code == 200:
            token = response.json()
            token['expires_at'] = (datetime.now() + timedelta(seconds=token['expires_in'])).timestamp()
            self.save_token(token)
            print("✅ Token refreshed successfully")
            return token
        else:
            print(f"❌ Token refresh failed: {response.status_code}")
            print("Need to re-authenticate")
            return self.authenticate()
    
    def get_valid_token(self):
        """Get a valid token, refreshing if needed"""
        if not self.token:
            return self.authenticate()
        
        expires_at = datetime.fromtimestamp(self.token.get('expires_at', 0))
        if datetime.now() >= expires_at - timedelta(minutes=5):
            return self.refresh_token()
        
        return self.token
    
    def test_api(self):
        """Test the API connection"""
        token = self.get_valid_token()
        if not token:
            print("❌ No valid token")
            return
        
        headers = {
            'Authorization': f"Bearer {token['access_token']}",
            'Accept': 'application/json'
        }
        
        # Get user's leagues
        url = "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games?format=json"
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            print("\n✅ API Connection Successful!")
            data = response.json()
            
            # Try to parse games
            try:
                games = data['fantasy_content']['users']['0']['user'][1]['games']
                print(f"\nFound {games['count']} fantasy games:")
                
                for key, value in games.items():
                    if key.isdigit():
                        game = value['game'][0]
                        print(f"  - {game.get('name', 'Unknown')} ({game.get('season', '')})")
            except:
                print("Response received but couldn't parse games")
                print(json.dumps(data, indent=2)[:500])
        else:
            print(f"❌ API request failed: {response.status_code}")
            print(response.text)

if __name__ == "__main__":
    auth = YahooAuthSimple()
    
    # Try to use existing token or authenticate
    if not auth.load_token():
        auth.authenticate()
    
    # Test the connection
    auth.test_api()