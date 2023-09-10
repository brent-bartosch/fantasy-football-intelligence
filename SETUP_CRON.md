# 🕐 Setting Up Automated RSS Ingestion

## Quick Setup (macOS)

### 1. Open crontab editor:
```bash
crontab -e
```

### 2. Add this line for daily updates at 8 AM:
```cron
0 8 * * * /Users/brentbartosch/Development/fantasy_football/scripts/daily_ingest.sh
```

### Alternative schedules:

**Twice daily (8 AM and 8 PM):**
```cron
0 8,20 * * * /Users/brentbartosch/Development/fantasy_football/scripts/daily_ingest.sh
```

**Every 6 hours during draft season (August-September):**
```cron
0 */6 * 8,9 * /Users/brentbartosch/Development/fantasy_football/scripts/daily_ingest.sh
```

**Every hour during draft day:**
```cron
0 * * * * /Users/brentbartosch/Development/fantasy_football/scripts/daily_ingest.sh
```

### 3. Save and exit (in vi):
- Press `ESC`
- Type `:wq`
- Press `Enter`

## Manual Run
```bash
cd /Users/brentbartosch/Development/fantasy_football
./scripts/daily_ingest.sh
```

## Check Logs
```bash
# View today's log
tail -f logs/rss_ingestion_$(date +%Y%m%d).log

# View all logs
ls -la logs/
```

## Verify Cron is Running
```bash
# List current cron jobs
crontab -l

# Check if cron ran (system log)
grep CRON /var/log/system.log
```

## Using macOS LaunchAgent (Alternative to cron)

If you prefer using macOS's native scheduler:

### 1. Create LaunchAgent plist:
```bash
cat > ~/Library/LaunchAgents/com.fantasy.rss.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.fantasy.rss</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/brentbartosch/Development/fantasy_football/scripts/daily_ingest.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/brentbartosch/Development/fantasy_football/logs/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/brentbartosch/Development/fantasy_football/logs/launchd.error.log</string>
</dict>
</plist>
EOF
```

### 2. Load the agent:
```bash
launchctl load ~/Library/LaunchAgents/com.fantasy.rss.plist
```

### 3. Check status:
```bash
launchctl list | grep fantasy
```

### 4. Unload if needed:
```bash
launchctl unload ~/Library/LaunchAgents/com.fantasy.rss.plist
```

## 📊 Monitor Your Data

After automation is set up, monitor trends:

```sql
-- Check daily ingestion stats
SELECT 
    DATE(date_published) as date,
    source,
    COUNT(*) as articles
FROM draft_analysis
GROUP BY DATE(date_published), source
ORDER BY date DESC, source;

-- See trending players
SELECT * FROM weekly_player_momentum
WHERE week = DATE_TRUNC('week', CURRENT_DATE)
ORDER BY weekly_mentions DESC
LIMIT 20;

-- Check system health
SELECT * FROM content_freshness;
```

## 🎯 Draft Day Special

On draft day, increase frequency:

```bash
# Run every 30 minutes
*/30 * * * * /Users/brentbartosch/Development/fantasy_football/scripts/daily_ingest.sh

# And run the draft assistant
python /Users/brentbartosch/Development/fantasy_football/scripts/draft_assistant.py
```