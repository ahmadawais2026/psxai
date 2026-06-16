from __future__ import annotations

import logging
import time
import datetime
from typing import Any, Dict, List, Optional
import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import PSX_PORTAL_BASE, CACHE_TTL_MARKET_WATCH
from data.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

# Module-level cache for market-watch
_market_watch_cache = None
_market_watch_cache_time = 0.0

class PSXMarketClock:
    def __init__(self):
        # PKT timezone: UTC+5
        self.tz = datetime.timezone(datetime.timedelta(hours=5))

    def get_now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc).astimezone(self.tz)

    def current_phase(self) -> str:
        now = self.get_now()
        day = now.weekday()  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
        
        if day in (5, 6):
            return "CLOSED"
            
        # Minutes since midnight
        minutes = now.hour * 60 + now.minute
        
        # Monday to Thursday
        if day in (0, 1, 2, 3):
            if 555 <= minutes < 570:  # 09:15 AM - 09:30 AM
                return "PRE_OPEN"
            elif 570 <= minutes < 572:  # 09:30 AM - 09:32 AM
                return "ORDER_MATCHING"
            elif 572 <= minutes < 930:  # 09:32 AM - 03:30 PM
                return "CONTINUOUS_TRADING"
            elif 935 <= minutes < 950:  # 03:35 PM - 03:50 PM
                return "POST_CLOSE"
            else:
                return "CLOSED"
                
        # Friday
        elif day == 4:
            if 540 <= minutes < 555:  # 09:00 AM - 09:15 AM
                return "PRE_OPEN"
            elif 555 <= minutes < 557:  # 09:15 AM - 09:17 AM
                return "ORDER_MATCHING"
            elif 557 <= minutes < 720:  # 09:17 AM - 12:00 PM
                return "CONTINUOUS_TRADING"
            elif 720 <= minutes < 855:  # 12:00 PM - 02:15 PM
                return "BREAK"
            elif 855 <= minutes < 870:  # 02:15 PM - 02:30 PM
                return "PRE_OPEN"
            elif 870 <= minutes < 872:  # 02:30 PM - 02:32 PM
                return "ORDER_MATCHING"
            elif 872 <= minutes < 990:  # 02:32 PM - 04:30 PM
                return "CONTINUOUS_TRADING"
            elif 995 <= minutes < 1010:  # 04:35 PM - 04:50 PM
                return "POST_CLOSE"
            else:
                return "CLOSED"
        
        return "CLOSED"

    def is_market_open(self) -> bool:
        return self.current_phase() in ("CONTINUOUS_TRADING", "PRE_OPEN", "ORDER_MATCHING")


def validate_ohlcv(open_val: float, high: float, low: float, close: float, volume: float) -> tuple[float, float, float, float, float]:
    """Ensure price data maintains logical consistency and non-negativity."""
    open_val = max(0.0, open_val)
    high = max(0.0, high)
    low = max(0.0, low)
    close = max(0.0, close)
    volume = max(0.0, float(volume))

    if high == 0.0:
        high = max(open_val, close)
    if low == 0.0:
        low = min(open_val, close)

    high = max(high, open_val, close)
    low = min(low, open_val, close)
    
    if high < low:
        high = low

    return open_val, high, low, close, volume


def get_valid_ldcp(symbol: str, fetched_ldcp: float) -> float:
    """Gets cached LDCP from Firestore if the fetched one is 0.0 or invalid, otherwise caches it."""
    cache_key = f"ldcp:{symbol.upper()}"
    if fetched_ldcp and fetched_ldcp > 0.0:
        try:
            set_cached(cache_key, fetched_ldcp)
        except Exception as e:
            logger.warning(f"Failed to cache LDCP in Firestore for {symbol}: {e}")
        return fetched_ldcp
    
    try:
        cached_val = get_cached(cache_key, ttl_seconds=86400 * 7)
        if cached_val is not None:
            return float(cached_val)
    except Exception as e:
        logger.warning(f"Failed to read LDCP from Firestore cache for {symbol}: {e}")
    return fetched_ldcp


def fetch_market_watch() -> Dict[str, Dict[str, Any]]:
    """Fetch the market-watch page and parse the entire market snapshot."""
    global _market_watch_cache, _market_watch_cache_time
    now = time.time()
    if _market_watch_cache is not None and (now - _market_watch_cache_time) < CACHE_TTL_MARKET_WATCH:
        return _market_watch_cache

    url = f"{PSX_PORTAL_BASE}/market-watch"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    attempts = 3
    delay = 1.0
    response = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                break
        except Exception as exc:
            logger.warning(f"Attempt {attempt + 1} to fetch market-watch failed: {exc}")
        if attempt < attempts - 1:
            time.sleep(delay)
            delay *= 2.0

    if not response or response.status_code != 200:
        logger.error(f"Failed to fetch market-watch after {attempts} attempts.")
        if _market_watch_cache is not None:
            return _market_watch_cache
        return {}

    try:
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table", class_="tbl")
        if not table:
            logger.error("Could not find table with class 'tbl' in market-watch HTML.")
            if _market_watch_cache is not None:
                return _market_watch_cache
            return {}

        thead = table.find("thead")
        tbody = table.find("tbody", class_="tbl__body")
        if not thead or not tbody:
            logger.error("Could not find thead or tbody in market-watch table.")
            if _market_watch_cache is not None:
                return _market_watch_cache
            return {}

        headers_list = [th.get("data-name") for th in thead.find_all("th")]
        rows = tbody.find_all("tr")

        snapshot = {}
        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue

            row_data = {}
            for i, cell in enumerate(cells):
                if i < len(headers_list):
                    name = headers_list[i]
                    if name in ["symbol", "sector", "listed"]:
                        if name == "symbol":
                            strong_tag = cell.find("strong")
                            if strong_tag:
                                val = strong_tag.text.strip()
                            else:
                                val = cell.get("data-search") or cell.text.strip()
                        else:
                            val = cell.text.strip()
                        row_data[name] = val
                    else:
                        order_val = cell.get("data-order")
                        try:
                            row_data[name] = float(order_val) if order_val else 0.0
                        except ValueError:
                            row_data[name] = 0.0

            symbol = row_data.get("symbol")
            if symbol:
                symbol = symbol.upper()
                ldcp = row_data.get("ldcp", 0.0)
                open_val = row_data.get("open", 0.0)
                high = row_data.get("high", 0.0)
                low = row_data.get("low", 0.0)
                current = row_data.get("close", 0.0)  # data-name="close" -> CURRENT
                volume = int(row_data.get("volume", 0))

                # Apply OHLC validation
                open_val, high, low, current, volume = validate_ohlcv(open_val, high, low, current, volume)

                snapshot[symbol] = {
                    "symbol": symbol,
                    "sector": row_data.get("sector", ""),
                    "listed": row_data.get("listed", ""),
                    "ldcp": ldcp,
                    "open": open_val,
                    "high": high,
                    "low": low,
                    "current": current,
                    "change": row_data.get("change", 0.0),
                    "change_percent": row_data.get("percentChange", 0.0),
                    "volume": volume,
                }

        if snapshot:
            _market_watch_cache = snapshot
            _market_watch_cache_time = now
            return snapshot
    except Exception as exc:
        logger.error(f"Error parsing market-watch HTML: {exc}")
        if _market_watch_cache is not None:
            return _market_watch_cache
        return {}

    return {}


def fetch_single_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Extract a single ticker from the market-watch cache/fetch."""
    sym = symbol.strip().upper().replace(".KA", "")
    snapshot = fetch_market_watch()
    if sym in snapshot:
        quote = snapshot[sym].copy()
        
        # Apply LDCP recovery cache logic only here for the single ticker
        quote["ldcp"] = get_valid_ldcp(sym, quote["ldcp"])
        
        # Re-apply OHLC validation since LDCP might have updated
        quote["open"], quote["high"], quote["low"], quote["current"], quote["volume"] = validate_ohlcv(
            quote["open"], quote["high"], quote["low"], quote["current"], quote["volume"]
        )
        
        # Recalculate change metrics
        quote["change"] = quote["current"] - quote["ldcp"]
        quote["change_percent"] = (quote["change"] / quote["ldcp"] * 100.0) if quote["ldcp"] > 0.0 else 0.0
        
        return quote
    
    # Fallback: if not in market watch, let's try to query timeseries/int directly
    logger.info(f"Ticker {sym} not found in market-watch. Attempting timeseries fallback.")
    ticks = fetch_intraday_ticks(sym)
    if ticks:
        # Reconstruct quote from tick data
        prices = [t[1] for t in ticks]
        volumes = [t[2] for t in ticks if len(t) > 2]
        
        current = prices[-1]
        open_val = prices[0]
        high = max(prices)
        low = min(prices)
        volume = sum(volumes) if volumes else 0
        
        ldcp = get_valid_ldcp(sym, open_val)
        open_val, high, low, current, volume = validate_ohlcv(open_val, high, low, current, volume)
        
        change = current - ldcp
        change_pct = (change / ldcp * 100) if ldcp > 0.0 else 0.0
        
        return {
            "symbol": sym,
            "sector": "",
            "listed": "",
            "ldcp": ldcp,
            "open": open_val,
            "high": high,
            "low": low,
            "current": current,
            "change": change,
            "change_percent": change_pct,
            "volume": volume,
        }
    return None


def fetch_intraday_ticks(symbol: str) -> List[List[Any]]:
    """Fetch intraday tick data from the /timeseries/int/{symbol} endpoint."""
    sym = symbol.strip().upper().replace(".KA", "")
    url = f"{PSX_PORTAL_BASE}/timeseries/int/{sym}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    attempts = 3
    delay = 1.0
    response = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                break
        except Exception as exc:
            logger.warning(f"Attempt {attempt + 1} to fetch intraday ticks for {sym} failed: {exc}")
        if attempt < attempts - 1:
            time.sleep(delay)
            delay *= 2.0

    if not response or response.status_code != 200:
        logger.error(f"Failed to fetch intraday ticks for {sym} after {attempts} attempts.")
        return []

    try:
        res = response.json()
        data = res.get("data", [])
        validated_data = []
        for item in data:
            if len(item) >= 2:
                ts = item[0]
                price = float(item[1])
                vol = float(item[2]) if len(item) >= 3 else 0.0
                
                _, price, _, price, vol = validate_ohlcv(price, price, price, price, vol)
                validated_data.append([ts, price, int(vol)])
        return validated_data
    except Exception as exc:
        logger.error(f"Error parsing intraday ticks for {sym}: {exc}")
        return []


def fetch_eod_history(symbol: str) -> pd.DataFrame:
    """Fetch historical EOD data from the /timeseries/eod/{symbol} endpoint."""
    sym = symbol.strip().upper().replace(".KA", "")
    url = f"{PSX_PORTAL_BASE}/timeseries/eod/{sym}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    attempts = 3
    delay = 1.0
    response = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                break
        except Exception as exc:
            logger.warning(f"Attempt {attempt + 1} to fetch EOD history for {sym} failed: {exc}")
        if attempt < attempts - 1:
            time.sleep(delay)
            delay *= 2.0

    if not response or response.status_code != 200:
        logger.error(f"Failed to fetch EOD history for {sym} after {attempts} attempts.")
        return pd.DataFrame()

    try:
        res = response.json()
        data = res.get("data", [])
        records = []
        for item in data:
            if len(item) >= 3:
                ts = item[0]
                close = float(item[1])
                volume = float(item[2])
                open_val = float(item[3]) if len(item) >= 4 else close
                
                high = max(open_val, close)
                low = min(open_val, close)
                
                open_val, high, low, close, volume = validate_ohlcv(open_val, high, low, close, volume)
                
                dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                
                records.append({
                    "Date": dt,
                    "Open": open_val,
                    "High": high,
                    "Low": low,
                    "Close": close,
                    "Volume": volume
                })
        
        if not records:
            return pd.DataFrame()
            
        df = pd.DataFrame(records)
        df.set_index("Date", inplace=True)
        df.sort_index(inplace=True)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df
    except Exception as exc:
        logger.error(f"Error parsing EOD history for {sym}: {exc}")
        return pd.DataFrame()
