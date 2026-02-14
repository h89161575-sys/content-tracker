# Discord Notification Module

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

from config import DISCORD_MAX_CHANGES

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
    for change in changes[:DISCORD_MAX_CHANGES]:  # Discord limit: 25 fields
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
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
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
        title = item.get("title") or item.get("name") or item.get("_id") or item.get("url") or "Unknown"
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
    for update in updates[:DISCORD_MAX_CHANGES]:
        item_id = update.get("id", "Unknown")
        field = update.get("field", "Unknown field")

        # Allow callers to provide a pre-formatted details string (e.g., a content diff).
        details_override = update.get("details")
        if details_override:
            details = str(details_override)
        else:
            old_val = _truncate(str(update.get("old", "")), 100)
            new_val = _truncate(str(update.get("new", "")), 100)
            details = f"ID: `{item_id}`\n`{old_val}` → `{new_val}`"

        type_label = update.get("type") or f"📝 Updated: {field}"
        
        changes.append({
            "type": str(type_label),
            "details": details
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
        title = item.get("title") or item.get("name") or item.get("_id") or item.get("url") or "Unknown"
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
    new_routes: List[str],
    *,
    changed_routes: Optional[List[str]] = None,
    removed_routes: Optional[List[str]] = None,
    crawled_changes: Optional[List[Dict[str, Any]]] = None,
    new_ssg_pages: Optional[List[str]] = None,
    removed_ssg_pages: Optional[List[str]] = None,
) -> bool:
    """Send enhanced notification when build ID changes (new deployment).

    Now includes:
    - Which specific pages had code changes (JS-Chunk-Diff)
    - New/removed SSG pages
    - Content previews for crawled changed routes
    """

    changes: List[Dict[str, Any]] = [{
        "type": "🏗️ Build ID",
        "details": f"`{old_build_id[:20]}...` → `{new_build_id[:20]}...`"
    }]

    # ── Changed pages (JS-Chunk-Diff) ──
    if changed_routes:
        routes_text = "\n".join([f"• `{r}`" for r in changed_routes[:15]])
        if len(changed_routes) > 15:
            routes_text += f"\n_... und {len(changed_routes) - 15} weitere_"
        changes.append({
            "type": f"📝 Code-Änderungen auf {len(changed_routes)} Seite(n)",
            "details": routes_text
        })

    # ── New routes ──
    if new_routes:
        routes_text = "\n".join([f"• `{r}`" for r in new_routes[:10]])
        changes.append({
            "type": "🆕 Neue Routen",
            "details": routes_text
        })

    # ── Removed routes ──
    if removed_routes:
        routes_text = "\n".join([f"• `{r}`" for r in removed_routes[:10]])
        changes.append({
            "type": "🗑️ Entfernte Routen",
            "details": routes_text
        })

    # ── SSG manifest changes ──
    if new_ssg_pages:
        ssg_text = "\n".join([f"• `{p}`" for p in new_ssg_pages[:10]])
        changes.append({
            "type": "📄 Neue SSG-Seiten",
            "details": ssg_text
        })
    if removed_ssg_pages:
        ssg_text = "\n".join([f"• `{p}`" for p in removed_ssg_pages[:10]])
        changes.append({
            "type": "📄 Entfernte SSG-Seiten",
            "details": ssg_text
        })

    # ── Crawled content previews ──
    if crawled_changes:
        live_pages = [c for c in crawled_changes if c.get("status") == "live"]
        for page in live_pages[:5]:
            title = page.get("title", page.get("route", "?"))
            preview = page.get("content_preview", "")
            detail = f"**[{_truncate(title, 80)}]({page.get('full_url', '')})** "
            if preview:
                detail += f"\n```\n{_truncate(preview, 300)}\n```"
            changes.append({
                "type": f"🔍 {_truncate(page.get('route', '?'), 60)}",
                "details": detail
            })

    # ── Build description summary ──
    desc_parts = ["Die Website wurde neu deployed."]
    if changed_routes:
        desc_parts.append(f"**{len(changed_routes)}** Seite(n) mit Code-Änderungen erkannt.")
    if not changed_routes and not new_routes and not removed_routes:
        desc_parts.append("Nur Build-Infrastruktur / shared Chunks geändert.")

    return send_discord_notification(
        webhook_url=webhook_url,
        title="🔔 Website Deployment Detected!",
        description=" ".join(desc_parts),
        changes=changes,
        page_url="https://drjoedispenza.com",
        color=0x9B59B6  # Purple
    )


def send_new_route_with_content_notification(
    webhook_url: str,
    site_name: str,
    base_url: str,
    routes_with_content: List[Dict[str, Any]]
) -> bool:
    """
    Send notification for new routes with page content preview.
    
    Args:
        webhook_url: Discord webhook URL
        site_name: Name of the site (e.g., "Site1", "Site2")
        base_url: Base URL of the site
        routes_with_content: List of dicts with keys:
            - route: The route path (e.g., "/product/new-item")
            - full_url: Full URL to the page
            - status: "live" | "pending" (404/unreachable)
            - title: Page title (if available)
            - content_preview: Text preview of page content (if available)
    """
    if not webhook_url or not routes_with_content:
        return False
    
    changes = []
    live_count = 0
    pending_count = 0
    
    for route_info in routes_with_content[:10]:  # Limit to 10 routes
        route = route_info.get("route", "Unknown")
        full_url = route_info.get("full_url", base_url + route)
        status = route_info.get("status", "unknown")
        title = route_info.get("title", "")
        content_preview = route_info.get("content_preview", "")
        
        if status == "live":
            live_count += 1
            # Build details with title and content preview
            details = f"**[{_truncate(title, 80) if title else route}]({full_url})**\n"
            if content_preview:
                # Show a preview of the page content
                truncated_content = _truncate(content_preview, 600)
                details += f"```\n{truncated_content}\n```"
            else:
                details += "_Kein Textinhalt extrahiert_"
            
            changes.append({
                "type": f"✅ {route}",
                "details": details
            })
        else:
            pending_count += 1
            changes.append({
                "type": f"⏳ {route}",
                "details": f"**Seite noch nicht erreichbar (404)**\n_Wird in Watch-Liste aufgenommen und weiter beobachtet_\n[Link]({full_url})"
            })
    
    # Build description
    description_parts = []
    if live_count > 0:
        description_parts.append(f"**{live_count}** neue Seite(n) sind bereits live")
    if pending_count > 0:
        description_parts.append(f"**{pending_count}** Seite(n) sind noch nicht erreichbar (werden weiter beobachtet)")
    
    description = " | ".join(description_parts) if description_parts else "Neue Routen gefunden"
    
    return send_discord_notification(
        webhook_url=webhook_url,
        title=f"🆕 Neue Routen auf {site_name}!",
        description=description,
        changes=changes,
        page_url=base_url,
        color=0x00FF00 if live_count > 0 else 0xFFAA00  # Green if live, Orange if pending
    )


def send_pending_route_now_live_notification(
    webhook_url: str,
    site_name: str,
    base_url: str,
    route_info: Dict[str, Any]
) -> bool:
    """
    Send notification when a previously pending route is now live.
    
    Args:
        webhook_url: Discord webhook URL
        site_name: Name of the site
        base_url: Base URL
        route_info: Dict with route, full_url, title, content_preview, first_seen
    """
    if not webhook_url or not route_info:
        return False
    
    route = route_info.get("route", "Unknown")
    full_url = route_info.get("full_url", base_url + route)
    title = route_info.get("title", "")
    content_preview = route_info.get("content_preview", "")
    first_seen = route_info.get("first_seen", "")
    
    details = f"**[{_truncate(title, 80) if title else route}]({full_url})**\n"
    if first_seen:
        details += f"_Erstmals entdeckt: {first_seen}_\n\n"
    if content_preview:
        truncated_content = _truncate(content_preview, 800)
        details += f"```\n{truncated_content}\n```"
    else:
        details += "_Kein Textinhalt extrahiert_"
    
    changes = [{
        "type": f"✅ Jetzt LIVE: {route}",
        "details": details
    }]
    
    return send_discord_notification(
        webhook_url=webhook_url,
        title=f"🎉 Zuvor wartende Seite ist jetzt live auf {site_name}!",
        description=f"Eine zuvor entdeckte Route, die nicht erreichbar war, ist jetzt verfügbar.",
        changes=changes,
        page_url=full_url,
        color=0x00FF00  # Green
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


def send_new_youtube_video_notification(
    webhook_url: str,
    videos: List[Dict[str, Any]]
) -> bool:
    """
    Send notification for new YouTube videos.
    
    Args:
        webhook_url: Discord webhook URL
        videos: List of video dicts with keys: video_id, title, published, thumbnail_url
    """
    if not webhook_url or not videos:
        return False
    
    # Build embeds for each video (max 10 per message)
    embeds = []
    for video in videos[:10]:
        video_id = video.get("video_id", "")
        title = video.get("title", "Neues Video")
        published = video.get("published", "")[:10] if video.get("published") else ""
        thumbnail_url = video.get("thumbnail_url", "")
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        embed = {
            "title": f"🎬 {_truncate(title, 250)}",
            "url": video_url,
            "color": 0xFF0000,  # YouTube Red
            "description": f"Ein neues Video wurde auf dem Dr. Joe Dispenza YouTube-Kanal veröffentlicht!",
            "fields": [
                {
                    "name": "📅 Veröffentlicht",
                    "value": published if published else "Unbekannt",
                    "inline": True
                },
                {
                    "name": "🔗 Link",
                    "value": f"[Video ansehen]({video_url})",
                    "inline": True
                }
            ],
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "footer": {
                "text": "YouTube Tracker"
            }
        }
        
        # Add thumbnail if available
        if thumbnail_url:
            embed["thumbnail"] = {"url": thumbnail_url}
        
        embeds.append(embed)
    
    payload = {"embeds": embeds}
    
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status in (200, 204):
                print(f"✅ YouTube notification sent: {len(videos)} new video(s)")
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


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max length with ellipsis."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


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
        title = item.get("title") or item.get("name") or item.get("_id") or item.get("url") or "Unknown"
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
    for update in updates[:DISCORD_MAX_CHANGES]:
        item_id = update.get("id", "Unknown")
        field = update.get("field", "Unknown field")

        # Allow callers to provide a pre-formatted details string (e.g., a content diff).
        details_override = update.get("details")
        if details_override:
            details = str(details_override)
        else:
            old_val = _truncate(str(update.get("old", "")), 100)
            new_val = _truncate(str(update.get("new", "")), 100)
            details = f"ID: `{item_id}`\n`{old_val}` → `{new_val}`"

        type_label = update.get("type") or f"📝 Updated: {field}"
        
        changes.append({
            "type": str(type_label),
            "details": details
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
        title = item.get("title") or item.get("name") or item.get("_id") or item.get("url") or "Unknown"
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


# NOTE: Primary send_build_change_notification is defined above (line ~194).
# This duplicate was removed to avoid divergence.


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


def send_new_youtube_video_notification(
    webhook_url: str,
    videos: List[Dict[str, Any]]
) -> bool:
    """
    Send notification for new YouTube videos.
    
    Args:
        webhook_url: Discord webhook URL
        videos: List of video dicts with keys: video_id, title, published, thumbnail_url
    """
    if not webhook_url or not videos:
        return False
    
    # Build embeds for each video (max 10 per message)
    embeds = []
    for video in videos[:10]:
        video_id = video.get("video_id", "")
        title = video.get("title", "Neues Video")
        published = video.get("published", "")[:10] if video.get("published") else ""
        thumbnail_url = video.get("thumbnail_url", "")
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        embed = {
            "title": f"🎬 {_truncate(title, 250)}",
            "url": video_url,
            "color": 0xFF0000,  # YouTube Red
            "description": f"Ein neues Video wurde auf dem Dr. Joe Dispenza YouTube-Kanal veröffentlicht!",
            "fields": [
                {
                    "name": "📅 Veröffentlicht",
                    "value": published if published else "Unbekannt",
                    "inline": True
                },
                {
                    "name": "🔗 Link",
                    "value": f"[Video ansehen]({video_url})",
                    "inline": True
                }
            ],
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "footer": {
                "text": "YouTube Tracker"
            }
        }
        
        # Add thumbnail if available
        if thumbnail_url:
            embed["thumbnail"] = {"url": thumbnail_url}
        
        embeds.append(embed)
    
    payload = {"embeds": embeds}
    
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status in (200, 204):
                print(f"✅ YouTube notification sent: {len(videos)} new video(s)")
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


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max length with ellipsis."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."
