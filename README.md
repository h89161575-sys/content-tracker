# Content Tracker 🔔

A website change tracker with Discord notifications. Monitors Next.js websites for content changes.

## Features

- ✅ Tracks multiple pages on a target website
- ✅ Extracts Next.js data directly from HTML (`__NEXT_DATA__`)
- ✅ Runs free on GitHub Actions (every 5 minutes)
- ✅ Intelligent diffing (ignores timestamps and noise)
- ✅ Discord webhook notifications

## Setup

### 1. Fork or clone this repository

### 2. Create a Discord Webhook

1. Go to your Discord Server → Server Settings → Integrations → Webhooks
2. Click "New Webhook" → Select channel → Copy URL

### 3. Add GitHub Secret

1. Go to your GitHub repository
2. Settings → Secrets and variables → Actions
3. New repository secret
4. Name: `DISCORD_WEBHOOK_URL`
5. Value: Your webhook URL

### 4. Done!

The tracker runs automatically every 5 minutes.

## Local Testing

```powershell
# Test without Discord
cd src
python tracker.py --test

# Test Discord notification
$env:DISCORD_WEBHOOK_URL = "your-webhook-url"
python tracker.py --test-notify
```

## Configuration

Edit `src/config.py` to customize:
- Pages to track
- Data paths to extract
- Keys to ignore during comparison
- Discord notification limits (e.g. how many changes are shown per message)

## How It Works

```
GitHub Actions → Python Script → Fetch Pages → Extract Data → Compare → Discord
     (cron)        tracker.py      urllib       __NEXT_DATA__    diff      webhook
```

## License

MIT
