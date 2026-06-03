"""
data/news_data.py
═══════════════════════════════════════════════════════════════════════
News fetching for PSX stocks.

Uses yfinance's built-in ticker news feature.
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List
import yfinance as yf

from config import CACHE_TTL_NEWS, PSX_SUFFIX
from data.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

def get_stock_news(symbol: str, max_articles: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch news articles for a stock ticker. Checks cache first.
    
    Args:
        symbol: Stock symbol without .KA suffix (e.g. 'OGDC').
        max_articles: Maximum number of articles to return.
        
    Returns:
        List of news items: {title, link, published, source}
    """
    cache_key = f"news:{symbol.upper()}"
    cached = get_cached(cache_key, CACHE_TTL_NEWS)
    if cached is not None:
        logger.info(f"News cache hit for {symbol}")
        return cached[:max_articles]
        
    logger.info(f"News cache miss for {symbol}. Fetching from yfinance...")
    
    # Normalise symbol and append suffix if needed
    clean_sym = symbol.strip().upper()
    yf_symbol = clean_sym if clean_sym.endswith(PSX_SUFFIX) else f"{clean_sym}{PSX_SUFFIX}"
    
    try:
        ticker = yf.Ticker(yf_symbol)
        raw_news = ticker.news
        if not raw_news:
            logger.warning(f"No news returned by yfinance for {yf_symbol}")
            return []
            
        articles = []
        seen_titles = set()
        
        for item in raw_news:
            title = item.get("title", "").strip()
            if not title or title in seen_titles:
                continue
                
            seen_titles.add(title)
            
            # Format published date
            pub_time = item.get("providerPublishTime")
            published_str = ""
            if pub_time:
                try:
                    dt = datetime.fromtimestamp(pub_time)
                    published_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    published_str = str(pub_time)
                    
            articles.append({
                "title": title,
                "link": item.get("link", ""),
                "published": published_str,
                "source": item.get("publisher", "Unknown Source")
            })
            
            if len(articles) >= max_articles:
                break
                
        set_cached(cache_key, articles)
        return articles
        
    except Exception as e:
        logger.error(f"Error fetching news for {yf_symbol}: {e}")
        return []
