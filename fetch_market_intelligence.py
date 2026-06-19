"""
fetch_market_intelligence.py
═══════════════════════════════════════════════════════════════════════
Comprehensive market intelligence fetcher for the PSX Investment Advisor.

Covers:
  1.  Pakistan Macro        — World Bank API (GDP, inflation, reserves, CA balance)
  2.  Currency & Forex      — yfinance (PKR vs USD, EUR, GBP, CNY, AED, SAR)
  3.  Global Commodities    — yfinance (oil, gas, coal, cotton, wheat, gold, fertilizer)
  4.  KSE Market Overview   — yfinance (KSE-100, KSE-30, Pakistan ETF, global indices)
  5.  Pakistan Business News — RSS feeds (Business Recorder, Dawn, Tribune, The News)
  6.  Geopolitical Events   — GDELT Project API (free, no key)
  7.  Pakistan Weather      — Open-Meteo API (free, no key) for 7 major cities
  8.  Sectoral Intelligence — Global sector ETF proxies + Pakistan-specific proxies
  9.  IMF & Economic Data   — IMF DataMapper API (free, no key)
  10. Company-Specific      — PSX announcements + GDELT + NewsAPI (if key set)

Output:
  market_intelligence/latest_intelligence.json   — always overwritten (agents read this)
  market_intelligence/intelligence_YYYYMMDD_HHMM.json  — timestamped archive

Optional API keys in .env:
  NEWS_API_KEY = ...    (newsapi.org — free 100 req/day, improves news coverage)

Usage:
  python fetch_market_intelligence.py                    # full fetch
  python fetch_market_intelligence.py --ticker OGDC      # + company-specific
  python fetch_market_intelligence.py --skip gdelt imf   # skip slow sections
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_intelligence")
REQUEST_TIMEOUT = 15
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Suppress noisy third-party loggers
for _noisy in ("trafilatura", "trafilatura.core", "trafilatura.utils", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)


# ═══════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════

def _get(url: str, params: dict = None, timeout: int = REQUEST_TIMEOUT) -> Optional[requests.Response]:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        return r if r.status_code == 200 else None
    except Exception as e:
        log.warning(f"    GET {url} failed: {e}")
        return None


def _yf_history(ticker: str, period: str = "1mo") -> Optional[pd.DataFrame]:
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=period, interval="1d")
        return df if not df.empty else None
    except Exception:
        return None


def _price_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Summarize a yfinance history DataFrame into a standard price dict."""
    current = round(float(df["Close"].iloc[-1]), 4)
    prev_week = round(float(df["Close"].iloc[-6]), 4) if len(df) >= 6 else current
    prev_month = round(float(df["Close"].iloc[0]), 4)
    return {
        "current": current,
        "change_1w_pct": round((current - prev_week) / prev_week * 100, 2),
        "change_1m_pct": round((current - prev_month) / prev_month * 100, 2),
        "high_1m": round(float(df["High"].max()), 4),
        "low_1m": round(float(df["Low"].min()), 4),
    }


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


# ═══════════════════════════════════════════════════════
#  1. PAKISTAN MACRO — World Bank API
# ═══════════════════════════════════════════════════════

WORLD_BANK_INDICATORS = {
    "inflation_cpi_pct":          "FP.CPI.TOTL.ZG",
    "gdp_growth_pct":             "NY.GDP.MKTP.KD.ZG",
    "gdp_usd_billions":           "NY.GDP.MKTP.CD",
    "current_account_pct_gdp":    "BN.CAB.XOKA.GD.ZS",
    "fx_reserves_usd":            "FI.RES.TOTL.CD",
    "govt_debt_pct_gdp":          "GC.DOD.TOTL.GD.ZS",
    "govt_deficit_pct_gdp":       "GC.NLD.TOTL.GD.ZS",
    "unemployment_pct":           "SL.UEM.TOTL.ZS",
    "exports_usd":                "NE.EXP.GNFS.CD",
    "imports_usd":                "NE.IMP.GNFS.CD",
    "trade_balance_pct_gdp":      "NE.RSB.GNFS.ZS",
    "fdi_net_inflows_usd":        "BX.KLT.DINV.CD.WD",
    "remittances_pct_gdp":        "BX.TRF.PWKR.DT.GD.ZS",
    "broad_money_growth_pct":     "FM.LBL.BMNY.ZG",
    "lending_interest_rate_pct":  "FR.INR.LEND",
    "deposit_interest_rate_pct":  "FR.INR.DPST",
    "real_interest_rate_pct":     "FR.INR.RINR",
    "inflation_producer_pct":     "FP.WPI.TOTL.XD.ZG",
    "gross_savings_pct_gdp":      "NY.GNS.ICTR.ZS",
    "gross_investment_pct_gdp":   "NE.GDI.TOTL.ZS",
}


def fetch_world_bank_macro() -> Dict[str, Any]:
    log.info("[Macro] Fetching World Bank Pakistan indicators...")
    base = "https://api.worldbank.org/v2/country/PK/indicator"
    results = {}

    for name, code in WORLD_BANK_INDICATORS.items():
        r = _get(f"{base}/{code}", params={"format": "json", "mrv": 5, "per_page": 5})
        if r:
            try:
                data = r.json()
                if len(data) >= 2 and data[1]:
                    entries = [e for e in data[1] if e.get("value") is not None]
                    if entries:
                        latest = entries[0]
                        results[name] = {
                            "value": round(latest["value"], 4) if latest["value"] else None,
                            "year": latest["date"],
                            "indicator_name": latest.get("indicator", {}).get("value", ""),
                            "history": [
                                {"year": e["date"], "value": round(e["value"], 4) if e["value"] else None}
                                for e in entries
                            ],
                        }
            except Exception as e:
                log.warning(f"    World Bank {name}: {e}")
        time.sleep(0.25)

    log.info(f"  [OK] World Bank: {len(results)}/{len(WORLD_BANK_INDICATORS)} indicators")
    return results


# ═══════════════════════════════════════════════════════
#  2. CURRENCY & FOREX — yfinance
# ═══════════════════════════════════════════════════════

FOREX_PAIRS = {
    "USD_PKR":  "PKR=X",
    "EUR_PKR":  "EURPKR=X",
    "GBP_PKR":  "GBPPKR=X",
    "CNY_PKR":  "CNYPKR=X",
    "AED_PKR":  "AEDPKR=X",
    "SAR_PKR":  "SARPKR=X",
    "JPY_PKR":  "JPYPKR=X",
    # Key cross rates that affect Pakistan trade
    "USD_CNY":  "USDCNY=X",
    "USD_INR":  "INR=X",
    "EUR_USD":  "EURUSD=X",
    "DXY":      "DX-Y.NYB",  # US Dollar Index — strengthens = bad for PKR
}


def fetch_forex() -> Dict[str, Any]:
    log.info("[Forex] Fetching currency rates...")
    results = {}

    for name, ticker in FOREX_PAIRS.items():
        df = _yf_history(ticker, period="1mo")
        if df is not None:
            results[name] = _price_summary(df)
        time.sleep(0.1)

    log.info(f"  [OK] Forex: {len(results)}/{len(FOREX_PAIRS)} pairs")
    return results


# ═══════════════════════════════════════════════════════
#  3. GLOBAL COMMODITIES — yfinance
# ═══════════════════════════════════════════════════════

COMMODITIES = {
    # Energy (critical for Pakistan — major importer)
    "crude_oil_wti":      "CL=F",
    "crude_oil_brent":    "BZ=F",
    "natural_gas_us":     "NG=F",
    "lng_proxy":          "UNG",      # US Natural Gas ETF as LNG proxy
    # Metals
    "gold":               "GC=F",
    "silver":             "SI=F",
    "copper":             "HG=F",
    "steel_proxy":        "SLX",      # VanEck Steel ETF
    "aluminum_proxy":     "JJU",      # iPath Aluminum ETN
    # Agriculture (Pakistan is a major cotton/wheat/sugarcane producer)
    "cotton":             "CT=F",
    "wheat":              "ZW=F",
    "corn":               "ZC=F",
    "sugar":              "SB=F",
    "palm_oil":           "PALM.KL",  # Bursa Malaysia palm oil
    "soybean":            "ZS=F",
    # Fertilizer proxies (critical for Pakistan agri sector)
    "cf_industries":      "CF",       # CF Industries — urea/ammonia producer
    "mosaic_co":          "MOS",      # Mosaic — phosphate/potash
    # Coal (Pakistan power sector)
    "coal_proxy":         "BTU",      # Peabody Energy as coal proxy
}


def fetch_commodities() -> Dict[str, Any]:
    log.info("[Commodities] Fetching global commodity prices...")
    results = {}

    for name, ticker in COMMODITIES.items():
        df = _yf_history(ticker, period="1mo")
        if df is not None:
            results[name] = _price_summary(df)
        time.sleep(0.1)

    log.info(f"  [OK] Commodities: {len(results)}/{len(COMMODITIES)} fetched")
    return results


# ═══════════════════════════════════════════════════════
#  4. KSE MARKET OVERVIEW — yfinance
# ═══════════════════════════════════════════════════════

MARKET_TICKERS = {
    "pakistan": {
        "KSE100":         "^KSE100",
        "KSE30":          "^KSE30",
        "Pakistan_ETF":   "PAK",      # Global X MSCI Pakistan ETF
    },
    "regional": {
        "India_Sensex":   "^BSESN",
        "India_ETF":      "INDA",
        "China_Shanghai": "000001.SS",
        "Bangladesh":     "^DSEX",
        "Sri_Lanka":      "^CSEALL",
    },
    "global": {
        "SP500":          "^GSPC",
        "Nasdaq":         "^IXIC",
        "MSCI_EM":        "EEM",
        "Frontier_Mkts":  "FM",
        "MSCI_World":     "URTH",
    },
    "bonds_rates": {
        "US_10Y_Yield":   "^TNX",    # US 10-year — affects EM capital flows
        "US_2Y_Yield":    "^IRX",
        "TIP_ETF":        "TIP",     # Inflation-protected bonds proxy
    },
}


def fetch_market_overview() -> Dict[str, Any]:
    log.info("[Market] Fetching KSE market overview and global indices...")
    results = {}

    for category, tickers in MARKET_TICKERS.items():
        results[category] = {}
        for name, ticker in tickers.items():
            df = _yf_history(ticker, period="3mo")
            if df is not None:
                summary = _price_summary(df)
                summary["avg_volume"] = int(df["Volume"].mean()) if df["Volume"].mean() > 0 else 0
                results[category][name] = summary
            time.sleep(0.1)

    log.info(f"  [OK] Market: {sum(len(v) for v in results.values())} indices fetched")
    return results


# ═══════════════════════════════════════════════════════
#  5. PAKISTAN BUSINESS NEWS — RSS Feeds (no API key)
# ═══════════════════════════════════════════════════════

RSS_FEEDS = {
    "brecorder_pakistan": "https://www.brecorder.com/feeds/pakistan",
    "brecorder_markets":  "https://www.brecorder.com/feeds/markets",
    "dawn_business":      "https://www.dawn.com/feeds/business",
    "dawn_latest":        "https://www.dawn.com/feeds/latest-news",
    "tribune_business":   "https://tribune.com.pk/feed/business",
    "arynews_business":   "https://arynews.tv/category/business/feed/",
    "thenews_latest":     "https://www.thenews.com.pk/rss/1/1",
}


def _parse_rss(feed_url: str, max_items: int = 20) -> List[Dict]:
    items = []
    r = _get(feed_url, timeout=10)
    if not r:
        return items
    try:
        # Normalize encoding declaration so ET can parse ISO-8859-1 feeds
        content = r.content
        if b"ISO-8859-1" in content[:200] or b"iso-8859-1" in content[:200]:
            content = content.decode("iso-8859-1").encode("utf-8")
            content = content.replace(b"ISO-8859-1", b"UTF-8").replace(b"iso-8859-1", b"UTF-8")

        root = ET.fromstring(content)
        channel = root.find("channel")
        entries = (channel.findall("item") if channel is not None else []) or root.findall(".//item")
        for entry in entries[:max_items]:
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            pub_date = (entry.findtext("pubDate") or "").strip()
            description = _strip_html(entry.findtext("description") or "")[:400]
            category = (entry.findtext("category") or "").strip()
            if title:
                items.append({
                    "title": title,
                    "link": link,
                    "published": pub_date,
                    "summary": description,
                    "category": category,
                })
    except Exception as e:
        log.warning(f"    RSS parse error {feed_url}: {e}")
    return items


def fetch_news_rss() -> Dict[str, Any]:
    log.info("[News] Fetching Pakistan business news via RSS...")
    results = {}
    total = 0

    for source, url in RSS_FEEDS.items():
        articles = _parse_rss(url)
        if articles:
            results[source] = articles
            total += len(articles)
            log.info(f"    {source}: {len(articles)} articles")
        else:
            log.warning(f"    {source}: no articles")

    log.info(f"  [OK] RSS: {total} articles from {len(results)} sources")
    return results


# ═══════════════════════════════════════════════════════
#  6. NEWS — NewsAPI (optional, needs NEWS_API_KEY)
# ═══════════════════════════════════════════════════════

NEWSAPI_QUERIES = [
    "Pakistan economy 2025",
    "Pakistan stock exchange KSE investment",
    "Pakistan IMF program review",
    "Pakistan SBP interest rate monetary policy",
    "Pakistan inflation CPI",
    "Pakistan CPEC China investment",
    "Pakistan exports remittances",
    "Pakistan energy crisis power sector",
    "Pakistan agriculture wheat cotton",
    "Pakistan political instability PTI",
    "Pakistan India relations geopolitical",
    "Pakistan flood drought weather impact",
]


def fetch_newsapi(extra_queries: List[str] = None) -> Dict[str, Any]:
    if not NEWS_API_KEY:
        log.info("[NewsAPI] Skipping — NEWS_API_KEY not set in .env")
        return {"status": "skipped", "reason": "NEWS_API_KEY not configured"}

    queries = NEWSAPI_QUERIES + (extra_queries or [])
    log.info(f"[NewsAPI] Querying {len(queries)} topics...")
    results = {}
    base = "https://newsapi.org/v2/everything"

    for q in queries:
        r = _get(base, params={
            "q": q, "language": "en", "sortBy": "publishedAt",
            "pageSize": 10, "apiKey": NEWS_API_KEY,
        })
        if r:
            try:
                articles = r.json().get("articles", [])
                results[q] = [{
                    "title": a.get("title", ""),
                    "source": a.get("source", {}).get("name", ""),
                    "published": a.get("publishedAt", ""),
                    "summary": (a.get("description") or "")[:400],
                    "url": a.get("url", ""),
                } for a in articles]
                log.info(f"    '{q}': {len(articles)} articles")
            except Exception as e:
                log.warning(f"    NewsAPI '{q}': {e}")
        time.sleep(0.5)

    log.info(f"  [OK] NewsAPI: {sum(len(v) for v in results.values())} total articles")
    return results


# ═══════════════════════════════════════════════════════
#  7. GEOPOLITICAL — GDELT Project API (free, no key)
# ═══════════════════════════════════════════════════════

GDELT_QUERIES = [
    # Macro & economy
    "Pakistan economy recession growth",
    "Pakistan IMF bailout program",
    "Pakistan inflation food prices",
    "Pakistan rupee devaluation currency",
    "Pakistan foreign exchange reserves",
    # Geopolitical
    "Pakistan India tension military border",
    "Pakistan China CPEC belt road",
    "Pakistan Afghanistan security",
    "Pakistan United States relations",
    "Pakistan FATF grey list terrorism",
    # Political
    "Pakistan political crisis government",
    "Pakistan election protest rally",
    # Sectoral
    "Pakistan energy power circular debt",
    "Pakistan oil gas exploration",
    "Pakistan banking sector NPL",
    "Pakistan cement construction infrastructure",
    "Pakistan textile exports apparel",
    "Pakistan fertilizer agriculture",
    # Natural events
    "Pakistan flood monsoon drought disaster",
]


def fetch_gdelt() -> Dict[str, Any]:
    log.info("[GDELT] Fetching geopolitical intelligence...")
    base = "https://api.gdeltproject.org/api/v2/doc/doc"
    results = {}

    for query in GDELT_QUERIES:
        r = _get(base, params={
            "query": query,
            "mode": "artlist",
            "maxrecords": 8,
            "format": "json",
            "timespan": "120h",  # last 5 days
        }, timeout=20)
        if r:
            try:
                articles = r.json().get("articles", [])
                results[query] = [{
                    "title": a.get("title", ""),
                    "url": a.get("url", ""),
                    "source": a.get("domain", ""),
                    "seendate": a.get("seendate", ""),
                    # GDELT tone: positive = bullish signal, negative = bearish
                    "tone": round(float(a["tone"]), 2) if a.get("tone") else None,
                } for a in articles]
                log.info(f"    '{query}': {len(articles)} articles")
            except Exception as e:
                log.warning(f"    GDELT '{query}': {e}")
        time.sleep(1.2)  # GDELT rate limit

    log.info(f"  [OK] GDELT: {len(results)} queries completed")
    return results


# ═══════════════════════════════════════════════════════
#  8. WEATHER — Open-Meteo API (free, no key)
# ═══════════════════════════════════════════════════════

PAKISTAN_CITIES = {
    "Karachi":    {"lat": 24.86, "lon": 67.01, "role": "financial_hub_main_port"},
    "Lahore":     {"lat": 31.55, "lon": 74.35, "role": "industrial_hub_punjab"},
    "Islamabad":  {"lat": 33.72, "lon": 73.04, "role": "capital_policy_center"},
    "Faisalabad": {"lat": 31.42, "lon": 73.08, "role": "textile_industrial_hub"},
    "Multan":     {"lat": 30.20, "lon": 71.47, "role": "agriculture_cotton_south_punjab"},
    "Sukkur":     {"lat": 27.70, "lon": 68.85, "role": "agriculture_sindh_irrigation"},
    "Quetta":     {"lat": 30.18, "lon": 66.99, "role": "resources_balochistan_afghanistan_trade"},
    "Peshawar":   {"lat": 34.01, "lon": 71.57, "role": "kpk_hub_afghan_trade"},
    "Hyderabad":  {"lat": 25.37, "lon": 68.36, "role": "sindh_agriculture_cotton"},
}


def fetch_weather() -> Dict[str, Any]:
    """
    Weather data for Pakistan. Relevant for:
    - Agriculture: cotton, wheat, sugarcane, rice seasons
    - Cement: construction slows in heavy rain
    - Energy: temperature drives AC/heating demand
    - Floods: infrastructure damage, displacement, crop losses
    """
    log.info("[Weather] Fetching Pakistan weather data (Open-Meteo)...")
    base = "https://api.open-meteo.com/v1/forecast"
    results = {}

    for city, info in PAKISTAN_CITIES.items():
        r = _get(base, params={
            "latitude": info["lat"],
            "longitude": info["lon"],
            "daily": (
                "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                "windspeed_10m_max,et0_fao_evapotranspiration,precipitation_hours"
            ),
            "current_weather": True,
            "timezone": "Asia/Karachi",
            "forecast_days": 7,
        })
        if r:
            try:
                data = r.json()
                current = data.get("current_weather", {})
                daily = data.get("daily", {})
                precip_7d = sum(p or 0 for p in (daily.get("precipitation_sum") or []))
                max_temps = [t for t in (daily.get("temperature_2m_max") or []) if t is not None]
                min_temps = [t for t in (daily.get("temperature_2m_min") or []) if t is not None]

                results[city] = {
                    "role": info["role"],
                    "current": {
                        "temperature_c": current.get("temperature"),
                        "windspeed_kmh": current.get("windspeed"),
                        "weathercode": current.get("weathercode"),
                    },
                    "forecast_7d": {
                        "total_precipitation_mm": round(precip_7d, 1),
                        "avg_max_temp_c": round(sum(max_temps) / len(max_temps), 1) if max_temps else None,
                        "avg_min_temp_c": round(sum(min_temps) / len(min_temps), 1) if min_temps else None,
                        "daily_precip_mm": daily.get("precipitation_sum", []),
                        "daily_max_temp_c": daily.get("temperature_2m_max", []),
                        "precip_hours": daily.get("precipitation_hours", []),
                    },
                    "investment_signals": _weather_signals(city, precip_7d, max_temps),
                }
                log.info(f"    {city}: {current.get('temperature')}°C, 7d rain: {precip_7d:.1f}mm")
            except Exception as e:
                log.warning(f"    Weather {city}: {e}")

    log.info(f"  [OK] Weather: {len(results)}/{len(PAKISTAN_CITIES)} cities")
    return results


def _weather_signals(city: str, precip_7d: float, max_temps: List[float]) -> List[str]:
    """Generate investment-relevant signals from weather data."""
    signals = []
    avg_max = sum(max_temps) / len(max_temps) if max_temps else 0

    if precip_7d > 100:
        signals.append("HEAVY_RAIN: Construction slowdown risk — negative for cement sector")
    if precip_7d > 200:
        signals.append("FLOOD_RISK: Potential crop damage and infrastructure disruption")
    if precip_7d < 5 and city in ("Multan", "Sukkur", "Hyderabad"):
        signals.append("DROUGHT_RISK: Low rainfall in agricultural belt — negative for fertilizer demand")
    if avg_max > 42:
        signals.append("EXTREME_HEAT: High energy demand — positive for KAPCO, KEL, HUBC")
    if avg_max > 38:
        signals.append("HIGH_HEAT: Elevated electricity demand — supportive for power sector")
    if avg_max < 15 and city == "Lahore":
        signals.append("COLD_WEATHER: Low construction activity in Punjab — watch cement volumes")
    return signals


# ═══════════════════════════════════════════════════════
#  9. SECTORAL INTELLIGENCE — ETF proxies + PSX sectors
# ═══════════════════════════════════════════════════════

SECTOR_PROXIES = {
    # Pakistan-specific ETF
    "pakistan_etf_PAK":         "PAK",
    # Global sector ETFs (as directional proxies for Pakistan sectors)
    "global_banks_KBE":         "KBE",
    "global_energy_XLE":        "XLE",
    "global_materials_XLB":     "XLB",    # chemicals, fertilizers, metals
    "global_utilities_XLU":     "XLU",
    "global_industrials_XLI":   "XLI",
    "global_consumer_XLP":      "XLP",    # consumer staples
    "emerging_mkts_EEM":        "EEM",
    "frontier_mkts_FM":         "FM",
    # Oil & gas specific
    "oil_producers_XOP":        "XOP",
    "oil_services_OIH":         "OIH",
    # Fertilizer & chemicals
    "fertilizer_proxy_CF":      "CF",
    "potash_proxy_MOS":         "MOS",
    # Steel & construction materials
    "steel_SLX":                "SLX",
    # Textile
    "cotton_BAL_proxy":         "BAL",    # iPath Bloomberg Cotton ETN
    # Pharma
    "global_pharma_IHE":        "IHE",
    # Tech/telecom
    "telecom_IYZ":              "IYZ",
}

# KSE sector tickers via yfinance (where available)
PSX_SECTOR_TICKERS = {
    "OGDC":  "OGDC.KA",
    "PPL":   "PPL.KA",
    "HBL":   "HBL.KA",
    "MCB":   "MCB.KA",
    "ENGRO": "ENGRO.KA",
    "LUCK":  "LUCK.KA",
    "PSO":   "PSO.KA",
    "HUBC":  "HUBC.KA",
    "UBL":   "UBL.KA",
    "MARI":  "MARI.KA",
}


def fetch_sectoral_intelligence() -> Dict[str, Any]:
    log.info("[Sectoral] Fetching sectoral intelligence...")
    results = {"global_proxies": {}, "psx_bellwethers": {}}

    for name, ticker in SECTOR_PROXIES.items():
        df = _yf_history(ticker, period="1mo")
        if df is not None:
            results["global_proxies"][name] = _price_summary(df)
        time.sleep(0.1)

    for name, ticker in PSX_SECTOR_TICKERS.items():
        df = _yf_history(ticker, period="1mo")
        if df is not None:
            results["psx_bellwethers"][name] = _price_summary(df)
        time.sleep(0.1)

    log.info(
        f"  [OK] Sectoral: {len(results['global_proxies'])} global + "
        f"{len(results['psx_bellwethers'])} PSX bellwethers"
    )
    return results


# ═══════════════════════════════════════════════════════
#  10. IMF & ECONOMIC DATA — IMF DataMapper API
# ═══════════════════════════════════════════════════════

IMF_INDICATORS = {
    "gdp_current_usd":          "NGDPD",       # GDP in USD billions
    "gdp_per_capita_usd":       "NGDPDPC",
    "gdp_growth_pct":           "NGDP_RPCH",   # Real GDP growth
    "inflation_avg_pct":        "PCPIPCH",      # CPI inflation (avg)
    "inflation_eop_pct":        "PCPIEPCH",     # CPI inflation (end of period)
    "current_account_usd":      "BCA",          # Current account balance
    "current_account_pct_gdp":  "BCA_NGDPD",
    "govt_net_lending_gdp":     "GGXCNL_NGDP", # Fiscal deficit
    "govt_gross_debt_gdp":      "GGXWDG_NGDP", # Public debt
    "unemployment_pct":         "LUR",
    "population_millions":      "LP",
    "exports_usd":              "TX_RPCH",      # Export volume change
    "imports_usd":              "TM_RPCH",      # Import volume change
    "fx_reserves_months":       "AIP_IX",       # Reserves in months of imports
}


def fetch_imf_data() -> Dict[str, Any]:
    log.info("[IMF] Fetching IMF DataMapper Pakistan indicators...")
    base = "https://www.imf.org/external/datamapper/api/v1"
    results = {"indicators": {}, "imf_news": []}

    for name, code in IMF_INDICATORS.items():
        r = _get(f"{base}/{code}/PAK")
        if r:
            try:
                data = r.json()
                values = data.get("values", {}).get(code, {}).get("PAK", {})
                if values:
                    sorted_years = sorted(values.keys(), reverse=True)[:5]
                    results["indicators"][name] = {
                        yr: round(values[yr], 4) if values[yr] is not None else None
                        for yr in sorted_years
                    }
            except Exception as e:
                log.warning(f"    IMF {name}: {e}")
        time.sleep(0.3)

    # IMF-Pakistan news via GDELT
    r = _get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": "IMF Pakistan loan program review Article IV",
            "mode": "artlist", "maxrecords": 15,
            "format": "json", "timespan": "168h",
        }, timeout=20
    )
    if r:
        try:
            results["imf_news"] = [{
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": a.get("domain", ""),
                "seendate": a.get("seendate", ""),
                "tone": round(float(a["tone"]), 2) if a.get("tone") else None,
            } for a in r.json().get("articles", [])]
        except Exception as e:
            log.warning(f"    IMF news: {e}")

    log.info(
        f"  [OK] IMF: {len(results['indicators'])} indicators, "
        f"{len(results['imf_news'])} news items"
    )
    return results


# ═══════════════════════════════════════════════════════
#  11. COMPANY & SECTOR INTELLIGENCE
# ═══════════════════════════════════════════════════════

# Per-sector: GDELT search queries, RSS keywords, and company name aliases
SECTOR_INTELLIGENCE: Dict[str, Dict] = {
    "Oil & Gas": {
        "gdelt_queries": [
            "Pakistan oil gas exploration production",
            "Pakistan OGRA gas price notification",
            "Pakistan petroleum crude oil policy",
            "Pakistan LNG import terminal",
            "Pakistan circular debt energy",
            "Pakistan Sui gas pipeline supply",
            "crude oil price OPEC production",
        ],
        "rss_keywords": ["oil", "gas", "petroleum", "LNG", "OGRA", "crude", "refinery", "fuel",
                         "circular debt", "exploration", "pipeline", "SNGP", "SSGC"],
        "newsapi_queries": ["Pakistan oil gas sector", "OGRA Pakistan gas price"],
    },
    "Banking": {
        "gdelt_queries": [
            "Pakistan SBP interest rate monetary policy",
            "Pakistan bank credit growth NPL",
            "Pakistan banking sector profitability",
            "Pakistan KIBOR rate spread",
            "Pakistan banking regulation SECP",
            "Pakistan remittances banking channel",
        ],
        "rss_keywords": ["SBP", "interest rate", "bank", "KIBOR", "monetary policy", "credit",
                         "NPL", "loan", "deposit", "policy rate", "remittance"],
        "newsapi_queries": ["Pakistan SBP policy rate", "Pakistan banking sector earnings"],
    },
    "Cement": {
        "gdelt_queries": [
            "Pakistan cement demand construction",
            "Pakistan infrastructure CPEC project",
            "Pakistan housing scheme real estate",
            "Pakistan coal price energy cost cement",
            "Pakistan cement export Afghanistan",
        ],
        "rss_keywords": ["cement", "construction", "infrastructure", "housing", "CPEC", "coal",
                         "dispatch", "offtake", "clinker", "real estate"],
        "newsapi_queries": ["Pakistan cement sector", "Pakistan construction infrastructure"],
    },
    "Fertilizer": {
        "gdelt_queries": [
            "Pakistan fertilizer urea production gas supply",
            "Pakistan agriculture crop season",
            "Pakistan wheat cotton sowing rabi kharif",
            "Pakistan fertilizer subsidy government",
            "urea DAP price global",
            "Pakistan gas price SNGP fertilizer",
        ],
        "rss_keywords": ["fertilizer", "urea", "DAP", "agriculture", "crop", "wheat", "cotton",
                         "rabi", "kharif", "farming", "gas supply", "subsidy"],
        "newsapi_queries": ["Pakistan fertilizer sector", "Pakistan agriculture crop urea"],
    },
    "Power": {
        "gdelt_queries": [
            "Pakistan power sector circular debt NEPRA",
            "Pakistan electricity tariff increase",
            "Pakistan load shedding energy crisis",
            "Pakistan IPP independent power producer",
            "Pakistan renewable solar wind energy",
            "Pakistan fuel cost power generation",
        ],
        "rss_keywords": ["power", "electricity", "NEPRA", "tariff", "load shedding", "IPP",
                         "circular debt", "WAPDA", "generation", "capacity", "renewable"],
        "newsapi_queries": ["Pakistan power sector NEPRA", "Pakistan electricity tariff IPP"],
    },
    "Textile": {
        "gdelt_queries": [
            "Pakistan textile exports apparel",
            "Pakistan cotton crop price",
            "Pakistan GSP Plus EU trade",
            "Pakistan textile order buyer demand",
            "Pakistan yarn fabric export",
            "cotton price global supply",
        ],
        "rss_keywords": ["textile", "cotton", "yarn", "fabric", "apparel", "exports", "GSP",
                         "garment", "spinning", "weaving", "order", "buyer"],
        "newsapi_queries": ["Pakistan textile exports", "Pakistan cotton crop"],
    },
    "Pharma": {
        "gdelt_queries": [
            "Pakistan pharma drug price DRAP",
            "Pakistan pharmaceutical import API",
            "Pakistan health sector medicine",
            "Pakistan drug regulatory authority",
        ],
        "rss_keywords": ["pharma", "drug", "medicine", "DRAP", "healthcare", "API",
                         "price increase", "import", "generics"],
        "newsapi_queries": ["Pakistan pharma sector DRAP", "Pakistan drug prices"],
    },
    "Steel": {
        "gdelt_queries": [
            "Pakistan steel iron scrap price",
            "Pakistan construction rebar demand",
            "Pakistan steel import duty tariff",
            "global steel price China demand",
        ],
        "rss_keywords": ["steel", "iron", "scrap", "rebar", "billet", "construction",
                         "import duty", "metal"],
        "newsapi_queries": ["Pakistan steel sector", "steel price Pakistan"],
    },
    "Automobile": {
        "gdelt_queries": [
            "Pakistan automobile car sales",
            "Pakistan auto sector import duty",
            "Pakistan vehicle production decline",
            "Pakistan car financing interest rate",
        ],
        "rss_keywords": ["automobile", "car", "vehicle", "auto", "sales", "financing",
                         "import", "assembly", "tractor"],
        "newsapi_queries": ["Pakistan auto sector sales", "Pakistan car sales"],
    },
    "Technology": {
        "gdelt_queries": [
            "Pakistan IT exports software",
            "Pakistan technology freelance remittance",
            "Pakistan startup fintech investment",
            "Pakistan BPO outsourcing",
        ],
        "rss_keywords": ["IT", "technology", "software", "export", "freelance", "startup",
                         "fintech", "digital", "outsourcing"],
        "newsapi_queries": ["Pakistan IT exports technology", "Pakistan software sector"],
    },
    "Food": {
        "gdelt_queries": [
            "Pakistan food inflation prices",
            "Pakistan FMCG consumer goods",
            "Pakistan sugar wheat flour prices",
        ],
        "rss_keywords": ["food", "FMCG", "consumer", "sugar", "flour", "inflation",
                         "prices", "dairy", "edible oil"],
        "newsapi_queries": ["Pakistan food inflation FMCG", "Pakistan consumer goods"],
    },
    "Chemical": {
        "gdelt_queries": [
            "Pakistan chemical industry PVC",
            "Pakistan polymer petrochemical",
            "Pakistan chemical export import",
        ],
        "rss_keywords": ["chemical", "polymer", "PVC", "petrochemical", "resin",
                         "industrial", "feedstock"],
        "newsapi_queries": ["Pakistan chemical sector", "Pakistan polymer industry"],
    },
    "Insurance": {
        "gdelt_queries": [
            "Pakistan insurance sector SECP",
            "Pakistan takaful life insurance premium",
        ],
        "rss_keywords": ["insurance", "takaful", "premium", "SECP", "life insurance", "claims"],
        "newsapi_queries": ["Pakistan insurance sector"],
    },
    "Transport": {
        "gdelt_queries": [
            "Pakistan transport logistics shipping",
            "Pakistan freight cargo trade",
            "Pakistan port Karachi KESC",
        ],
        "rss_keywords": ["transport", "logistics", "shipping", "freight", "cargo", "port", "airline"],
        "newsapi_queries": ["Pakistan transport logistics sector"],
    },
    "Real Estate": {
        "gdelt_queries": [
            "Pakistan real estate property market",
            "Pakistan housing construction REIT",
            "Pakistan property prices Karachi Lahore",
        ],
        "rss_keywords": ["real estate", "property", "housing", "REIT", "construction", "plot", "apartment"],
        "newsapi_queries": ["Pakistan real estate property market"],
    },
    "Paper & Packaging": {
        "gdelt_queries": [
            "Pakistan paper board packaging industry",
            "Pakistan pulp paper import",
        ],
        "rss_keywords": ["paper", "board", "packaging", "carton", "pulp", "print"],
        "newsapi_queries": ["Pakistan paper packaging sector"],
    },
    "Tobacco": {
        "gdelt_queries": [
            "Pakistan tobacco cigarette tax",
            "Pakistan FED tobacco duty",
        ],
        "rss_keywords": ["tobacco", "cigarette", "FED", "excise", "smoking"],
        "newsapi_queries": ["Pakistan tobacco sector tax"],
    },
    "Miscellaneous": {
        "gdelt_queries": [
            "Pakistan business corporate earnings",
            "Pakistan KSE stock market",
        ],
        "rss_keywords": ["Pakistan", "KSE", "earnings", "profit", "dividend", "results"],
        "newsapi_queries": ["Pakistan business earnings"],
    },
}

# Fallback for unknown/unlisted sectors
_SECTOR_FALLBACK = {
    "gdelt_queries": [
        "Pakistan economy business sector",
        "Pakistan KSE stock corporate earnings",
    ],
    "rss_keywords": ["Pakistan", "KSE", "stock", "earnings", "profit", "dividend"],
    "newsapi_queries": ["Pakistan business sector earnings"],
}


def _resolve_sector(ticker: str) -> tuple[str, str]:
    """
    Look up ticker in PSX_TICKERS (local DB) first, then fall back to yfinance.
    Returns (company_name, sector).
    """
    try:
        from data.psx_tickers import PSX_TICKERS
        if ticker in PSX_TICKERS:
            info = PSX_TICKERS[ticker]
            return info["name"], info["sector"]
    except Exception:
        pass

    # yfinance fallback
    try:
        t = yf.Ticker(f"{ticker}.KA")
        info = t.info or {}
        name = info.get("longName") or info.get("shortName") or ticker
        sector = info.get("sector") or info.get("industry") or "Unknown"
        return name, sector
    except Exception:
        return ticker, "Unknown"


def _filter_rss_by_keywords(articles: List[Dict], keywords: List[str]) -> List[Dict]:
    """Return articles whose title or summary contains any of the keywords (case-insensitive)."""
    kw_lower = [k.lower() for k in keywords]
    matched = []
    for a in articles:
        text = (a.get("title", "") + " " + a.get("summary", "")).lower()
        if any(k in text for k in kw_lower):
            matched.append(a)
    return matched


def _fetch_article_content(url: str, max_chars: int = 4000) -> str:
    """
    Fetch and extract full article text from a URL.
    Uses trafilatura (best quality) with a regex <p>-tag fallback.
    Returns empty string on failure — never raises.
    """
    try:
        import trafilatura
        r = _get(url, timeout=15)
        if r:
            text = trafilatura.extract(
                r.text,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )
            if text and len(text) > 100:
                return text[:max_chars]
    except Exception:
        pass

    # Fallback: extract <p> tags via regex
    try:
        r = _get(url, timeout=15)
        if r:
            html = re.sub(
                r'<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>',
                '', r.text, flags=re.DOTALL | re.IGNORECASE
            )
            paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL | re.IGNORECASE)
            text = " ".join(
                _strip_html(p).strip() for p in paragraphs
                if len(_strip_html(p).strip()) > 60
            )
            if text:
                return text[:max_chars]
    except Exception:
        pass

    return ""


def _google_news_rss(query: str, max_items: int = 25) -> List[Dict]:
    """
    Search Google News RSS — aggregates Pakistani and global sources.
    Extracts the real article URL from Google's redirect link so that
    full content scraping hits the original publisher, not Google.
    """
    from urllib.parse import quote_plus, urlparse, parse_qs
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en&gl=PK&ceid=PK:en"
    articles = _parse_rss(url, max_items=max_items)

    # Google News RSS wraps the real URL in a redirect — unwrap it
    for a in articles:
        link = a.get("link", "")
        # Format: https://news.google.com/rss/articles/...?url=REAL_URL or via <source>
        # The real URL is in the <link> tag after the redirect
        # Better: extract from the description href
        if "news.google.com" in link:
            try:
                parsed = urlparse(link)
                qs = parse_qs(parsed.query)
                if "url" in qs:
                    a["link"] = qs["url"][0]
            except Exception:
                pass  # keep original link

    return articles


def _gdelt_historical(query: str, days_back: int = 365, max_records: int = 250) -> List[Dict]:
    """
    Query GDELT over the past N days using timespan in hours.
    GDELT supports up to ~8760h (1 year). Returns up to max_records articles.
    """
    timespan = f"{min(days_back * 24, 8760)}h"
    r = _get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": query,
            "mode": "artlist",
            "maxrecords": max_records,
            "format": "json",
            "timespan": timespan,
        },
        timeout=25,
    )
    if r:
        try:
            return [{
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": a.get("domain", ""),
                "seendate": a.get("seendate", ""),
                "tone": round(float(a["tone"]), 2) if a.get("tone") else None,
                "query": query,
                "content": "",
            } for a in r.json().get("articles", [])]
        except Exception as e:
            log.warning(f"    GDELT historical '{query}': {e}")
    return []


def fetch_company_intelligence(ticker: str, all_rss_articles: Dict[str, List] = None) -> Dict[str, Any]:
    """
    Fetch company-specific and sector-specific intelligence for a PSX ticker.

    - Resolves sector from PSX_TICKERS (361 companies) or yfinance fallback
    - Searches GDELT over the past 1 YEAR using sector-tailored queries
    - Searches Google News RSS for sector + company queries
    - Filters RSS articles by sector keywords + company name
    - Fetches full article content for all relevant articles via trafilatura
    - Optionally queries NewsAPI with sector-specific queries (if key set)
    """
    ticker = ticker.upper()
    company_name, sector = _resolve_sector(ticker)
    sector_cfg = SECTOR_INTELLIGENCE.get(sector, _SECTOR_FALLBACK)

    log.info(f"[Company] {ticker} — {company_name} ({sector})")
    results = {
        "ticker": ticker,
        "company_name": company_name,
        "sector": sector,
        "company_news_rss": [],
        "sector_news_rss": [],
        "google_news_sector": [],
        "google_news_company": [],
        "company_gdelt": [],
        "sector_gdelt_historical": [],
        "newsapi_articles": [],
    }

    # ── 1. RSS filtering (current headlines) ──────────────────────────
    if all_rss_articles is None:
        log.info("  Fetching RSS feeds for filtering...")
        all_rss_articles = fetch_news_rss()

    all_articles_flat = [a for articles in all_rss_articles.values() for a in articles]

    company_keywords = [ticker] + [w for w in company_name.split() if len(w) > 3]
    company_rss = _filter_rss_by_keywords(all_articles_flat, company_keywords)[:10]
    sector_rss = _filter_rss_by_keywords(all_articles_flat, sector_cfg["rss_keywords"])[:20]

    # Fetch full content for RSS matches
    log.info(f"    Fetching full content for {len(company_rss)} company + {len(sector_rss)} sector RSS articles...")
    for a in company_rss:
        if a.get("link"):
            a["content"] = _fetch_article_content(a["link"])
            time.sleep(0.3)
    for a in sector_rss:
        if a.get("link"):
            a["content"] = _fetch_article_content(a["link"])
            time.sleep(0.3)

    results["company_news_rss"] = company_rss
    results["sector_news_rss"] = sector_rss
    log.info(f"    RSS: {len(company_rss)} company, {len(sector_rss)} sector")

    # ── 2. Google News RSS (broader coverage, current + recent) ────────
    log.info("    Fetching Google News RSS...")
    for q in sector_cfg["gdelt_queries"][:3]:   # top 3 sector queries
        articles = _google_news_rss(q, max_items=20)
        for a in articles:
            if a.get("link"):
                a["content"] = _fetch_article_content(a["link"])
                time.sleep(0.3)
        results["google_news_sector"].extend(articles)
        time.sleep(0.5)

    company_gn_query = f"{company_name} Pakistan"
    company_gn = _google_news_rss(company_gn_query, max_items=15)
    for a in company_gn:
        if a.get("link"):
            a["content"] = _fetch_article_content(a["link"])
            time.sleep(0.3)
    results["google_news_company"] = company_gn

    log.info(
        f"    Google News: {len(results['google_news_sector'])} sector, "
        f"{len(results['google_news_company'])} company"
    )

    # ── 3. GDELT — company queries (last 1 year) ───────────────────────
    company_gdelt_queries = [
        f"{ticker} Pakistan",
        f'"{company_name}" Pakistan',
        f"{ticker} KSE earnings dividend profit",
    ]
    for q in company_gdelt_queries:
        articles = _gdelt_historical(q, days_back=365, max_records=50)
        results["company_gdelt"].extend(articles)
        log.info(f"    GDELT company '{q}': {len(articles)} articles")
        time.sleep(1.2)

    # ── 4. GDELT — sector queries (last 1 year, up to 250 per query) ───
    log.info(f"    Fetching 1-year GDELT history for {sector} sector ({len(sector_cfg['gdelt_queries'])} queries)...")
    for q in sector_cfg["gdelt_queries"]:
        articles = _gdelt_historical(q, days_back=365, max_records=250)
        results["sector_gdelt_historical"].extend(articles)
        log.info(f"    GDELT sector '{q}': {len(articles)} articles")
        time.sleep(1.2)

    # Fetch full content for top GDELT sector articles (most recent 20)
    top_gdelt = sorted(
        results["sector_gdelt_historical"],
        key=lambda x: x.get("seendate", ""),
        reverse=True
    )[:20]
    log.info(f"    Fetching full content for top {len(top_gdelt)} GDELT sector articles...")
    for a in top_gdelt:
        if a.get("url"):
            a["content"] = _fetch_article_content(a["url"])
            time.sleep(0.4)

    log.info(
        f"    GDELT: {len(results['company_gdelt'])} company (1yr), "
        f"{len(results['sector_gdelt_historical'])} sector (1yr)"
    )

    # ── 5. NewsAPI — sector + company queries (if key set) ────────────
    if NEWS_API_KEY:
        newsapi_queries = sector_cfg["newsapi_queries"] + [f"{ticker} Pakistan"]
        for q in newsapi_queries:
            r = _get("https://newsapi.org/v2/everything", params={
                "q": q, "language": "en", "sortBy": "publishedAt",
                "pageSize": 10, "apiKey": NEWS_API_KEY,
            })
            if r:
                try:
                    articles = [{
                        "title": a.get("title", ""),
                        "source": a.get("source", {}).get("name", ""),
                        "published": a.get("publishedAt", ""),
                        "summary": (a.get("description") or "")[:400],
                        "url": a.get("url", ""),
                        "content": "",
                        "query": q,
                    } for a in r.json().get("articles", [])]
                    # Fetch full content for NewsAPI articles too
                    for a in articles:
                        if a.get("url"):
                            a["content"] = _fetch_article_content(a["url"])
                            time.sleep(0.3)
                    results["newsapi_articles"].extend(articles)
                except Exception as e:
                    log.warning(f"    NewsAPI '{q}': {e}")
            time.sleep(0.5)
        log.info(f"    NewsAPI: {len(results['newsapi_articles'])} articles")

    log.info(
        f"  [OK] {ticker} ({sector}): "
        f"{len(results['company_news_rss'])} company RSS, "
        f"{len(results['sector_news_rss'])} sector RSS, "
        f"{len(results['google_news_sector'])} Google News sector, "
        f"{len(results['company_gdelt'])} company GDELT (1yr), "
        f"{len(results['sector_gdelt_historical'])} sector GDELT (1yr)"
    )

    # ── NewsAPI — sector + company queries ────────────────────────────
    if NEWS_API_KEY:
        newsapi_queries = sector_cfg["newsapi_queries"] + [f"{ticker} Pakistan"]
        for q in newsapi_queries:
            r = _get("https://newsapi.org/v2/everything", params={
                "q": q, "language": "en", "sortBy": "publishedAt",
                "pageSize": 8, "apiKey": NEWS_API_KEY,
            })
            if r:
                try:
                    results["newsapi_articles"].extend([{
                        "title": a.get("title", ""),
                        "source": a.get("source", {}).get("name", ""),
                        "published": a.get("publishedAt", ""),
                        "summary": (a.get("description") or "")[:400],
                        "url": a.get("url", ""),
                        "query": q,
                    } for a in r.json().get("articles", [])])
                except Exception as e:
                    log.warning(f"    NewsAPI '{q}': {e}")
            time.sleep(0.5)
        log.info(f"    NewsAPI: {len(results['newsapi_articles'])} articles")

    log.info(
        f"  [OK] {ticker} ({sector}): "
        f"{len(results['company_news_rss'])} company RSS, "
        f"{len(results['sector_news_rss'])} sector RSS, "
        f"{len(results['google_news_sector'])} Google News sector, "
        f"{len(results['company_gdelt'])} company GDELT (1yr), "
        f"{len(results['sector_gdelt_historical'])} sector GDELT (1yr)"
    )
    return results


# ═══════════════════════════════════════════════════════
#  AGENT CONTEXT BUILDER
# ═══════════════════════════════════════════════════════

def build_agent_context_summary(intelligence: Dict[str, Any]) -> str:
    """
    Produce a condensed plain-text summary of the intelligence data
    for injection into Gemini agent prompts as context.
    """
    lines = [
        f"=== MARKET INTELLIGENCE SNAPSHOT ===",
        f"Fetched at: {intelligence.get('fetched_at_human', 'N/A')}",
        "",
    ]

    # Macro
    macro = intelligence.get("macro", {})
    if macro:
        lines.append("--- PAKISTAN MACRO ---")
        for k, v in macro.items():
            if isinstance(v, dict) and "value" in v:
                lines.append(f"  {k}: {v['value']} ({v.get('year', '')})")
        lines.append("")

    # Forex
    forex = intelligence.get("forex", {})
    if forex and "USD_PKR" in forex:
        usd = forex["USD_PKR"]
        lines.append("--- CURRENCY ---")
        lines.append(f"  USD/PKR: {usd.get('current')} (1M chg: {usd.get('change_1m_pct')}%)")
        for pair, data in forex.items():
            if pair != "USD_PKR" and isinstance(data, dict):
                lines.append(f"  {pair}: {data.get('current')} (1M: {data.get('change_1m_pct')}%)")
        lines.append("")

    # Commodities
    commodities = intelligence.get("commodities", {})
    if commodities:
        lines.append("--- COMMODITIES ---")
        key_commodities = ["crude_oil_brent", "natural_gas_us", "cotton", "wheat", "gold", "coal_proxy"]
        for k in key_commodities:
            if k in commodities:
                d = commodities[k]
                lines.append(f"  {k}: {d.get('current')} (1M: {d.get('change_1m_pct')}%)")
        lines.append("")

    # KSE Market
    market = intelligence.get("market", {})
    kse = market.get("pakistan", {}) if market else {}
    if kse:
        lines.append("--- KSE MARKET ---")
        for idx, data in kse.items():
            lines.append(f"  {idx}: {data.get('current')} (1M: {data.get('change_1m_pct')}%)")
        lines.append("")

    # Weather signals
    weather = intelligence.get("weather", {})
    signals = []
    for city_data in weather.values():
        signals.extend(city_data.get("investment_signals", []))
    if signals:
        lines.append("--- WEATHER SIGNALS ---")
        for s in signals:
            lines.append(f"  {s}")
        lines.append("")

    # Recent headlines (top 10 from business recorder)
    news = intelligence.get("news", {})
    br_news = news.get("business_recorder", [])
    if br_news:
        lines.append("--- RECENT HEADLINES (Business Recorder) ---")
        for article in br_news[:10]:
            lines.append(f"  • {article['title']}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════

ALL_SECTIONS = ["macro", "forex", "commodities", "market", "news", "newsapi", "gdelt", "weather", "sectoral", "imf"]

SECTION_MAP = {
    "macro":      ("Pakistan Macro (World Bank)",      fetch_world_bank_macro),
    "forex":      ("Currency & FX Rates",              fetch_forex),
    "commodities":("Global Commodities",               fetch_commodities),
    "market":     ("KSE Market Overview",              fetch_market_overview),
    "news":       ("Pakistan Business News (RSS)",     fetch_news_rss),
    "newsapi":    ("NewsAPI",                          fetch_newsapi),
    "gdelt":      ("Geopolitical Events (GDELT)",      fetch_gdelt),
    "weather":    ("Pakistan Weather (Open-Meteo)",    fetch_weather),
    "sectoral":   ("Sectoral Intelligence",            fetch_sectoral_intelligence),
    "imf":        ("IMF & Economic Data",              fetch_imf_data),
}


def main():
    parser = argparse.ArgumentParser(description="PSX Market Intelligence Fetcher")
    parser.add_argument(
        "--ticker", type=str, default=None,
        help="Also fetch company-specific intelligence (e.g. --ticker OGDC)"
    )
    parser.add_argument(
        "--only", nargs="+", choices=ALL_SECTIONS,
        help="Run only these sections (e.g. --only forex commodities)"
    )
    parser.add_argument(
        "--skip", nargs="+", choices=ALL_SECTIONS, default=[],
        help="Skip these sections (e.g. --skip gdelt imf)"
    )
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    started_at = datetime.now()

    print("=" * 62)
    print("  PSX Market Intelligence Fetcher")
    print(f"  {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    if NEWS_API_KEY:
        print("  NewsAPI: configured")
    else:
        print("  NewsAPI: not configured (set NEWS_API_KEY in .env for better news)")
    print("=" * 62)

    sections_to_run = args.only if args.only else ALL_SECTIONS
    sections_to_run = [s for s in sections_to_run if s not in args.skip]

    intelligence = {
        "fetched_at": started_at.isoformat(),
        "fetched_at_human": started_at.strftime("%Y-%m-%d %H:%M:%S PKT"),
        "sections_run": sections_to_run,
    }

    for key in sections_to_run:
        label, fn = SECTION_MAP[key]
        print(f"\n[{key.upper()}] {label}")
        try:
            intelligence[key] = fn()
        except Exception as e:
            log.error(f"Section '{key}' failed: {e}")
            intelligence[key] = {"error": str(e)}

    # Optional company-specific — pass already-fetched RSS to avoid re-fetching
    if args.ticker:
        print(f"\n[COMPANY] {args.ticker}")
        rss_cache = intelligence.get("news") or None
        intelligence["company_specific"] = fetch_company_intelligence(args.ticker, rss_cache)

    # Build condensed agent context summary
    intelligence["agent_context_summary"] = build_agent_context_summary(intelligence)

    # Save latest (always overwritten — agents read this)
    output_path = os.path.join(OUTPUT_DIR, "latest_intelligence.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(intelligence, f, indent=2, ensure_ascii=False, default=str)

    # Save timestamped archive
    ts = started_at.strftime("%Y%m%d_%H%M")
    archive_path = os.path.join(OUTPUT_DIR, f"intelligence_{ts}.json")
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(intelligence, f, indent=2, ensure_ascii=False, default=str)

    elapsed = (datetime.now() - started_at).total_seconds()
    print("\n" + "=" * 62)
    print(f"  Done in {elapsed:.1f}s")
    print(f"  Output: {output_path}")
    print(f"  Archive: {archive_path}")
    print("=" * 62)

    # Print the agent context summary
    print("\n" + intelligence["agent_context_summary"])


if __name__ == "__main__":
    main()
