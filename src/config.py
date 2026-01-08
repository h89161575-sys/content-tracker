# Configuration for Dr. Joe Dispenza Website Tracker

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
    PageConfig(
        name="Homepage",
        url="https://drjoedispenza.com/",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Retreats",
        url="https://drjoedispenza.com/retreats",
        data_path="props.pageProps.upcomingOccasions"
    ),
    PageConfig(
        name="Shop",
        url="https://drjoedispenza.com/shop/categories?shopSection=All%20Products",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="Blog",
        url="https://drjoedispenza.com/dr-joes-blog",
        data_path="props.pageProps"
    ),
    PageConfig(
        name="DrJoeLive",
        url="https://drjoedispenza.com/dr-joe-live",
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
