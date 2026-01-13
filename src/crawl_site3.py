"""
Deep URL Discovery Script for Site3 (drjoedispenza.de)
Recursively crawls all reachable pages to find every accessible URL.
"""

import urllib.request
import urllib.error
import re
import gzip
import time
from typing import Set, Dict
from collections import defaultdict

# All known entry points - add more if you discover hidden series
SEED_URLS = [
    # Main site pages
    "https://www.drjoedispenza.de/",
    "https://www.drjoedispenza.de/mission",
    "https://www.drjoedispenza.de/dr-joe-live",
    "https://www.drjoedispenza.de/angebotsubersicht",
    "https://www.drjoedispenza.de/videos",
    "https://www.drjoedispenza.de/testimonials",
    
    # Blog archives
    "https://www.drjoedispenza.de/ubersicht-blogbeitrage-2026",
    "https://www.drjoedispenza.de/ubersicht-blogbeitrage-2025",
    "https://www.drjoedispenza.de/ubersicht-blogbeitrage-2024",
    "https://www.drjoedispenza.de/ubersicht-blogbeitrage-2023",
    "https://www.drjoedispenza.de/ubersicht-blogbeitrage-2022",
    
    # Course/product pages
    "https://www.drjoedispenza.de/source-filmbundle",
    "https://www.drjoedispenza.de/the-formula-2025",
    "https://www.drjoedispenza.de/redesigning-your-destiny",
    "https://www.drjoedispenza.de/supernatural-change",
    
    # Hidden series (Generating Series)
    "https://www.drjoedispenza.de/warum-du-nicht-nachsehen-solltest",
    "https://www.drjoedispenza.de/generating-series-herzlich-willkommen-2026",
    
    # New User Provided Course URLs
    "https://www.drjoedispenza.de/segnung-der-energiezentren-v",
    "https://www.drjoedispenza.de/tag-2",
    "https://www.drjoedispenza.de/source-willkommen-2025",
    
    # Legal pages
    "https://www.drjoedispenza.de/agb",
    "https://www.drjoedispenza.de/datenschutz",
    "https://www.drjoedispenza.de/impressum",
]

def fetch_page(url: str) -> str:
    """Fetch a page and return its HTML content."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "gzip, deflate",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""
        print(f"  ❌ HTTP {e.code}: {url}")
        return ""
    except Exception as e:
        print(f"  ❌ Error: {url} - {e}")
        return ""

def extract_site3_links(html: str) -> Set[str]:
    """Extract all drjoedispenza.de links from HTML."""
    pattern = r'href=["\']?(https?://(?:www\.)?drjoedispenza\.de[^"\'> #]*)'
    links = set(re.findall(pattern, html, re.IGNORECASE))
    
    # Normalize URLs
    normalized = set()
    for link in links:
        # Remove trailing slashes, anchors, and query params for consistency (optional)
        link = link.split('#')[0].rstrip('/')
        # Skip non-page resources
        if any(ext in link.lower() for ext in ['.jpg', '.png', '.gif', '.pdf', '.css', '.js', '.xml']):
            continue
        if link:
            normalized.add(link)
    return normalized

def deep_crawl_site3(max_pages: int = 200) -> Dict[str, Set[str]]:
    """
    Deep crawl Site3 following all internal links.
    Returns dict with 'all_urls' and 'by_category'.
    """
    discovered: Set[str] = set()
    to_visit = set(SEED_URLS)
    visited: Set[str] = set()
    pages_crawled = 0
    
    # Track which seed found which URLs
    url_sources: Dict[str, str] = {}
    
    print(f"🚀 Starting deep crawl with {len(SEED_URLS)} seed URLs...")
    print("=" * 70)
    
    while to_visit and pages_crawled < max_pages:
        url = to_visit.pop()
        
        if url in visited:
            continue
            
        visited.add(url)
        pages_crawled += 1
        
        # Rate limiting
        time.sleep(0.3)
        
        short_url = url.replace("https://www.drjoedispenza.de", "")
        print(f"[{pages_crawled:3d}] 📡 {short_url or '/'}")
        
        html = fetch_page(url)
        if not html:
            continue
        
        new_links = extract_site3_links(html)
        
        for link in new_links:
            if link not in discovered:
                discovered.add(link)
                if link not in url_sources:
                    url_sources[link] = url
            if link not in visited:
                to_visit.add(link)
    
    # Categorize URLs
    categories: Dict[str, Set[str]] = defaultdict(set)
    
    for url in discovered:
        path = url.replace("https://www.drjoedispenza.de", "").lower()
        
        if "/blog" in path or "blogbeitrage" in path:
            categories["blogs"].add(url)
        elif any(x in path for x in ["/tag-", "erzeuge-", "generating", "nachsehen"]):
            categories["generating_series"].add(url)
        elif any(x in path for x in ["/agb", "/datenschutz", "/impressum"]):
            categories["legal"].add(url)
        elif any(x in path for x in ["formula", "redesigning", "source", "supernatural", "abundance"]):
            categories["courses"].add(url)
        elif any(x in path for x in ["/mission", "/videos", "/testimonials", "/dr-joe"]):
            categories["main_pages"].add(url)
        else:
            categories["other"].add(url)
    
    return {
        "all_urls": discovered,
        "by_category": dict(categories),
        "url_sources": url_sources,
        "pages_crawled": pages_crawled,
    }

if __name__ == "__main__":
    result = deep_crawl_site3(max_pages=150)
    
    print("\n" + "=" * 70)
    print(f"✅ CRAWL COMPLETE - Found {len(result['all_urls'])} unique URLs")
    print(f"   Pages crawled: {result['pages_crawled']}")
    print("=" * 70)
    
    # Print by category
    for category, urls in sorted(result["by_category"].items()):
        print(f"\n📁 {category.upper()} ({len(urls)} URLs):")
        print("-" * 40)
        for url in sorted(urls):
            short = url.replace("https://www.drjoedispenza.de", "")
            print(f"  {short or '/'}")
    
    # Save to file
    with open("site3_all_urls.txt", "w", encoding="utf-8") as f:
        f.write(f"# Site3 (drjoedispenza.de) - All Discovered URLs\n")
        f.write(f"# Crawled at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Total URLs: {len(result['all_urls'])}\n\n")
        
        for category, urls in sorted(result["by_category"].items()):
            f.write(f"\n## {category.upper()}\n")
            for url in sorted(urls):
                f.write(f"{url}\n")
    
    print(f"\n💾 Saved all URLs to site3_all_urls.txt")
