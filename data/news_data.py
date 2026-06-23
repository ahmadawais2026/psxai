"""
data/news_data.py
═══════════════════════════════════════════════════════════════════════
News fetching for PSX stocks with Advanced Tiered Sourcing, 
WAF-Bypass (cloudscraper), In-Memory Deduplication, and LLM Entity Resolution.
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any, Dict, List
import cloudscraper
import urllib.parse
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

from config import CACHE_TTL_NEWS, GEMINI_API_KEY, GEMINI_MODEL, USE_VERTEX, VERTEX_PROJECT, VERTEX_LOCATION, map_model_name
from data.cache import get_cached, set_cached
from data.psx_tickers import PSX_TICKERS

logger = logging.getLogger(__name__)

# WAF Bypass Scraper
scraper = cloudscraper.create_scraper(browser={
    'browser': 'chrome',
    'platform': 'windows',
    'desktop': True
})

def _jaccard_similarity(str1: str, str2: str) -> float:
    """Calculate Jaccard Similarity between two strings (shingled words)."""
    set1 = set(str1.lower().split())
    set2 = set(str2.lower().split())
    if not set1 or not set2:
        return 0.0
    intersection = set1.intersection(set2)
    union = set1.union(set2)
    return len(intersection) / len(union)

def _deduplicate_articles(articles: List[Dict[str, Any]], threshold: float = 0.65) -> List[Dict[str, Any]]:
    """Deduplicate articles based on Jaccard similarity of titles."""
    unique_articles = []
    for item in articles:
        is_duplicate = False
        title1 = item.get("title", "")
        for existing in unique_articles:
            title2 = existing.get("title", "")
            if _jaccard_similarity(title1, title2) > threshold:
                is_duplicate = True
                break
        if not is_duplicate:
            unique_articles.append(item)
    return unique_articles

def _article_matches_company(
    title: str,
    desc: str,
    clean_sym: str,
    company_name: str = "",
    prefix: bool = False,
) -> bool:
    """True if the article text references the target company.

    ``prefix=True`` matches the ticker at a word-start only (no trailing word
    boundary), so brand variants like 'OGDCL' match the ticker 'OGDC'. Looser
    matches are refined downstream by the fail-open LLM entity filter, so the
    prefix mode is safe for direct-source feeds (e.g. Business Recorder).
    """
    text = f"{title or ''}\n{desc or ''}"
    pat = rf"\b{re.escape(clean_sym)}" if prefix else rf"\b{re.escape(clean_sym)}\b"
    if re.search(pat, text, re.IGNORECASE):
        return True
    # First-word name fallback is too noisy for the broad BRecorder feeds — e.g.
    # "Pakistan State Oil"/"Pakistan Stock Exchange" -> "Pakistan", which matches
    # nearly every PK business headline. Only use it in exact (non-prefix) mode,
    # where the source feed is already entity-scoped. In prefix mode the ticker
    # match alone is the signal (BRecorder reliably writes OGDCL/PSO/AGP/HUBCO).
    if company_name and not prefix:
        first_word = company_name.split()[0]
        if len(first_word) > 3 and re.search(rf"\b{re.escape(first_word)}\b", text, re.IGNORECASE):
            return True
    return False

def filter_news_with_llm(symbol: str, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    LLM-Powered Entity Resolution (RAG pass 1).
    Discards articles that are generic noise and not specifically about the target entity.
    """
    if not articles:
        return articles
        
    if not USE_VERTEX and not GEMINI_API_KEY:
        return articles
        
    if USE_VERTEX:
        client = genai.Client(
            vertexai=True,
            project=VERTEX_PROJECT,
            location=VERTEX_LOCATION
        )
        model = map_model_name(GEMINI_MODEL)
    else:
        client = genai.Client(api_key=GEMINI_API_KEY)
        model = GEMINI_MODEL
        
    filtered = []

    # We will process them in one batch to save time and API calls
    prompt = f"You are a financial entity resolution engine. Filter the following news headlines for the stock '{symbol}'. Return ONLY a JSON list of indices (0-indexed) of the articles that are explicitly and genuinely about this corporate entity or its direct market sector impacts. Discard generic noise. Respond ONLY with a JSON array of integers.\n\n"

    for i, art in enumerate(articles):
        prompt += f"[{i}] {art.get('title')}\n"

    try:
        # Disable thinking and pin temperature to 0 so the response is a clean,
        # preamble-free JSON array (gemini-3.x reasoning models otherwise emit
        # thoughts/brackets that break the index parse). Mirrors the
        # thinking_budget=0 pattern in fetch_psx_announcements_pdf.
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        # Parse the JSON array from the response. The model can still wrap the
        # answer in prose or echo a bracketed token (e.g. "[2024]"), so scan ALL
        # bracket groups and keep the one that parses to a list of ints — the
        # real index array — rather than blindly taking the first match.
        import json
        indices = None
        for candidate in re.findall(r'\[[\s\S]*?\]', response.text or ""):
            try:
                parsed = json.loads(candidate)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, list) and all(isinstance(x, int) for x in parsed):
                # Prefer the largest valid index array found.
                if indices is None or len(parsed) > len(indices):
                    indices = parsed

        if indices:
            for i in indices:
                if 0 <= i < len(articles):
                    filtered.append(articles[i])
            logger.info(f"LLM Entity Resolution filtered {len(articles)} articles down to {len(filtered)} for {symbol}")

        # Fail open: an empty/garbage result means the parse or the model failed,
        # NOT that every article is noise. Never collapse a non-empty input to
        # empty — that is what silently dropped the AGP merger news.
        if filtered:
            return filtered
        logger.warning(
            f"LLM Entity Resolution returned no usable indices for {symbol}; "
            f"keeping all {len(articles)} articles (fail-open)."
        )
    except Exception as e:
        logger.error(f"LLM Entity Resolution failed for {symbol}: {e}")

    return articles # Fallback to all articles if LLM fails or filters to empty

def _parse_pub_date(published: str) -> datetime:
    """Best-effort parse of the various ``published`` formats into a datetime.

    Undated or unparseable items sort to the bottom (``datetime.min``) so the
    freshest dated articles always rank first.
    """
    if not published:
        return datetime.min
    s = str(published).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
    return datetime.min

def fetch_psx_announcements_pdf(symbol: str, max_pdfs: int = 5) -> List[Dict[str, Any]]:
    """
    Tier 0: Scrape PSX Portal for recent corporate announcements and use Gemini Flash-Lite
    to extract Title, Date, and Summary from the PDF bytes natively.
    """
    if not USE_VERTEX:
        return []
        
    logger.info(f"Fetching PSX Portal PDFs for {symbol}...")
    url = f'https://dps.psx.com.pk/company/{symbol.upper()}'
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
            
        soup = BeautifulSoup(r.text, 'html.parser')
        pdf_links = soup.find_all('a', href=lambda h: h and '/download/document/' in h)
        
        client = genai.Client(vertexai=True, project=VERTEX_PROJECT, location=VERTEX_LOCATION)
        extracted = []
        
        for pdf_link in pdf_links[:max_pdfs]:
            pdf_url = 'https://dps.psx.com.pk' + pdf_link['href']
            
            # 1. Download PDF bytes
            pdf_resp = requests.get(pdf_url, headers=headers, timeout=10)
            if pdf_resp.status_code != 200:
                continue
                
            pdf_bytes = pdf_resp.content
            if len(pdf_bytes) < 1000 or not pdf_bytes.startswith(b"%PDF"):
                continue # Skip invalid PDFs or empty HTML error pages
                
            # 2. Extract with Gemini 3.1 Flash-Lite
            prompt = "Extract the following from this PSX announcement: Title, Date, Category (e.g. Board Meeting, Financial Results, Merger, Other), and a brief Summary of the material information. Format as JSON with keys: title, date, category, summary."
            
            try:
                response = client.models.generate_content(
                    model='gemini-3.1-flash-lite-preview',
                    contents=[
                        types.Part.from_bytes(data=pdf_bytes, mime_type='application/pdf'),
                        prompt
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=2048,
                        thinking_config=types.ThinkingConfig(thinking_budget=0)
                    )
                )
                
                # 3. Parse JSON Output
                text = response.text
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    title = data.get("title", "PSX Announcement")
                    summary = data.get("summary", "")
                    if data.get("category"):
                        title = f"[{data['category']}] {title}"
                        
                    extracted.append({
                        "title": title,
                        "link": pdf_url,
                        "published": data.get("date", ""),
                        "source": "PSX Portal",
                        "summary": summary
                    })
            except Exception as e:
                logger.error(f"Gemini PDF extraction failed for {pdf_url}: {e}")
                
        return extracted
    except Exception as e:
        logger.error(f"Failed to fetch PSX Portal PDFs for {symbol}: {e}")
        return []

def fetch_mettis_announcements(symbol: str) -> List[Dict[str, Any]]:
    """
    Fetch structured news from Mettis Global's WordPress REST API.
    """
    extracted = []
    try:
        url = "https://mettisglobal.news/wp-json/wp/v2/posts"
        params = {"per_page": 10, "search": symbol}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        
        if r.status_code == 200:
            posts = r.json()
            for post in posts:
                title = post.get("title", {}).get("rendered", "")
                # Skip if the symbol is not in the title (to avoid generic search hits)
                if symbol.upper() not in title.upper():
                    continue
                
                # Try to extract a summary snippet from excerpt
                excerpt = post.get("excerpt", {}).get("rendered", "")
                import re
                summary = re.sub('<[^<]+>', '', excerpt).strip()
                
                extracted.append({
                    "title": title,
                    "link": post.get("link", ""),
                    "published": post.get("date", ""),
                    "source": "Mettis Global",
                    "summary": summary
                })
    except Exception as e:
        logger.error(f"Failed to fetch Mettis Global for {symbol}: {e}")

    return extracted


# Business Recorder has no per-company feed and its on-site search is a
# client-side Google Custom Search widget (not server-scrapeable). Its category
# feeds, however, are clean RSS with full HTML summaries. We pull these and
# filter to the target company. BRecorder is NOT indexed by Google News (a query
# for a high-coverage name returns dozens of items across other PK outlets and
# zero from brecorder.com), so these feeds are the ONLY way to surface its PSX
# coverage in the pipeline. They are a recent rolling window (~30-40 items each),
# so this catches current/material company news (mergers, results), not history.
BRECORDER_FEEDS = [
    "https://www.brecorder.com/feeds/business",   # highest company yield (PSX, results)
    "https://www.brecorder.com/feeds/pakistan",   # national / corporate
    "https://www.brecorder.com/feeds/markets",    # PSX index + commodity-driven names
]


def fetch_brecorder_news(symbol: str, company_name: str = "") -> List[Dict[str, Any]]:
    """
    Tier 0.3: Business Recorder category feeds, filtered to the target company.
    Mirrors the RSS parse in fetch_market_intelligence._parse_rss (incl. the
    ISO-8859-1 encoding normalization) and reuses the module-level cloudscraper.
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for feed_url in BRECORDER_FEEDS:
        try:
            r = scraper.get(feed_url, timeout=10)
            if r.status_code != 200:
                logger.warning(f"BRecorder feed {feed_url} for {symbol}: HTTP {r.status_code}")
                continue
            content = r.content
            # Some BRecorder feeds declare ISO-8859-1; re-encode so ET parses cleanly.
            if b"ISO-8859-1" in content[:200] or b"iso-8859-1" in content[:200]:
                content = content.decode("iso-8859-1").encode("utf-8")
                content = content.replace(b"ISO-8859-1", b"UTF-8").replace(b"iso-8859-1", b"UTF-8")
            root = ET.fromstring(content)
            for item in root.findall("./channel/item"):
                title = (item.findtext("title") or "").strip()
                desc = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()
                if not title or title in seen:
                    continue
                if not _article_matches_company(title, desc, symbol, company_name, prefix=True):
                    continue
                seen.add(title)
                out.append({
                    "title": title,
                    "link": (item.findtext("link") or "").strip(),
                    "published": (item.findtext("pubDate") or "").strip(),
                    "source": "Business Recorder",
                    "summary": desc[:400],
                })
        except Exception as e:
            logger.warning(f"BRecorder feed {feed_url} failed for {symbol}: {e}")
    if out:
        logger.info(f"Business Recorder Tier 0.3: matched {len(out)} articles for {symbol}")
    return out


def get_stock_news(symbol: str, max_articles: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch news articles for a stock ticker from AskAnalyst endpoints (Tier 1),
    and fall back/supplement with Google News RSS (Tier 2/3).
    Includes in-memory deduplication and LLM noise filtering.
    """
    cache_key = f"news_rss_v3:{symbol.upper()}"
    cached = get_cached(cache_key, CACHE_TTL_NEWS)
    if cached is not None:
        logger.info(f"News cache hit for {symbol}")
        return cached[:max_articles]
        
    logger.info(f"News cache miss for {symbol}. Fetching Tier 1...")
    
    clean_sym = symbol.strip().upper()
    raw_articles = []
    seen_titles = set()
    
    company_id = None
    company_name = ""
    if clean_sym in PSX_TICKERS:
        company_id = PSX_TICKERS[clean_sym].get("askanalyst_id")
        company_name = PSX_TICKERS[clean_sym].get("name", "")
        
    # Tier 0.1: Direct PSX Portal PDF Extraction
    psx_announcements = fetch_psx_announcements_pdf(clean_sym)
    for ann in psx_announcements:
        if ann["title"] not in seen_titles:
            seen_titles.add(ann["title"])
            raw_articles.append(ann)
            
    # Tier 0.2: Mettis Global JSON API
    mettis_news = fetch_mettis_announcements(clean_sym)
    for ann in mettis_news:
        if ann["title"] not in seen_titles:
            seen_titles.add(ann["title"])
            raw_articles.append(ann)

    # Tier 0.3: Business Recorder category feeds (absent from Google News, so this
    # is the only path to BRecorder's PSX coverage). Routed through the fail-open
    # LLM entity filter below (source not in the trusted Tier-0 bypass set).
    for ann in fetch_brecorder_news(clean_sym, company_name):
        if ann["title"] not in seen_titles:
            seen_titles.add(ann["title"])
            raw_articles.append(ann)

    # Tier 1: Try AskAnalyst endpoints via cloudscraper
    if company_id:
        try:
            url = f"https://api.askanalyst.com.pk/api/news/{company_id}"
            r = scraper.get(url, timeout=8)
            if r.status_code == 200:
                news_data = r.json()
                items = news_data.get("data", []) if isinstance(news_data, dict) else []
                for item in items:
                    title = item.get("title", "").strip()
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        raw_articles.append({
                            "title": title,
                            "link": item.get("link") or f"https://www.askanalyst.com.pk/company/{company_id}",
                            "published": item.get("created_at") or item.get("date") or "",
                            "source": "AskAnalyst"
                        })
        except Exception as e:
            logger.warning(f"Tier 1 AskAnalyst company fetch failed: {e}")
            
    # Tier 2: AskAnalyst All News filtering
    try:
        url = "https://api.askanalyst.com.pk/api/news/all"
        params = {"page": 1, "postsperpage": 100}
        r = scraper.get(url, params=params, timeout=8)
        if r.status_code == 200:
            news_data = r.json()
            items = news_data.get("data", []) if isinstance(news_data, dict) else []
            for item in items:
                title = item.get("title", "")
                desc = item.get("description", "")
                if _article_matches_company(title, desc, clean_sym, company_name):
                    title_clean = title.strip()
                    if title_clean and title_clean not in seen_titles:
                        seen_titles.add(title_clean)
                        raw_articles.append({
                            "title": title_clean,
                            "link": item.get("link") or "https://www.askanalyst.com.pk/news",
                            "published": item.get("created_at") or item.get("date") or "",
                            "source": "AskAnalyst"
                        })
    except Exception as e:
        logger.warning(f"Tier 2 AskAnalyst market news failed: {e}")
        
    # Tier 3: Google News RSS via cloudscraper. Always run — otherwise a
    # stale-but-large AskAnalyst feed (e.g. OGDC's 2024-only company feed)
    # crowds out fresh wire news and the result is months out of date.
    if True:
        # Query the COMPANY NAME, not the bare ticker. The bare ticker is
        # ambiguous — e.g. "AGP" matches "Attorney General of Pakistan", which
        # floods the feed with acronym noise and buries the real company news
        # (the AGP merger was lost exactly this way). Pairing the full name with
        # a Pakistan/PSX-scoped ticker clause keeps results on-entity.
        if company_name:
            core = company_name.replace(" Limited", "").replace(" Ltd", "").strip()
            query = f'"{company_name}" OR "{core} Limited" OR ("{clean_sym}" Pakistan PSX)'
        else:
            query = f'"{clean_sym}" Pakistan PSX stock'
        logger.info(f"Supplementing with Google News RSS Tier 3 (query: {query})")
        rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}"
        # news.google.com intermittently throttles the shared cloud egress IP
        # with a transient non-200 (usually 429); retry with backoff so a single
        # throttle doesn't leave the analysis with zero news. One success per
        # hour is enough — the result is cached for CACHE_TTL_NEWS.
        try:
            import time
            response = None
            for _attempt in range(3):
                response = scraper.get(rss_url, timeout=10)
                if response.status_code == 200:
                    break
                logger.warning(f"Tier 3 Google News RSS for {clean_sym}: HTTP {response.status_code} on attempt {_attempt + 1}/3 (throttled?)")
                if _attempt < 2:
                    time.sleep(2 * (_attempt + 1))  # 2s, then 4s
            if response is not None and response.status_code == 200:
                root = ET.fromstring(response.content)
                _rss_items = root.findall('./channel/item')
                logger.info(f"Tier 3 Google News RSS for {clean_sym}: HTTP 200, {len(_rss_items)} items returned")
                for item in _rss_items:
                    title = item.find('title').text if item.find('title') is not None else ""
                    link = item.find('link').text if item.find('link') is not None else ""
                    pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
                    source = item.find('source').text if item.find('source') is not None else "Google News"
                    
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
                        
                    raw_articles.append({
                        "title": clean_title,
                        "link": link,
                        "published": published_str,
                        "source": source
                    })
        except Exception as e:
            logger.error(f"Tier 3 Google News RSS failed for {clean_sym}: {e}")
            
    # Sort newest-first so truncation keeps the LATEST news, then prefer the
    # last 12 months — falling back to the full sorted list if too few recent
    # items remain, so sparse-coverage tickers don't end up empty.
    raw_articles.sort(key=lambda a: _parse_pub_date(a.get("published", "")), reverse=True)
    cutoff = datetime.now() - timedelta(days=365)
    recent = [a for a in raw_articles if _parse_pub_date(a.get("published", "")) >= cutoff]
    ranked = recent if len(recent) >= 3 else raw_articles

    # Apply Deduplication Pipeline
    deduped_articles = _deduplicate_articles(ranked)
    
    # Apply LLM Entity Resolution (Noise Filtering) ONLY to Tier 2/3 sources
    tier0_articles = [a for a in deduped_articles if a.get("source") in ("PSX Portal", "Mettis Global")]
    other_articles = [a for a in deduped_articles if a.get("source") not in ("PSX Portal", "Mettis Global")]
    
    filtered_other = filter_news_with_llm(symbol, other_articles[:20])
    
    final_articles = tier0_articles + filtered_other
    final_articles.sort(key=lambda a: _parse_pub_date(a.get("published", "")), reverse=True)
    
    # Truncate and cache. Do NOT cache an empty result — a transient zero-fetch
    # (blocked source, LLM hiccup) would otherwise be frozen for the whole TTL
    # and starve every later run of news.
    final_articles = final_articles[:max_articles]
    if final_articles:
        set_cached(cache_key, final_articles)
    return final_articles
