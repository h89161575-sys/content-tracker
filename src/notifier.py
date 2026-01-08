# Discord Notification Module

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

def send_discord_notification(
    webhook_url: str,
    title: str,
    description: str,
    changes: List[Dict[str, Any]],
    page_url: str,
    color: int = 0x00FF00  # Green default
) -> bool:
    """
    Send a formatted notification to Discord via webhook.
    
    Args:
        webhook_url: Discord webhook URL
        title: Embed title
        description: Main description text
        changes: List of change details
        page_url: URL of the changed page
        color: Embed color (hex as int)
    
    Returns:
        True if successful, False otherwise
    """
    if not webhook_url:
        print("⚠️  No Discord webhook URL configured")
        return False
    
    # Build embed fields from changes
    fields = []
    for change in changes[:10]:  # Discord limit: 25 fields, we use max 10
        fields.append({
            "name": change.get("type", "Change"),
            "value": _truncate(change.get("details", "No details"), 1024),
            "inline": False
        })
    
    embed = {
        "title": f"🔔 {title}",
        "description": _truncate(description, 4096),
        "color": color,
        "fields": fields,
        "url": page_url,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "footer": {
            "text": "Content Tracker"
        }
    }
    
    payload = {
        "embeds": [embed]
    }
    
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status in (200, 204):
                print(f"✅ Discord notification sent: {title}")
                return True
            else:
                print(f"❌ Discord returned status {response.status}")
                return False
                
    except urllib.error.HTTPError as e:
        print(f"❌ Discord HTTP error: {e.code} - {e.reason}")
        return False
    except urllib.error.URLError as e:
        print(f"❌ Discord URL error: {e.reason}")
        return False
    except Exception as e:
        print(f"❌ Discord error: {e}")
        return False


def send_new_items_notification(
    webhook_url: str,
    page_name: str,
    page_url: str,
    new_items: List[Dict[str, Any]]
) -> bool:
    """Send notification for new items (retreats, products, etc.)"""
    
    changes = []
    for item in new_items[:5]:  # Limit to 5 items
        # Try to extract meaningful info
        title = item.get("title") or item.get("name") or item.get("_id", "Unknown")
        status = item.get("status", "")
        start_date = item.get("startDate", "")
        
        details = f"**{title}**"
        if status:
            details += f"\nStatus: `{status}`"
        if start_date:
            details += f"\nStart: `{start_date[:10]}`"
            
        changes.append({
            "type": "🆕 New Item",
            "details": details
        })
    
    return send_discord_notification(
        webhook_url=webhook_url,
        title=f"New items on {page_name}!",
        description=f"Found {len(new_items)} new item(s) on the {page_name} page.",
        changes=changes,
        page_url=page_url,
        color=0x00FF00  # Green
    )


def send_updated_items_notification(
    webhook_url: str,
    page_name: str,
    page_url: str,
    updates: List[Dict[str, Any]]
) -> bool:
    """Send notification for updated items."""
    
    changes = []
    for update in updates[:5]:
        item_id = update.get("id", "Unknown")
        field = update.get("field", "Unknown field")
        old_val = _truncate(str(update.get("old", "")), 100)
        new_val = _truncate(str(update.get("new", "")), 100)
        
        changes.append({
            "type": f"📝 Updated: {field}",
            "details": f"ID: `{item_id}`\n`{old_val}` → `{new_val}`"
        })
    
    return send_discord_notification(
        webhook_url=webhook_url,
        title=f"Updates on {page_name}",
        description=f"Found {len(updates)} change(s) on the {page_name} page.",
        changes=changes,
        page_url=page_url,
        color=0xFFAA00  # Orange
    )


def send_removed_items_notification(
    webhook_url: str,
    page_name: str,
    page_url: str,
    removed_items: List[Dict[str, Any]]
) -> bool:
    """Send notification for removed items."""
    
    changes = []
    for item in removed_items[:5]:
        title = item.get("title") or item.get("name") or item.get("_id", "Unknown")
        changes.append({
            "type": "🗑️ Removed",
            "details": f"**{title}**"
        })
    
    return send_discord_notification(
        webhook_url=webhook_url,
        title=f"Items removed from {page_name}",
        description=f"{len(removed_items)} item(s) were removed from the {page_name} page.",
        changes=changes,
        page_url=page_url,
        color=0xFF0000  # Red
    )


def send_build_change_notification(
    webhook_url: str,
    old_build_id: str,
    new_build_id: str,
    new_routes: List[str]
) -> bool:
    """Send notification when build ID changes (new deployment)."""
    
    changes = [{
        "type": "🏗️ Build ID",
        "details": f"`{old_build_id[:20]}...` → `{new_build_id[:20]}...`"
    }]
    
    if new_routes:
        routes_text = "\n".join([f"• `{r}`" for r in new_routes[:10]])
        changes.append({
            "type": "🆕 New Routes Found",
            "details": routes_text
        })
    
    return send_discord_notification(
        webhook_url=webhook_url,
        title="Website Deployment Detected!",
        description="The website has been redeployed. This could mean new features or content.",
        changes=changes,
        page_url="https://drjoedispenza.com",
        color=0x9B59B6  # Purple
    )


def send_test_notification(webhook_url: str) -> bool:
    """Send a test notification to verify webhook is working."""
    
    return send_discord_notification(
        webhook_url=webhook_url,
        title="Test Notification",
        description="✅ Your Dr. Joe Tracker is working correctly!",
        changes=[{
            "type": "🧪 Test",
            "details": "This is a test message. If you see this, notifications are set up correctly."
        }],
        page_url="https://drjoedispenza.com",
        color=0x3498DB  # Blue
    )


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max length with ellipsis."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."
