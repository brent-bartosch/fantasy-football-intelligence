# Local-Only Yahoo OAuth Setup

## Step 1: Register Your Yahoo App

Go to https://developer.yahoo.com/apps/ and create a new app with:

- **Application Name**: Brents FF tracker
- **Description**: Grabbing historical information from past drafts
- **Homepage URL**: `https://localhost:8000`
- **Redirect URI(s)**: `https://localhost:8000/callback`
- **OAuth Client Type**: Confidential Client
- **API Permissions**: Fantasy Sports - Read

Click "Create App" and save your Client ID and Client Secret.

## Step 2: Update Your .env File

Edit `/Users/brentbartosch/Development/fantasy_football/.env` and replace:
- `PASTE_YOUR_CLIENT_ID_HERE` with your actual Client ID
- `PASTE_YOUR_CLIENT_SECRET_HERE` with your actual Client Secret

Keep the redirect URI as: `https://localhost:8000/callback`

## Step 3: Install Required Packages

```bash
cd /Users/brentbartosch/Development/fantasy_football
pip install requests requests-oauthlib python-dotenv
```

## Step 4: Run Authentication

We'll use a self-signed certificate for HTTPS (completely local, no external services):

```bash
python scripts/oauth_https_server.py
```

This will:
1. Create a self-signed SSL certificate for localhost (stored in `config/`)
2. Start an HTTPS server on port 8000
3. Wait for the OAuth callback

In another terminal, run:
```bash
python scripts/yahoo_auth.py
```

## What Happens:

1. Browser opens to Yahoo's auth page
2. You authorize the app
3. Yahoo redirects to `https://localhost:8000/callback`
4. **Your browser will show a certificate warning** - this is normal!
   - Click "Advanced" or "Show Details"
   - Click "Proceed to localhost" or "Accept the Risk and Continue"
5. The local server captures the auth code
6. Token is saved to `config/yahoo_token.json`

## Certificate Warning is Normal!

Since we're using a self-signed certificate for localhost, browsers will warn you. This is expected and safe because:
- The certificate is only for your local machine
- No data leaves your computer
- It's just to satisfy Yahoo's HTTPS requirement

## Alternative: Manual Code Entry

If the HTTPS server gives you trouble, you can use the fallback:

1. The script will open Yahoo auth in your browser
2. After authorizing, copy the ENTIRE URL from your browser's address bar
3. Paste it into the terminal when prompted
4. The script will extract the code and complete authentication

## Files Created Locally:

All files stay on your machine:
- `config/localhost.crt` - Self-signed certificate
- `config/localhost.key` - Certificate private key  
- `config/yahoo_token.json` - Your OAuth tokens
- `config/auth_code.txt` - Temporary auth code (deleted after use)

## Security Notes:

- Everything stays local - no external servers
- The self-signed cert is only valid for localhost
- Tokens are stored locally in `config/`
- Add `config/` to `.gitignore` (already done)

## Troubleshooting:

**"Address already in use" error:**
```bash
# Kill any process using port 8000
lsof -i :8000
kill -9 <PID>
```

**Certificate errors:**
- Make sure you're accessing `https://localhost:8000` (with HTTPS)
- Accept/trust the certificate warning in your browser

**"Invalid redirect_uri" from Yahoo:**
- Ensure you registered exactly: `https://localhost:8000/callback`
- No trailing slashes
- Must be HTTPS, not HTTP