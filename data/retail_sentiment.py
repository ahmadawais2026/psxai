"""
data/retail_sentiment.py
════════════════════════════════════════════════════════════════════════
Retail sentiment and attention signals for PSX equities.

Sources (all free):
  1. Google Trends (pytrends) — search volume as retail attention proxy
  2. Reddit PRAW              — r/PakistanStockExchange, r/pakistanfinance
  3. AHL brokerage PDFs      — publicly hosted research report parsing

Google Trends Logic (from academic research on frontier markets):
  - High/surging search volume for a ticker → retail attention shock
  - Attention shock + positive price momentum + LIPI inflow = high-prob signal
  - Rate of change vs. 4-week baseline is the key metric (not absolute volume)

Reddit Logic:
  - Fetches hot/new posts containing ticker symbols
  - Simple keyword sentiment: bullish/bearish word lists + upvote weighting

AHL Brokerage Reports:
  - Downloads PDF from arifhabibltd.com/api/research/open?path=...
  - Extracts target prices, ratings (Buy/Hold/Sell), and top picks using
    pdfplumber + regex (no LLM required for structured AHL format)
════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from data.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

_TTL_TRENDS  = 3600 * 2   # 2 hours
_TTL_REDDIT  = 60 * 20    # 20 min
_TTL_BROKER  = 3600 * 12  # 12 hours (reports are daily)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Simple sentiment word lists — extend with Roman Urdu terms as needed
_BULLISH_WORDS = {
    "buy", "bullish", "strong", "growth", "rally", "upside", "accumulate",
    "outperform", "overweight", "target", "positive", "recovery", "dividend",
    "profit", "earnings beat", "undervalued", "cheap", "opportunity",
    "khareed", "acha", "mazboot",   # Roman Urdu: buy, good, strong
}
_BEARISH_WORDS = {
    "sell", "bearish", "weak", "decline", "downside", "avoid", "underperform",
    "underweight", "negative", "loss", "miss", "overvalued", "expensive",
    "becho", "gira", "nuksan",      # Roman Urdu: sell, fell, loss
}


# ── Google Trends ──────────────────────────────────────────────────────────

def get_google_trends_signal(symbols: List[str]) -> Dict[str, Any]:
    """
    Fetch Google Trends search volume for PSX tickers.

    Uses pytrends unofficial API. Compares current week vs. 4-week average
    to compute an "attention shock" score. High positive score = retail FOMO.

    Args:
        symbols: List of PSX ticker symbols (e.g. ['OGDC', 'ENGRO'])
        
    Returns:
        {ticker: {current_interest, baseline_avg, attention_shock_pct, signal}}
    """
    cache_key = f"sentiment:gtrends:{':'.join(sorted(symbols[:5]))}"
    cached = get_cached(cache_key, _TTL_TRENDS)
    if cached is not None:
        return cached

    result: Dict[str, Any] = {}

    try:
        from pytrends.request import TrendReq

        # Batch in groups of 5 (pytrends limit)
        batches = [symbols[i:i+5] for i in range(0, min(len(symbols), 15), 5)]
        pt = TrendReq(hl="en-US", tz=300, timeout=(5, 15))  # PKT = UTC+5

        for batch in batches:
            # Use PSX context terms to disambiguate from non-financial results
            kw_list = [f"{s} PSX" for s in batch]
            try:
                pt.build_payload(kw_list, timeframe="today 1-m", geo="PK")
                df = pt.interest_over_time()
                if df is None or df.empty:
                    continue

                for kw, sym in zip(kw_list, batch):
                    if kw not in df.columns:
                        continue
                    series = df[kw].values
                    if len(series) < 2:
                        continue

                    current = float(series[-1])
                    baseline = float(series[:-1].mean()) if len(series) > 1 else current

                    if baseline > 0:
                        shock_pct = round((current - baseline) / baseline * 100, 1)
                    else:
                        shock_pct = 0.0

                    if shock_pct > 50:
                        signal = "HIGH_ATTENTION"
                    elif shock_pct > 20:
                        signal = "RISING_ATTENTION"
                    elif shock_pct < -30:
                        signal = "FADING_INTEREST"
                    else:
                        signal = "NORMAL"

                    result[sym] = {
                        "current_interest": current,
                        "baseline_avg_4w":  round(baseline, 1),
                        "attention_shock_pct": shock_pct,
                        "signal": signal,
                        "source": "Google Trends (PK)",
                    }

                time.sleep(0.5)  # Rate limit respect

            except Exception as e:
                logger.debug("Trends batch %s failed: %s", batch, e)

    except ImportError:
        logger.info("pytrends not installed — skipping Google Trends")
    except Exception as e:
        logger.warning("Google Trends fetch failed: %s", e)

    if result:
        set_cached(cache_key, result)
    return result


# ── Reddit Sentiment ───────────────────────────────────────────────────────

def get_reddit_sentiment(symbol: str) -> Dict[str, Any]:
    """
    Bypassed due to Reddit API restrictions (403 Forbidden).
    """
    logger.info(f"Reddit sentiment for {symbol} bypassed due to API restrictions.")
    return {}


# ── AHL Brokerage Research Ingestion ──────────────────────────────────────

def _extract_ahl_pdf_data(pdf_bytes: bytes) -> Dict[str, Any]:
    """
    Parse an AHL brokerage PDF and extract target prices, ratings, top picks.
    AHL has consistent formatting making regex extraction reliable without LLMs.
    """
    import io
    try:
        import pdfplumber
    except ImportError:
        logger.info("pdfplumber not installed — skip AHL PDF parsing")
        return {}

    result: Dict[str, Any] = {"top_picks": [], "ratings": {}, "index_targets": {}}

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            full_text = "\n".join(
                page.extract_text() or "" for page in pdf.pages
            )

        # Extract KSE-100 target / PE multiples
        pe_match = re.search(r"KSE[\-\s]?100.*?(\d+\.?\d*)[xX]\s*(?:P/?E|forward)", full_text, re.IGNORECASE)
        if pe_match:
            result["index_targets"]["fy_pe_multiple"] = float(pe_match.group(1))

        # Extract price targets: TICKER — Buy — TP: 123.45
        for m in re.finditer(
            r"\b([A-Z]{2,6})\b[^\n]*?(Buy|Hold|Sell|Accumulate|Reduce)\b[^\n]*?(?:TP|Target)[:\s]*(?:PKR|Rs\.?)?\s*(\d[\d,]*\.?\d*)",
            full_text, re.IGNORECASE
        ):
            ticker, rating, tp = m.group(1), m.group(2), m.group(3)
            result["ratings"][ticker] = {
                "rating": rating.title(),
                "target_price_pkr": float(tp.replace(",", "")),
            }

        # Extract top picks (common AHL format: "Top Picks: OGDC, PPL, PSO")
        top_picks_match = re.search(
            r"(?:top picks?|preferred stocks?)[:\-\s]+([A-Z, &]+)", full_text, re.IGNORECASE
        )
        if top_picks_match:
            picks_text = top_picks_match.group(1)
            result["top_picks"] = [
                p.strip() for p in re.findall(r"[A-Z]{2,6}", picks_text)
            ]

        # Extract macro summary numbers (e.g., remittances, LSM)
        remit_m = re.search(r"remittances?\s*[:\-]?\s*\$?(\d+\.?\d*)\s*(?:bn|billion)", full_text, re.IGNORECASE)
        if remit_m:
            result["remittances_usd_bn"] = float(remit_m.group(1))

    except Exception as e:
        logger.debug("AHL PDF parse error: %s", e)

    return result


def get_ahl_research() -> Dict[str, Any]:
    """
    Download and parse the latest Arif Habib Limited (AHL) morning brief PDF.

    AHL publicly hosts their reports at:
    https://arifhabibltd.com/api/research/open?path=<filename>

    We first scrape their research listing page to find the latest PDF URL,
    then parse it with pdfplumber.

    Returns:
        {top_picks, ratings: {TICKER: {rating, target_price_pkr}},
         index_targets, report_date, source}
    """
    cache_key = "broker:ahl_research"
    cached = get_cached(cache_key, _TTL_BROKER)
    if cached is not None:
        return cached

    result: Dict[str, Any] = {"source": "AHL (arifhabibltd.com)"}

    try:
        from bs4 import BeautifulSoup

        # Fetch AHL research listing
        r = requests.get(
            "https://arifhabibltd.com/research",
            headers=_HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            r = requests.get(
                "https://arifhabibltd.com/research/morning-briefs",
                headers=_HEADERS,
                timeout=10,
            )

        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")

            # Find most recent PDF link
            pdf_url = None
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if "research" in href.lower() and href.endswith(".pdf"):
                    pdf_url = href if href.startswith("http") else f"https://arifhabibltd.com{href}"
                    break
                # AHL API format
                if "research/open" in href or "fetchpdf" in href:
                    pdf_url = href if href.startswith("http") else f"https://arifhabibltd.com{href}"
                    break

            if pdf_url:
                pdf_r = requests.get(pdf_url, headers=_HEADERS, timeout=15)
                if pdf_r.status_code == 200 and b"%PDF" in pdf_r.content[:10]:
                    parsed = _extract_ahl_pdf_data(pdf_r.content)
                    result.update(parsed)
                    result["report_url"] = pdf_url
                    result["fetched_at"] = datetime.utcnow().isoformat()

    except Exception as e:
        logger.warning("AHL research fetch failed: %s", e)

    if len(result) > 2:  # More than just source/fetched_at
        set_cached(cache_key, result)
    return result


# ── Foundation Securities Research ────────────────────────────────────────

def get_foundation_research(latest_n: int = 3) -> List[Dict[str, Any]]:
    """
    Scrape Foundation Securities research reports.

    Foundation uses predictable timestamp-based URLs:
    /backend/public/fetchpdf/uploads/research/<timestamp>.pdf

    Returns list of parsed report dicts (same schema as AHL).
    """
    cache_key = "broker:foundation_research"
    cached = get_cached(cache_key, _TTL_BROKER)
    if cached is not None:
        return cached

    reports = []
    try:
        from bs4 import BeautifulSoup

        r = requests.get(
            "https://www.foundation-sec.com/research/",
            headers=_HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            r = requests.get(
                "https://foundation-sec.com/research",
                headers=_HEADERS,
                timeout=10,
            )

        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            pdf_links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "fetchpdf" in href or (
                    "research" in href.lower() and ".pdf" in href.lower()
                ):
                    url = href if href.startswith("http") else f"https://www.foundation-sec.com{href}"
                    pdf_links.append(url)

            for pdf_url in pdf_links[:latest_n]:
                try:
                    pdf_r = requests.get(pdf_url, headers=_HEADERS, timeout=12)
                    if pdf_r.status_code == 200 and b"%PDF" in pdf_r.content[:10]:
                        parsed = _extract_ahl_pdf_data(pdf_r.content)  # Same schema
                        parsed["report_url"] = pdf_url
                        parsed["source"] = "Foundation Securities"
                        reports.append(parsed)
                except Exception as e:
                    logger.debug("Foundation PDF %s failed: %s", pdf_url, e)

    except Exception as e:
        logger.warning("Foundation research fetch failed: %s", e)

    if reports:
        set_cached(cache_key, reports)
    return reports


# ── Aggregated Retail Sentiment Snapshot ──────────────────────────────────

def get_retail_sentiment_snapshot(symbol: str) -> Dict[str, Any]:
    """
    Combine Reddit + Google Trends + AHL top-picks into a unified
    retail sentiment context for a given ticker.

    Returns:
        {reddit, google_trends, ahl_mentioned, broker_rating, combined_label}
    """
    cache_key = f"sentiment:snapshot:{symbol.upper()}"
    cached = get_cached(cache_key, _TTL_REDDIT)
    if cached is not None:
        return cached

    snapshot: Dict[str, Any] = {"symbol": symbol.upper()}

    # Reddit
    try:
        reddit = get_reddit_sentiment(symbol)
        if reddit:
            snapshot["reddit"] = reddit
    except Exception as e:
        logger.debug("Reddit snapshot failed: %s", e)

    # AHL research — check if ticker is a top pick or has a rating
    try:
        ahl = get_ahl_research()
        sym = symbol.upper()
        if sym in ahl.get("top_picks", []):
            snapshot["ahl_top_pick"] = True
        if sym in ahl.get("ratings", {}):
            snapshot["broker_rating"] = ahl["ratings"][sym]
    except Exception as e:
        logger.debug("AHL snapshot failed: %s", e)

    # Derive combined label
    labels = []
    if snapshot.get("reddit", {}).get("sentiment_label"):
        labels.append(snapshot["reddit"]["sentiment_label"])
    if snapshot.get("ahl_top_pick"):
        labels.append("BULLISH")  # AHL top pick is a positive signal
    if snapshot.get("broker_rating", {}).get("rating") in ("Buy", "Accumulate"):
        labels.append("BULLISH")
    elif snapshot.get("broker_rating", {}).get("rating") in ("Sell", "Reduce"):
        labels.append("BEARISH")

    if labels:
        bull_count = labels.count("BULLISH")
        bear_count = labels.count("BEARISH")
        if bull_count > bear_count:
            snapshot["combined_label"] = "BULLISH"
        elif bear_count > bull_count:
            snapshot["combined_label"] = "BEARISH"
        else:
            snapshot["combined_label"] = "NEUTRAL"

    if snapshot:
        set_cached(cache_key, snapshot)
    return snapshot
