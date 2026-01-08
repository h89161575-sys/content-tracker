# Main Tracker Script for Website Change Monitoring

import json
import os
import re
import sys
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import urllib.error

from config import (
    PAGES_TO_TRACK,
    DISCORD_WEBHOOK_URL,
    SNAPSHOTS_DIR,
    IGNORE_KEYS,
    TIMESTAMP_KEYS,
    PageConfig,
)
from notifier import (
    send_new_items_notification,
    send_updated_items_notification,
    send_removed_items_notification,
    send_build_change_notification,
    send_test_notification,
)


def fetch_page(url: str) -> Optional[str]:
    """Fetch HTML content from a URL."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP error fetching {url}: {e.code}")
        return None
    except urllib.error.URLError as e:
        print(f"❌ URL error fetching {url}: {e.reason}")
        return None
    except Exception as e:
        print(f"❌ Error fetching {url}: {e}")
        return None


def extract_next_data(html: str) -> Optional[Dict[str, Any]]:
    """Extract __NEXT_DATA__ JSON from HTML."""
    pattern = r'<script\s+id="__NEXT_DATA__"\s+type="application/json"[^>]*>(.*?)</script>'
    match = re.search(pattern, html, re.DOTALL)
    
    if not match:
        return None
    
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as e:
        print(f"❌ JSON decode error: {e}")
        return None


def get_nested_value(data: Dict[str, Any], path: str) -> Any:
    """Get a nested value from a dict using dot notation."""
    keys = path.split(".")
    current = data
    
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    
    return current


def normalize_data(data: Any) -> Any:
    """
    Normalize data for comparison by removing/standardizing volatile fields.
    """
    if isinstance(data, dict):
        normalized = {}
        for key, value in data.items():
            # Skip ignored keys
            if key in IGNORE_KEYS:
                continue
            # Normalize timestamp keys to just the date
            if key in TIMESTAMP_KEYS and isinstance(value, str):
                normalized[key] = value[:10] if len(value) >= 10 else value
            else:
                normalized[key] = normalize_data(value)
        return normalized
    elif isinstance(data, list):
        return [normalize_data(item) for item in data]
    else:
        return data


def get_snapshot_path(page_name: str) -> str:
    """Get the path for a page's snapshot file."""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', page_name.lower())
    return os.path.join(SNAPSHOTS_DIR, f"{safe_name}.json")


def load_snapshot(page_name: str) -> Optional[Dict[str, Any]]:
    """Load the previous snapshot for a page."""
    path = get_snapshot_path(page_name)
    if not os.path.exists(path):
        return None
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️  Error loading snapshot for {page_name}: {e}")
        return None


def save_snapshot(page_name: str, data: Dict[str, Any]) -> None:
    """Save a snapshot for a page."""
    path = get_snapshot_path(page_name)
    
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": data
    }
    
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
    
    print(f"💾 Saved snapshot for {page_name}")


def get_items_by_id(data: Any) -> Dict[str, Any]:
    """Extract items with _id or id field from data."""
    items = {}
    
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                item_id = item.get("_id") or item.get("id")
                if item_id:
                    items[item_id] = item
    elif isinstance(data, dict):
        # Recursively search for arrays with IDs
        for key, value in data.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        item_id = item.get("_id") or item.get("id")
                        if item_id:
                            items[item_id] = item
    
    return items


def compare_items(
    old_items: Dict[str, Any],
    new_items: Dict[str, Any]
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Compare old and new items.
    Returns: (new_items, updated_items, removed_items)
    """
    old_ids = set(old_items.keys())
    new_ids = set(new_items.keys())
    
    # New items
    added = [new_items[id] for id in (new_ids - old_ids)]
    
    # Removed items
    removed = [old_items[id] for id in (old_ids - new_ids)]
    
    # Updated items
    updated = []
    for id in (old_ids & new_ids):
        old_item = normalize_data(old_items[id])
        new_item = normalize_data(new_items[id])
        
        if old_item != new_item:
            # Find what changed
            for key in set(list(old_item.keys()) + list(new_item.keys())):
                old_val = old_item.get(key)
                new_val = new_item.get(key)
                if old_val != new_val:
                    updated.append({
                        "id": id,
                        "field": key,
                        "old": old_val,
                        "new": new_val
                    })
    
    return added, updated, removed


def compute_hash(data: Any) -> str:
    """Compute a hash of normalized data for quick comparison."""
    normalized = normalize_data(data)
    json_str = json.dumps(normalized, sort_keys=True)
    return hashlib.md5(json_str.encode()).hexdigest()


def track_page(page: PageConfig) -> bool:
    """
    Track a single page for changes.
    Returns True if changes were detected.
    """
    print(f"\n📡 Tracking: {page.name} ({page.url})")
    
    # Fetch the page
    html = fetch_page(page.url)
    if not html:
        print(f"⚠️  Could not fetch {page.name}")
        return False
    
    # Extract Next.js data
    next_data = extract_next_data(html)
    if not next_data:
        print(f"⚠️  No __NEXT_DATA__ found on {page.name}")
        return False
    
    # Get the specific data we care about
    page_data = get_nested_value(next_data, page.data_path)
    if page_data is None:
        print(f"⚠️  Could not find data at path '{page.data_path}'")
        page_data = next_data  # Fall back to full data
    
    # Load previous snapshot
    old_snapshot = load_snapshot(page.name)
    
    # If no previous snapshot, just save current and return
    if old_snapshot is None:
        print(f"📝 First snapshot for {page.name}")
        save_snapshot(page.name, page_data)
        return False
    
    old_data = old_snapshot.get("data", {})
    
    # Quick hash comparison first
    old_hash = compute_hash(old_data)
    new_hash = compute_hash(page_data)
    
    if old_hash == new_hash:
        print(f"✅ No changes on {page.name}")
        return False
    
    print(f"🔄 Changes detected on {page.name}!")
    
    # Detailed comparison for items with IDs
    old_items = get_items_by_id(old_data)
    new_items = get_items_by_id(page_data)
    
    added, updated, removed = compare_items(old_items, new_items)
    
    # Send notifications
    if DISCORD_WEBHOOK_URL:
        if added:
            send_new_items_notification(
                DISCORD_WEBHOOK_URL,
                page.name,
                page.url,
                added
            )
        
        if updated:
            send_updated_items_notification(
                DISCORD_WEBHOOK_URL,
                page.name,
                page.url,
                updated
            )
        
        if removed:
            send_removed_items_notification(
                DISCORD_WEBHOOK_URL,
                page.name,
                page.url,
                removed
            )
    else:
        print("⚠️  No Discord webhook configured - skipping notifications")
    
    # Save new snapshot
    save_snapshot(page.name, page_data)
    
    return True


def track_build_manifest() -> bool:
    """Track the build manifest for new routes/deployments."""
    print("\n📡 Tracking: Build Manifest")
    
    # First, get current build ID from homepage
    html = fetch_page("https://drjoedispenza.com/")
    if not html:
        return False
    
    next_data = extract_next_data(html)
    if not next_data:
        return False
    
    build_id = next_data.get("buildId")
    if not build_id:
        print("⚠️  Could not find buildId")
        return False
    
    print(f"📦 Current buildId: {build_id}")
    
    # Load previous build manifest snapshot
    old_snapshot = load_snapshot("build_manifest")
    
    # Fetch build manifest
    manifest_url = f"https://drjoedispenza.com/_next/static/{build_id}/_buildManifest.js"
    manifest_content = fetch_page(manifest_url)
    
    if not manifest_content:
        return False
    
    # Extract routes from manifest
    # Format: self.__BUILD_MANIFEST={...routes...}
    routes = set(re.findall(r'"(/[^"]*)"', manifest_content))
    
    current_data = {
        "buildId": build_id,
        "routes": sorted(list(routes)),
        "manifestHash": hashlib.md5(manifest_content.encode()).hexdigest()
    }
    
    if old_snapshot is None:
        print(f"📝 First build manifest snapshot")
        save_snapshot("build_manifest", current_data)
        return False
    
    old_data = old_snapshot.get("data", {})
    old_build_id = old_data.get("buildId", "")
    old_routes = set(old_data.get("routes", []))
    
    # Check for changes
    if old_build_id != build_id:
        print(f"🔄 Build ID changed: {old_build_id[:20]}... → {build_id[:20]}...")
        
        new_routes = routes - old_routes
        if new_routes:
            print(f"🆕 New routes: {new_routes}")
        
        if DISCORD_WEBHOOK_URL:
            send_build_change_notification(
                DISCORD_WEBHOOK_URL,
                old_build_id,
                build_id,
                sorted(list(new_routes))
            )
        
        save_snapshot("build_manifest", current_data)
        return True
    
    print("✅ No build changes")
    return False


def track_build_manifest_site2() -> bool:
    """Track the build manifest for Site2 (German shop)."""
    print("\n📡 Tracking: Site2 Build Manifest")
    
    # Get current build ID from Site2 homepage
    html = fetch_page("https://drjoedispenza.info/s/Drjoedispenza")
    if not html:
        return False
    
    next_data = extract_next_data(html)
    if not next_data:
        return False
    
    build_id = next_data.get("buildId")
    if not build_id:
        print("⚠️  Could not find buildId for Site2")
        return False
    
    print(f"📦 Site2 buildId: {build_id}")
    
    # Extract shopPages from the NEXT_DATA (contains all registered pages)
    page_props = get_nested_value(next_data, "props.pageProps")
    shop_pages = []
    if page_props:
        # Try to find shopPages or similar sitemap data
        initial_data = page_props.get("initialData", {})
        content_store = initial_data.get("contentPageStore", {})
        shop_pages = content_store.get("shopPages", [])
        if not shop_pages:
            # Fallback: extract all slugs from the data
            shop_pages = content_store.get("allSlugs", [])
    
    # Load previous snapshot
    old_snapshot = load_snapshot("build_manifest_site2")
    
    current_data = {
        "buildId": build_id,
        "shopPages": sorted([str(p) for p in shop_pages]) if shop_pages else [],
        "pageCount": len(shop_pages) if shop_pages else 0
    }
    
    if old_snapshot is None:
        print(f"📝 First Site2 build manifest snapshot ({current_data['pageCount']} pages)")
        save_snapshot("build_manifest_site2", current_data)
        return False
    
    old_data = old_snapshot.get("data", {})
    old_build_id = old_data.get("buildId", "")
    old_pages = set(old_data.get("shopPages", []))
    new_pages = set(current_data.get("shopPages", []))
    
    changes_detected = False
    
    # Check for new pages
    added_pages = new_pages - old_pages
    if added_pages:
        print(f"🆕 New Site2 pages: {added_pages}")
        changes_detected = True
        if DISCORD_WEBHOOK_URL:
            send_build_change_notification(
                DISCORD_WEBHOOK_URL,
                f"Site2: {len(old_pages)} pages",
                f"Site2: {len(new_pages)} pages",
                sorted(list(added_pages))[:10]  # Limit to 10
            )
    
    # Check for build ID change
    if old_build_id != build_id:
        print(f"🔄 Site2 Build ID changed")
        changes_detected = True
    
    if changes_detected:
        save_snapshot("build_manifest_site2", current_data)
        return True
    
    print("✅ No Site2 build changes")
    return False


def main():
    """Main entry point."""
    print("=" * 60)
    print(f"🚀 Website Change Tracker")
    print(f"📅 {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}")
    print("=" * 60)
    
    # Check for command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "--test":
            print("\n🧪 Running in test mode...")
            # Just run once and print results
            pass
        elif sys.argv[1] == "--test-notify":
            print("\n🧪 Testing Discord notification...")
            if DISCORD_WEBHOOK_URL:
                success = send_test_notification(DISCORD_WEBHOOK_URL)
                sys.exit(0 if success else 1)
            else:
                print("❌ DISCORD_WEBHOOK_URL not set!")
                sys.exit(1)
        elif sys.argv[1] == "--help":
            print("\nUsage:")
            print("  python tracker.py           # Run tracker")
            print("  python tracker.py --test    # Test mode (no notifications)")
            print("  python tracker.py --test-notify  # Test Discord webhook")
            sys.exit(0)
    
    changes_detected = False
    
    # Track all configured pages
    for page in PAGES_TO_TRACK:
        try:
            if track_page(page):
                changes_detected = True
        except Exception as e:
            print(f"❌ Error tracking {page.name}: {e}")
    
    # Track build manifest for Site1
    try:
        if track_build_manifest():
            changes_detected = True
    except Exception as e:
        print(f"❌ Error tracking build manifest: {e}")
    
    # Track build manifest for Site2
    try:
        if track_build_manifest_site2():
            changes_detected = True
    except Exception as e:
        print(f"❌ Error tracking Site2 build manifest: {e}")
    
    print("\n" + "=" * 60)
    if changes_detected:
        print("📢 Changes were detected!")
    else:
        print("✅ No changes detected")
    print("=" * 60)
    
    # For GitHub Actions: exit with code based on changes
    # This allows workflows to conditionally commit snapshots
    sys.exit(0)


if __name__ == "__main__":
    main()
