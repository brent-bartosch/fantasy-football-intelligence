# Yahoo Developer App Setup Guide

## Filling Out the Create Application Form

### Application Name
✅ **"Brents FF tracker"** - This looks good!

### Description
✅ **"Grabbing historical information from past drafts."** - Perfect description

### Homepage URL
⚠️ **Required field** - For a local development app, use one of these options:
- `http://localhost:8000` (recommended)
- `http://127.0.0.1:8000`
- Your personal website if you have one

### Redirect URI(s)
⚠️ **Required field** - This is critical for OAuth flow. Add:
```
http://localhost:8000/callback
```
You can add multiple URIs (one per line) if needed:
```
http://localhost:8000/callback
http://127.0.0.1:8000/callback
```

### OAuth Client Type
✅ **Confidential Client** - Correct choice for a server-side application

### API Permissions
✅ **Fantasy Sports - Read** - This is all you need for analytics

### Complete the Form
After filling in the Homepage URL and Redirect URI(s), click **Create App**

## After Creating the App

You'll receive:
1. **Client ID** (Consumer Key)
2. **Client Secret** (Consumer Secret)

Save these immediately!

## Setting Up OAuth in Your Project

### 1. Create Environment File
Create `.env` file in your project root with your credentials:
```bash
# Yahoo OAuth Credentials
YAHOO_CLIENT_ID=your_client_id_here
YAHOO_CLIENT_SECRET=your_client_secret_here
YAHOO_REDIRECT_URI=http://localhost:8000/callback

# Optional: for token storage
YAHOO_ACCESS_TOKEN=
YAHOO_REFRESH_TOKEN=
YAHOO_TOKEN_EXPIRY=
```

### 2. Install Required Python Packages
```bash
pip install requests requests-oauthlib python-dotenv
```

### 3. OAuth Flow Implementation
Create `scripts/yahoo_auth.py`:

```python
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
        
        print(f"Opening browser for authorization...")
        print(f"If browser doesn't open, visit: {authorization_url}")
        webbrowser.open(authorization_url)
        
        # Get the callback URL from user
        print("\nAfter authorizing, you'll be redirected to a URL starting with:")
        print(f"{self.redirect_uri}?code=...")
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
```

### 4. Simple HTTP Server for Callback
Create `scripts/oauth_callback_server.py`:

```python
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
import time

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle OAuth callback"""
        query_components = parse_qs(urlparse(self.path).query)
        
        if 'code' in query_components:
            self.server.auth_code = query_components['code'][0]
            
            # Send success response
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            success_html = """
            <html>
            <body>
                <h1>✅ Authorization Successful!</h1>
                <p>You can close this window and return to your terminal.</p>
                <script>window.close();</script>
            </body>
            </html>
            """
            self.wfile.write(success_html.encode())
        else:
            self.send_response(400)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress server logs

def start_callback_server(port=8000):
    """Start temporary server to handle OAuth callback"""
    server = HTTPServer(('localhost', port), OAuthCallbackHandler)
    server.auth_code = None
    
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    
    print(f"Callback server running on http://localhost:{port}")
    
    # Wait for auth code (timeout after 2 minutes)
    timeout = 120
    start_time = time.time()
    
    while server.auth_code is None and (time.time() - start_time) < timeout:
        time.sleep(1)
    
    server.shutdown()
    return server.auth_code
```

### 5. Test API Connection
Create `scripts/test_yahoo_api.py`:

```python
import requests
from yahoo_auth import YahooFantasyAuth

def test_api_connection():
    """Test Yahoo Fantasy API connection"""
    auth = YahooFantasyAuth()
    headers = auth.get_headers()
    
    # Test endpoint - get user's games
    url = "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games"
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        print("✅ API connection successful!")
        print(f"Response: {response.text[:500]}...")
    else:
        print(f"❌ API error: {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    test_api_connection()
```

## Quick Start Steps

1. **Complete the Yahoo form** with:
   - Homepage URL: `http://localhost:8000`
   - Redirect URI: `http://localhost:8000/callback`

2. **Save your credentials** to `.env` file

3. **Run authentication**:
   ```bash
   cd /Users/brentbartosch/Development/fantasy_football
   python scripts/yahoo_auth.py
   ```

4. **Test the connection**:
   ```bash
   python scripts/test_yahoo_api.py
   ```

## Troubleshooting

### Common Issues:

1. **"Invalid redirect_uri"**
   - Make sure the redirect URI in your code EXACTLY matches what you registered
   - Include the protocol (http://)
   - Don't add trailing slashes

2. **"Invalid client"**
   - Double-check Client ID and Secret
   - Ensure no extra spaces in .env file

3. **"Insufficient scope"**
   - Make sure you selected "Fantasy Sports - Read" permission

4. **Token expires**
   - Tokens last 1 hour
   - Refresh tokens last up to 1 year
   - The auth class handles refresh automatically

## Security Notes

- **Never commit** `.env` file or `yahoo_token.json` to git
- Add to `.gitignore`:
  ```
  .env
  config/yahoo_token.json
  ```
- Keep your Client Secret secure
- Tokens are sensitive - treat like passwords