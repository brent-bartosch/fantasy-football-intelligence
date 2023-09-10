#!/bin/bash
# Daily RSS Ingestion Script for Fantasy Football Analysis
# Run this via cron to keep your draft analysis current

# Set up environment
export PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:$PATH"
cd /Users/brentbartosch/Development/fantasy_football

# Log file
LOG_FILE="logs/rss_ingestion_$(date +%Y%m%d).log"
mkdir -p logs

echo "========================================" >> $LOG_FILE
echo "Starting RSS Ingestion: $(date)" >> $LOG_FILE
echo "========================================" >> $LOG_FILE

# Run the RSS ingester
python scripts/rss_ingester.py >> $LOG_FILE 2>&1

# Check if successful
if [ $? -eq 0 ]; then
    echo "✅ RSS Ingestion completed successfully" >> $LOG_FILE
    
    # Optional: Run scoring adjustments after new data
    echo "Running scoring adjustments..." >> $LOG_FILE
    python scripts/scoring_adjuster.py >> $LOG_FILE 2>&1
else
    echo "❌ RSS Ingestion failed" >> $LOG_FILE
fi

echo "Ingestion finished: $(date)" >> $LOG_FILE
echo "" >> $LOG_FILE

# Optional: Send notification (uncomment if you want notifications)
# osascript -e 'display notification "Fantasy RSS ingestion complete" with title "Fantasy Football"'