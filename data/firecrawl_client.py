"""
data/firecrawl_client.py
═════════════════════════════════════════════════════════════════════════
Thin wrapper over the Firecrawl v2 REST API for extracting clean markdown
from unstructured web pages — news articles and research reports — where
plain HTML scraping (brittle CSS selectors) or JS-rendered SPAs fail.

Firecrawl is used ONLY for unstructured content. Structured numeric data
(financial statements, OHLCV prices) comes from the AskAnalyst / PSX DPS
JSON APIs, which are faster, free, and cleaner — never scrape those.

Configuration:
    FIRECRAWL_API_KEY   — required; absent ⇒ all calls return None so the
                          caller can fall back to its own scraper.

Cost note: each scrape consumes Firecrawl credits, so callers should cache
results (e.g. on the news/report item) and avoid re-scraping.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.firecrawl.dev/v2/scrape"
_DEFAULT_TIMEOUT = 40  # seconds; JS-rendered pages can be slow


def is_configured() -> bool:
    """True when a Firecrawl API key is available."""
    return bool(os.getenv("FIRECRAWL_API_KEY"))


def scrape_markdown(
    url: str,
    only_main_content: bool = True,
    wait_for_ms: Optional[int] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Optional[str]:
    """
    Scrape *url* and return clean markdown, or None on any failure / when
    no API key is configured (so the caller can fall back).

    Args:
        url:                The page to scrape.
        only_main_content:  Strip nav/footer/sidebar boilerplate.
        wait_for_ms:        Optional JS-render wait for SPA pages.
        timeout:            HTTP timeout in seconds.
    """
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        return None
    if not url:
        return None

    payload = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": only_main_content,
    }
    if wait_for_ms:
        payload["waitFor"] = wait_for_ms

    try:
        resp = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        logger.warning("Firecrawl request failed for %s: %s", url, e)
        return None

    if resp.status_code != 200:
        logger.warning("Firecrawl HTTP %s for %s: %s", resp.status_code, url, resp.text[:200])
        return None

    try:
        data = resp.json()
    except ValueError:
        logger.warning("Firecrawl returned non-JSON for %s", url)
        return None

    if not data.get("success"):
        logger.warning("Firecrawl unsuccessful for %s: %s", url, str(data)[:200])
        return None

    markdown = (data.get("data") or {}).get("markdown")
    return markdown or None
