"""
data/news_data.py
═══════════════════════════════════════════════════════════════════════
News fetching for PSX stocks.

Uses Google News RSS scoped to local Pakistani financial outlets
(Dawn, Business Recorder, Mettis Global, AskAnalyst).
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List
import requests
import xml.etree.ElementTree as ET

from config import CACHE_TTL_NEWS
from data.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

def get_stock_news(symbol: str, max_articles: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch news articles for a stock ticker from local Pakistani sources.
    
    Args:
        symbol: Stock symbol without suffix (e.g. 'OGDC').
        max_articles: Maximum number of articles to return.
        
    Returns:
        List of news items: {title, link, published, source}
    """
    cache_key = f"news_rss:{symbol.upper()}"
    cached = get_cached(cache_key, CACHE_TTL_NEWS)
    if cached is not None:
        logger.info(f"News cache hit for {symbol}")
        return cached[:max_articles]
        
    logger.info(f"News cache miss for {symbol}. Fetching from local RSS sources...")
    
    clean_sym = symbol.strip().upper()
    
    # Scope to Pakistani financial media as requested
    query = f'"{clean_sym}" (site:dawn.com OR site:brecorder.com OR site:mettisglobal.news OR site:askanalyst.com.pk)'
    rss_url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}"
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(rss_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        articles = []
        seen_titles = set()
        
        # Parse standard RSS items
        for item in root.findall('./channel/item'):
            title = item.find('title').text if item.find('title') is not None else ""
            link = item.find('link').text if item.find('link') is not None else ""
            pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
            source = item.find('source').text if item.find('source') is not None else "Unknown Source"
            
            clean_title = title.strip()
            if not clean_title or clean_title in seen_titles:
                continue
                
            seen_titles.add(clean_title)
            
            # Reformat pubDate to a cleaner format if possible
            published_str = pub_date
            try:
                # Example: Tue, 04 Jun 2026 15:30:00 GMT
                dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z")
                published_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
                
            articles.append({
                "title": clean_title,
                "link": link,
                "published": published_str,
                "source": source
            })
            
            if len(articles) >= max_articles:
                break
                
        set_cached(cache_key, articles)
        return articles
        
    except Exception as e:
        logger.error(f"Error fetching news for {clean_sym} via RSS: {e}")
        return []
