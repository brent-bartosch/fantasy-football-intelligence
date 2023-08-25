import os
import json
from datetime import datetime, timedelta
from requests_oauthlib import OAuth2Session
from dotenv import load_dotenv
import webbrowser

load_dotenv()

class YahooFantasyAuth:
    """Handle Yahoo Fantasy Sports OAuth 2.0 authentication"""
    
    AUTHORIZE_URL = 'https://api.login.yahoo.com/oauth2/request_auth'
    TOKEN_URL = 'https://api.login.yahoo.com/oauth2/get_token'
    
    def __init__(self):
        self.client_id = os.getenv('YAHOO_CLIENT_ID')
        self.client_secret = os.getenv('YAHOO_CLIENT_SECRET')
        self.redirect_uri = os.getenv('YAHOO_REDIRECT_URI')
        
        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            raise ValueError("Missing Yahoo OAuth credentials in .env file")
        
        self.token = None
        self.load_token()
    
    def authenticate(self):
        """Initiate OAuth flow"""
        import subprocess
        import time
        
        yahoo = OAuth2Session(
            self.client_id,
            redirect_uri=self.redirect_uri,
            scope=['fspt-r']  # Fantasy Sports Read scope
        )
        
        # Get authorization URL
        authorization_url, state = yahoo.authorization_url(
            self.AUTHORIZE_URL,
            access_type='offline'  # Request refresh token
        )
        
        # Start HTTPS callback server in background
        print("Starting HTTPS callback server...")
        server_process = subprocess.Popen(
            ['python', 'scripts/oauth_https_server.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(2)  # Give server time to start
        
        print(f"\nOpening browser for authorization...")
        print(f"If browser doesn't open, visit:\n{authorization_url}\n")
        webbrowser.open(authorization_url)
        
        print("Waiting for authorization (check your browser)...")
        print("Note: You may see a certificate warning - click 'Advanced' and proceed to localhost")
        
        # Wait for the server to handle the callback
        server_process.wait(timeout=120)  # 2 minute timeout
        
        # Read the auth code from file
        try:
            with open('config/auth_code.txt', 'r') as f:
                auth_code = f.read().strip()
            
            # Build callback URL
            callback_url = f"{self.redirect_uri}?code={auth_code}&state={state}"
            
            # Clean up temp file
            os.remove('config/auth_code.txt')
            
        except (FileNotFoundError, TimeoutError):
            print("\n❌ Timeout or no code received.")
            print("Manual mode: After authorizing, copy the full URL from your browser")
            callback_url = input("Paste the full redirect URL here: ").strip()
        
        # Exchange code for token
        yahoo = OAuth2Session(
            self.client_id,
            redirect_uri=self.redirect_uri,
            state=state
        )
        
        token = yahoo.fetch_token(
            self.TOKEN_URL,
            authorization_response=callback_url,
            client_secret=self.client_secret
        )
        
        self.save_token(token)
        print("✅ Authentication successful! Token saved.")
        return token
    
    def save_token(self, token):
        """Save token to file"""
        token_file = 'config/yahoo_token.json'
        os.makedirs('config', exist_ok=True)
        
        with open(token_file, 'w') as f:
            json.dump(token, f, indent=2)
        
        self.token = token
    
    def load_token(self):
        """Load token from file"""
        token_file = 'config/yahoo_token.json'
        
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                self.token = json.load(f)
                print("✅ Loaded existing token")
                return True
        return False
    
    def refresh_token_if_needed(self):
        """Refresh token if expired"""
        if not self.token:
            return self.authenticate()
        
        # Check if token is expired
        expires_at = datetime.fromtimestamp(self.token.get('expires_at', 0))
        if datetime.now() >= expires_at - timedelta(minutes=5):
            print("Token expired or expiring soon, refreshing...")
            
            yahoo = OAuth2Session(self.client_id)
            new_token = yahoo.refresh_token(
                self.TOKEN_URL,
                refresh_token=self.token['refresh_token'],
                client_id=self.client_id,
                client_secret=self.client_secret
            )
            
            self.save_token(new_token)
            print("✅ Token refreshed")
        
        return self.token
    
    def get_headers(self):
        """Get headers for API requests"""
        self.refresh_token_if_needed()
        return {
            'Authorization': f"Bearer {self.token['access_token']}",
            'Accept': 'application/json'
        }

if __name__ == "__main__":
    # Test authentication
    auth = YahooFantasyAuth()
    auth.authenticate()