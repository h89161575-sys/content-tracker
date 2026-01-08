# Configuration for Website Change Tracker

import os
from dataclasses import dataclass
from typing import Optional

@dataclass
class PageConfig:
    """Configuration for a single page to track."""
    name: str
    url: str
    data_path: str  # JSONPath-like path to extract from __NEXT_DATA__
    
PAGES_TO_TRACK = [
    # === Site 1 (Main English site) ===
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
        name="Site1-Blog",
        url="https://drjoedispenza.com/dr-joes-blog",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Site1-Live",
        url="https://drjoedispenza.com/dr-joe-live",
        data_path="props.pageProps"
    ),
    
    # === Site 2 (German shop) ===
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
]

# Discord Webhook URL - set via environment variable
DISCORD_WEBHOOK_URL: Optional[str] = os.environ.get("DISCORD_WEBHOOK_URL")

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
