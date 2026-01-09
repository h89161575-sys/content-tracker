# Configuration for Website Change Tracker

import os
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class PageConfig:
    """Configuration for a single page to track."""
    name: str
    url: str
    data_path: str  # JSONPath-like path to extract from __NEXT_DATA__
    
PAGES_TO_TRACK = [
    # === Site 1 ===
    PageConfig(
        name="Site1-Home",
        url="https://drjoedispenza.com/",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Site1-Retreats",
        url="https://drjoedispenza.com/retreats",
        data_path="props.pageProps.upcomingOccasions"
    ),
    PageConfig(
        name="Site1-Shop",
        url="https://drjoedispenza.com/shop/categories?shopSection=All%20Products",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Site1-Shop-German",
        url="https://drjoedispenza.com/shop/categories?shopSection=All%20Products&f=Deutsch",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Site1-Blog",
        url="https://drjoedispenza.com/dr-joes-blog",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Site1-Live",
        url="https://drjoedispenza.com/dr-joe-live",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Site1-Support",
        url="https://support.drjoedispenza.com/",
        data_path="props.pageProps"
    ),
    
    # === Site 2 ===
    PageConfig(
        name="Site2-Home",
        url="https://drjoedispenza.info/s/Drjoedispenza",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Site2-Products",
        url="https://drjoedispenza.info/s/Drjoedispenza/produkte",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Site2-Courses",
        url="https://drjoedispenza.info/s/Drjoedispenza/dispenza-onlinekurse",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Site2-Blog",
        url="https://drjoedispenza.info/s/Drjoedispenza/blog",
        data_path="props.pageProps"
    ),
    # Site 2 - Product Categories (track content changes for each category)
    PageConfig(name="Site2-Books", url="https://drjoedispenza.info/s/Drjoedispenza/produkte?block_2912279_group_id=16407", data_path="props.pageProps"),
    PageConfig(name="Site2-Meditations", url="https://drjoedispenza.info/s/Drjoedispenza/produkte?block_2912279_group_id=4346", data_path="props.pageProps"),
    PageConfig(name="Site2-Audiobooks", url="https://drjoedispenza.info/s/Drjoedispenza/produkte?block_2912279_group_id=4348", data_path="props.pageProps"),
    PageConfig(name="Site2-CDs", url="https://drjoedispenza.info/s/Drjoedispenza/produkte?block_2912279_group_id=16408", data_path="props.pageProps"),
    PageConfig(name="Site2-Music", url="https://drjoedispenza.info/s/Drjoedispenza/produkte?block_2912279_group_id=4347", data_path="props.pageProps"),
    PageConfig(name="Site2-Free", url="https://drjoedispenza.info/s/Drjoedispenza/produkte?block_2912279_group_id=4350", data_path="props.pageProps"),
    PageConfig(name="Site2-Mask", url="https://drjoedispenza.info/s/Drjoedispenza/produkte?block_2912279_group_id=16188", data_path="props.pageProps"),
    PageConfig(name="Site2-Contact", url="https://drjoedispenza.info/s/Drjoedispenza/kontakt_seite", data_path="props.pageProps"),
    
    # === Site 3 (German Marketing Site - drjoedispenza.de) ===
    PageConfig(name="Site3-Home", url="https://www.drjoedispenza.de", data_path="props.pageProps"),
    PageConfig(name="Site3-Mission", url="https://www.drjoedispenza.de/mission", data_path="props.pageProps"),
    PageConfig(name="Site3-Source", url="https://www.drjoedispenza.de/source-filmbundle", data_path="props.pageProps"),
    PageConfig(name="Site3-Formula", url="https://www.drjoedispenza.de/the-formula-2025", data_path="props.pageProps"),
    PageConfig(name="Site3-Redesigning", url="https://www.drjoedispenza.de/redesigning-your-destiny", data_path="props.pageProps"),
    PageConfig(name="Site3-Supernatural", url="https://www.drjoedispenza.de/supernatural-change", data_path="props.pageProps"),
    PageConfig(name="Site3-DrJoeLive", url="https://www.drjoedispenza.de/dr-joe-live", data_path="props.pageProps"),
    PageConfig(name="Site3-Blogs", url="https://www.drjoedispenza.de/ubersicht-blogbeitrage-2025", data_path="props.pageProps"),
    PageConfig(name="Site3-Testimonials", url="https://www.drjoedispenza.de/testimonials", data_path="props.pageProps"),
    PageConfig(name="Site3-Videos", url="https://www.drjoedispenza.de/videos", data_path="props.pageProps"),
    PageConfig(name="Site3-Copenhagen", url="https://www.drjoedispenza.de/kopenhagen-warteliste-2026", data_path="props.pageProps"),
    
    # === Site 4 ===
    PageConfig(name="Site4-Home", url="https://metamorphllc.net/", data_path="props.pageProps"),
    
    # === Site 5 ===
    PageConfig(name="Site5-Home", url="https://innerscienceresearch.org/", data_path="props.pageProps"),
    PageConfig(name="Site5-About", url="https://innerscienceresearch.org/about/", data_path="props.pageProps"),
    PageConfig(name="Site5-Research", url="https://innerscienceresearch.org/research/", data_path="props.pageProps"),
    PageConfig(name="Site5-Explore", url="https://innerscienceresearch.org/explore/", data_path="props.pageProps"),
    PageConfig(name="Site5-Events", url="https://innerscienceresearch.org/join-us/", data_path="props.pageProps"),
    PageConfig(name="Site5-Donate", url="https://innerscienceresearch.org/donate/", data_path="props.pageProps"),
    PageConfig(name="Site5-FAQ", url="https://innerscienceresearch.org/faq/", data_path="props.pageProps"),
    PageConfig(name="Site5-Partners", url="https://innerscienceresearch.org/shared-mission-organizations/", data_path="props.pageProps"),
    PageConfig(name="Site5-Stream", url="https://innerscienceresearch.org/stream/", data_path="props.pageProps"),
    PageConfig(name="Site5-Invitations", url="https://innerscienceresearch.org/invitations/", data_path="props.pageProps"),
    PageConfig(name="Site5-Live", url="https://innerscienceresearch.org/live/", data_path="props.pageProps"),
    PageConfig(name="Site5-CatalystBusiness", url="https://innerscienceresearch.org/global-catalyst-business/", data_path="props.pageProps"),
]

# Discord Webhook URL - set via environment variable
DISCORD_WEBHOOK_URL: Optional[str] = os.environ.get("DISCORD_WEBHOOK_URL")

# Discord notification display limit (Discord embed limit: max 25 fields per embed)
# You can override this via environment variable DISCORD_MAX_CHANGES.
DISCORD_MAX_CHANGES = max(1, min(int(os.environ.get("DISCORD_MAX_CHANGES", "20")), 25))

# Site1 content tracker: section headings to exclude from hashing/diffing to reduce noise.
# Override via env var SITE1_CONTENT_EXCLUDE_SECTION_HEADINGS (comma-separated list).
_SITE1_CONTENT_EXCLUDE_RAW = os.environ.get(
    "SITE1_CONTENT_EXCLUDE_SECTION_HEADINGS",
    "Recommended Scientific Research",
)
SITE1_CONTENT_EXCLUDE_SECTION_HEADINGS: List[str] = [
    h.strip() for h in _SITE1_CONTENT_EXCLUDE_RAW.split(",") if h.strip()
]

# Site1 content tracker: HTML class substrings to exclude entirely (useful to ignore
# dynamic recommendation widgets that cause noisy updates).
# Override via env var SITE1_CONTENT_EXCLUDE_HTML_CLASS_SUBSTRINGS (comma-separated list).
_SITE1_CONTENT_EXCLUDE_CLASS_RAW = os.environ.get(
    "SITE1_CONTENT_EXCLUDE_HTML_CLASS_SUBSTRINGS",
    "recommendedBlogs",
)
SITE1_CONTENT_EXCLUDE_HTML_CLASS_SUBSTRINGS: List[str] = [
    c.strip() for c in _SITE1_CONTENT_EXCLUDE_CLASS_RAW.split(",") if c.strip()
]

# Path to store snapshots
SNAPSHOTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "snapshots")

# Keys to ignore when comparing (these change frequently but aren't meaningful)
IGNORE_KEYS = [
    "__N_SSP",
    "__N_SSG", 
    "isFallback",
    "gssp",
    "dynamicIds",
    "scriptLoader",
    "locale",
    "locales",
    "defaultLocale",
    "isPreview",
    "notFoundSrcPage",
]

# Keys that contain timestamps or session data (normalize these)
TIMESTAMP_KEYS = [
    "createdAt",
    "updatedAt", 
    "lastModified",
    "timestamp",
    "_updatedAt",
    "_createdAt",
]

# YouTube Channel ID to track (Dr. Joe Dispenza)
# RSS Feed URL: https://www.youtube.com/feeds/videos.xml?channel_id=UCSTTPGPS-lm0YVb4DMJ3lTA
YOUTUBE_CHANNEL_ID = "UCSTTPGPS-lm0YVb4DMJ3lTA"
