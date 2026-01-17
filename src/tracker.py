# Main Tracker Script for Website Change Monitoring

import difflib
import gzip
import json
import os
import re
import sys
import hashlib
import zlib
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import urllib.error

from config import (
    PAGES_TO_TRACK,
    DISCORD_WEBHOOK_URL,
    DISCORD_MAX_CHANGES,
    SITE1_CONTENT_EXCLUDE_SECTION_HEADINGS,
    SITE1_CONTENT_EXCLUDE_HTML_CLASS_SUBSTRINGS,
    SNAPSHOTS_DIR,
    IGNORE_KEYS,
    TIMESTAMP_KEYS,
    PageConfig,
    YOUTUBE_CHANNEL_ID,
)
from notifier import (
    send_new_items_notification,
    send_updated_items_notification,
    send_removed_items_notification,
    send_build_change_notification,
    send_test_notification,
    send_new_youtube_video_notification,
)


def fetch_page(url: str) -> Optional[str]:
    """Fetch HTML content from a URL."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        # Some sites return gzipped HTML even without explicitly asking; we handle it below.
        "Accept-Encoding": "gzip, deflate",
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()

            # Handle common HTTP-level compression
            content_encoding = (response.headers.get("Content-Encoding") or "").lower()
            if "gzip" in content_encoding or raw[:2] == b"\x1f\x8b":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            elif "deflate" in content_encoding:
                try:
                    raw = zlib.decompress(raw)
                except Exception:
                    try:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                    except Exception:
                        pass

            charset = None
            try:
                charset = response.headers.get_content_charset()  # type: ignore[attr-defined]
            except Exception:
                charset = None

            return raw.decode(charset or "utf-8", errors="replace")
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


def extract_site6_bootstrap_data(html: str) -> Optional[Dict[str, Any]]:
    """Extract data from Site6's bootstrap object.
    
    Site6 uses JavaScript object literals (with single quotes, unquoted keys, etc.)
    which is not valid JSON. This function handles the conversion.
    """
    # Pattern to find 'const bootstrap = {...}' or 'var bootstrap = {...}'
    # Use a greedy match to get the full object, then find the matching closing brace
    match = re.search(r'(?:const|var|let)\s+bootstrap\s*=\s*\{', html)
    if not match:
        return None
    
    start_idx = match.end() - 1  # Position of opening brace
    
    # Find matching closing brace by counting braces
    brace_count = 0
    end_idx = start_idx
    in_string = False
    string_char = None
    
    for i, char in enumerate(html[start_idx:], start_idx):
        if in_string:
            if char == string_char and html[i-1] != '\\':
                in_string = False
        else:
            if char in ('"', "'"):
                in_string = True
                string_char = char
            elif char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1
                    break
    
    if end_idx <= start_idx:
        return None
    
    js_obj = html[start_idx:end_idx]
    
    # Convert JavaScript object literal to valid JSON:
    # 1. Replace single-quoted strings with double-quoted
    # 2. Quote unquoted keys
    # 3. Handle trailing commas
    
    try:
        # Strategy: Use regex to fix common JS-to-JSON issues
        json_str = js_obj
        
        # Replace single quotes around keys and string values with double quotes
        # This is a simplified approach - handles most common cases
        json_str = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', json_str)
        
        # Quote unquoted keys: { key: value } -> { "key": value }
        json_str = re.sub(r'(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', json_str)
        
        # Remove trailing commas before } or ]
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
        
        # Handle boolean values (true/false are the same in JS and JSON)
        # Handle undefined -> null
        json_str = re.sub(r'\bundefined\b', 'null', json_str)
        
        data = json.loads(json_str)
        
        # Parse 'project_data' string if it exists
        if 'project_data' in data and isinstance(data['project_data'], str):
            try:
                data['project_data'] = json.loads(data['project_data'])
            except json.JSONDecodeError:
                pass  # Keep as string if parsing fails
        
        return data
        
    except json.JSONDecodeError as e:
        # Fallback: extract key fields manually using regex
        result = {}
        
        # Extract projectDate (for change detection)
        date_match = re.search(r"'projectDate'\s*:\s*\"([^\"]+)\"", js_obj)
        if date_match:
            result['projectDate'] = date_match.group(1)
        
        # Extract projectName
        name_match = re.search(r"'projectName'\s*:\s*\"([^\"]+)\"", js_obj)
        if name_match:
            result['projectName'] = name_match.group(1)
        
        # Extract project_data as raw string for hashing
        pd_match = re.search(r"'project_data'\s*:\s*\"((?:[^\"\\]|\\.)*)\"", js_obj, re.DOTALL)
        if pd_match:
            result['project_data'] = pd_match.group(1)
        
        # Extract pid
        pid_match = re.search(r"'pid'\s*:\s*\"([^\"]+)\"", js_obj)
        if pid_match:
            result['pid'] = pid_match.group(1)
        
        if result:
            return result
        
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


class _BodyTextExtractor(HTMLParser):
    """Extract readable text from HTML, inserting newlines on common block boundaries."""

    _BLOCK_TAGS = {
        "p",
        "div",
        "section",
        "article",
        "main",
        "header",
        "footer",
        "nav",
        "aside",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ul",
        "ol",
        "br",
        "hr",
        "table",
        "tr",
    }

    _HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    _SKIP_TAGS = {"script", "style", "noscript", "svg"}

    @staticmethod
    def _normalize_heading(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip().casefold()

    def __init__(
        self,
        exclude_section_headings: Optional[List[str]] = None,
        exclude_container_class_substrings: Optional[List[str]] = None,
    ) -> None:
        super().__init__()
        self._parts: List[str] = []
        self._skip_depth = 0
        self._skip_container_depth = 0
        self._heading_tag: Optional[str] = None
        self._heading_text_parts: List[str] = []
        self._excluded_heading_norms = {
            self._normalize_heading(h) for h in (exclude_section_headings or []) if h and h.strip()
        }
        self._exclude_class_substrings = [
            s.strip().lower()
            for s in (exclude_container_class_substrings or [])
            if s and s.strip()
        ]

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return

        # Skip entire containers based on class name markers (used to exclude noisy widgets).
        if self._skip_container_depth:
            self._skip_container_depth += 1
            return

        if self._exclude_class_substrings:
            class_attr = None
            for name, value in attrs:
                if name and name.lower() == "class" and value:
                    class_attr = value.lower()
                    break
            if class_attr and any(sub in class_attr for sub in self._exclude_class_substrings):
                self._skip_container_depth = 1
                return

        # Ignore markup inside headings; we only keep the heading text.
        if tag in self._HEADING_TAGS:
            self._heading_tag = tag
            self._heading_text_parts = []
            return

        if self._heading_tag:
            return

        if tag in self._BLOCK_TAGS and self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return

        if self._skip_container_depth:
            self._skip_container_depth -= 1
            return

        if self._heading_tag:
            # End of current heading: keep or drop the heading line.
            if tag == self._heading_tag:
                heading_text = " ".join(self._heading_text_parts).strip()
                if heading_text and self._normalize_heading(heading_text) not in self._excluded_heading_norms:
                    if self._parts and not self._parts[-1].endswith("\n"):
                        self._parts.append("\n")
                    self._parts.append(heading_text)
                    self._parts.append("\n")

                self._heading_tag = None
                self._heading_text_parts = []
            return

        if tag in self._BLOCK_TAGS and self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._skip_container_depth:
            return
        text = data.strip()
        if text:
            if self._heading_tag:
                self._heading_text_parts.append(text)
                return
            self._parts.append(text)
            self._parts.append(" ")

    def get_text(self) -> str:
        return "".join(self._parts)


def _extract_title_from_html(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    title = unescape(match.group(1))
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _extract_clean_body_html(html: str) -> str:
    """Extract body HTML and remove scripts/styles/noscript blocks to reduce noise."""
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    body_content = body_match.group(1) if body_match else html

    body_content = re.sub(r"<script[^>]*>.*?</script>", "", body_content, flags=re.DOTALL | re.IGNORECASE)
    body_content = re.sub(r"<style[^>]*>.*?</style>", "", body_content, flags=re.DOTALL | re.IGNORECASE)

    return body_content


def _extract_text_from_body_html(
    body_html: str,
    *,
    exclude_section_headings: Optional[List[str]] = None,
    exclude_container_class_substrings: Optional[List[str]] = None,
) -> str:
    extractor = _BodyTextExtractor(
        exclude_section_headings=exclude_section_headings,
        exclude_container_class_substrings=exclude_container_class_substrings,
    )
    try:
        extractor.feed(body_html)
        extractor.close()
    except Exception:
        # HTML can be malformed; return best-effort text.
        pass

    raw = unescape(extractor.get_text())

    # Normalize whitespace while keeping some line structure.
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"\n[ \t]+", "\n", raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)

    lines = []
    for line in raw.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _truncate_for_discord_field_name(text: str, max_length: int = 256) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def _summarize_text_diff(old_text: str, new_text: str, *, context_lines: int = 2, max_changed_lines: int = 8) -> str:
    """
    Build a small, readable diff excerpt around the first detected change.

    Returned text is formatted as a Discord `diff` code block so removals/additions
    are shown in red/green (depending on client).
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    if old_lines == new_lines:
        return ""

    # Unified diff is the most Discord-friendly format: lines starting with
    # '-'/'+' get color-highlighted inside ```diff``` blocks.
    diff_iter = difflib.unified_diff(
        old_lines,
        new_lines,
        n=context_lines,
        lineterm="",
    )

    lines: List[str] = []
    hunk_started = False
    removed = 0
    added = 0
    truncated = False

    # Keep this comfortably under Discord's 1024 field limit, because we also
    # prepend title + URL in the field.
    max_total_lines = max(12, (context_lines * 2) + (max_changed_lines * 2) + 6)
    max_line_len = 220

    for line in diff_iter:
        # Drop file headers, keep only the first hunk body.
        if line.startswith("---") or line.startswith("+++"):
            continue

        if line.startswith("@@"):
            if hunk_started:
                break  # only first hunk
            hunk_started = True
            continue  # skip hunk header (line numbers are noise here)

        if not hunk_started:
            continue

        # Cap the number of +/- lines for readability
        if line.startswith("-"):
            if removed >= max_changed_lines:
                truncated = True
                continue
            removed += 1
        elif line.startswith("+"):
            if added >= max_changed_lines:
                truncated = True
                continue
            added += 1

        # Prevent overly long single lines from blowing up the embed field.
        if len(line) > max_line_len:
            line = line[: max_line_len - 3] + "..."
            truncated = True

        lines.append(line)
        if len(lines) >= max_total_lines:
            truncated = True
            break

    if not lines:
        return ""

    if truncated:
        # Start with a space so it won't be colored as +/- in diff blocks.
        lines.append("  ... (gekürzt)")

    return "```diff\n" + "\n".join(lines) + "\n```"


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
    
    # Try to extract Next.js data, fall back to HTML content if not available
    next_data = extract_next_data(html)
    
    # Check for Site6 bootstrap data fallback
    if not next_data and "visme.co" in page.url:
         site6_data = extract_site6_bootstrap_data(html)
         if site6_data:
             next_data = site6_data
             print(f"✅ Extracted Site6 data for {page.name}")
    if next_data:
        # Get the specific data we care about
        page_data = get_nested_value(next_data, page.data_path)
        if page_data is None:
            print(f"⚠️  Could not find data at path '{page.data_path}'")
            page_data = next_data  # Fall back to full data
    else:
        # No Next.js data - use HTML content hash for tracking
        print(f"ℹ️  No __NEXT_DATA__ on {page.name} - using HTML tracking")
        # Extract just the body content to reduce noise from headers/scripts
        import re as regex
        body_match = regex.search(r'<body[^>]*>(.*?)</body>', html, regex.DOTALL | regex.IGNORECASE)
        body_content = body_match.group(1) if body_match else html
        # Remove scripts and styles to focus on content
        body_content = regex.sub(r'<script[^>]*>.*?</script>', '', body_content, flags=regex.DOTALL | regex.IGNORECASE)
        body_content = regex.sub(r'<style[^>]*>.*?</style>', '', body_content, flags=regex.DOTALL | regex.IGNORECASE)
        page_data = {"_html_hash": hashlib.md5(body_content.encode()).hexdigest(), "_content_length": len(body_content)}
    
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


def track_sitemap_site5() -> bool:
    """Track WordPress XML sitemaps for Site5 to detect new pages."""
    print("\n📡 Tracking: Site5 XML Sitemaps")
    
    # List of all Site5 sitemaps to track
    sitemap_urls = [
        "https://innerscienceresearch.org/wp-sitemap-posts-page-1.xml",
        "https://innerscienceresearch.org/wp-sitemap-posts-post-1.xml",
        "https://innerscienceresearch.org/wp-sitemap-posts-sdm_downloads-1.xml",
        "https://innerscienceresearch.org/wp-sitemap-taxonomies-category-1.xml",
    ]
    
    all_urls = set()
    
    # Fetch all sitemaps and extract URLs
    for sitemap_url in sitemap_urls:
        content = fetch_page(sitemap_url)
        if content:
            # Extract URLs from XML
            import re as regex
            urls = regex.findall(r'<loc>(https?://[^<]+)</loc>', content)
            all_urls.update(urls)
    
    print(f"📊 Found {len(all_urls)} total URLs in Site5 sitemaps")
    
    # Load previous snapshot
    old_snapshot = load_snapshot("sitemap_site5")
    
    current_data = {
        "urls": sorted(list(all_urls)),
        "count": len(all_urls),
        "hash": hashlib.md5(str(sorted(all_urls)).encode()).hexdigest()
    }
    
    if old_snapshot is None:
        print(f"📝 First Site5 sitemap snapshot ({len(all_urls)} URLs)")
        save_snapshot("sitemap_site5", current_data)
        return False
    
    old_data = old_snapshot.get("data", {})
    old_urls = set(old_data.get("urls", []))
    
    # Check for new/removed URLs
    new_urls = all_urls - old_urls
    removed_urls = old_urls - all_urls
    
    changes_detected = False
    
    if new_urls:
        print(f"🆕 New Site5 pages detected: {len(new_urls)}")
        for url in list(new_urls)[:5]:
            print(f"   + {url}")
        changes_detected = True
        
        if DISCORD_WEBHOOK_URL:
            send_build_change_notification(
                DISCORD_WEBHOOK_URL,
                f"Site5: {len(old_urls)} pages",
                f"Site5: {len(all_urls)} pages (+{len(new_urls)} new)",
                sorted(list(new_urls))[:10]
            )
    
    if removed_urls:
        print(f"🗑️ Removed Site5 pages: {len(removed_urls)}")
        changes_detected = True
    
    if changes_detected:
        save_snapshot("sitemap_site5", current_data)
        return True
    
    print("✅ No Site5 sitemap changes")
    return False


def track_sitemap_site4() -> bool:
    """Track WordPress XML sitemaps for Site4 to detect new pages."""
    print("\n📡 Tracking: Site4 XML Sitemaps")
    
    sitemap_url = "https://metamorphllc.net/wp-sitemap.xml"
    content = fetch_page(sitemap_url)
    
    if not content:
        print("⚠️  Could not fetch Site4 sitemap")
        return False
    
    # Extract all sitemap URLs from index
    import re as regex
    sub_sitemaps = regex.findall(r'<loc>(https?://[^<]+\.xml)</loc>', content)
    
    all_urls = set()
    
    # Fetch each sub-sitemap and extract page URLs
    for sub_sitemap in sub_sitemaps:
        sub_content = fetch_page(sub_sitemap)
        if sub_content:
            urls = regex.findall(r'<loc>(https?://[^<]+)</loc>', sub_content)
            # Filter out .xml files to get actual page URLs
            page_urls = [u for u in urls if not u.endswith('.xml')]
            all_urls.update(page_urls)
    
    print(f"📊 Found {len(all_urls)} total URLs in Site4 sitemaps")
    
    # Load previous snapshot
    old_snapshot = load_snapshot("sitemap_site4")
    
    current_data = {
        "urls": sorted(list(all_urls)),
        "count": len(all_urls),
        "hash": hashlib.md5(str(sorted(all_urls)).encode()).hexdigest()
    }
    
    if old_snapshot is None:
        print(f"📝 First Site4 sitemap snapshot ({len(all_urls)} URLs)")
        save_snapshot("sitemap_site4", current_data)
        return False
    
    old_data = old_snapshot.get("data", {})
    old_urls = set(old_data.get("urls", []))
    
    new_urls = all_urls - old_urls
    removed_urls = old_urls - all_urls
    
    changes_detected = False
    
    if new_urls:
        print(f"🆕 New Site4 pages detected: {len(new_urls)}")
        changes_detected = True
        if DISCORD_WEBHOOK_URL:
            send_build_change_notification(
                DISCORD_WEBHOOK_URL,
                f"Site4: {len(old_urls)} pages",
                f"Site4: {len(all_urls)} pages (+{len(new_urls)} new)",
                sorted(list(new_urls))[:10]
            )
    
    if removed_urls:
        print(f"🗑️ Removed Site4 pages: {len(removed_urls)}")
        changes_detected = True
    
    if changes_detected:
        save_snapshot("sitemap_site4", current_data)
        return True
    
    print("✅ No Site4 sitemap changes")
    return False


def track_sitemap_site1() -> bool:
    """Track XML sitemap for Site1 (drjoedispenza.com) to detect new pages."""
    print("\n📡 Tracking: Site1 XML Sitemap")
    
    sitemap_url = "https://drjoedispenza.com/sitemap.xml"
    content = fetch_page(sitemap_url)
    
    if not content:
        print("⚠️  Could not fetch Site1 sitemap")
        return False
    
    # Extract all URLs from sitemap
    import re as regex
    all_urls = set(regex.findall(r'<loc>(https?://[^<]+)</loc>', content))
    
    print(f"📊 Found {len(all_urls)} total URLs in Site1 sitemap")
    
    # Load previous snapshot
    old_snapshot = load_snapshot("sitemap_site1")
    
    current_data = {
        "urls": sorted(list(all_urls)),
        "count": len(all_urls),
        "hash": hashlib.md5(str(sorted(all_urls)).encode()).hexdigest()
    }
    
    if old_snapshot is None:
        print(f"📝 First Site1 sitemap snapshot ({len(all_urls)} URLs)")
        save_snapshot("sitemap_site1", current_data)
        return False
    
    old_data = old_snapshot.get("data", {})
    old_urls = set(old_data.get("urls", []))
    
    # Check for new/removed URLs
    new_urls = all_urls - old_urls
    removed_urls = old_urls - all_urls
    
    changes_detected = False
    
    if new_urls:
        print(f"🆕 New Site1 pages detected: {len(new_urls)}")
        for url in list(new_urls)[:5]:
            print(f"   + {url}")
        changes_detected = True
        
        if DISCORD_WEBHOOK_URL:
            send_build_change_notification(
                DISCORD_WEBHOOK_URL,
                f"Site1: {len(old_urls)} pages",
                f"Site1: {len(all_urls)} pages (+{len(new_urls)} new)",
                sorted(list(new_urls))[:10]
            )
    
    if removed_urls:
        print(f"🗑️ Removed Site1 pages: {len(removed_urls)}")
        for url in list(removed_urls)[:5]:
            print(f"   - {url}")
        changes_detected = True
        
        if DISCORD_WEBHOOK_URL:
            send_removed_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Sitemap",
                "https://drjoedispenza.com/sitemap.xml",
                [{"url": u} for u in sorted(list(removed_urls))[:10]]
            )
    
    if changes_detected:
        save_snapshot("sitemap_site1", current_data)
        return True
    
    print("✅ No Site1 sitemap changes")
    return False


def track_sitemap_content_site1() -> bool:
    """
    Track CONTENT CHANGES on Site1 pages from sitemap.
    Excludes blog posts and stories of transformation to reduce load.
    """
    print("\n📡 Tracking: Site1 Page Content (filtered)")
    
    # Load sitemap snapshot to get URLs
    sitemap_snapshot = load_snapshot("sitemap_site1")
    if not sitemap_snapshot:
        print("⚠️  No sitemap snapshot found - run sitemap tracking first")
        return False
    
    all_urls = sitemap_snapshot.get("data", {}).get("urls", [])
    
    # Filter out blogs, stories, and individual product pages (too many)
    # Shop page already tracks all products
    EXCLUDE_PATTERNS = [
        "/dr-joes-blog/",
        "/stories-of-transformation/",
        "/product-details/",
    ]
    
    filtered_urls = [
        url for url in all_urls 
        if not any(pattern in url for pattern in EXCLUDE_PATTERNS)
    ]
    
    print(f"📊 Tracking content on {len(filtered_urls)} pages (excluded {len(all_urls) - len(filtered_urls)} blog/story posts)")
    
    # Load previous content hashes
    old_snapshot = load_snapshot("content_site1")
    old_data = old_snapshot.get("data", {}) if old_snapshot else {}
    old_hashes = old_data.get("hashes", {}) or {}
    old_text_hashes = old_data.get("text_hashes", {}) or {}
    old_texts = old_data.get("texts", {}) or {}
    old_titles = old_data.get("titles", {}) or {}
    old_excluded_headings = old_data.get("exclude_section_headings")
    old_excluded_classes = old_data.get("exclude_html_class_substrings")

    current_excluded_headings = SITE1_CONTENT_EXCLUDE_SECTION_HEADINGS
    current_excluded_classes = SITE1_CONTENT_EXCLUDE_HTML_CLASS_SUBSTRINGS
    baseline_reset = (
        old_snapshot is not None
        and (
            old_excluded_headings != current_excluded_headings
            or old_excluded_classes != current_excluded_classes
        )
    )
    if baseline_reset:
        print(
            "ℹ️  Site1-Content filter settings changed - updating baseline and skipping notifications for this run"
        )
    
    new_hashes = {}
    new_text_hashes = {}
    new_texts = {}
    new_titles = {}
    changes = []
    errors = []
    
    import time
    
    for i, url in enumerate(filtered_urls):
        # Progress indicator every 50 pages
        if i > 0 and i % 50 == 0:
            print(f"   Progress: {i}/{len(filtered_urls)} pages...")
        
        html = fetch_page(url)
        if not html:
            errors.append(url)
            continue
        
        # Legacy hash: cleaned body HTML (keeps compatibility with existing snapshots)
        clean_body_html = _extract_clean_body_html(html)
        html_hash = hashlib.md5(clean_body_html.encode()).hexdigest()
        new_hashes[url] = html_hash

        # Text extraction for meaningful diffs + future (less noisy) comparisons
        title = _extract_title_from_html(html) or old_titles.get(url, "")
        new_titles[url] = title

        extracted_text_full = _extract_text_from_body_html(
            clean_body_html,
            exclude_section_headings=current_excluded_headings,
            exclude_container_class_substrings=current_excluded_classes,
        )
        text_hash = hashlib.md5(extracted_text_full.encode()).hexdigest()
        new_text_hashes[url] = text_hash
        new_texts[url] = extracted_text_full

        # Check if content changed
        old_text_hash = old_text_hashes.get(url)
        if not baseline_reset and old_text_hash is not None and old_text_hash != text_hash:
            changes.append(url)
        
        # Rate limiting: small delay to avoid hammering server
        time.sleep(0.1)
    
    print(f"   ✅ Fetched {len(new_hashes)} pages, {len(errors)} errors")
    
    # Report changes
    changes_detected = False
    
    if changes and not baseline_reset:
        print(f"🔄 Content changed on {len(changes)} pages:")
        for url in changes[:DISCORD_MAX_CHANGES]:
            print(f"   ~ {url}")
        changes_detected = True
        
        if DISCORD_WEBHOOK_URL:
            updates: List[Dict[str, Any]] = []
            for url in changes[:DISCORD_MAX_CHANGES]:
                title = new_titles.get(url) or old_titles.get(url, "")
                details_lines: List[str] = []
                if title:
                    details_lines.append(f"**{title}**")
                details_lines.append(f"URL: {url}")

                old_text = old_texts.get(url)
                new_text = new_texts.get(url, "")
                if old_text:
                    # Feinschliff: keep Discord output compact by showing only changed lines (+/-),
                    # without unchanged context lines.
                    diff_summary = _summarize_text_diff(old_text, new_text, context_lines=0)
                    if diff_summary:
                        details_lines.append("Diff (rot = entfernt, grün = neu):")
                        details_lines.append(diff_summary)
                    else:
                        details_lines.append("Hinweis: Kein Textunterschied erkennbar (evtl. nur HTML/Struktur).")
                else:
                    details_lines.append("Hinweis: Text-Baseline wurde neu erstellt; Diff ist ab dem nächsten Lauf verfügbar.")

                updates.append({
                    "id": url,
                    "field": "content",
                    "type": _truncate_for_discord_field_name(f"📝 Content: {title}" if title else "📝 Content geändert"),
                    "details": "\n".join(details_lines),
                })

            send_updated_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Content",
                "https://drjoedispenza.com",
                updates
            )
    
    # Save new hashes
    current_data = {
        "hashes": new_hashes,
        "text_hashes": new_text_hashes,
        "texts": new_texts,
        "titles": new_titles,
        "exclude_section_headings": current_excluded_headings,
        "exclude_html_class_substrings": current_excluded_classes,
        "count": len(new_hashes),
        "tracked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }
    save_snapshot("content_site1", current_data)
    
    if not changes and old_snapshot:
        print("✅ No content changes detected")
    elif not old_snapshot:
        print(f"📝 First content snapshot ({len(new_hashes)} pages)")
    
    return changes_detected


def track_youtube_channel() -> bool:
    """
    Track YouTube channel for new video uploads via RSS feed.
    The RSS feed returns the latest 15 videos - no API key needed.
    """
    print("\n📡 Tracking: YouTube-DrJoeDispenza")
    
    # YouTube RSS feed URL
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
    
    # Fetch RSS feed
    feed_content = fetch_page(feed_url)
    if not feed_content:
        print("⚠️  Could not fetch YouTube RSS feed")
        return False
    
    # Parse video entries from XML using regex (keeps dependencies minimal)
    # Extract: <yt:videoId>, <title>, <published>, <media:thumbnail url="...">
    video_entries = re.findall(
        r'<entry>(.*?)</entry>',
        feed_content,
        re.DOTALL
    )
    
    videos = []
    for entry in video_entries:
        video_id_match = re.search(r'<yt:videoId>([^<]+)</yt:videoId>', entry)
        title_match = re.search(r'<title>([^<]+)</title>', entry)
        published_match = re.search(r'<published>([^<]+)</published>', entry)
        thumbnail_match = re.search(r'<media:thumbnail url="([^"]+)"', entry)
        
        if video_id_match:
            videos.append({
                "video_id": video_id_match.group(1),
                "title": unescape(title_match.group(1)) if title_match else "Unbekannt",
                "published": published_match.group(1) if published_match else "",
                "thumbnail_url": thumbnail_match.group(1) if thumbnail_match else ""
            })
    
    print(f"📊 Found {len(videos)} videos in RSS feed")
    
    if not videos:
        print("⚠️  No videos found in feed")
        return False
    
    # Load previous snapshot
    old_snapshot = load_snapshot("youtube_drjoedispenza")
    
    # Current video IDs
    current_video_ids = {v["video_id"] for v in videos}
    
    current_data = {
        "video_ids": sorted(list(current_video_ids)),
        "videos": videos,  # Store full video data for reference
        "count": len(videos),
        "last_checked": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }
    
    if old_snapshot is None:
        print(f"📝 First YouTube snapshot ({len(videos)} videos)")
        save_snapshot("youtube_drjoedispenza", current_data)
        return False
    
    old_data = old_snapshot.get("data", {})
    old_video_ids = set(old_data.get("video_ids", []))
    
    # Find new videos
    new_video_ids = current_video_ids - old_video_ids
    
    if not new_video_ids:
        print("✅ No new YouTube videos")
        return False
    
    # Get full video info for new videos
    new_videos = [v for v in videos if v["video_id"] in new_video_ids]
    
    print(f"🆕 New YouTube videos detected: {len(new_videos)}")
    for video in new_videos:
        print(f"   + {video['title'][:50]}...")
    
    # Send Discord notification
    if DISCORD_WEBHOOK_URL:
        send_new_youtube_video_notification(DISCORD_WEBHOOK_URL, new_videos)
    else:
        print("⚠️  No Discord webhook configured - skipping notification")
    
    # Save updated snapshot
    save_snapshot("youtube_drjoedispenza", current_data)
    
    return True

def main():
    """Main entry point."""
    # Prevent UnicodeEncodeError on Windows consoles (e.g. cp1252) when printing emojis.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

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
    
    # Track XML sitemaps for Site4 (WordPress - detects new pages)
    try:
        if track_sitemap_site4():
            changes_detected = True
    except Exception as e:
        print(f"❌ Error tracking Site4 sitemaps: {e}")
    
    # Track XML sitemaps for Site5 (WordPress - detects new pages)
    try:
        if track_sitemap_site5():
            changes_detected = True
    except Exception as e:
        print(f"❌ Error tracking Site5 sitemaps: {e}")
    
    # Track XML sitemap for Site1 (drjoedispenza.com - detects new/removed pages)
    try:
        if track_sitemap_site1():
            changes_detected = True
    except Exception as e:
        print(f"❌ Error tracking Site1 sitemap: {e}")
    
    # Track CONTENT changes on Site1 pages (excludes blogs, stories, product-details)
    try:
        if track_sitemap_content_site1():
            changes_detected = True
    except Exception as e:
        print(f"❌ Error tracking Site1 content: {e}")
    
    # Track YouTube channel for new videos
    try:
        if track_youtube_channel():
            changes_detected = True
    except Exception as e:
        print(f"❌ Error tracking YouTube channel: {e}")
    
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
