# Quick test script - copy to `test_webhook.py` locally and paste your webhook URL.
#
# Important:
# - `test_webhook.py` is intentionally gitignored and should NOT be committed.
# - Configure the tracker via the environment variable `DISCORD_WEBHOOK_URL` for real runs.
#
WEBHOOK_URL = "PASTE_YOUR_DISCORD_WEBHOOK_URL_HERE"

import json
import urllib.request

data = json.dumps({
    "embeds": [{
        "title": "🔔 Test Notification",
        "description": "✅ Your Content Tracker is working correctly!",
        "color": 0x3498DB,
        "fields": [{
            "name": "🧪 Test",
            "value": "This is a test message. If you see this, notifications are set up correctly.",
            "inline": False
        }],
        "footer": {"text": "Content Tracker"}
    }]
}).encode("utf-8")

req = urllib.request.Request(
    WEBHOOK_URL,
    data=data,
    headers={
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    },
    method="POST"
)

try:
    response = urllib.request.urlopen(req, timeout=30)
    print(f"✅ Success! Status: {response.status}")
except Exception as e:
    print(f"❌ Error: {e}")

