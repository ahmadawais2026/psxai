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
    Fetch news articles for a stock ticker from AskAnalyst endpoints as primary,
    and fall back/supplement with Google News RSS from local sources.
    
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
        
    logger.info(f"News cache miss for {symbol}. Fetching from AskAnalyst endpoints...")
    
    import re
    clean_sym = symbol.strip().upper()
    articles = []
    seen_titles = set()
    
    # 1. Try to resolve AskAnalyst company ID using local PSX_TICKERS
    from data.psx_tickers import PSX_TICKERS
    company_id = None
    company_name = ""
    if clean_sym in PSX_TICKERS:
        company_id = PSX_TICKERS[clean_sym].get("askanalyst_id")
        company_name = PSX_TICKERS[clean_sym].get("name", "")
        
    # 2. Try company-specific news endpoint
    if company_id:
        try:
            url = f"https://api.askanalyst.com.pk/api/news/{company_id}"
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                news_data = r.json()
                items = news_data.get("data", []) if isinstance(news_data, dict) else []
                for item in items:
                    title = item.get("title", "").strip()
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        articles.append({
                            "title": title,
                            "link": item.get("link") or f"https://www.askanalyst.com.pk/company/{company_id}",
                            "published": item.get("created_at") or item.get("date") or "",
                            "source": "AskAnalyst"
                        })
        except Exception as e:
            logger.warning(f"Failed to fetch company news from AskAnalyst: {e}")
            
    # 3. Try market-wide news /news/all and filter for mentions of the symbol or company name
    try:
        url = "https://api.askanalyst.com.pk/api/news/all"
        params = {"page": 1, "postsperpage": 100}
        r = requests.get(url, params=params, timeout=8)
        if r.status_code == 200:
            news_data = r.json()
            items = news_data.get("data", []) if isinstance(news_data, dict) else []
            for item in items:
                title = item.get("title", "")
                desc = item.get("description", "")
                # check if symbol or parts of the name are mentioned
                match = False
                if re.search(rf"\b{clean_sym}\b", title, re.IGNORECASE) or re.search(rf"\b{clean_sym}\b", desc, re.IGNORECASE):
                    match = True
                elif company_name:
                    first_word = company_name.split()[0]
                    if len(first_word) > 3:
                        if re.search(rf"\b{first_word}\b", title, re.IGNORECASE) or re.search(rf"\b{first_word}\b", desc, re.IGNORECASE):
                            match = True
                            
                if match:
                    title_clean = title.strip()
                    if title_clean and title_clean not in seen_titles:
                        seen_titles.add(title_clean)
                        articles.append({
                            "title": title_clean,
                            "link": item.get("link") or "https://www.askanalyst.com.pk/news",
                            "published": item.get("created_at") or item.get("date") or "",
                            "source": "AskAnalyst"
                        })
    except Exception as e:
        logger.warning(f"Failed to fetch market news from AskAnalyst: {e}")
        
    # 4. Fallback/Supplement with Google News RSS if we don't have enough articles
    if len(articles) < max_articles:
        logger.info(f"Only found {len(articles)} articles on AskAnalyst. Supplementing with RSS...")
        query = f'"{clean_sym}" (site:dawn.com OR site:brecorder.com OR site:mettisglobal.news OR site:askanalyst.com.pk)'
        rss_url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}"
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(rss_url, headers=headers, timeout=10)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                for item in root.findall('./channel/item'):
                    title = item.find('title').text if item.find('title') is not None else ""
                    link = item.find('link').text if item.find('link') is not None else ""
                    pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
                    source = item.find('source').text if item.find('source') is not None else "Unknown Source"
                    
                    clean_title = title.strip()
                    if not clean_title or clean_title in seen_titles:
                        continue
                        
                    seen_titles.add(clean_title)
                    published_str = pub_date
                    try:
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
                    if len(articles) >= max_articles * 2:
                        break
        except Exception as e:
            logger.error(f"Error fetching news for {clean_sym} via RSS fallback: {e}")
            
    # Truncate and cache
    articles = articles[:max_articles]
    set_cached(cache_key, articles)
    return articles
