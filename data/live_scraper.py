"""
data/live_scraper.py
Targeted live news fetch called once per analysis run.
Pulls fresh company-specific news from AskAnalyst and refreshes
the general PSX news file — both used as context by the agent pipeline.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
NEWS_DIR = BASE_DIR / "market_data" / "news"


def fetch_company_news_live(ticker: str) -> List[Dict[str, Any]]:
    """
    Fetch fresh company-specific news/announcements from the AskAnalyst API
    for *ticker* and save to market_data/news/{TICKER}_news.json.

    Uses the company's askanalyst_id from PSX_TICKERS if available;
    otherwise resolves the ID via the API company list.

    Returns the list of news dicts (empty list on any failure).
    """
    ticker = ticker.upper()
    try:
        sys.path.insert(0, str(BASE_DIR))
        from scrape_askanalyst import get_company_id, fetch_company_news
        from data.psx_tickers import PSX_TICKERS

        # Prefer the pre-mapped ID to avoid an extra API call
        company_id = None
        if ticker in PSX_TICKERS:
            company_id = PSX_TICKERS[ticker].get("askanalyst_id")

        if not company_id:
            result = get_company_id(ticker)
            # get_company_id returns (id, name) tuple
            company_id = result[0] if isinstance(result, tuple) else result

        if not company_id:
            logger.warning("Could not resolve AskAnalyst ID for %s — skipping live news", ticker)
            return []

        news_data = fetch_company_news(company_id)
        if not news_data:
            logger.info("No company news returned for %s", ticker)
            return []

        items = news_data if isinstance(news_data, list) else []

        # Save for reuse during this session
        NEWS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = NEWS_DIR / f"{ticker}_news.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2, default=str)

        logger.info("Saved %d news items for %s → %s", len(items), ticker, out_path)
        return items

    except Exception as e:
        logger.warning("Live company news fetch failed for %s: %s", ticker, e)
        return []


def refresh_market_news() -> bool:
    """
    Refresh the general PSX market news file (market_data/news/latest_news.json)
    by calling the AskAnalyst market scraper.

    Returns True on success, False on failure.
    """
    try:
        sys.path.insert(0, str(BASE_DIR))
        from scrape_askanalyst_market import fetch_news
        fetch_news(count=50)
        logger.info("Market news refreshed successfully.")
        return True
    except Exception as e:
        logger.warning("Market news refresh failed: %s", e)
        return False
