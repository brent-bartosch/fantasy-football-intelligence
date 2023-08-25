#!/usr/bin/env python3
"""
All-in-one Yahoo OAuth setup for local development
Handles HTTPS certificate creation and OAuth flow
"""

import os
import sys
import json
import ssl
import time
import threading
import webbrowser
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta

try:
    from requests_oauthlib import OAuth2Session
    from dotenv import load_dotenv
except ImportError:
    print("Installing required packages...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", 
                          "requests", "requests-oauthlib", "python-dotenv"])
    from requests_oauthlib import OAuth2Session
    from dotenv import load_dotenv

load_dotenv()

# Global variable to store the auth code
AUTH_CODE = None
SERVER_READY = threading.Event()

class OAuthHandler(BaseHTTPRequestHandler):
    """Handle OAuth callback"""
    
    def do_GET(self):
        global AUTH_CODE
        query = parse_qs(urlparse(self.path).query)
        
        if 'code' in query:
            AUTH_CODE = query['code'][0]
            
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            html = """
            <html>
            <head>
                <title>Success!</title>
                <style>
                    body { 
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        margin: 0;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    }
                    .container {
                        text-align: center;
                        padding: 2rem;
                        background: white;
                        border-radius: 10px;
                        box-shadow: 0 10px 40px rgba(0,0,0,0.1);
                    }
                    h1 { color: #10b981; margin: 0 0 1rem 0; }
                    p { color: #6b7280; }
                    .code { 
                        background: #f3f4f6; 
                        padding: 0.5rem 1rem; 
                        border-radius: 5px;
                        font-family: monospace;
                        color: #4b5563;
                        margin-top: 1rem;
                    }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>✅ Authorization Successful!</h1>
                    <p>You can close this window and return to your terminal.</p>
                    <div class="code">Code received: ***hidden***</div>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode())
        else:
            self.send_response(400)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress logs

def create_certificate():
    """Create self-signed certificate for localhost"""
    cert_dir = 'config'
    os.makedirs(cert_dir, exist_ok=True)
    
    cert_file = os.path.join(cert_dir, 'localhost.crt')
    key_file = os.path.join(cert_dir, 'localhost.key')
    
    if not os.path.exists(cert_file) or not os.path.exists(key_file):
        print("📜 Creating self-signed certificate for localhost...")
        
        try:
            # Try to use openssl
            result = subprocess.run([
                'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
                '-keyout', key_file, '-out', cert_file,
                '-days', '365', '-nodes',
                '-subj', '/CN=localhost'
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print("✅ Certificate created successfully")
            else:
                raise Exception(result.stderr)
                
        except Exception as e:
            print(f"⚠️  Could not create certificate with openssl: {e}")
            print("Creating fallback certificate...")
            
            # Fallback: create a basic self-signed cert using Python
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
            import datetime
            
            # Generate key
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            
            # Create certificate
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
            ])
            
            cert = x509.CertificateBuilder().subject_name(
                subject
            ).issuer_name(
                issuer
            ).public_key(
                key.public_key()
            ).serial_number(
                x509.random_serial_number()
            ).not_valid_before(
                datetime.datetime.utcnow()
            ).not_valid_after(
                datetime.datetime.utcnow() + datetime.timedelta(days=365)
            ).sign(key, hashes.SHA256())
            
            # Write key
            with open(key_file, 'wb') as f:
                f.write(key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                ))
            
            # Write certificate
            with open(cert_file, 'wb') as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            
            print("✅ Fallback certificate created")
    
    return cert_file, key_file

def start_https_server():
    """Start HTTPS server in background thread"""
    global SERVER_READY
    
    cert_file, key_file = create_certificate()
    
    httpd = HTTPServer(('localhost', 8000), OAuthHandler)
    
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    
    def serve():
        SERVER_READY.set()
        httpd.handle_request()  # Handle one request then stop
    
    thread = threading.Thread(target=serve)
    thread.daemon = True
    thread.start()
    
    # Wait for server to be ready
    SERVER_READY.wait(timeout=5)
    print("🔒 HTTPS server ready on https://localhost:8000")
    
    return thread

def setup_yahoo_oauth():
    """Complete Yahoo OAuth setup"""
    
    print("\n" + "="*60)
    print("🏈 Yahoo Fantasy Football OAuth Setup")
    print("="*60)
    
    # Check for credentials
    client_id = os.getenv('YAHOO_CLIENT_ID')
    client_secret = os.getenv('YAHOO_CLIENT_SECRET')
    
    if not client_id or client_id == 'PASTE_YOUR_CLIENT_ID_HERE':
        print("\n❌ Yahoo credentials not found in .env file")
        print("\n1. First, create a Yahoo app at:")
        print("   https://developer.yahoo.com/apps/")
        print("\n2. Use these settings:")
        print("   - Homepage URL: https://localhost:8000")
        print("   - Redirect URI: https://localhost:8000/callback")
        print("\n3. Then add your credentials to the .env file:")
        print("   YAHOO_CLIENT_ID=your_client_id")
        print("   YAHOO_CLIENT_SECRET=your_client_secret")
        return None
    
    print("\n✅ Credentials found")
    print(f"Client ID: {client_id[:20]}...")
    
    # Start HTTPS server
    print("\n🚀 Starting local HTTPS server...")
    server_thread = start_https_server()
    
    # Create OAuth session
    redirect_uri = 'https://localhost:8000/callback'
    yahoo = OAuth2Session(
        client_id,
        redirect_uri=redirect_uri,
        scope=['fspt-r']
    )
    
    # Get authorization URL
    auth_url, state = yahoo.authorization_url(
        'https://api.login.yahoo.com/oauth2/request_auth',
        access_type='offline'
    )
    
    print("\n📱 Opening browser for authorization...")
    print("If the browser doesn't open, visit:")
    print(f"\n{auth_url}\n")
    
    webbrowser.open(auth_url)
    
    print("⏳ Waiting for authorization...")
    print("\n⚠️  IMPORTANT: Your browser will show a certificate warning!")
    print("   This is normal. Click 'Advanced' → 'Proceed to localhost'")
    
    # Wait for auth code
    timeout = 120
    start_time = time.time()
    
    while AUTH_CODE is None and (time.time() - start_time) < timeout:
        time.sleep(1)
    
    if AUTH_CODE is None:
        print("\n❌ Timeout waiting for authorization")
        print("\nManual fallback: Copy the entire URL from your browser")
        callback_url = input("Paste URL here: ").strip()
        AUTH_CODE = parse_qs(urlparse(callback_url).query).get('code', [None])[0]
    
    if not AUTH_CODE:
        print("❌ No authorization code received")
        return None
    
    print("\n✅ Authorization code received!")
    
    # Exchange code for token
    print("🔄 Exchanging code for access token...")
    
    yahoo = OAuth2Session(client_id, redirect_uri=redirect_uri, state=state)
    
    try:
        token = yahoo.fetch_token(
            'https://api.login.yahoo.com/oauth2/get_token',
            code=AUTH_CODE,
            client_secret=client_secret
        )
        
        # Add expiry timestamp
        token['expires_at'] = (datetime.now() + timedelta(seconds=token['expires_in'])).timestamp()
        
        # Save token
        os.makedirs('config', exist_ok=True)
        with open('config/yahoo_token.json', 'w') as f:
            json.dump(token, f, indent=2)
        
        print("\n🎉 Success! Token saved to config/yahoo_token.json")
        print(f"   Access token: {token['access_token'][:30]}...")
        print(f"   Expires in: {token['expires_in']} seconds")
        print(f"   Refresh token: {'Yes' if 'refresh_token' in token else 'No'}")
        
        return token
        
    except Exception as e:
        print(f"\n❌ Failed to get token: {e}")
        return None

def test_connection(token):
    """Test the Yahoo API connection"""
    import requests
    
    print("\n🧪 Testing API connection...")
    
    headers = {
        'Authorization': f"Bearer {token['access_token']}",
        'Accept': 'application/json'
    }
    
    url = "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games?format=json"
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        print("✅ API connection successful!")
        
        try:
            data = response.json()
            games = data['fantasy_content']['users']['0']['user'][1]['games']
            
            print(f"\n📊 Found {games['count']} fantasy games in your account:")
            
            for key, value in games.items():
                if key.isdigit():
                    game = value['game'][0]
                    name = game.get('name', 'Unknown')
                    season = game.get('season', '')
                    code = game.get('code', '')
                    print(f"   - {name} {season} ({code})")
                    
        except Exception as e:
            print(f"Could not parse games: {e}")
    else:
        print(f"❌ API test failed: {response.status_code}")
        print(response.text[:200])

if __name__ == "__main__":
    token = setup_yahoo_oauth()
    
    if token:
        test_connection(token)
        print("\n✨ Setup complete! You can now run import scripts.")
    else:
        print("\n❌ Setup failed. Please check the instructions above.")