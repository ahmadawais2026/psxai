"""
fetch_news_content.py
=====================
Enriches market_data/news/latest_news.json by scraping full article text
for each news item that only has a short `description` snippet.

Run after fetch_news() (or scrape_askanalyst_market.py) refreshes the
news list.  Already-scraped items are skipped (idempotent).

Usage:
  python fetch_news_content.py
  python fetch_news_content.py --force    # re-scrape even existing content
  python fetch_news_content.py --limit 20 # only scrape first N items
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE_DIR   = Path(__file__).parent
NEWS_PATH  = BASE_DIR / "market_data" / "news" / "latest_news.json"
MIN_CONTENT_LEN = 250   # chars; shorter than this is treated as "no content"
REQUEST_TIMEOUT = 12    # seconds per article
RATE_LIMIT_SEC  = 0.6   # polite delay between requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Domain → ordered list of CSS selectors to try (first match with enough text wins)
DOMAIN_SELECTORS: dict[str, list[str]] = {
    "www.brecorder.com":    ["article", ".story-content", ".content-body", "div.story"],
    "epaper.brecorder.com": ["div.article-body", ".content-details", ".article-text",
                              "article", "div.col-md-8"],
    "tribune.com.pk":       ["span.story-text", ".story-content", "article",
                              ".content-area", "div.story-body"],
    "www.dawn.com":         ["article", ".story__content", ".story-body",
                              "div.template-story"],
    "www.thenews.pk":       ["div.siteContent", "article", ".news-detail",
                              "div.article-body"],
    "mettisglobal.news":    ["article", ".entry-content", ".post-content",
                              "div.td-post-content"],
}
# Generic fallback selectors tried for unknown domains
GENERIC_SELECTORS = [
    "article", "main", ".article-body", ".story-content",
    ".content-body", ".entry-content", ".post-body",
    "div.content", "#article-content",
]


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _clean(text: str) -> str:
    """Collapse whitespace and remove boilerplate fragments."""
    text = re.sub(r"\s+", " ", text).strip()
    # Trim off common footer noise (share buttons, related links, etc.)
    for cutoff in ("Related Stories", "Also Read", "Read More", "Subscribe to",
                   "Follow us on", "Copyright ©", "All rights reserved"):
        idx = text.lower().find(cutoff.lower())
        if idx > MIN_CONTENT_LEN:
            text = text[:idx].strip()
    return text


def scrape_article(url: str) -> Optional[str]:
    """
    Fetch `url` and return the article body text, or None if unavailable.
    Tries domain-specific selectors first, then generic fallbacks.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                            allow_redirects=True)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"    [!] fetch error: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    domain = _domain(url)
    selectors = DOMAIN_SELECTORS.get(domain, []) + GENERIC_SELECTORS

    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            text = _clean(el.get_text(" ", strip=True))
            if len(text) >= MIN_CONTENT_LEN:
                return text

    return None


def enrich_news(force: bool = False, limit: Optional[int] = None) -> None:
    if not NEWS_PATH.exists():
        print(f"[-] News file not found: {NEWS_PATH}")
        return

    with open(NEWS_PATH, encoding="utf-8") as f:
        items: list[dict] = json.load(f)

    print(f"[*] {len(items)} news items loaded from {NEWS_PATH.name}")

    enriched = skipped = failed = 0
    processed = 0

    for item in items:
        if limit and processed >= limit:
            break

        title = (item.get("title") or "")[:70]
        url   = item.get("link") or ""

        # Skip if already has sufficient content
        existing = item.get("content") or ""
        if not force and len(existing) >= MIN_CONTENT_LEN:
            skipped += 1
            continue

        if not url:
            item["content"] = item.get("description") or ""
            skipped += 1
            continue

        print(f"  [{processed + 1}] {title}")
        content = scrape_article(url)
        processed += 1

        if content and len(content) >= MIN_CONTENT_LEN:
            item["content"] = content
            words = len(content.split())
            print(f"    [OK] {words} words from {_domain(url)}")
            enriched += 1
        else:
            # Fall back to description so the field always exists
            item["content"] = item.get("description") or ""
            print(f"    [~] scraped too short — keeping description ({_domain(url)})")
            failed += 1

        time.sleep(RATE_LIMIT_SEC)

    # Save enriched file
    with open(NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n[done] enriched={enriched}  kept-description={failed}  skipped={skipped}")
    print(f"       saved -> {NEWS_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich PSX news with full article text")
    parser.add_argument("--force",  action="store_true", help="Re-scrape even items that already have content")
    parser.add_argument("--limit",  type=int, default=None, help="Only process first N items")
    args = parser.parse_args()
    enrich_news(force=args.force, limit=args.limit)
