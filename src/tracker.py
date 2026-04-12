# Main Tracker Script for Website Change Monitoring

import difflib
import copy
import gzip
import json
import os
import re
import sys
import hashlib
import secrets
import zlib
from collections import Counter
from datetime import datetime, timezone
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
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
    send_new_route_with_content_notification,
    send_pending_route_now_live_notification,
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


def fetch_json(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[Any] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Optional[Any]:
    """Fetch JSON from a URL."""
    request_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
    if headers:
        request_headers.update(headers)

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    try:
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()

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

            return json.loads(raw.decode(charset or "utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        print(f"âŒ HTTP error fetching JSON {url}: {e.code} {body[:200]}")
        return None
    except urllib.error.URLError as e:
        print(f"âŒ URL error fetching JSON {url}: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        print(f"âŒ JSON decode error fetching {url}: {e}")
        return None
    except Exception as e:
        print(f"âŒ Error fetching JSON {url}: {e}")
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
        exclude_container_id_substrings: Optional[List[str]] = None,
        exclude_container_tags: Optional[List[str]] = None,
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
        self._exclude_id_substrings = [
            s.strip().lower()
            for s in (exclude_container_id_substrings or [])
            if s and s.strip()
        ]
        self._exclude_tags = {
            tag.strip().lower()
            for tag in (exclude_container_tags or [])
            if tag and tag.strip()
        }

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return

        # Skip entire containers based on class name markers (used to exclude noisy widgets).
        if self._skip_container_depth:
            self._skip_container_depth += 1
            return

        if tag in self._exclude_tags:
            self._skip_container_depth = 1
            return

        if self._exclude_class_substrings:
            class_attr = None
            id_attr = None
            data_no_snippet = None
            for name, value in attrs:
                if not name or not value:
                    continue
                lowered_name = name.lower()
                lowered_value = value.lower()
                if lowered_name == "class":
                    class_attr = lowered_value
                elif lowered_name == "id":
                    id_attr = lowered_value
                elif lowered_name == "data-nosnippet":
                    data_no_snippet = lowered_value

            if class_attr and any(sub in class_attr for sub in self._exclude_class_substrings):
                self._skip_container_depth = 1
                return
            if self._exclude_id_substrings and id_attr and any(
                sub in id_attr for sub in self._exclude_id_substrings
            ):
                self._skip_container_depth = 1
                return
            if data_no_snippet == "true":
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
    title = _repair_common_utf8_mojibake(title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _repair_common_utf8_mojibake(text: str) -> str:
    """Repair common UTF-8 mojibake that leaks through mislabelled HTML responses."""
    if not text:
        return text

    markers = ("â€™", "â€œ", "â€", "â€“", "â€”", "â€¦", "Ã", "Â")
    marker_hits = sum(text.count(marker) for marker in markers)
    if marker_hits == 0:
        return text

    best_text = text
    best_hits = marker_hits
    for source_encoding in ("cp1252", "latin-1"):
        try:
            repaired = text.encode(source_encoding).decode("utf-8")
        except Exception:
            continue

        repaired_hits = sum(repaired.count(marker) for marker in markers)
        if repaired_hits < best_hits:
            best_text = repaired
            best_hits = repaired_hits

    return best_text


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
    exclude_container_id_substrings: Optional[List[str]] = None,
    exclude_container_tags: Optional[List[str]] = None,
) -> str:
    extractor = _BodyTextExtractor(
        exclude_section_headings=exclude_section_headings,
        exclude_container_class_substrings=exclude_container_class_substrings,
        exclude_container_id_substrings=exclude_container_id_substrings,
        exclude_container_tags=exclude_container_tags,
    )
    try:
        extractor.feed(body_html)
        extractor.close()
    except Exception:
        # HTML can be malformed; return best-effort text.
        pass

    raw = _repair_common_utf8_mojibake(unescape(extractor.get_text()))

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


def _extract_route_preview_text_from_html(html: str) -> str:
    """
    Extract a concise route preview from HTML.

    Strategy:
    1) Prefer <main> content when available.
    2) Otherwise remove header/nav/footer wrappers to avoid shell-only previews.
    3) Fall back to full body extraction.
    """
    clean_body = _extract_clean_body_html(html)

    main_match = re.search(r"<main[^>]*>(.*?)</main>", clean_body, re.DOTALL | re.IGNORECASE)
    if main_match:
        main_text = _extract_text_from_body_html(main_match.group(1))
        if main_text:
            return main_text

    without_shell = re.sub(
        r"<(header|nav|footer)\b[^>]*>.*?</\1>",
        "",
        clean_body,
        flags=re.DOTALL | re.IGNORECASE,
    )
    shell_reduced_text = _extract_text_from_body_html(without_shell)
    if shell_reduced_text:
        return shell_reduced_text

    return _extract_text_from_body_html(clean_body)


_HTML_TRACKING_VERSION = 2
_HTML_TRACKING_EXCLUDED_CLASS_SUBSTRINGS = [
    "cookie",
    "consent",
    "cli-",
    "wt-cli",
    "gform",
    "gfield",
    "sharedaddy",
    "et_pb_social_media_follow",
    "et_pb_member_social_links",
    "et_pb_newsletter",
]
_HTML_TRACKING_EXCLUDED_ID_SUBSTRINGS = [
    "cookie",
    "consent",
    "cli",
]
_HTML_TRACKING_EXCLUDED_TAGS = [
    "header",
    "nav",
    "footer",
    "form",
]


def _extract_stable_html_tracking_text(html: str) -> str:
    """Extract stable human-readable page text while excluding shell and widget noise."""
    clean_body_html = _extract_clean_body_html(html)

    main_match = re.search(r"<main[^>]*>(.*?)</main>", clean_body_html, re.DOTALL | re.IGNORECASE)
    target_html = main_match.group(1) if main_match else clean_body_html

    stable_text = _extract_text_from_body_html(
        target_html,
        exclude_container_class_substrings=_HTML_TRACKING_EXCLUDED_CLASS_SUBSTRINGS,
        exclude_container_id_substrings=_HTML_TRACKING_EXCLUDED_ID_SUBSTRINGS,
        exclude_container_tags=_HTML_TRACKING_EXCLUDED_TAGS,
    )
    if stable_text:
        return stable_text

    # Final fallback in case aggressive filtering stripped everything.
    return _extract_text_from_body_html(clean_body_html)


def _build_html_tracking_snapshot_data(html: str) -> Dict[str, Any]:
    """Build a text-based snapshot for plain HTML pages."""
    extracted_text = _extract_stable_html_tracking_text(html)
    return {
        "_trackingMode": "html_text",
        "_trackingVersion": _HTML_TRACKING_VERSION,
        "title": _extract_title_from_html(html),
        "textHash": hashlib.md5(extracted_text.encode()).hexdigest(),
        "text": extracted_text,
        "contentPreview": extracted_text[:1500] if extracted_text else "",
    }


# Site1 product-detail pages (drjoedispenza.com) load product text client-side via
# MongoDB App Services. Calling the same function gives us meaningful preview text.
_SITE1_REALM_BASE_URLS = [
    "https://us-east-1.aws.services.cloud.mongodb.com/api/client/v2.0/app/production-lzmdf",
    "https://services.cloud.mongodb.com/api/client/v2.0/app/production-lzmdf",
]
_SITE1_APP_RUNNER_BASE_URL = "https://8jmuszggp2.us-east-1.awsapprunner.com/api/v1"
_SITE1_SHOP_URL = "https://drjoedispenza.com/shop/categories?shopSection=All%20Products"
_SITE1_SHOP_COLLECTION_STATUSES = ["Active", "Coming Soon"]
_SITE1_PUBLIC_POLICY_IDS = [
    {
        "_id": "6386285ce0a78f695e6ac6d1",
        "reference_url": "https://drjoedispenza.com/shipping-and-returns-policy",
    }
]
_SITE2_BASE_URL = "https://drjoedispenza.info/s/Drjoedispenza"
_SITE2_DISCOVERY_URLS = [
    _SITE2_BASE_URL,
    f"{_SITE2_BASE_URL}/produkte",
    f"{_SITE2_BASE_URL}/dispenza-onlinekurse",
    f"{_SITE2_BASE_URL}/blog",
]
_MYMM_REALM_BASE_URL = "https://us-east-1.aws.realm.mongodb.com/api/client/v2.0/app/production-lzmdf"
_MYMM_EVENTS_REFERENCE_URL = "https://events.drjoedispenza.com/"
_MYMM_APP_LOGIN_PAYLOAD = {
    "application": "mymm",
    "options": {
        "device": {
            "sdkVersion": "1.7.0",
            "platform": "react-native",
            "platformVersion": "0.0.0",
        }
    },
}


def _call_site1_realm_function(function_name: str, arguments: List[Any]) -> Optional[Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    for base_url in _SITE1_REALM_BASE_URLS:
        try:
            login_req = urllib.request.Request(
                f"{base_url}/auth/providers/anon-user/login",
                data=b"{}",
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(login_req, timeout=20) as login_response:
                login_text = login_response.read().decode("utf-8", errors="replace")
            login_data = json.loads(login_text)

            access_token = login_data.get("access_token")
            if not access_token:
                continue

            payload = {
                "name": function_name,
                "arguments": arguments,
                "service": "mainCluster",
            }
            call_req = urllib.request.Request(
                f"{base_url}/functions/call",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    **headers,
                    "Authorization": f"Bearer {access_token}",
                },
                method="POST",
            )
            with urllib.request.urlopen(call_req, timeout=20) as call_response:
                call_text = call_response.read().decode("utf-8", errors="replace")
            return json.loads(call_text)
        except urllib.error.HTTPError as e:
            # Region mismatch: try next base URL.
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            if e.code == 404 and "App not available in requested region" in body:
                continue
        except Exception:
            continue

    return None


def _extract_object_id(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("$oid", "_id", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def _fetch_site1_collection_product_ids() -> Optional[Tuple[List[str], List[str]]]:
    public_catalog = _fetch_site1_public_catalog_snapshot_data()
    if public_catalog:
        product_ids = set()
        collection_names = set()
        for item in public_catalog.get("items", []):
            if not isinstance(item, dict):
                continue
            product_id = str(item.get("_id") or "").strip()
            if product_id:
                product_ids.add(product_id)
            for category in item.get("categories", []) or []:
                category_name = str(category or "").strip()
                if category_name:
                    collection_names.add(category_name)

        if product_ids:
            return sorted(product_ids), sorted(collection_names)

    collections_payload = _call_site1_realm_function(
        "fetchProductCollections",
        [1, 200, None, "", _SITE1_SHOP_COLLECTION_STATUSES, []],
    )
    if not isinstance(collections_payload, list):
        return None

    collection_names: List[str] = []
    product_ids = set()
    for collection in collections_payload:
        if not isinstance(collection, dict):
            continue

        collection_name = str(collection.get("name") or "").strip()
        if collection_name:
            collection_names.append(collection_name)

        products = collection.get("products")
        if not isinstance(products, list):
            continue

        for product_ref in products:
            product_id = _extract_object_id(product_ref)
            if product_id:
                product_ids.add(product_id)

    return sorted(product_ids), sorted(set(collection_names))


def _project_site1_inventory_variant(variant: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(variant, dict):
        return None

    projected = {
        "variantId": variant.get("variantId"),
        "options": variant.get("options"),
        "sku": variant.get("sku"),
        "barcode": variant.get("barcode"),
        "pricing": variant.get("pricing"),
        "inventoryCost": variant.get("inventoryCost"),
    }
    return normalize_data(projected)


def _build_site1_product_page_url(title_value: Any) -> str:
    title = str(title_value or "").strip()
    if not title:
        return _SITE1_SHOP_URL
    return "https://drjoedispenza.com/product-details/" + urllib.parse.quote(title, safe="")


def _project_site1_inventory_product(product: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(product, dict):
        return None

    product_id = _extract_object_id(product.get("_id"))
    if not product_id:
        return None

    product_details = product.get("productDetails")
    short_description = ""
    if isinstance(product_details, dict):
        short_description = str(product_details.get("shortDescription") or "").strip()

    content_preview = _build_site1_product_preview_text(product)

    variants: List[Dict[str, Any]] = []
    raw_variants = product.get("variants")
    if isinstance(raw_variants, list):
        for raw_variant in raw_variants:
            projected_variant = _project_site1_inventory_variant(raw_variant)
            if projected_variant:
                variants.append(projected_variant)
        variants.sort(key=lambda item: str(item.get("variantId") or ""))

    projected = normalize_data(
        {
        "_id": product_id,
        "title": product.get("title"),
        "status": product.get("status"),
        "type": product.get("type"),
        "availableOnUnlimited": product.get("availableOnUnlimited"),
        "visibleOnUnlimited": product.get("visibleOnUnlimited"),
        "categories": product.get("categories"),
        "collections": product.get("collections"),
        "pricing": product.get("pricing"),
        "options": product.get("options"),
        "variants": variants,
        "restrictions": product.get("restrictions"),
        "images": product.get("images"),
        "originalPublishedDate": product.get("originalPublishedDate"),
        "shortDescription": short_description,
        "url": _build_site1_product_page_url(product.get("title")),
        }
    )
    if content_preview:
        projected["contentPreview"] = content_preview
    return projected


def _fetch_site1_inventory_snapshot_data() -> Optional[Dict[str, Any]]:
    collection_result = _fetch_site1_collection_product_ids()
    if not collection_result:
        return None

    product_ids, collection_names = collection_result
    if not product_ids:
        return None

    response = _post_site1_app_runner_json(
        "users/record",
        {
            "collectionName": "products",
            "query": {},
            "isPublic": True,
        },
    )
    if not isinstance(response, dict):
        return None

    response_data = response.get("data")
    if not isinstance(response_data, list):
        return None

    visible_product_ids = set(product_ids)
    products: List[Dict[str, Any]] = []
    for raw_product in response_data:
        if not isinstance(raw_product, dict):
            continue
        product_id = _extract_object_id(raw_product.get("_id"))
        if not product_id or product_id not in visible_product_ids:
            continue
        projected_product = _project_site1_inventory_product(raw_product)
        if projected_product:
            products.append(projected_product)

    products.sort(key=lambda item: str(item.get("_id") or ""))

    return {
        "products": products,
        "productIds": product_ids,
        "collectionNames": collection_names,
        "count": len(products),
    }


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    if isinstance(value, dict):
        for key in ("$numberInt", "$numberLong"):
            raw_value = value.get(key)
            if isinstance(raw_value, str) and raw_value.strip().lstrip("-").isdigit():
                return int(raw_value.strip())
    return None


def _build_site1_community_group_url(title_value: Any) -> str:
    title = str(title_value or "").strip()
    if not title:
        return "https://drjoedispenza.com/community"
    return "https://drjoedispenza.com/community-group/" + urllib.parse.quote(title, safe="")


def _post_site1_app_runner_json(path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    response = fetch_json(
        f"{_SITE1_APP_RUNNER_BASE_URL}/{path.lstrip('/')}",
        method="POST",
        payload=payload,
    )
    if not isinstance(response, dict):
        return None
    return response


def _get_site1_app_runner_json(path: str) -> Optional[Dict[str, Any]]:
    response = fetch_json(f"{_SITE1_APP_RUNNER_BASE_URL}/{path.lstrip('/')}")
    if not isinstance(response, dict):
        return None
    return response


def _load_site1_inventory_item_ids() -> set[str]:
    inventory_ids: set[str] = set()
    old_snapshot = load_snapshot("site1_inventory_api")
    old_data = old_snapshot.get("data", {}) if old_snapshot else {}
    old_products = old_data.get("products", []) if isinstance(old_data, dict) else []

    for product in old_products:
        if not isinstance(product, dict):
            continue
        product_id = str(product.get("_id") or "").strip()
        if product_id:
            inventory_ids.add(product_id)

    if inventory_ids:
        return inventory_ids

    current_inventory = _fetch_site1_inventory_snapshot_data()
    if not current_inventory:
        return inventory_ids

    for product in current_inventory.get("products", []):
        if not isinstance(product, dict):
            continue
        product_id = str(product.get("_id") or "").strip()
        if product_id:
            inventory_ids.add(product_id)

    return inventory_ids


def _project_site1_public_catalog_product(product: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(product, dict):
        return None

    product_id = _extract_object_id(product.get("_id"))
    if not product_id:
        return None

    variants: List[Dict[str, Any]] = []
    raw_variants = product.get("variants")
    if isinstance(raw_variants, list):
        for raw_variant in raw_variants:
            projected_variant = _project_site1_inventory_variant(raw_variant)
            if projected_variant:
                variants.append(projected_variant)
        variants.sort(key=lambda item: str(item.get("variantId") or ""))

    projected = {
        "_id": product_id,
        "title": str(product.get("title") or "").strip(),
        "status": product.get("status"),
        "type": product.get("type"),
        "categories": sorted(str(value) for value in (product.get("categories") or []) if str(value).strip()),
        "pricing": product.get("pricing"),
        "variants": variants,
        "restrictions": product.get("restrictions"),
        "images": product.get("images"),
        "score": product.get("score"),
        "url": _build_site1_product_page_url(product.get("title")),
    }
    return normalize_data(projected)


def _fetch_site1_public_catalog_snapshot_data() -> Optional[Dict[str, Any]]:
    products: List[Dict[str, Any]] = []
    page = 1
    page_size = 50
    total_count = 0

    while True:
        response = _post_site1_app_runner_json(
            "products/fetchProducts",
            {
                "shopByType": "categories",
                "shopSection": "all products",
                "page": page,
                "paginationSize": page_size,
                "sort": {"originalPublishedDate": -1},
                "filters": [],
                "searchTerm": "",
                "status": ["Active", "Coming Soon"],
            },
        )
        if not response:
            break

        response_data = response.get("data")
        if not isinstance(response_data, list) or not response_data or not isinstance(response_data[0], dict):
            break

        first_block = response_data[0]
        metadata = first_block.get("metadata")
        if isinstance(metadata, list) and metadata and isinstance(metadata[0], dict):
            total_count = _coerce_int(metadata[0].get("total")) or total_count

        page_items = first_block.get("data")
        if not isinstance(page_items, list) or not page_items:
            break

        for raw_product in page_items:
            projected_product = _project_site1_public_catalog_product(raw_product)
            if projected_product:
                products.append(projected_product)

        if total_count and len(products) >= total_count:
            break
        if len(page_items) < page_size:
            break
        page += 1

    if not products:
        return None

    product_map = {str(item.get("_id") or ""): item for item in products if str(item.get("_id") or "").strip()}
    ordered_products = sorted(product_map.values(), key=lambda item: str(item.get("_id") or ""))

    return {
        "items": ordered_products,
        "count": len(ordered_products),
        "typeCounts": dict(sorted(Counter(str(item.get("type") or "") for item in ordered_products).items())),
    }


def _project_site1_public_category(category: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(category, dict):
        return None

    category_id = _extract_object_id(category.get("_id"))
    if not category_id:
        return None

    return normalize_data(
        {
            "_id": category_id,
            "title": str(category.get("title") or "").strip(),
            "status": category.get("status"),
            "editable": category.get("editable"),
            "descriptionPreview": str(category.get("description") or "").strip(),
            "url": _SITE1_SHOP_URL,
        }
    )


def _fetch_site1_public_categories_snapshot_data() -> Optional[Dict[str, Any]]:
    response = _post_site1_app_runner_json(
        "users/record",
        {
            "collectionName": "categories",
            "query": {"status": "Active"},
            "isPublic": True,
        },
    )
    if not response:
        return None

    response_data = response.get("data")
    if not isinstance(response_data, list):
        return None

    items: List[Dict[str, Any]] = []
    for raw_category in response_data:
        projected_category = _project_site1_public_category(raw_category)
        if projected_category:
            items.append(projected_category)

    items.sort(key=lambda item: str(item.get("title") or ""))
    return {
        "items": items,
        "count": len(items),
    }


def _project_site1_subscription(subscription: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(subscription, dict):
        return None

    subscription_id = _extract_object_id(subscription.get("_id"))
    if not subscription_id:
        return None

    recommended_products = subscription.get("recommendedProducts")
    recommended_product_ids: List[str] = []
    if isinstance(recommended_products, list):
        for product_ref in recommended_products:
            if isinstance(product_ref, dict):
                product_id = _extract_object_id(product_ref.get("objId") or product_ref.get("_id") or product_ref.get("id"))
                if product_id:
                    recommended_product_ids.append(product_id)

    return normalize_data(
        {
            "_id": subscription_id,
            "title": str(subscription.get("title") or "").strip(),
            "status": subscription.get("status"),
            "type": subscription.get("type"),
            "shortDescription": str(subscription.get("shortDescription") or "").strip(),
            "annual": subscription.get("annual"),
            "monthly": subscription.get("monthly"),
            "options": subscription.get("options"),
            "categories": subscription.get("categories"),
            "variants": subscription.get("variants"),
            "samples": subscription.get("samples"),
            "availableOnUnlimited": subscription.get("availableOnUnlimited"),
            "recommendedProductIds": sorted(set(recommended_product_ids)),
            "url": "https://drjoedispenza.com/dr-joe-live",
        }
    )


def _fetch_site1_subscriptions_snapshot_data() -> Optional[Dict[str, Any]]:
    response = _post_site1_app_runner_json(
        "collection/find-record",
        {
            "collection": "subscriptions",
            "query": {"type": "drJoeLive"},
        },
    )
    if not response:
        return None

    projected_subscription = _project_site1_subscription(response.get("data"))
    if not projected_subscription:
        return None

    return {
        "items": [projected_subscription],
        "count": 1,
    }


def _project_site1_policy(policy: Any, *, reference_url: str) -> Optional[Dict[str, Any]]:
    if not isinstance(policy, dict):
        return None

    policy_id = _extract_object_id(policy.get("_id"))
    if not policy_id:
        return None

    sections = policy.get("sections")
    normalized_sections: List[Dict[str, Any]] = []
    content_fragments: List[str] = []
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            title = str(section.get("title") or "").strip()
            content_html = section.get("content")
            if isinstance(content_html, str) and content_html.strip():
                content_fragments.append(content_html)
            normalized_sections.append(
                {
                    "title": title,
                    "contentPreview": _extract_text_preview_from_html_fragment(content_html),
                }
            )

    return normalize_data(
        {
            "_id": policy_id,
            "title": str(policy.get("title") or "").strip(),
            "sections": normalized_sections,
            "contentPreview": _extract_text_preview_from_html_fragment("".join(content_fragments)),
            "url": reference_url,
        }
    )


def _fetch_site1_policies_snapshot_data() -> Optional[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for policy_info in _SITE1_PUBLIC_POLICY_IDS:
        response = _post_site1_app_runner_json(
            "collection/find-record",
            {
                "collection": "policiesAndDisclaimers",
                "query": {"_id": policy_info["_id"]},
            },
        )
        if not response:
            continue
        projected_policy = _project_site1_policy(
            response.get("data"),
            reference_url=policy_info["reference_url"],
        )
        if projected_policy:
            items.append(projected_policy)

    if not items:
        return None

    items.sort(key=lambda item: str(item.get("title") or ""))
    return {
        "items": items,
        "count": len(items),
    }


def _project_site1_community_group(group: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(group, dict):
        return None

    group_id = _extract_object_id(group.get("_id"))
    if not group_id:
        return None

    next_conversation = group.get("nextConversation") if isinstance(group.get("nextConversation"), dict) else {}
    previous_conversation = (
        group.get("previousConversation") if isinstance(group.get("previousConversation"), dict) else {}
    )
    personalized_message = (
        group.get("personalizedMessage") if isinstance(group.get("personalizedMessage"), dict) else {}
    )
    title = str(group.get("title") or "").strip()

    return normalize_data(
        {
            "_id": group_id,
            "title": title,
            "status": group.get("status"),
            "restriction": group.get("restriction"),
            "locked": group.get("locked"),
            "reason": str(group.get("reason") or "").strip(),
            "descriptionPreview": str(group.get("description") or "").strip(),
            "image": group.get("image"),
            "bannerImage": group.get("bannerImage"),
            "nextConversationDate": str(next_conversation.get("conversationDate") or "").strip(),
            "nextConversationTime": str(next_conversation.get("time") or "").strip(),
            "nextConversationTimeZone": str(next_conversation.get("timeZone") or "").strip(),
            "nextConversationIsTbd": next_conversation.get("isTbd"),
            "nextConversationBrightCoveId": str(next_conversation.get("brightCoveId") or "").strip(),
            "previousConversationDate": str(previous_conversation.get("conversationDate") or "").strip(),
            "previousConversationBrightCoveId": str(previous_conversation.get("brightCoveId") or "").strip(),
            "personalizedMessageTitle": str(personalized_message.get("title") or "").strip(),
            "personalizedMessageUpdatedAt": str(personalized_message.get("lastModified") or "").strip(),
            "url": _build_site1_community_group_url(title),
        }
    )


def _fetch_site1_community_groups_snapshot_data() -> Optional[Dict[str, Any]]:
    response = _post_site1_app_runner_json(
        "users/record",
        {
            "collectionName": "communityGroups",
            "query": {},
            "isPublic": True,
        },
    )
    if not response:
        return None

    response_data = response.get("data")
    if not isinstance(response_data, list):
        return None

    items: List[Dict[str, Any]] = []
    for raw_group in response_data:
        projected_group = _project_site1_community_group(raw_group)
        if projected_group:
            items.append(projected_group)

    items.sort(key=lambda item: str(item.get("title") or ""))
    return {
        "items": items,
        "count": len(items),
    }


def _fetch_site1_routing_config_snapshot_data() -> Optional[Dict[str, Any]]:
    response = _get_site1_app_runner_json("routing-config")
    if not response:
        return None

    data = response.get("data")
    if not isinstance(data, dict):
        return None

    function_overrides = data.get("functionOverrides")
    if not isinstance(function_overrides, dict):
        function_overrides = {}

    user_overrides = data.get("userOverrides")
    if not isinstance(user_overrides, dict):
        user_overrides = {}

    user_fallback_counts: Counter[str] = Counter()
    for override in user_overrides.values():
        if not isinstance(override, dict):
            continue
        fallback_name = str(override.get("globalFallback") or "").strip() or "__unknown__"
        user_fallback_counts[fallback_name] += 1

    items: List[Dict[str, Any]] = [
        normalize_data(
            {
                "_id": "__meta__",
                "globalFallback": str(data.get("globalFallback") or "").strip(),
                "fallbackToRealmOnNodeError": data.get("fallbackToRealmOnNodeError"),
                "userOverrideCount": len(user_overrides),
                "userOverrideFallbacks": dict(sorted(user_fallback_counts.items())),
            }
        )
    ]

    for function_name, target in sorted(function_overrides.items()):
        items.append(
            normalize_data(
                {
                    "_id": str(function_name),
                    "target": str(target or "").strip(),
                }
            )
        )

    return {
        "items": items,
        "functionOverrideCount": len(function_overrides),
        "userOverrideCount": len(user_overrides),
    }


def _project_site1_media_settings(settings_record: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(settings_record, dict):
        return None

    settings_id = _extract_object_id(settings_record.get("_id"))
    if not settings_id:
        return None

    announcement = settings_record.get("announcementData") if isinstance(settings_record.get("announcementData"), dict) else {}

    return normalize_data(
        {
            "_id": settings_id,
            "type": str(settings_record.get("type") or "").strip(),
            "domain": str(settings_record.get("domain") or "").strip(),
            "title": str(announcement.get("title") or "").strip(),
            "path": str(announcement.get("path") or "").strip(),
            "height": _coerce_int(announcement.get("height")),
            "mobileHeight": _coerce_int(announcement.get("mobileHeight")),
            "color": str(announcement.get("color") or "").strip(),
            "url": "https://drjoedispenza.com/",
        }
    )


def _fetch_site1_media_settings_snapshot_data() -> Optional[Dict[str, Any]]:
    settings_record = _call_site1_realm_function(
        "findOne",
        [
            {
                "database": "MainDB",
                "collection": "settings",
                "query": {"type": "media"},
            }
        ],
    )
    projected_settings = _project_site1_media_settings(settings_record)
    if not projected_settings:
        return None

    return {
        "items": [projected_settings],
        "count": 1,
    }


def _project_site1_drjoe_live_record(record: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None

    record_id = _extract_object_id(record.get("_id"))
    if not record_id:
        return None

    conversation = record.get("conversationDate") if isinstance(record.get("conversationDate"), dict) else {}
    return normalize_data(
        {
            "_id": record_id,
            "title": str(record.get("title") or "").strip(),
            "activeView": str(record.get("activeView") or "").strip(),
            "conversationDate": _realm_epoch_ms_to_iso(conversation.get("fullDate")),
            "conversationTime": str(conversation.get("time") or "").strip(),
            "conversationTimeZone": str(conversation.get("timeZone") or "").strip(),
            "image": record.get("image"),
            "brightCoveId": str(record.get("brightCoveId") or "").strip(),
            "url": f"https://drjoedispenza.com/dr-joe-live/{record_id}",
        }
    )


def _fetch_site1_drjoe_live_snapshot_data() -> Optional[Dict[str, Any]]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    records = _call_site1_realm_function(
        "find",
        [
            {
                "database": "MainDB",
                "collection": "drJoeLive",
                "query": {
                    "conversationDate.fullDate": {"$gte": {"$date": {"$numberLong": str(now_ms)}}},
                    "activeView": {"$in": ["upcoming", "live"]},
                    "status": "Active",
                },
                "project": {
                    "conversationDate": {"$numberInt": "1"},
                    "title": {"$numberInt": "1"},
                    "image": {"$numberInt": "1"},
                    "activeView": {"$numberInt": "1"},
                    "brightCoveId": {"$numberInt": "1"},
                },
                "sort": {"conversationDate.fullDate": {"$numberInt": "1"}},
                "limit": {"$numberInt": "5"},
            }
        ],
    )
    if not isinstance(records, list):
        return None

    items: List[Dict[str, Any]] = []
    for raw_record in records:
        projected_record = _project_site1_drjoe_live_record(raw_record)
        if projected_record:
            items.append(projected_record)

    items.sort(key=lambda item: (str(item.get("conversationDate") or ""), str(item.get("_id") or "")))
    return {
        "items": items,
        "count": len(items),
    }


def _project_site1_event_preview_product(product: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(product, dict):
        return None

    product_id = _extract_object_id(product.get("_id"))
    if not product_id:
        return None

    images = product.get("images")
    if isinstance(images, list):
        images = [value for value in images if isinstance(value, str) and value.strip()]

    return normalize_data(
        {
            "_id": product_id,
            "title": str(product.get("title") or "").strip(),
            "status": product.get("status"),
            "type": product.get("type"),
            "eventType": str(product.get("eventType") or "").strip(),
            "startDate": _realm_epoch_ms_to_iso(product.get("startDate")),
            "endDate": _realm_epoch_ms_to_iso(product.get("endDate")),
            "registrationStart": _realm_epoch_ms_to_iso(product.get("registrationStart")),
            "availableOnUnlimited": product.get("availableOnUnlimited"),
            "hideFromUpcoming": product.get("hideFromUpcoming"),
            "images": images,
            "url": _build_site1_product_page_url(product.get("title")),
        }
    )


def _fetch_site1_event_preview_snapshot_data() -> Optional[Dict[str, Any]]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    products = _call_site1_realm_function(
        "find",
        [
            {
                "database": "MainDB",
                "collection": "products",
                "query": {
                    "status": {"$in": ["Active", "Coming Soon"]},
                    "availableOnUnlimited": True,
                    "type": "event",
                    "startDate": {"$gte": {"$date": {"$numberLong": str(now_ms)}}},
                    "hideFromUpcoming": {"$ne": True},
                },
                "project": {
                    "title": {"$numberInt": "1"},
                    "status": {"$numberInt": "1"},
                    "type": {"$numberInt": "1"},
                    "eventType": {"$numberInt": "1"},
                    "startDate": {"$numberInt": "1"},
                    "endDate": {"$numberInt": "1"},
                    "registrationStart": {"$numberInt": "1"},
                    "images": {"$numberInt": "1"},
                    "availableOnUnlimited": {"$numberInt": "1"},
                    "hideFromUpcoming": {"$numberInt": "1"},
                },
                "sort": {"startDate": {"$numberInt": "1"}},
                "limit": {"$numberInt": "50"},
            }
        ],
    )
    if not isinstance(products, list):
        return None

    items: List[Dict[str, Any]] = []
    for raw_product in products:
        projected_product = _project_site1_event_preview_product(raw_product)
        if projected_product:
            items.append(projected_product)

    items.sort(key=lambda item: (str(item.get("startDate") or ""), str(item.get("title") or "")))
    return {
        "items": items,
        "count": len(items),
    }


def _fetch_site1_brightcove_refs_snapshot_data() -> Optional[Dict[str, Any]]:
    refs_by_id: Dict[str, Dict[str, Any]] = {}

    def add_ref(
        video_id_value: Any,
        *,
        title: str,
        source_tag: str,
        owner_title: str,
        url: str,
    ) -> None:
        video_id = str(video_id_value or "").strip()
        if not video_id:
            return

        existing = refs_by_id.get(video_id)
        if not existing:
            existing = {
                "_id": video_id,
                "title": title.strip() or video_id,
                "sourceTags": [],
                "ownerTitles": [],
                "url": url,
            }
            refs_by_id[video_id] = existing

        source_tags = set(str(value) for value in (existing.get("sourceTags") or []) if str(value).strip())
        source_tags.add(source_tag)
        existing["sourceTags"] = sorted(source_tags)

        owner_titles = set(str(value) for value in (existing.get("ownerTitles") or []) if str(value).strip())
        if owner_title.strip():
            owner_titles.add(owner_title.strip())
        existing["ownerTitles"] = sorted(owner_titles)

        if not str(existing.get("url") or "").strip() and url.strip():
            existing["url"] = url.strip()

    subscriptions_data = _fetch_site1_subscriptions_snapshot_data() or {}
    for item in subscriptions_data.get("items", []):
        if not isinstance(item, dict):
            continue
        base_title = str(item.get("title") or "").strip()
        base_url = str(item.get("url") or "").strip()

        for sample in item.get("samples", []):
            if not isinstance(sample, dict):
                continue
            add_ref(
                sample.get("videoId"),
                title=str(sample.get("title") or "").strip(),
                source_tag="subscription.sample",
                owner_title=base_title,
                url=base_url,
            )

        for variant in item.get("variants", []):
            if not isinstance(variant, dict):
                continue
            variant_id = str(variant.get("variantId") or "").strip()
            variant_label = f"{base_title} [{variant_id}]" if variant_id else base_title
            for sample in variant.get("samples", []):
                if not isinstance(sample, dict):
                    continue
                add_ref(
                    sample.get("videoId"),
                    title=str(sample.get("title") or "").strip(),
                    source_tag="subscription.variant_sample",
                    owner_title=variant_label,
                    url=base_url,
                )

    community_data = _fetch_site1_community_groups_snapshot_data() or {}
    for item in community_data.get("items", []):
        if not isinstance(item, dict):
            continue
        group_title = str(item.get("title") or "").strip()
        group_url = str(item.get("url") or "").strip()
        add_ref(
            item.get("nextConversationBrightCoveId"),
            title=f"{group_title} Next Conversation".strip(),
            source_tag="community_group.next_conversation",
            owner_title=group_title,
            url=group_url,
        )
        add_ref(
            item.get("previousConversationBrightCoveId"),
            title=f"{group_title} Previous Conversation".strip(),
            source_tag="community_group.previous_conversation",
            owner_title=group_title,
            url=group_url,
        )

    drjoe_live_data = _fetch_site1_drjoe_live_snapshot_data() or {}
    for item in drjoe_live_data.get("items", []):
        if not isinstance(item, dict):
            continue
        add_ref(
            item.get("brightCoveId"),
            title=str(item.get("title") or "").strip(),
            source_tag="drjoe_live",
            owner_title=str(item.get("title") or "").strip(),
            url=str(item.get("url") or "").strip(),
        )

    items = sorted(
        (normalize_data(value) for value in refs_by_id.values()),
        key=lambda entry: str(entry.get("_id") or ""),
    )
    return {
        "items": items,
        "count": len(items),
    }


def _get_mymm_identity_path() -> str:
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    return os.path.join(SNAPSHOTS_DIR, "mymm_client_identity.json")


def _load_or_create_mymm_identity() -> Dict[str, str]:
    env_device_id = os.environ.get("MYMM_DEVICE_ID", "").strip()
    env_unique_user_key = os.environ.get("MYMM_UNIQUE_USER_KEY", "").strip()
    if env_device_id and env_unique_user_key:
        return {
            "deviceId": env_device_id,
            "uniqueUserKey": env_unique_user_key,
        }

    path = _get_mymm_identity_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            device_id = str(data.get("deviceId") or "").strip()
            unique_user_key = str(data.get("uniqueUserKey") or "").strip()
            if device_id and unique_user_key:
                return {
                    "deviceId": device_id,
                    "uniqueUserKey": unique_user_key,
                }
        except Exception:
            pass

    identity = {
        "deviceId": secrets.token_hex(8),
        "uniqueUserKey": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(identity, f, indent=2)
    return identity


def _login_mymm_app() -> Optional[str]:
    identity = _load_or_create_mymm_identity()
    payload = {
        **_MYMM_APP_LOGIN_PAYLOAD,
        "deviceId": identity["deviceId"],
        "uniqueUserKey": identity["uniqueUserKey"],
    }

    response = fetch_json(
        f"{_MYMM_REALM_BASE_URL}/auth/providers/custom-function/login",
        method="POST",
        payload=payload,
        headers={
            "Accept": "application/json",
            "User-Agent": "okhttp/4.9.1",
        },
    )
    if not isinstance(response, dict):
        return None

    access_token = response.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None
    return access_token


def _call_mymm_realm_function(function_name: str, arguments: List[Any]) -> Optional[Any]:
    access_token = _login_mymm_app()
    if not access_token:
        return None

    return fetch_json(
        f"{_MYMM_REALM_BASE_URL}/functions/call",
        method="POST",
        payload={
            "name": function_name,
            "arguments": arguments,
            "service": "mainCluster",
        },
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "okhttp/4.9.1",
        },
    )


def _extract_realm_epoch_ms(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    if isinstance(value, dict):
        if "$numberLong" in value:
            raw_value = value.get("$numberLong")
            if isinstance(raw_value, str) and raw_value.strip().isdigit():
                return int(raw_value.strip())
        if "$date" in value:
            return _extract_realm_epoch_ms(value.get("$date"))
    return None


def _realm_epoch_ms_to_iso(value: Any) -> str:
    timestamp_ms = _extract_realm_epoch_ms(value)
    if timestamp_ms is None:
        return ""
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def _project_mymm_event(event: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(event, dict):
        return None

    event_id = _extract_object_id(event.get("_id"))
    if not event_id:
        return None

    image_value = (
        event.get("image")
        or event.get("imageUrl")
        or event.get("thumbnail")
        or event.get("cardImage")
        or event.get("coverImage")
    )

    projected = {
        "_id": event_id,
        "name": str(event.get("name") or "").strip(),
        "subTitle": str(event.get("subTitle") or "").strip(),
        "status": event.get("status"),
        "eventType": event.get("eventType"),
        "startDate": _realm_epoch_ms_to_iso(event.get("startDate")),
        "endDate": _realm_epoch_ms_to_iso(event.get("endDate")),
        "image": image_value,
    }
    return normalize_data(projected)


def _fetch_mymm_all_events_data() -> Optional[Dict[str, Any]]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    events_payload = _call_mymm_realm_function(
        "find",
        [
            {
                "database": "MainDB",
                "collection": "mymmEvents",
                "query": {
                    "endDate": {
                        "$gte": {
                            "$date": {
                                "$numberLong": str(now_ms),
                            }
                        }
                    }
                },
                "project": {},
                "sort": {},
            }
        ],
    )
    if not isinstance(events_payload, list):
        return None

    events: List[Dict[str, Any]] = []
    for raw_event in events_payload:
        projected_event = _project_mymm_event(raw_event)
        if projected_event:
            events.append(projected_event)

    events.sort(
        key=lambda item: (
            str(item.get("startDate") or ""),
            str(item.get("name") or ""),
            str(item.get("_id") or ""),
        )
    )

    return {
        "events": events,
        "count": len(events),
    }


def _build_site1_product_preview_text(product_data: Dict[str, Any]) -> str:
    product_details = product_data.get("productDetails")
    if not isinstance(product_details, dict):
        return ""

    fragments: List[str] = []

    short_description = product_details.get("shortDescription")
    if isinstance(short_description, str) and short_description.strip():
        fragments.append(short_description)

    long_description = product_details.get("longDescription")
    if isinstance(long_description, list):
        for section in long_description:
            if not isinstance(section, dict):
                continue
            section_title = str(section.get("title") or "").strip()
            content_html = section.get("content")
            if not isinstance(content_html, str) or not content_html.strip():
                continue

            section_parts: List[str] = []
            if section_title:
                section_parts.append(f"<h3>{escape(section_title)}</h3>")
            section_parts.append(content_html)
            fragments.append("<section>" + "".join(section_parts) + "</section>")

    if not fragments:
        return ""

    return _extract_text_from_body_html("<div>" + "".join(fragments) + "</div>")


def _fetch_site1_product_preview_from_api(route: str) -> Optional[Dict[str, str]]:
    parsed = urllib.parse.urlparse(route)
    route_path = parsed.path or route.split("?", 1)[0]

    marker = "/product-details/"
    if not route_path.startswith(marker):
        return None

    product_slug = urllib.parse.unquote(route_path[len(marker):]).strip()
    if not product_slug:
        return None

    product_payload = _call_site1_realm_function(
        "getProductWithInventorySnapshot",
        [product_slug, False, False],
    )
    if not isinstance(product_payload, dict):
        return None

    title = str(product_payload.get("title") or "").strip()
    preview_text = _build_site1_product_preview_text(product_payload)

    if not title and not preview_text:
        return None

    return {
        "title": title,
        "text": preview_text,
    }


def _extract_site1_product_detail_routes_from_html(html: str) -> List[str]:
    routes = set(re.findall(r"/product-details/[^\"'#?<> ]+", html or ""))
    return sorted(routes)


def _extract_route_path(route_or_url: str) -> str:
    parsed = urllib.parse.urlparse(str(route_or_url or "").strip())
    return parsed.path or str(route_or_url or "").strip()


def _load_site1_inventory_route_paths() -> set[str]:
    """Load product-detail routes already covered by Site1 shop API trackers."""
    covered_paths: set[str] = set()
    for snapshot_name, items_key in (
        ("site1_inventory_api", "products"),
        ("site1_public_catalog_api", "items"),
    ):
        snapshot = load_snapshot(snapshot_name)
        snapshot_data = snapshot.get("data", {}) if snapshot else {}
        snapshot_products = snapshot_data.get(items_key, []) if isinstance(snapshot_data, dict) else []

        for product in snapshot_products:
            if not isinstance(product, dict):
                continue
            product_url = str(product.get("url") or "").strip()
            if product_url:
                covered_paths.add(_extract_route_path(product_url))

    if covered_paths:
        return covered_paths

    current_inventory = _fetch_site1_inventory_snapshot_data()
    if not current_inventory:
        return covered_paths

    for product in current_inventory.get("products", []):
        if not isinstance(product, dict):
            continue
        product_url = str(product.get("url") or "").strip()
        if product_url:
            covered_paths.add(_extract_route_path(product_url))

    return covered_paths


def _extract_site2_relative_route(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    prefix = "/s/Drjoedispenza"
    if path.startswith(prefix):
        path = path[len(prefix):] or "/"
    if not path.startswith("/"):
        path = "/" + path
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _extract_site2_discovered_routes(page_url: str, page_props: Optional[Dict[str, Any]]) -> set[str]:
    routes = {_extract_site2_relative_route(page_url)}
    if not isinstance(page_props, dict):
        return routes

    initial_data = page_props.get("initialData", {})
    content_store = initial_data.get("contentPageStore", {}) if isinstance(initial_data, dict) else {}
    if not isinstance(content_store, dict):
        return routes

    seller_products = content_store.get("sellerProducts", {})
    if isinstance(seller_products, dict):
        for payload in seller_products.values():
            products: List[Any] = []
            if isinstance(payload, dict):
                payload_list = payload.get("list")
                if isinstance(payload_list, list):
                    products = payload_list
            elif isinstance(payload, list):
                products = payload

            for product in products:
                if not isinstance(product, dict):
                    continue
                slug = str(product.get("slug") or "").strip().strip("/")
                if slug:
                    routes.add("/" + slug)

    sorted_categories = content_store.get("sortedCategories", {})
    if isinstance(sorted_categories, dict):
        for block_id, categories in sorted_categories.items():
            if not isinstance(categories, list):
                continue
            for category in categories:
                if not isinstance(category, dict):
                    continue
                category_id = category.get("id")
                if category_id is None:
                    continue
                routes.add(f"/produkte?block_{block_id}_group_id={category_id}")

    return routes


def _sanitize_site2_page_data_for_tracking(page_data: Any) -> Any:
    """Remove Site2 catalog subtrees from generic page tracking to avoid duplicate alerts."""
    if not isinstance(page_data, dict):
        return page_data

    sanitized = copy.deepcopy(page_data)
    sanitized.pop("_sentryBaggage", None)
    sanitized.pop("_sentryTraceData", None)
    sanitized.pop("userSessionId", None)
    sanitized.pop("experiments", None)
    initial_data = sanitized.get("initialData")
    if isinstance(initial_data, dict):
        initial_data.pop("contentPageStore", None)
        initial_data.pop("productsStore", None)

    return sanitized


def _extract_text_preview_from_html_fragment(html_fragment: Any) -> str:
    if not isinstance(html_fragment, str) or not html_fragment.strip():
        return ""
    return _extract_text_from_body_html("<div>" + html_fragment + "</div>")


def _extract_site2_cover_reference(covers: Any) -> str:
    if isinstance(covers, list):
        for cover in covers:
            if isinstance(cover, str) and cover.strip():
                return cover.strip()
            if isinstance(cover, dict):
                for key in ("url", "src", "id"):
                    candidate = cover.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
    if isinstance(covers, dict):
        for key in ("url", "src", "id"):
            candidate = covers.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""


def _project_site2_catalog_category(block_id: str, raw_category: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_category, dict):
        return None

    category_id = raw_category.get("id")
    if category_id is None:
        return None
    if str(category_id).strip() == "0":
        # Elopage injects a localized "All products" placeholder category that
        # flips language across requests and does not carry real membership.
        return None

    description_preview = _extract_text_preview_from_html_fragment(raw_category.get("description"))
    route = f"/produkte?block_{block_id}_group_id={category_id}"

    projected = {
        "_id": f"{block_id}:{category_id}",
        "title": str(raw_category.get("title") or "").strip(),
        "descriptionPreview": description_preview,
        "hidden": raw_category.get("hidden"),
        "position": raw_category.get("position"),
        "color": raw_category.get("color"),
        "productIds": sorted(str(pid) for pid in (raw_category.get("productIds") or []) if str(pid).strip()),
        "route": route,
        "url": _SITE2_BASE_URL + route,
    }
    return normalize_data(projected)


def _project_site2_catalog_product(raw_product: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_product, dict):
        return None

    product_id = str(raw_product.get("id") or "").strip()
    if not product_id:
        return None

    slug = str(raw_product.get("slug") or "").strip().strip("/")
    route = "/" + slug if slug else ""
    description_preview = _extract_text_preview_from_html_fragment(raw_product.get("description"))

    projected = {
        "_id": product_id,
        "title": str(raw_product.get("name") or "").strip(),
        "slug": slug,
        "displayPrice": raw_product.get("displayPrice"),
        "displayOldPrice": raw_product.get("displayOldPrice"),
        "displayCurrencyId": raw_product.get("displayCurrencyId"),
        "free": raw_product.get("free"),
        "private": raw_product.get("private"),
        "coverRef": _extract_site2_cover_reference(raw_product.get("covers")),
        "descriptionPreview": description_preview,
        "route": route,
        "url": (_SITE2_BASE_URL + route) if route else _SITE2_BASE_URL,
        "categoryIds": [],
        "categoryTitles": [],
        "sourcePages": [],
    }
    return normalize_data(projected)


def _merge_site2_catalog_product(
    products_by_id: Dict[str, Dict[str, Any]],
    projected_product: Dict[str, Any],
    *,
    source_route: str,
    category_membership: Dict[str, set[str]],
    category_titles_by_id: Dict[str, str],
) -> None:
    product_id = str(projected_product.get("_id") or "").strip()
    if not product_id:
        return

    existing = products_by_id.get(product_id)
    if not existing:
        existing = copy.deepcopy(projected_product)
        products_by_id[product_id] = existing

    for field in (
        "title",
        "slug",
        "displayPrice",
        "displayOldPrice",
        "displayCurrencyId",
        "free",
        "private",
        "coverRef",
        "descriptionPreview",
        "route",
        "url",
    ):
        current_value = existing.get(field)
        next_value = projected_product.get(field)
        if (current_value in (None, "", [])) and next_value not in (None, "", []):
            existing[field] = next_value

    source_pages = set(str(value) for value in (existing.get("sourcePages") or []) if str(value).strip())
    if source_route:
        source_pages.add(source_route)
    existing["sourcePages"] = sorted(source_pages)

    category_ids = set(str(value) for value in (existing.get("categoryIds") or []) if str(value).strip())
    category_ids.update(category_membership.get(product_id, set()))
    existing["categoryIds"] = sorted(category_ids)
    existing["categoryTitles"] = sorted(
        category_titles_by_id[category_id]
        for category_id in category_ids
        if category_id in category_titles_by_id and category_titles_by_id[category_id]
    )


def _fetch_site2_catalog_snapshot_data() -> Optional[Dict[str, Any]]:
    build_id = ""
    routes: set[str] = set()
    products_by_id: Dict[str, Dict[str, Any]] = {}
    categories_by_id: Dict[str, Dict[str, Any]] = {}
    category_membership: Dict[str, set[str]] = {}
    category_titles_by_id: Dict[str, str] = {}

    for page_url in _SITE2_DISCOVERY_URLS:
        html = fetch_page(page_url)
        if not html:
            continue

        next_data = extract_next_data(html)
        if not next_data:
            continue

        if not build_id:
            build_id = str(next_data.get("buildId") or "").strip()

        page_props = get_nested_value(next_data, "props.pageProps")
        routes.update(_extract_site2_discovered_routes(page_url, page_props))

        if not isinstance(page_props, dict):
            continue

        initial_data = page_props.get("initialData", {})
        content_store = initial_data.get("contentPageStore", {}) if isinstance(initial_data, dict) else {}
        if not isinstance(content_store, dict):
            continue

        sorted_categories = content_store.get("sortedCategories", {})
        if isinstance(sorted_categories, dict):
            for block_id, categories in sorted_categories.items():
                if not isinstance(categories, list):
                    continue
                block_key = str(block_id)
                for raw_category in categories:
                    projected_category = _project_site2_catalog_category(block_key, raw_category)
                    if not projected_category:
                        continue
                    category_key = str(projected_category.get("_id") or "")
                    categories_by_id[category_key] = projected_category
                    category_title = str(projected_category.get("title") or "").strip()
                    if category_title:
                        category_titles_by_id[category_key] = category_title
                    for product_id in projected_category.get("productIds", []):
                        category_membership.setdefault(str(product_id), set()).add(category_key)

        seller_products = content_store.get("sellerProducts", {})
        if isinstance(seller_products, dict):
            for payload in seller_products.values():
                products: List[Any] = []
                if isinstance(payload, dict):
                    payload_list = payload.get("list")
                    if isinstance(payload_list, list):
                        products = payload_list
                elif isinstance(payload, list):
                    products = payload

                for raw_product in products:
                    projected_product = _project_site2_catalog_product(raw_product)
                    if not projected_product:
                        continue
                    _merge_site2_catalog_product(
                        products_by_id,
                        projected_product,
                        source_route=_extract_site2_relative_route(page_url),
                        category_membership=category_membership,
                        category_titles_by_id=category_titles_by_id,
                    )

    if not build_id and not products_by_id and not categories_by_id and not routes:
        return None

    return {
        "buildId": build_id,
        "routes": sorted(routes),
        "products": sorted(products_by_id.values(), key=lambda item: str(item.get("_id") or "")),
        "categories": sorted(categories_by_id.values(), key=lambda item: str(item.get("_id") or "")),
        "countProducts": len(products_by_id),
        "countCategories": len(categories_by_id),
    }


# Site7 help center: filter out dynamic meta/related blocks to avoid noise.
_SITE7_HELP_FILTER_VERSION = 2
_SITE7_HELP_UPDATED_LINE_RE = re.compile(
    r"^(?:"
    r"(?:heute|gestern|diese woche|letzte woche|diesen monat|letzten monat|dieses jahr|letztes jahr)\s+aktualisiert"
    r"|vor\s+(?:(?:über|mehr als)\s+)?\d+\s+(?:tag|tage|tagen|woche|wochen|monat|monate|monaten|jahr|jahre|jahren)\s+aktualisiert"
    r"|vor\s+(?:einem|einer)\s+(?:tag|woche|monat|jahr)\s+aktualisiert"
    r"|aktualisiert\s+vor\s+.*"
    r"|zuletzt\s+aktualisiert.*"
    r"|last\s+updated.*"
    r"|updated\s+(?:today|yesterday|this week|last week|\d+\s+(?:day|days|week|weeks|month|months|year|years)\s+ago)"
    r")$",
    re.IGNORECASE,
)


def _filter_site7_helpcenter_text(text: str) -> str:
    if not text:
        return text
    cleaned_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _SITE7_HELP_UPDATED_LINE_RE.match(stripped):
            continue
        cleaned_lines.append(stripped)
    return "\n".join(cleaned_lines)


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

def _save_snapshot_ascii(page_name: str, data: Dict[str, Any]) -> None:
    """Save a snapshot without emoji output for Windows cp1252 consoles."""
    path = get_snapshot_path(page_name)
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": data,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
    print(f"[snapshot] Saved snapshot for {page_name}")


def get_items_by_id(data: Any) -> Dict[str, Any]:
    """Extract nested items with _id or id field from JSON-like data."""
    items: Dict[str, Any] = {}
    visited: set[int] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node_identity = id(node)
            if node_identity in visited:
                return
            visited.add(node_identity)

            item_id = node.get("_id") or node.get("id")
            if item_id is not None and str(item_id).strip():
                items[str(item_id)] = node

            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(node, list):
            node_identity = id(node)
            if node_identity in visited:
                return
            visited.add(node_identity)

            for item in node:
                if isinstance(item, (dict, list)):
                    visit(item)

    visit(data)
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


def _humanize_change_field_name(field_name: str) -> str:
    if not field_name:
        return "Change"
    text = field_name.replace("_", " ")
    text = re.sub(r"(?<!^)([A-Z])", r" \1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1].upper() + text[1:] if text else "Change"


def _format_change_value_for_notification(value: Any, max_length: int = 120) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    if isinstance(value, (dict, list)):
        text = json.dumps(normalize_data(value), ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text


def build_grouped_item_updates(
    old_items: Dict[str, Any],
    new_items: Dict[str, Any],
    updates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group field-level updates by item and attach a readable item label."""
    grouped: Dict[str, Dict[str, Any]] = {}

    for update in updates:
        item_id = str(update.get("id") or "Unknown")
        current_item = new_items.get(item_id) or old_items.get(item_id) or {}
        display_name = ""
        if isinstance(current_item, dict):
            display_name = str(
                current_item.get("title")
                or current_item.get("name")
                or current_item.get("slug")
                or ""
            ).strip()

        if not display_name:
            display_name = item_id

        entry = grouped.setdefault(
            item_id,
            {
                "id": item_id,
                "display_name": display_name,
                "changes": [],
            },
        )

        field_label = _humanize_change_field_name(str(update.get("field") or "Change"))
        old_value = _format_change_value_for_notification(update.get("old"))
        new_value = _format_change_value_for_notification(update.get("new"))
        entry["changes"].append(f"{field_label}: `{old_value}` -> `{new_value}`")

    grouped_updates: List[Dict[str, Any]] = []
    for item_id, entry in grouped.items():
        details_lines: List[str] = []
        if entry["display_name"] != item_id:
            details_lines.append(f"ID: `{item_id}`")
        details_lines.extend(entry["changes"])
        grouped_updates.append(
            {
                "id": item_id,
                "type": f"📝 Updated: {entry['display_name']}",
                "details": "\n".join(details_lines),
            }
        )

    grouped_updates.sort(key=lambda item: str(item.get("type") or ""))
    return grouped_updates


def _build_generic_page_update(page_name: str, page_url: str, old_data: Any, new_data: Any) -> List[Dict[str, Any]]:
    """Create a fallback update when data changed but no item-level IDs were found."""
    if (
        isinstance(old_data, dict)
        and isinstance(new_data, dict)
        and new_data.get("_trackingMode") == "html_text"
    ):
        title = str(new_data.get("title") or old_data.get("title") or page_name).strip()
        old_text = str(old_data.get("text") or "").strip()
        new_text = str(new_data.get("text") or "").strip()
        details_lines = [f"**{title}**", f"URL: {page_url}"]

        if old_text and new_text:
            diff_summary = _summarize_text_diff(old_text, new_text, context_lines=0)
            if diff_summary:
                details_lines.append("Diff (rot = entfernt, grün = neu):")
                details_lines.append(diff_summary)
            else:
                old_preview = str(old_data.get("contentPreview") or "").strip()
                new_preview = str(new_data.get("contentPreview") or "").strip()
                if old_preview != new_preview:
                    details_lines.append("Preview:")
                    details_lines.append(_truncate(new_preview or "(kein Text)", 650))
                else:
                    details_lines.append("Hinweis: HTML-Seite geändert, aber kein stabiler Text-Diff erkennbar.")
        else:
            details_lines.append("Hinweis: Text-Baseline wurde neu erstellt; Diff ist ab dem nächsten Lauf verfügbar.")

        return [{
            "id": page_name,
            "field": "content",
            "type": _truncate_for_discord_field_name(f"📝 Content: {title}" if title else "📝 Content geändert"),
            "details": "\n".join(details_lines),
        }]

    details_lines = ["Structured page data changed."]

    if isinstance(old_data, dict) and isinstance(new_data, dict):
        changed_keys: List[str] = []
        for key in sorted(set(old_data.keys()) | set(new_data.keys())):
            if normalize_data(old_data.get(key)) != normalize_data(new_data.get(key)):
                changed_keys.append(key)

        if changed_keys:
            preview = ", ".join(f"`{key}`" for key in changed_keys[:8])
            if len(changed_keys) > 8:
                preview += f", ... (+{len(changed_keys) - 8} weitere)"
            details_lines.append(f"Top-level keys: {preview}")

    return [{
        "id": page_name,
        "field": "page_data",
        "type": "📝 Updated: Page payload",
        "details": "\n".join(details_lines),
    }]


def _track_items_snapshot(
    *,
    snapshot_name: str,
    notification_name: str,
    reference_url: str,
    current_data: Dict[str, Any],
    items_key: str = "items",
) -> bool:
    current_items = current_data.get(items_key, []) if isinstance(current_data, dict) else []
    if not isinstance(current_items, list):
        return False

    old_snapshot = load_snapshot(snapshot_name)
    if old_snapshot is None:
        print(f"[snapshot] First snapshot for {snapshot_name} ({len(current_items)} item(s))")
        _save_snapshot_ascii(snapshot_name, current_data)
        return False

    old_data = old_snapshot.get("data", {})
    old_items = old_data.get(items_key, []) if isinstance(old_data, dict) else []

    if compute_hash(old_items) == compute_hash(current_items):
        print(f"[snapshot] No changes for {snapshot_name}")
        return False

    old_item_map = get_items_by_id(old_items)
    new_item_map = get_items_by_id(current_items)
    added, updated, removed = compare_items(old_item_map, new_item_map)
    grouped_updates = build_grouped_item_updates(old_item_map, new_item_map, updated)

    print(
        f"[snapshot] Changes for {snapshot_name}: +{len(added)} "
        f"~{len(grouped_updates)} -{len(removed)}"
    )

    if DISCORD_WEBHOOK_URL:
        if added:
            send_new_items_notification(
                DISCORD_WEBHOOK_URL,
                notification_name,
                reference_url,
                added,
            )
        if grouped_updates:
            send_updated_items_notification(
                DISCORD_WEBHOOK_URL,
                notification_name,
                reference_url,
                grouped_updates,
            )
        if removed:
            send_removed_items_notification(
                DISCORD_WEBHOOK_URL,
                notification_name,
                reference_url,
                removed,
            )
    else:
        print("[snapshot] No Discord webhook configured - skipping notifications")

    _save_snapshot_ascii(snapshot_name, current_data)
    return True


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
        if page.url.startswith(_SITE2_BASE_URL):
            page_data = _sanitize_site2_page_data_for_tracking(page_data)
    else:
        # No Next.js data - extract readable page text instead of hashing raw HTML.
        print(f"ℹ️  No __NEXT_DATA__ on {page.name} - using HTML tracking")
        page_data = _build_html_tracking_snapshot_data(html)
    
    # Load previous snapshot
    old_snapshot = load_snapshot(page.name)
    
    # If no previous snapshot, just save current and return
    if old_snapshot is None:
        print(f"📝 First snapshot for {page.name}")
        save_snapshot(page.name, page_data)
        return False
    
    old_data = old_snapshot.get("data", {})

    if (
        isinstance(page_data, dict)
        and page_data.get("_trackingMode") == "html_text"
    ):
        old_tracking_version = 0
        if isinstance(old_data, dict):
            try:
                old_tracking_version = int(old_data.get("_trackingVersion") or 0)
            except (TypeError, ValueError):
                old_tracking_version = 0

        if (
            (isinstance(old_data, dict) and old_data.get("_html_hash"))
            or (
                isinstance(old_data, dict)
                and old_data.get("_trackingMode") == "html_text"
                and old_tracking_version < int(page_data.get("_trackingVersion") or 0)
            )
        ):
            print(
                f"[html] Updating snapshot baseline for {page.name} "
                "to stable HTML text tracking; skipping notifications this run"
            )
            save_snapshot(page.name, page_data)
            return False

    if page.url.startswith(_SITE2_BASE_URL) and isinstance(old_data, dict):
        old_initial_data = old_data.get("initialData", {})
        if (
            ("_sentryBaggage" in old_data)
            or ("_sentryTraceData" in old_data)
            or ("userSessionId" in old_data)
            or ("experiments" in old_data)
            or (
                isinstance(old_initial_data, dict)
                and ("contentPageStore" in old_initial_data or "productsStore" in old_initial_data)
            )
        ):
            print(
                f"[site2] Updating generic snapshot baseline for {page.name} "
                "to exclude transient session/catalog payload; skipping notifications this run"
            )
            save_snapshot(page.name, page_data)
            return False
    
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
    grouped_updates = build_grouped_item_updates(old_items, new_items, updated)
    fallback_updates: List[Dict[str, Any]] = []
    if not added and not updated and not removed:
        fallback_updates = _build_generic_page_update(page.name, page.url, old_data, page_data)
    
    # Send notifications
    if DISCORD_WEBHOOK_URL:
        if added:
            send_new_items_notification(
                DISCORD_WEBHOOK_URL,
                page.name,
                page.url,
                added
            )
        
        if grouped_updates or fallback_updates:
            send_updated_items_notification(
                DISCORD_WEBHOOK_URL,
                page.name,
                page.url,
                grouped_updates or fallback_updates
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


def track_site1_inventory_api() -> bool:
    """
    Track Site1 shop inventory directly via the public API.

    Product IDs come from the public collection function the frontend uses, then
    the App Runner inventory endpoint returns the current product metadata.
    """
    print("\nðŸ“¡ Tracking: Site1 Shop Inventory API")

    current_data = _fetch_site1_inventory_snapshot_data()
    if not current_data:
        print("âš ï¸  Could not fetch Site1 inventory snapshot data")
        return False

    current_products = current_data.get("products", [])
    print(
        f"   ðŸ“Š Inventory snapshot returned {len(current_products)} products from "
        f"{len(current_data.get('collectionNames', []))} collections"
    )

    old_snapshot = load_snapshot("site1_inventory_api")
    if old_snapshot is None:
        print(f"ðŸ“ First inventory API snapshot ({len(current_products)} products)")
        save_snapshot("site1_inventory_api", current_data)
        return False

    old_data = old_snapshot.get("data", {})
    old_products = old_data.get("products", []) if isinstance(old_data, dict) else []

    if compute_hash(normalize_data(old_products)) == compute_hash(normalize_data(current_products)):
        print("âœ… No inventory API changes")
        if (
            old_data.get("productIds") != current_data.get("productIds")
            or old_data.get("collectionNames") != current_data.get("collectionNames")
        ):
            save_snapshot("site1_inventory_api", current_data)
        return False

    old_items = get_items_by_id(old_products)
    new_items = get_items_by_id(current_products)
    added, updated, removed = compare_items(old_items, new_items)
    grouped_updates = build_grouped_item_updates(old_items, new_items, updated)

    print(
        f"ðŸ”„ Inventory API changes detected: +{len(added)} "
        f"~{len(updated)} -{len(removed)}"
    )

    if DISCORD_WEBHOOK_URL:
        if added:
            send_new_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Inventory-API",
                _SITE1_SHOP_URL,
                added,
            )

        if updated:
            send_updated_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Inventory-API",
                _SITE1_SHOP_URL,
                grouped_updates,
            )

        if removed:
            send_removed_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Inventory-API",
                _SITE1_SHOP_URL,
                removed,
            )
    else:
        print("âš ï¸  No Discord webhook configured - skipping notifications")

    save_snapshot("site1_inventory_api", current_data)
    return True


def track_site1_homepage_product_details() -> bool:
    """
    Track product-detail routes linked directly from the Site1 homepage.

    This covers homepage-featured products/resources that may not appear in the
    shop inventory collection feed but are still live and reachable.
    """
    print("\n[site1] Tracking: Homepage Product Detail Routes")

    html = fetch_page("https://drjoedispenza.com/")
    if not html:
        print("[site1] Could not fetch homepage for product-detail discovery")
        return False

    routes = _extract_site1_product_detail_routes_from_html(html)
    inventory_route_paths = _load_site1_inventory_route_paths()
    if inventory_route_paths:
        inventory_covered_routes = [
            route for route in routes
            if _extract_route_path(route) in inventory_route_paths
        ]
        if inventory_covered_routes:
            print(
                f"[site1] Skipping {len(inventory_covered_routes)} homepage product-detail route(s) "
                "already covered by inventory tracking"
            )
        routes = [
            route for route in routes
            if _extract_route_path(route) not in inventory_route_paths
        ]

    print(f"[site1] Found {len(routes)} homepage-only product-detail route(s)")

    current_items: List[Dict[str, Any]] = []
    for route in routes:
        route_info = _fetch_route_content("https://drjoedispenza.com", route)
        title = str(route_info.get("title") or "").strip()
        if not title:
            title = urllib.parse.unquote(route.split("/product-details/", 1)[-1]).strip()

        current_items.append(
            {
                "_id": route,
                "title": title,
                "url": route_info.get("full_url") or ("https://drjoedispenza.com" + route),
                "status": route_info.get("status"),
                "contentPreview": str(route_info.get("content_preview") or "").strip(),
            }
        )

    current_items.sort(key=lambda item: str(item.get("_id") or ""))
    current_data = {
        "items": current_items,
        "count": len(current_items),
    }

    old_snapshot = load_snapshot("site1_homepage_product_details")
    if old_snapshot is None:
        print(f"[site1] First homepage product-detail snapshot ({len(current_items)} routes)")
        _save_snapshot_ascii("site1_homepage_product_details", current_data)
        return False

    old_data = old_snapshot.get("data", {})
    old_items_list = old_data.get("items", []) if isinstance(old_data, dict) else []

    if compute_hash(normalize_data(old_items_list)) == compute_hash(normalize_data(current_items)):
        print("[site1] No homepage product-detail route changes")
        return False

    old_items = get_items_by_id(old_items_list)
    new_items = get_items_by_id(current_items)
    added, updated, removed = compare_items(old_items, new_items)
    grouped_updates = build_grouped_item_updates(old_items, new_items, updated)

    print(
        f"[site1] Homepage product-detail changes detected: +{len(added)} "
        f"~{len(grouped_updates)} -{len(removed)}"
    )

    if DISCORD_WEBHOOK_URL:
        if added:
            send_new_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Homepage-Product-Details",
                "https://drjoedispenza.com/",
                added,
            )
        if grouped_updates:
            send_updated_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Homepage-Product-Details",
                "https://drjoedispenza.com/",
                grouped_updates,
            )
        if removed:
            send_removed_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Homepage-Product-Details",
                "https://drjoedispenza.com/",
                removed,
            )
    else:
        print("[site1] No Discord webhook configured - skipping notifications")

    _save_snapshot_ascii("site1_homepage_product_details", current_data)
    return True


def track_site1_public_catalog_api() -> bool:
    """
    Track the full public Site1 shop feed.

    Notifications are suppressed for products already covered by the richer
    inventory tracker, plus event products that are handled by the event preview
    tracker.
    """
    print("\n[site1] Tracking: Public Catalog API")

    current_data = _fetch_site1_public_catalog_snapshot_data()
    if not current_data:
        print("[site1] Could not fetch public catalog snapshot data")
        return False

    current_items = current_data.get("items", [])
    current_inventory_ids = _load_site1_inventory_item_ids()
    current_data["catalogOnlyCount"] = sum(
        1
        for item in current_items
        if str(item.get("_id") or "").strip() not in current_inventory_ids
        and str(item.get("type") or "").strip() != "event"
    )
    print(
        f"[site1] Public catalog returned {len(current_items)} item(s); "
        f"{current_data['catalogOnlyCount']} catalog-only non-event item(s)"
    )

    old_snapshot = load_snapshot("site1_public_catalog_api")
    if old_snapshot is None:
        print(f"[site1] First public catalog snapshot ({len(current_items)} items)")
        _save_snapshot_ascii("site1_public_catalog_api", current_data)
        return False

    old_data = old_snapshot.get("data", {})
    old_items = old_data.get("items", []) if isinstance(old_data, dict) else []

    if compute_hash(old_items) == compute_hash(current_items):
        print("[site1] No public catalog changes")
        return False

    old_item_map = get_items_by_id(old_items)
    new_item_map = get_items_by_id(current_items)
    added, updated, removed = compare_items(old_item_map, new_item_map)
    grouped_updates = build_grouped_item_updates(old_item_map, new_item_map, updated)

    def should_notify(item_id: str) -> bool:
        if not item_id or item_id in current_inventory_ids:
            return False
        item = new_item_map.get(item_id) or old_item_map.get(item_id) or {}
        return str(item.get("type") or "").strip() != "event"

    added_to_notify = [item for item in added if should_notify(str(item.get("_id") or item.get("id") or ""))]
    grouped_updates_to_notify = [update for update in grouped_updates if should_notify(str(update.get("id") or ""))]
    removed_to_notify = [
        item for item in removed if should_notify(str(item.get("_id") or item.get("id") or ""))
    ]

    print(
        f"[site1] Public catalog changes detected: +{len(added)} ~{len(grouped_updates)} -{len(removed)}; "
        f"notifying +{len(added_to_notify)} ~{len(grouped_updates_to_notify)} -{len(removed_to_notify)}"
    )

    if DISCORD_WEBHOOK_URL:
        if added_to_notify:
            send_new_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Public-Catalog-API",
                _SITE1_SHOP_URL,
                added_to_notify,
            )
        if grouped_updates_to_notify:
            send_updated_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Public-Catalog-API",
                _SITE1_SHOP_URL,
                grouped_updates_to_notify,
            )
        if removed_to_notify:
            send_removed_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site1-Public-Catalog-API",
                _SITE1_SHOP_URL,
                removed_to_notify,
            )
    else:
        print("[site1] No Discord webhook configured - skipping notifications")

    _save_snapshot_ascii("site1_public_catalog_api", current_data)
    return True


def track_site1_public_categories() -> bool:
    print("\n[site1] Tracking: Public Categories")
    current_data = _fetch_site1_public_categories_snapshot_data()
    if not current_data:
        print("[site1] Could not fetch public category data")
        return False
    return _track_items_snapshot(
        snapshot_name="site1_public_categories",
        notification_name="Site1-Categories-API",
        reference_url=_SITE1_SHOP_URL,
        current_data=current_data,
    )


def track_site1_subscriptions_api() -> bool:
    print("\n[site1] Tracking: Public Subscriptions")
    current_data = _fetch_site1_subscriptions_snapshot_data()
    if not current_data:
        print("[site1] Could not fetch subscription data")
        return False
    return _track_items_snapshot(
        snapshot_name="site1_subscriptions_api",
        notification_name="Site1-Subscriptions-API",
        reference_url="https://drjoedispenza.com/dr-joe-live",
        current_data=current_data,
    )


def track_site1_policies_api() -> bool:
    print("\n[site1] Tracking: Public Policies")
    current_data = _fetch_site1_policies_snapshot_data()
    if not current_data:
        print("[site1] Could not fetch public policy data")
        return False
    return _track_items_snapshot(
        snapshot_name="site1_policies_api",
        notification_name="Site1-Policies-API",
        reference_url=_SITE1_PUBLIC_POLICY_IDS[0]["reference_url"],
        current_data=current_data,
    )


def track_site1_community_groups() -> bool:
    print("\n[site1] Tracking: Community Groups")
    current_data = _fetch_site1_community_groups_snapshot_data()
    if not current_data:
        print("[site1] Could not fetch community group data")
        return False
    return _track_items_snapshot(
        snapshot_name="site1_community_groups",
        notification_name="Site1-Community-Groups",
        reference_url="https://drjoedispenza.com/community",
        current_data=current_data,
    )


def track_site1_routing_config() -> bool:
    print("\n[site1] Tracking: Routing Config")
    current_data = _fetch_site1_routing_config_snapshot_data()
    if not current_data:
        print("[site1] Could not fetch routing config")
        return False
    return _track_items_snapshot(
        snapshot_name="site1_routing_config",
        notification_name="Site1-Routing-Config",
        reference_url=f"{_SITE1_APP_RUNNER_BASE_URL}/routing-config",
        current_data=current_data,
    )


def track_site1_media_settings() -> bool:
    print("\n[site1] Tracking: Homepage Announcement Settings")
    current_data = _fetch_site1_media_settings_snapshot_data()
    if not current_data:
        print("[site1] Could not fetch homepage announcement settings")
        return False
    return _track_items_snapshot(
        snapshot_name="site1_media_settings",
        notification_name="Site1-Homepage-Announcement",
        reference_url="https://drjoedispenza.com/",
        current_data=current_data,
    )


def track_site1_drjoe_live_preview() -> bool:
    print("\n[site1] Tracking: Dr Joe Live Preview")
    current_data = _fetch_site1_drjoe_live_snapshot_data()
    if not current_data:
        print("[site1] Could not fetch Dr Joe Live preview data")
        return False
    return _track_items_snapshot(
        snapshot_name="site1_drjoe_live_preview",
        notification_name="Site1-DrJoeLive-Preview",
        reference_url="https://drjoedispenza.com/dr-joe-live",
        current_data=current_data,
    )


def track_site1_event_preview() -> bool:
    print("\n[site1] Tracking: Event Preview Products")
    current_data = _fetch_site1_event_preview_snapshot_data()
    if not current_data:
        print("[site1] Could not fetch event preview products")
        return False
    return _track_items_snapshot(
        snapshot_name="site1_event_preview",
        notification_name="Site1-Event-Preview",
        reference_url="https://drjoedispenza.com/retreats",
        current_data=current_data,
    )


def track_site1_brightcove_refs() -> bool:
    print("\n[site1] Tracking: Brightcove Video References")
    current_data = _fetch_site1_brightcove_refs_snapshot_data()
    if not current_data:
        print("[site1] Could not fetch Brightcove video references")
        return False
    return _track_items_snapshot(
        snapshot_name="site1_brightcove_refs",
        notification_name="Site1-Brightcove-Refs",
        reference_url="https://drjoedispenza.com/dr-joe-live",
        current_data=current_data,
    )


def track_mymm_app_events() -> bool:
    """Track the public event list shown in the Making Your Mind Matter app."""
    print("\n[app] Tracking: MyMM App Events")

    current_data = _fetch_mymm_all_events_data()
    if not current_data:
        print("[app] Could not fetch MyMM app event list")
        return False

    current_events = current_data.get("events", [])
    print(f"[app] Event list returned {len(current_events)} active event(s)")

    old_snapshot = load_snapshot("mymm_app_events")
    if old_snapshot is None:
        print(f"[app] First MyMM app event snapshot ({len(current_events)} events)")
        _save_snapshot_ascii("mymm_app_events", current_data)
        return False

    old_data = old_snapshot.get("data", {})
    old_events = old_data.get("events", []) if isinstance(old_data, dict) else []

    if compute_hash(old_events) == compute_hash(current_events):
        print("[app] No MyMM app event changes")
        return False

    old_items = get_items_by_id(old_events)
    new_items = get_items_by_id(current_events)
    added, updated, removed = compare_items(old_items, new_items)
    grouped_updates = build_grouped_item_updates(old_items, new_items, updated)

    print(
        f"[app] MyMM app event changes detected: +{len(added)} "
        f"~{len(updated)} -{len(removed)}"
    )

    if DISCORD_WEBHOOK_URL:
        if added:
            send_new_items_notification(
                DISCORD_WEBHOOK_URL,
                "MyMM-App-Events",
                _MYMM_EVENTS_REFERENCE_URL,
                added,
            )

        if updated:
            send_updated_items_notification(
                DISCORD_WEBHOOK_URL,
                "MyMM-App-Events",
                _MYMM_EVENTS_REFERENCE_URL,
                grouped_updates,
            )

        if removed:
            send_removed_items_notification(
                DISCORD_WEBHOOK_URL,
                "MyMM-App-Events",
                _MYMM_EVENTS_REFERENCE_URL,
                removed,
            )
    else:
        print("[app] No Discord webhook configured - skipping notifications")

    _save_snapshot_ascii("mymm_app_events", current_data)
    return True


# =============================================================================
# PENDING ROUTES WATCH-LIST (for routes discovered but not yet live)
# =============================================================================

def _get_pending_routes_path() -> str:
    """Get the path to the pending routes JSON file."""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    return os.path.join(SNAPSHOTS_DIR, "pending_routes.json")


def _load_pending_routes() -> Dict[str, List[Dict[str, Any]]]:
    """
    Load the pending routes watch-list.
    Returns dict keyed by site name, each value is a list of pending route dicts.
    """
    path = _get_pending_routes_path()
    if not os.path.exists(path):
        return {}
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️  Error loading pending routes: {e}")
        return {}


def _save_pending_routes(pending: Dict[str, List[Dict[str, Any]]]) -> None:
    """Save the pending routes watch-list."""
    path = _get_pending_routes_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pending, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Error saving pending routes: {e}")


def _fetch_route_content(base_url: str, route: str) -> Dict[str, Any]:
    """
    Fetch a route and extract its content.
    
    Returns dict with:
        - status: "live" | "pending" (404/error)
        - title: Page title (if available)
        - content_preview: Extracted text content (if available)
        - http_status: Actual HTTP status code
    """
    full_url = base_url.rstrip("/") + route
    
    result = {
        "route": route,
        "full_url": full_url,
        "status": "pending",
        "title": "",
        "content_preview": "",
        "http_status": 0,
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
    
    try:
        req = urllib.request.Request(full_url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as response:
            result["http_status"] = response.status
            
            if response.status == 200:
                result["status"] = "live"
                
                # Read and decode content
                raw = response.read()
                if raw[:2] == b"\x1f\x8b":  # gzip magic bytes
                    try:
                        raw = gzip.decompress(raw)
                    except Exception:
                        pass
                
                html = raw.decode("utf-8", errors="replace")
                
                # Extract title
                result["title"] = _extract_title_from_html(html)
                
                # Extract route preview text from HTML (prefer main content, reduce shell noise).
                body_text = _extract_route_preview_text_from_html(html)

                # Site1 product-detail routes render core content client-side.
                # Pull product text from the same backend function used by the frontend.
                product_preview = None
                if "drjoedispenza.com" in base_url.lower():
                    product_preview = _fetch_site1_product_preview_from_api(route)
                if product_preview:
                    api_title = product_preview.get("title", "").strip()
                    api_text = product_preview.get("text", "").strip()
                    if api_title:
                        result["title"] = api_title
                    if api_text:
                        body_text = api_text
                
                # Take first ~2000 chars as preview
                result["content_preview"] = body_text[:2000] if body_text else ""
            else:
                result["status"] = "pending"
                
    except urllib.error.HTTPError as e:
        result["http_status"] = e.code
        result["status"] = "pending"
        print(f"   ⏳ Route {route}: HTTP {e.code}")
    except urllib.error.URLError as e:
        result["status"] = "pending"
        print(f"   ⏳ Route {route}: URL error - {e.reason}")
    except Exception as e:
        result["status"] = "pending"
        print(f"   ⏳ Route {route}: Error - {e}")
    
    return result


def track_pending_routes() -> bool:
    """
    Check all pending routes to see if any are now live.
    Sends notifications for routes that become available.
    Returns True if any route became live.
    """
    print("\n📡 Checking pending routes watch-list...")
    
    pending = _load_pending_routes()
    
    if not pending:
        print("   ✅ No pending routes to check")
        return False
    
    total_pending = sum(len(routes) for routes in pending.values())
    print(f"   📋 {total_pending} pending route(s) across {len(pending)} site(s)")
    
    changes_detected = False
    updated_pending = {}
    
    for site_name, routes in pending.items():
        # Determine base URL from site name
        if site_name == "Site1":
            base_url = "https://drjoedispenza.com"
        elif site_name == "Site2":
            base_url = "https://drjoedispenza.info/s/Drjoedispenza"
        else:
            # Try to extract from first route's full_url if available
            base_url = routes[0].get("base_url", "https://drjoedispenza.com") if routes else ""
        
        still_pending = []
        
        for route_entry in routes:
            route = route_entry.get("route", "")
            first_seen = route_entry.get("first_seen", "")
            
            if not route:
                continue
            
            # Rate limiting
            import time
            time.sleep(0.3)
            
            # Try to fetch the route
            result = _fetch_route_content(base_url, route)
            
            if result["status"] == "live":
                # Route is now live! Send notification
                print(f"   🎉 Route now LIVE: {route}")
                changes_detected = True
                
                if DISCORD_WEBHOOK_URL:
                    send_pending_route_now_live_notification(
                        DISCORD_WEBHOOK_URL,
                        site_name,
                        base_url,
                        {
                            "route": route,
                            "full_url": result["full_url"],
                            "title": result["title"],
                            "content_preview": result["content_preview"],
                            "first_seen": first_seen,
                        }
                    )
            else:
                # Still pending, keep in list
                still_pending.append(route_entry)
                print(f"   ⏳ Still pending: {route}")
        
        if still_pending:
            updated_pending[site_name] = still_pending
    
    # Save updated pending list
    _save_pending_routes(updated_pending)
    
    remaining = sum(len(routes) for routes in updated_pending.values())
    print(f"   📋 {remaining} route(s) still pending")
    
    return changes_detected


def _parse_build_manifest_chunks(manifest_content: str) -> Dict[str, List[str]]:
    """
    Parse _buildManifest.js to extract per-route chunk mappings.

    The manifest uses a compact JS function format like:
        self.__BUILD_MANIFEST=function(s,e,a,...){return{
            "/": [s,e,a,"static/chunks/pages/index-abc123.js"],
            "/retreats": ["static/chunks/pages/retreats-def456.js"],
            ...
        }}("static/chunks/120-aaa.js","static/chunks/2084-bbb.js",...)

    This parser resolves the variable references to their actual chunk paths.
    """
    # Step 1: Extract the variable parameter names from the function signature
    func_match = re.search(r'function\(([^)]+)\)\s*\{', manifest_content)
    if not func_match:
        return {}

    param_names = [p.strip() for p in func_match.group(1).split(",")]

    # Step 2: Extract the actual argument values at the end of the IIFE call
    # Pattern: }}("chunk1","chunk2",...)
    args_match = re.search(r'\}\}\s*\(([^)]+)\)', manifest_content)
    if not args_match:
        return {}

    # Parse the argument values (quoted strings)
    arg_values = re.findall(r'"([^"]*)"', args_match.group(1))

    # Build variable → chunk path mapping
    var_map: Dict[str, str] = {}
    for i, name in enumerate(param_names):
        if i < len(arg_values):
            var_map[name] = arg_values[i]

    # Step 3: Extract route → chunk array mappings from the return block
    route_chunks: Dict[str, List[str]] = {}

    # Find the return block content
    return_match = re.search(r'return\s*\{(.*?),\s*sortedPages\s*:', manifest_content, re.DOTALL)
    if not return_match:
        # Fallback: try without sortedPages
        return_match = re.search(r'return\s*\{(.*)\}\s*\}', manifest_content, re.DOTALL)

    if not return_match:
        return {}

    return_body = return_match.group(1)

    # Extract each route and its chunk array
    # Pattern: "/route-name":[items]
    route_pattern = re.compile(r'"(/[^"]*)":\s*\[([^\]]*)\]')
    for route_match in route_pattern.finditer(return_body):
        route = route_match.group(1)
        items_str = route_match.group(2).strip()

        if not items_str:
            route_chunks[route] = []
            continue

        # Parse the array items: mix of variable names and quoted strings
        chunks: List[str] = []
        for item in re.findall(r'(?:"([^"]*)")|([a-zA-Z_][a-zA-Z0-9_]*)', items_str):
            quoted, var_name = item
            if quoted:
                chunks.append(quoted)
            elif var_name and var_name in var_map:
                chunks.append(var_map[var_name])

        route_chunks[route] = chunks

    return route_chunks


def _parse_ssg_manifest(ssg_content: str) -> List[str]:
    """
    Parse _ssgManifest.js to extract the list of statically generated pages.

    Format: self.__SSG_MANIFEST=new Set(["\\u002F","\\u002Fretreats",...])
    The fetched content may contain double-escaped unicode (\\u002F) which
    we need to decode first.
    """
    # First, unescape any \\uXXXX sequences to their actual characters
    def _unescape_unicode(text: str) -> str:
        return re.sub(
            r'\\u([0-9a-fA-F]{4})',
            lambda m: chr(int(m.group(1), 16)),
            text,
        )

    unescaped = _unescape_unicode(ssg_content)

    # Extract content inside Set([...])
    set_match = re.search(r'new\s+Set\(\[(.*?)\]\)', unescaped, re.DOTALL)
    if not set_match:
        return []

    raw = set_match.group(1)
    # Extract quoted path strings
    pages = re.findall(r'"([^"]*)"', raw)
    return sorted(pages)


def _diff_route_chunks(
    old_chunks: Dict[str, List[str]],
    new_chunks: Dict[str, List[str]],
) -> Tuple[List[str], List[str], List[str]]:
    """
    Compare per-route chunk mappings between two builds.

    Returns:
        (changed_routes, new_routes, removed_routes)
    Where changed_routes are routes whose JS chunks changed (= code changed for that page).
    """
    old_routes = set(old_chunks.keys())
    new_routes_set = set(new_chunks.keys())

    added = sorted(new_routes_set - old_routes)
    removed = sorted(old_routes - new_routes_set)

    changed: List[str] = []
    for route in sorted(old_routes & new_routes_set):
        # Compare only page-specific chunks (filter out shared chunks)
        old_page = [c for c in old_chunks[route] if "/pages/" in c]
        new_page = [c for c in new_chunks[route] if "/pages/" in c]
        if old_page != new_page:
            changed.append(route)

    return changed, added, removed


def _crawl_changed_routes_content(
    base_url: str,
    routes: List[str],
    max_routes: int = 15,
) -> List[Dict[str, Any]]:
    """
    Crawl a list of routes to extract their current content for diff analysis.

    Returns list of dicts with: route, full_url, title, content_preview, status
    """
    import time

    results: List[Dict[str, Any]] = []
    # Filter out internal/dynamic routes that won't resolve
    skip_patterns = [
        "[", "]]", "_app", "_error", "404", "robots.txt", "sitemap.xml",
    ]

    crawlable = [
        r for r in routes
        if not any(pat in r for pat in skip_patterns)
    ][:max_routes]

    for route in crawlable:
        time.sleep(0.3)
        result = _fetch_route_content(base_url, route)
        results.append(result)

    return results


def track_build_manifest() -> bool:
    """
    Track the build manifest for new routes/deployments.

    Enhanced with:
    1. JS-Chunk-Diff: identifies which specific pages changed by comparing chunk filenames
    2. SSG Manifest tracking: detects changes in statically generated pages
    3. Full-Crawl on build change: crawls changed routes to extract content diffs
    """
    print("\n📡 Tracking: Build Manifest (Enhanced)")

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

    # ── Fetch & parse build manifest ──
    manifest_url = f"https://drjoedispenza.com/_next/static/{build_id}/_buildManifest.js"
    manifest_content = fetch_page(manifest_url)

    if not manifest_content:
        return False

    # Extract routes (legacy) and per-route chunk mappings (new)
    routes = set(re.findall(r'"(/[^"]*)"', manifest_content))
    route_chunks = _parse_build_manifest_chunks(manifest_content)
    print(f"   📊 Parsed {len(route_chunks)} route-chunk mappings")

    # ── Fetch & parse SSG manifest ──
    ssg_url = f"https://drjoedispenza.com/_next/static/{build_id}/_ssgManifest.js"
    ssg_content = fetch_page(ssg_url)
    ssg_pages: List[str] = []
    if ssg_content:
        ssg_pages = _parse_ssg_manifest(ssg_content)
        print(f"   📊 SSG pages: {len(ssg_pages)}")

    current_data = {
        "buildId": build_id,
        "routes": sorted(list(routes)),
        "manifestHash": hashlib.md5(manifest_content.encode()).hexdigest(),
        "routeChunks": {r: chunks for r, chunks in route_chunks.items()},
        "ssgPages": ssg_pages,
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

        # ── Strategy 1: JS-Chunk-Diff ──
        old_route_chunks = old_data.get("routeChunks", {})
        changed_routes: List[str] = []
        new_route_list: List[str] = []
        removed_route_list: List[str] = []

        if old_route_chunks:
            changed_routes, new_route_list, removed_route_list = _diff_route_chunks(
                old_route_chunks, route_chunks
            )
            if changed_routes:
                print(f"📝 Pages with code changes: {len(changed_routes)}")
                for r in changed_routes[:10]:
                    print(f"   ~ {r}")
            if new_route_list:
                print(f"🆕 New routes: {len(new_route_list)}")
                for r in new_route_list[:10]:
                    print(f"   + {r}")
            if removed_route_list:
                print(f"🗑️  Removed routes: {len(removed_route_list)}")
                for r in removed_route_list[:10]:
                    print(f"   - {r}")
            if not changed_routes and not new_route_list and not removed_route_list:
                print("   ℹ️  Only shared chunks changed (framework/library update)")
        else:
            # First run with chunk tracking – use legacy route comparison
            new_route_list = sorted(routes - old_routes)
            if new_route_list:
                print(f"🆕 New routes (legacy): {new_route_list}")

        # ── Strategy 3: SSG Manifest Diff ──
        old_ssg_pages = set(old_data.get("ssgPages", []))
        new_ssg_pages = set(ssg_pages) - old_ssg_pages
        removed_ssg_pages = old_ssg_pages - set(ssg_pages)
        if new_ssg_pages:
            print(f"📄 New SSG pages: {sorted(new_ssg_pages)}")
        if removed_ssg_pages:
            print(f"📄 Removed SSG pages: {sorted(removed_ssg_pages)}")

        # ── Fetch content for NEW routes ──
        if new_route_list:
            routes_with_content = []
            pending_routes_to_add = []

            import time
            for route in sorted(new_route_list):
                time.sleep(0.3)  # Rate limiting
                print(f"   Fetching {route}...")

                result = _fetch_route_content("https://drjoedispenza.com", route)
                routes_with_content.append(result)

                if result["status"] == "pending":
                    # Add to watch-list
                    pending_routes_to_add.append({
                        "route": route,
                        "base_url": "https://drjoedispenza.com",
                        "first_seen": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    })

            # Save pending routes to watch-list
            if pending_routes_to_add:
                pending = _load_pending_routes()
                if "Site1" not in pending:
                    pending["Site1"] = []
                # Avoid duplicates
                existing_routes = {r["route"] for r in pending["Site1"]}
                for entry in pending_routes_to_add:
                    if entry["route"] not in existing_routes:
                        pending["Site1"].append(entry)
                _save_pending_routes(pending)
                print(f"   📋 Added {len(pending_routes_to_add)} route(s) to watch-list")

            # Send notification with content
            if DISCORD_WEBHOOK_URL:
                send_new_route_with_content_notification(
                    DISCORD_WEBHOOK_URL,
                    "Site1 (drjoedispenza.com)",
                    "https://drjoedispenza.com",
                    routes_with_content
                )

        # ── Strategy 2: Full-crawl of CHANGED routes ──
        crawled_changes: List[Dict[str, Any]] = []
        if changed_routes:
            print(f"   🔍 Crawling {min(len(changed_routes), 15)} changed route(s) for content diff...")
            crawled_changes = _crawl_changed_routes_content(
                "https://drjoedispenza.com",
                changed_routes,
                max_routes=15,
            )

        # ── Send enhanced build change notification ──
        if DISCORD_WEBHOOK_URL:
            send_build_change_notification(
                DISCORD_WEBHOOK_URL,
                old_build_id,
                build_id,
                new_route_list,
                changed_routes=changed_routes,
                removed_routes=removed_route_list,
                crawled_changes=crawled_changes,
                new_ssg_pages=sorted(new_ssg_pages) if new_ssg_pages else [],
                removed_ssg_pages=sorted(removed_ssg_pages) if removed_ssg_pages else [],
            )

        save_snapshot("build_manifest", current_data)
        return True

    print("✅ No build changes")
    return False


def track_build_manifest_site2() -> bool:
    """Track the build manifest for Site2 (German shop)."""
    print("\n📡 Tracking: Site2 Build Manifest")

    build_id = ""
    shop_pages: set[str] = set()

    for page_url in _SITE2_DISCOVERY_URLS:
        html = fetch_page(page_url)
        if not html:
            continue

        next_data = extract_next_data(html)
        if not next_data:
            continue

        if not build_id:
            build_id = str(next_data.get("buildId") or "").strip()

        page_props = get_nested_value(next_data, "props.pageProps")
        shop_pages.update(_extract_site2_discovered_routes(page_url, page_props))

    if not build_id:
        print("⚠️  Could not find buildId for Site2")
        return False
    
    print(f"📦 Site2 buildId: {build_id}")
    
    # Load previous snapshot
    old_snapshot = load_snapshot("build_manifest_site2")
    
    current_data = {
        "buildId": build_id,
        "shopPages": sorted(shop_pages),
        "pageCount": len(shop_pages),
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
        
        # Fetch content for each new page
        routes_with_content = []
        pending_routes_to_add = []
        
        import time
        for page_slug in sorted(added_pages)[:10]:  # Limit to 10
            # Site2 pages are accessed via /s/Drjoedispenza/<slug>
            route = f"/{page_slug}" if page_slug.startswith("/") else f"/{page_slug}"
            time.sleep(0.3)  # Rate limiting
            print(f"   Fetching {route}...")
            
            # Site2 has a different URL structure
            full_url = f"https://drjoedispenza.info/s/Drjoedispenza{route}"
            result = {
                "route": route,
                "full_url": full_url,
                "status": "pending",
                "title": "",
                "content_preview": "",
            }
            
            # Try to fetch the page
            try:
                html = fetch_page(full_url)
                if html:
                    result["status"] = "live"
                    result["title"] = _extract_title_from_html(html)
                    clean_body = _extract_clean_body_html(html)
                    body_text = _extract_text_from_body_html(clean_body)
                    result["content_preview"] = body_text[:2000] if body_text else ""
                else:
                    result["status"] = "pending"
            except Exception as e:
                print(f"   ⏳ Error fetching {route}: {e}")
                result["status"] = "pending"
            
            routes_with_content.append(result)
            
            if result["status"] == "pending":
                pending_routes_to_add.append({
                    "route": route,
                    "base_url": "https://drjoedispenza.info/s/Drjoedispenza",
                    "first_seen": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                })
        
        # Save pending routes to watch-list
        if pending_routes_to_add:
            pending = _load_pending_routes()
            if "Site2" not in pending:
                pending["Site2"] = []
            existing_routes = {r["route"] for r in pending["Site2"]}
            for entry in pending_routes_to_add:
                if entry["route"] not in existing_routes:
                    pending["Site2"].append(entry)
            _save_pending_routes(pending)
            print(f"   📋 Added {len(pending_routes_to_add)} route(s) to Site2 watch-list")
        
        # Send notification with content
        if DISCORD_WEBHOOK_URL:
            send_new_route_with_content_notification(
                DISCORD_WEBHOOK_URL,
                "Site2 (drjoedispenza.info)",
                "https://drjoedispenza.info/s/Drjoedispenza",
                routes_with_content
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


def track_site2_catalog() -> bool:
    """Track Site2 catalog products and categories via the public page payloads."""
    print("\n[site2] Tracking: Catalog Data")

    current_data = _fetch_site2_catalog_snapshot_data()
    if not current_data:
        print("[site2] Could not fetch Site2 catalog snapshot data")
        return False

    current_products = current_data.get("products", [])
    current_categories = current_data.get("categories", [])
    current_routes = current_data.get("routes", [])
    print(
        f"[site2] Catalog snapshot returned {len(current_products)} product(s), "
        f"{len(current_categories)} categor(y/ies), {len(current_routes)} route(s)"
    )

    old_snapshot = load_snapshot("site2_catalog")
    if old_snapshot is None:
        print(
            f"[site2] First Site2 catalog snapshot "
            f"({len(current_products)} products / {len(current_categories)} categories)"
        )
        _save_snapshot_ascii("site2_catalog", current_data)
        return False

    old_data = old_snapshot.get("data", {})
    old_products = old_data.get("products", []) if isinstance(old_data, dict) else []
    old_categories = old_data.get("categories", []) if isinstance(old_data, dict) else []
    old_routes = old_data.get("routes", []) if isinstance(old_data, dict) else []

    if any(str(item.get("_id") or "").endswith(":0") for item in old_categories if isinstance(item, dict)):
        print("[site2] Updating catalog baseline to drop localized placeholder category 0")
        _save_snapshot_ascii("site2_catalog", current_data)
        return False

    products_changed = compute_hash(old_products) != compute_hash(current_products)
    categories_changed = compute_hash(old_categories) != compute_hash(current_categories)
    routes_changed = compute_hash(old_routes) != compute_hash(current_routes)

    if not products_changed and not categories_changed and not routes_changed:
        print("[site2] No catalog changes")
        return False

    changes_detected = products_changed or categories_changed

    if products_changed:
        old_product_items = get_items_by_id(old_products)
        new_product_items = get_items_by_id(current_products)
        added_products, updated_products, removed_products = compare_items(old_product_items, new_product_items)
        grouped_product_updates = build_grouped_item_updates(
            old_product_items,
            new_product_items,
            updated_products,
        )

        print(
            f"[site2] Product changes detected: +{len(added_products)} "
            f"~{len(grouped_product_updates)} -{len(removed_products)}"
        )

        if DISCORD_WEBHOOK_URL:
            if added_products:
                send_new_items_notification(
                    DISCORD_WEBHOOK_URL,
                    "Site2-Catalog-Products",
                    f"{_SITE2_BASE_URL}/produkte",
                    added_products,
                )
            if grouped_product_updates:
                send_updated_items_notification(
                    DISCORD_WEBHOOK_URL,
                    "Site2-Catalog-Products",
                    f"{_SITE2_BASE_URL}/produkte",
                    grouped_product_updates,
                )
            if removed_products:
                send_removed_items_notification(
                    DISCORD_WEBHOOK_URL,
                    "Site2-Catalog-Products",
                    f"{_SITE2_BASE_URL}/produkte",
                    removed_products,
                )

    if categories_changed:
        old_category_items = get_items_by_id(old_categories)
        new_category_items = get_items_by_id(current_categories)
        added_categories, updated_categories, removed_categories = compare_items(old_category_items, new_category_items)
        grouped_category_updates = build_grouped_item_updates(
            old_category_items,
            new_category_items,
            updated_categories,
        )

        print(
            f"[site2] Category changes detected: +{len(added_categories)} "
            f"~{len(grouped_category_updates)} -{len(removed_categories)}"
        )

        if DISCORD_WEBHOOK_URL:
            if added_categories:
                send_new_items_notification(
                    DISCORD_WEBHOOK_URL,
                    "Site2-Catalog-Categories",
                    f"{_SITE2_BASE_URL}/produkte",
                    added_categories,
                )
            if grouped_category_updates:
                send_updated_items_notification(
                    DISCORD_WEBHOOK_URL,
                    "Site2-Catalog-Categories",
                    f"{_SITE2_BASE_URL}/produkte",
                    grouped_category_updates,
                )
            if removed_categories:
                send_removed_items_notification(
                    DISCORD_WEBHOOK_URL,
                    "Site2-Catalog-Categories",
                    f"{_SITE2_BASE_URL}/produkte",
                    removed_categories,
                )

    if routes_changed:
        print(
            f"[site2] Route inventory changed: {len(old_routes)} -> {len(current_routes)} "
            "(notification handled by Site2 build tracker)"
        )

    _save_snapshot_ascii("site2_catalog", current_data)
    return changes_detected


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
    failed_sitemaps: List[str] = []
    
    # Fetch all sitemaps and extract URLs
    for sitemap_url in sitemap_urls:
        content = fetch_page(sitemap_url)
        if not content:
            failed_sitemaps.append(sitemap_url)
            continue
        # Extract URLs from XML
        import re as regex
        urls = regex.findall(r'<loc>(https?://[^<]+)</loc>', content)
        all_urls.update(urls)
    
    print(f"📊 Found {len(all_urls)} total URLs in Site5 sitemaps")
    
    # Load previous snapshot
    old_snapshot = load_snapshot("sitemap_site5")
    
    if old_snapshot is None:
        current_data = {
            "urls": sorted(list(all_urls)),
            "count": len(all_urls),
            "hash": hashlib.md5(str(sorted(all_urls)).encode()).hexdigest()
        }
        print(f"📝 First Site5 sitemap snapshot ({len(all_urls)} URLs)")
        save_snapshot("sitemap_site5", current_data)
        return False
    
    old_data = old_snapshot.get("data", {})
    old_urls = set(old_data.get("urls", []))
    effective_urls = set(all_urls)
    if failed_sitemaps:
        effective_urls |= old_urls
        print(
            f"⚠️  {len(failed_sitemaps)} Site5 sitemap(s) failed - suppressing removals for this run"
        )

    current_data = {
        "urls": sorted(list(effective_urls)),
        "count": len(effective_urls),
        "hash": hashlib.md5(str(sorted(effective_urls)).encode()).hexdigest()
    }
    
    # Check for new/removed URLs
    new_urls = all_urls - old_urls
    removed_urls = set() if failed_sitemaps else (old_urls - all_urls)
    
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
    
    if changes_detected or failed_sitemaps:
        save_snapshot("sitemap_site5", current_data)
        return changes_detected
    
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
    failed_sub_sitemaps: List[str] = []
    
    # Fetch each sub-sitemap and extract page URLs
    for sub_sitemap in sub_sitemaps:
        sub_content = fetch_page(sub_sitemap)
        if not sub_content:
            failed_sub_sitemaps.append(sub_sitemap)
            continue
        urls = regex.findall(r'<loc>(https?://[^<]+)</loc>', sub_content)
        # Filter out .xml files to get actual page URLs
        page_urls = [u for u in urls if not u.endswith('.xml')]
        all_urls.update(page_urls)
    
    print(f"📊 Found {len(all_urls)} total URLs in Site4 sitemaps")
    
    # Load previous snapshot
    old_snapshot = load_snapshot("sitemap_site4")
    
    if old_snapshot is None:
        current_data = {
            "urls": sorted(list(all_urls)),
            "count": len(all_urls),
            "hash": hashlib.md5(str(sorted(all_urls)).encode()).hexdigest()
        }
        print(f"📝 First Site4 sitemap snapshot ({len(all_urls)} URLs)")
        save_snapshot("sitemap_site4", current_data)
        return False
    
    old_data = old_snapshot.get("data", {})
    old_urls = set(old_data.get("urls", []))
    effective_urls = set(all_urls)
    if failed_sub_sitemaps:
        effective_urls |= old_urls
        print(
            f"⚠️  {len(failed_sub_sitemaps)} Site4 sub-sitemap(s) failed - suppressing removals for this run"
        )

    current_data = {
        "urls": sorted(list(effective_urls)),
        "count": len(effective_urls),
        "hash": hashlib.md5(str(sorted(effective_urls)).encode()).hexdigest()
    }
    
    new_urls = all_urls - old_urls
    removed_urls = set() if failed_sub_sitemaps else (old_urls - all_urls)
    
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
    
    if changes_detected or failed_sub_sitemaps:
        save_snapshot("sitemap_site4", current_data)
        return changes_detected
    
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
            from urllib.parse import urlparse

            routes_with_content = []
            pending_routes_to_add = []

            import time
            for url in sorted(new_urls):
                parsed = urlparse(url)
                if parsed.netloc and parsed.netloc != "drjoedispenza.com":
                    continue

                route = parsed.path or "/"
                if parsed.query:
                    route = f"{route}?{parsed.query}"

                time.sleep(0.3)
                result = _fetch_route_content("https://drjoedispenza.com", route)
                routes_with_content.append(result)

                if result["status"] == "pending":
                    pending_routes_to_add.append({
                        "route": route,
                        "base_url": "https://drjoedispenza.com",
                        "first_seen": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    })

            if pending_routes_to_add:
                pending = _load_pending_routes()
                if "Site1" not in pending:
                    pending["Site1"] = []
                existing_routes = {r["route"] for r in pending["Site1"]}
                for entry in pending_routes_to_add:
                    if entry["route"] not in existing_routes:
                        pending["Site1"].append(entry)
                _save_pending_routes(pending)
                print(f"   ?? Added {len(pending_routes_to_add)} route(s) to watch-list")

            if routes_with_content:
                send_new_route_with_content_notification(
                    DISCORD_WEBHOOK_URL,
                    "Site1-Sitemap",
                    "https://drjoedispenza.com",
                    routes_with_content
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
    
    # Filter out blogs, stories, and individual product pages.
    # Product-detail routes are intentionally handled by inventory/homepage-specific
    # trackers so Site1 content alerts do not duplicate shop notifications.
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


def track_site7_helpcenter() -> bool:
    """
    Track Site7 help center for changes.
    
    This function:
    1. Crawls the entire help center to discover all pages (collections and articles)
    2. Detects new and removed pages
    3. Tracks content changes on each individual page
    """
    import time
    
    print("\n📡 Tracking: Site7 Help Center")
    
    BASE_URL = "https://hilfe.drjoedispenza.de"
    START_URL = f"{BASE_URL}/de/"
    
    def extract_site7_links(html: str, base_url: str) -> set:
        """Extract all internal help center links from HTML."""
        links = set()
        # Match relative and absolute href links
        patterns = [
            r'href="(/de/[^"#]+)"',
            r'href="(' + re.escape(BASE_URL) + r'/de/[^"#]+)"',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, html)
            for match in matches:
                if match.startswith("/"):
                    full_url = base_url + match
                else:
                    full_url = match
                # Normalize URL (remove trailing slash for consistency)
                full_url = full_url.rstrip("/")
                # Only include actual content pages (collections and articles)
                if "/de/collections/" in full_url or "/de/articles/" in full_url:
                    links.add(full_url)
                elif full_url == f"{base_url}/de":
                    links.add(full_url)
        return links
    
    def crawl_site7() -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
        """Crawl the entire Site7 help center and return discovered pages plus failed fetches."""
        discovered = {}
        to_visit = {START_URL.rstrip("/")}
        visited = set()
        failed_urls: List[str] = []
        
        while to_visit:
            url = to_visit.pop()
            if url in visited:
                continue
            visited.add(url)
            
            html = fetch_page(url)
            if not html:
                failed_urls.append(url)
                continue
            
            # Extract text content for hashing
            clean_body = _extract_clean_body_html(html)
            text_content = _extract_text_from_body_html(
                clean_body,
                exclude_section_headings=[],
                exclude_container_class_substrings=["avatar__info", "related_articles"]
            )
            text_content = _filter_site7_helpcenter_text(text_content)
            content_hash = hashlib.md5(text_content.encode()).hexdigest()
            title = _extract_title_from_html(html) or url.split("/")[-1]
            
            discovered[url] = {
                "hash": content_hash,
                "title": title,
                "text": text_content  # Store full text for diff calculation
            }
            
            # Find more links
            new_links = extract_site7_links(html, BASE_URL)
            for link in new_links:
                if link not in visited:
                    to_visit.add(link)
            
            # Rate limiting
            time.sleep(0.15)
        
        return discovered, failed_urls
    
    # Crawl the site
    print("   🔍 Crawling Site7 help center...")
    current_pages, crawl_errors = crawl_site7()
    
    print(f"   📊 Found {len(current_pages)} pages")
    if crawl_errors:
        print(
            f"   ⚠️ Crawl had {len(crawl_errors)} fetch error(s) - preserving old pages and suppressing removals"
        )
    
    if not current_pages:
        print("   ⚠️ No pages found - skipping")
        return False
    
    # Load previous snapshot
    old_snapshot = load_snapshot("site7_helpcenter")
    old_data = old_snapshot.get("data", {}) if old_snapshot else {}
    old_filter_version = old_data.get("filter_version")
    baseline_reset = old_snapshot is not None and old_filter_version != _SITE7_HELP_FILTER_VERSION
    if baseline_reset:
        print(
            "   ℹ️ Site7-Filter geändert - Baseline wird aktualisiert, keine Content-Benachrichtigungen in diesem Lauf"
        )
    
    if old_snapshot is None:
        current_data = {
            "pages": current_pages,
            "urls": sorted(current_pages.keys()),
            "count": len(current_pages),
            "last_crawled": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "filter_version": _SITE7_HELP_FILTER_VERSION,
        }
        print(f"   📝 First Site7 snapshot ({len(current_pages)} pages)")
        save_snapshot("site7_helpcenter", current_data)
        return False
    
    old_pages = old_data.get("pages", {})
    old_urls = set(old_data.get("urls", []))
    effective_pages = dict(current_pages)
    if crawl_errors:
        for url, old_page in old_pages.items():
            if url not in effective_pages:
                effective_pages[url] = old_page
    current_urls = set(effective_pages.keys())

    current_data = {
        "pages": effective_pages,
        "urls": sorted(current_urls),
        "count": len(current_urls),
        "last_crawled": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "filter_version": _SITE7_HELP_FILTER_VERSION,
    }
    
    changes_detected = False
    
    # Check for new pages
    fresh_current_urls = set(current_pages.keys())
    new_urls = fresh_current_urls - old_urls
    if new_urls:
        print(f"   🆕 New Site7 pages: {len(new_urls)}")
        for url in sorted(new_urls)[:5]:
            title = current_pages[url].get("title", "")
            print(f"      + {title[:40]}..." if len(title) > 40 else f"      + {title}")
        changes_detected = True
        
        if DISCORD_WEBHOOK_URL:
            new_items = []
            for url in sorted(new_urls)[:10]:
                new_items.append({
                    "title": current_pages[url].get("title", url.split("/")[-1]),
                    "url": url
                })
            send_new_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site7-Helpcenter",
                START_URL,
                new_items
            )
    
    # Check for removed pages
    removed_urls = set() if crawl_errors else (old_urls - current_urls)
    if removed_urls:
        print(f"   🗑️ Removed Site7 pages: {len(removed_urls)}")
        for url in sorted(removed_urls)[:5]:
            title = old_pages.get(url, {}).get("title", url.split("/")[-1])
            print(f"      - {title[:40]}..." if len(title) > 40 else f"      - {title}")
        changes_detected = True
        
        if DISCORD_WEBHOOK_URL:
            removed_items = []
            for url in sorted(removed_urls)[:10]:
                removed_items.append({
                    "title": old_pages.get(url, {}).get("title", url.split("/")[-1]),
                    "url": url
                })
            send_removed_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site7-Helpcenter",
                START_URL,
                removed_items
            )
    
    # Check for content changes on existing pages
    content_changes = []
    if not baseline_reset:
        for url in fresh_current_urls & old_urls:
            old_hash = old_pages.get(url, {}).get("hash", "")
            new_hash = current_pages[url].get("hash", "")
            if old_hash and new_hash and old_hash != new_hash:
                content_changes.append({
                    "url": url,
                    "title": current_pages[url].get("title", url.split("/")[-1])
                })
    
    if content_changes:
        print(f"   📝 Content changed on {len(content_changes)} pages:")
        for change in content_changes[:5]:
            title = change["title"]
            print(f"      ~ {title[:40]}..." if len(title) > 40 else f"      ~ {title}")
        changes_detected = True
        
        if DISCORD_WEBHOOK_URL:
            updates = []
            for change in content_changes[:DISCORD_MAX_CHANGES]:
                details_lines = []
                details_lines.append(f"**{change['title']}**")
                details_lines.append(f"URL: {change['url']}")
                
                # Get old and new text for diff
                old_text = old_pages.get(change["url"], {}).get("text", "")
                new_text = current_pages[change["url"]].get("text", "")
                
                if old_text and new_text:
                    diff_summary = _summarize_text_diff(old_text, new_text, context_lines=0)
                    if diff_summary:
                        details_lines.append("Diff (rot = entfernt, grün = neu):")
                        details_lines.append(diff_summary)
                    else:
                        details_lines.append("Hinweis: Kein Textunterschied erkennbar (evtl. nur HTML/Struktur).")
                else:
                    details_lines.append("Hinweis: Text-Baseline wurde neu erstellt; Diff ist ab dem nächsten Lauf verfügbar.")
                
                updates.append({
                    "id": change["url"],
                    "field": "content",
                    "type": _truncate_for_discord_field_name(f"📝 {change['title']}"),
                    "details": "\n".join(details_lines)
                })
            send_updated_items_notification(
                DISCORD_WEBHOOK_URL,
                "Site7-Helpcenter",
                START_URL,
                updates
            )
    
    if changes_detected or baseline_reset or crawl_errors:
        save_snapshot("site7_helpcenter", current_data)
        return changes_detected
    
    print("   ✅ No Site7 changes detected")
    return False

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

    # Track Site2 catalog products/categories directly from the public page payloads
    try:
        if track_site2_catalog():
            changes_detected = True
    except Exception as e:
        print(f"[site2] Error tracking catalog data: {e}")
    
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
    
    # Track Site1 shop inventory directly via API
    try:
        if track_site1_inventory_api():
            changes_detected = True
    except Exception as e:
        print(f"âŒ Error tracking Site1 inventory API: {e}")

    # Track the broader public Site1 catalog feed (suppressed for inventory-covered items)
    try:
        if track_site1_public_catalog_api():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking public catalog API: {e}")

    # Track public Site1 category metadata from the shop backend
    try:
        if track_site1_public_categories():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking public categories: {e}")

    # Track public subscription metadata such as Dr Joe Live pricing/content
    try:
        if track_site1_subscriptions_api():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking subscriptions API: {e}")

    # Track public policy/disclaimer content exposed by the storefront backend
    try:
        if track_site1_policies_api():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking policies API: {e}")

    # Track public community group metadata, including Brightcove-backed conversation IDs
    try:
        if track_site1_community_groups():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking community groups: {e}")

    # Track backend routing config for early feature/backend rollout changes
    try:
        if track_site1_routing_config():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking routing config: {e}")

    # Track homepage announcement/banner settings exposed via public Realm data
    try:
        if track_site1_media_settings():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking homepage announcement settings: {e}")

    # Track upcoming/live Dr Joe Live metadata before it necessarily lands in page HTML
    try:
        if track_site1_drjoe_live_preview():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking Dr Joe Live preview data: {e}")

    # Track Brightcove video IDs exposed through public Site1 APIs
    try:
        if track_site1_brightcove_refs():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking Brightcove references: {e}")

    # Track upcoming event products from the public Realm-backed preview feed
    try:
        if track_site1_event_preview():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking event preview products: {e}")

    # Track homepage-linked Site1 product-detail routes (covers non-inventory products/resources)
    try:
        if track_site1_homepage_product_details():
            changes_detected = True
    except Exception as e:
        print(f"[site1] Error tracking homepage product-detail routes: {e}")

    # Track public event list shown in the MyMM mobile app
    try:
        if track_mymm_app_events():
            changes_detected = True
    except Exception as e:
        print(f"[app] Error tracking MyMM app events: {e}")

    # Track YouTube channel for new videos
    try:
        if track_youtube_channel():
            changes_detected = True
    except Exception as e:
        print(f"❌ Error tracking YouTube channel: {e}")
    
    # Track Site7 help center (hilfe.drjoedispenza.de) for new pages and content changes
    try:
        if track_site7_helpcenter():
            changes_detected = True
    except Exception as e:
        print(f"❌ Error tracking Site7 help center: {e}")
    
    # Check pending routes watch-list (routes discovered but not yet live)
    try:
        if track_pending_routes():
            changes_detected = True
    except Exception as e:
        print(f"❌ Error checking pending routes: {e}")
    
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
