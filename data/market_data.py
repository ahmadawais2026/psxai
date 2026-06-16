"""
data/market_data.py
═══════════════════════════════════════════════════════════════════════
AskAnalyst and REST-based data retrieval for Pakistan Stock Exchange (PSX) equities.

Every public function transparently:
  1.  Handles local PSX tickers and AskAnalyst company mapping.
  2.  Checks the SQLite cache (data/cache.py) before hitting the
      network.
  3.  Returns structured dicts / DataFrames ready for consumption by
      the technical-analysis and agent layers.

Usage::

    from data.market_data import get_quote, get_history, get_fundamentals

    quote = get_quote("OGDC")
    df    = get_history("OGDC", period="6mo")
    fund  = get_fundamentals("HBL")
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
# No yfinance import - migrated to AskAnalyst and Yahoo REST APIs.

# Create a requests session with a default timeout to prevent network hangs
class TimeoutHTTPAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.pop("timeout", 5)
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        timeout = kwargs.get("timeout")
        if timeout is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)

_session = requests.Session()
_adapter = TimeoutHTTPAdapter(timeout=5)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

from config import (
    CACHE_TTL_FUNDAMENTALS,
    CACHE_TTL_HISTORY,
    CACHE_TTL_QUOTE,
    HISTORY_PERIOD_DAILY,
    PSX_SUFFIX,
)
from data.cache import get_cached, set_cached
from data.psx_tickers import search_tickers as _search_local

logger = logging.getLogger(__name__)


# ── Internal Helpers ─────────────────────────────────────────────────


def _yahoo_symbol(symbol: str) -> str:
    """
    Normalise a user-supplied symbol to its Yahoo Finance form.

    Strips whitespace, upper-cases, and appends ``.KA`` if the suffix
    is not already present.

    Args:
        symbol: Raw ticker string (e.g. ``'ogdc'``, ``'OGDC.KA'``).

    Returns:
        Canonical Yahoo ticker (e.g. ``'OGDC.KA'``).
    """
    s = symbol.strip().upper()
    if not s.endswith(PSX_SUFFIX):
        s += PSX_SUFFIX
    return s


def _local_symbol(symbol: str) -> str:
    """Strip the ``.KA`` suffix for use as cache key / display."""
    return symbol.strip().upper().replace(PSX_SUFFIX, "")


def _get_askanalyst_id(symbol: str) -> Optional[int]:
    """Resolve the AskAnalyst company ID using the local PSX_TICKERS database."""
    from data.psx_tickers import PSX_TICKERS
    local = _local_symbol(symbol)
    if local in PSX_TICKERS:
        return PSX_TICKERS[local].get("askanalyst_id")
    return None


def _fetch_yahoo_chart_rest(ticker: str, range_str: str = "1mo", interval_str: str = "1d") -> Optional[Dict[str, Any]]:
    """Fetch raw chart JSON from Yahoo Finance REST API directly."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"range": range_str, "interval": interval_str}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning(f"Yahoo chart REST fetch failed for {ticker}: {e}")
    return None


def _safe_get(info: dict, key: str, default: Any = None) -> Any:
    """Safely extract a key from a dictionary."""
    val = info.get(key, default)
    if val is None or val == "None":
        return default
    return val


def _get_clean_name(local: str, info: dict) -> str:
    """Get a clean, professional name for the company."""
    from data.psx_tickers import PSX_TICKERS
    if local in PSX_TICKERS:
        return PSX_TICKERS[local].get("name", local)
    
    ask_name = _safe_get(info, "name", _safe_get(info, "label", local))
    if "," in ask_name and (".KA" in ask_name or local in ask_name):
        return ask_name.split(",")[0].replace(".KA", "").strip()
    return ask_name


def _parse_period_key(key: str) -> Optional[Any]:
    import datetime
    # Try %b-%y (e.g. 'Jun-25', 'Dec-24')
    try:
        return datetime.datetime.strptime(key.strip(), "%b-%y")
    except ValueError:
        pass
    # Try %Y (e.g. '2025')
    try:
        return datetime.datetime.strptime(key.strip(), "%Y")
    except ValueError:
        pass
    return None


def _parse_firestore_financials_to_highlights(data: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    import numpy as np
    
    result = {}
    
    # 1. Determine the latest period key (e.g., '2025', 'Sep-25')
    period_keys = []
    inc_stmt = data.get("income_statement", [])
    if inc_stmt:
        sample_row = inc_stmt[0]
        for k in sample_row.keys():
            if k not in ["Metric", "Unit", "symbol", "period", "last_updated"]:
                dt = _parse_period_key(k)
                if dt:
                    period_keys.append((dt, k))
                    
    if not period_keys:
        bal_sheet = data.get("balance_sheet", [])
        if bal_sheet:
            sample_row = bal_sheet[0]
            for k in sample_row.keys():
                if k not in ["Metric", "Unit", "symbol", "period", "last_updated"]:
                    dt = _parse_period_key(k)
                    if dt:
                        period_keys.append((dt, k))
                        
    if not period_keys:
        return {}
        
    latest_dt, latest_period = max(period_keys)
    result["latest_period"] = latest_period
    
    def clean_val(val):
        if val is None:
            return 0.0
        try:
            if isinstance(val, float) and np.isnan(val):
                return 0.0
            return float(val)
        except:
            return 0.0

    def get_metric_value(statement_name, synonyms):
        rows = data.get(statement_name, [])
        for row in rows:
            metric_name = row.get("Metric", "").strip()
            if any(syn.lower() in metric_name.lower() for syn in synonyms):
                return clean_val(row.get(latest_period))
        return 0.0

    revenue = get_metric_value("income_statement", ["total revenue", "net sales", "markup/interest revenue", "mark-up/interest revenue"])
    net_income = get_metric_value("income_statement", ["profit after tax", "net income", "net profit", "profit for the period"])
    operating_profit = get_metric_value("income_statement", ["operating profit", "operating profit/ (loss)", "net mark-up/interest income", "net markup/interest income"])
    eps = get_metric_value("income_statement", ["eps - basic", "eps", "earnings per share"])
    
    total_assets = get_metric_value("balance_sheet", ["total asset - total assets", "total assets", "total asset"])
    total_liabilities = get_metric_value("balance_sheet", ["total liabilities - total liabilities", "total liabilities", "total liability"])
    cash = get_metric_value("balance_sheet", ["cash & bank balances", "cash and balances with treasury banks", "cash and cash equivalents"])
    
    operating_cash_flow = get_metric_value("cash_flow", ["operating cash flow", "net cash generated from operating activities", "cash flow from operating activities"])
    free_cash_flow = get_metric_value("cash_flow", ["free cash flow"])
    
    roe = get_metric_value("income_statement", ["return on equity", "roe"])
    if roe == 0.0:
        roe = None
        
    dividend_yield = get_metric_value("income_statement", ["dividend yield"])
    if dividend_yield == 0.0:
        dividend_yield = None
    
    operating_margin = (operating_profit / revenue * 100) if revenue > 0 else 0.0
    net_margin = (net_income / revenue * 100) if revenue > 0 else 0.0
    
    result.update({
        "revenue": revenue,
        "net_income": net_income,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "cash": cash,
        "operating_cash_flow": operating_cash_flow,
        "free_cash_flow": free_cash_flow,
        "eps": eps,
        "roe": roe,
        "dividend_yield": dividend_yield
    })
    
    return result



# ── Quote ────────────────────────────────────────────────────────────


def get_quote(symbol: str) -> Dict[str, Any]:
    """
    Fetch the current (or most-recent) quote for a PSX stock.

    First attempts to pull real-time intraday data directly from the official
    PSX Data Portal (dps.psx.com.pk). If that fails or returns no data, falls
    back to the yfinance history overlay layer.

    The result is cached for ``CACHE_TTL_QUOTE`` seconds (default 5 min).

    Args:
        symbol: PSX ticker without suffix (e.g. ``'OGDC'``).

    Returns:
        Dict with keys: ``symbol``, ``name``, ``price``,
        ``change``, ``change_percent``, ``volume``, ``market_cap``,
        ``day_high``, ``day_low``, ``open``, ``previous_close``,
        ``fifty_two_week_high``, ``fifty_two_week_low``, ``currency``.

    Raises:
        No exceptions are raised; errors are caught and an
        ``error`` key is set in the returned dict.
    """
    local = _local_symbol(symbol)
    cache_key = f"quote:{local}"

    cached = get_cached(cache_key, CACHE_TTL_QUOTE)
    if cached is not None:
        return cached

    try:
        # 1. Try to fetch real-time quote from the official PSX Data Portal
        psx_quote = None
        try:
            from data import psx_portal
            portal_quote = psx_portal.fetch_single_quote(local)
            if portal_quote:
                psx_quote = {
                    "symbol":                local,
                    "name":                  _get_clean_name(local, {}),
                    "price":                 portal_quote["current"],
                    "change":               portal_quote["change"],
                    "change_percent":       portal_quote["change_percent"],
                    "volume":               portal_quote["volume"],
                    "market_cap":           0,
                    "day_high":             portal_quote["high"],
                    "day_low":              portal_quote["low"],
                    "open":                 portal_quote["open"],
                    "previous_close":       portal_quote["ldcp"],
                    "fifty_two_week_high":  0,
                    "fifty_two_week_low":   0,
                    "currency":             "PKR",
                }
        except Exception as exc:
            logger.warning("PSX Portal fetch failed for %s: %s", local, exc)

        # 3. Fallback to AskAnalyst and Yahoo REST if PSX Portal fetch failed or was empty
        if psx_quote is not None:
            quote = psx_quote
        else:
            ask_id = _get_askanalyst_id(local)
            quote_raw = None
            if ask_id:
                try:
                    url = f"https://api.askanalyst.com.pk/api/sharepricedatanew/{ask_id}"
                    r_ask = requests.get(url, timeout=8)
                    if r_ask.status_code == 200:
                        quote_raw = r_ask.json()
                except Exception as e:
                    logger.warning("AskAnalyst fallback quote fetch failed for %s: %s", local, e)
            
            if quote_raw and isinstance(quote_raw, dict):
                price = float(quote_raw.get("current") or quote_raw.get("close") or 0.0)
                open_val = float(quote_raw.get("open") or 0.0)
                high_val = float(quote_raw.get("high") or 0.0)
                low_val = float(quote_raw.get("low") or 0.0)
                volume = int(float(quote_raw.get("volume") or 0.0))
                prev_close = float(quote_raw.get("ldcp") or 0.0)
                change = float(quote_raw.get("change") or 0.0)
                change_percent = float(quote_raw.get("change_in_percentage") or 0.0)
                market_cap = float(quote_raw.get("market_cap") or 0.0) * 1_000_000
                
                quote = {
                    "symbol":                local,
                    "name":                  _get_clean_name(local, quote_raw),
                    "price":                 price,
                    "change":               change,
                    "change_percent":       change_percent,
                    "volume":               volume,
                    "market_cap":           market_cap,
                    "day_high":             high_val,
                    "day_low":              low_val,
                    "open":                 open_val,
                    "previous_close":       prev_close,
                    "fifty_two_week_high":  float(quote_raw.get("fifty_two_week_high") or 0.0),
                    "fifty_two_week_low":   float(quote_raw.get("fifty_two_week_low") or 0.0),
                    "currency":             "PKR",
                }
            else:
                try:
                    res = _fetch_yahoo_chart_rest(_yahoo_symbol(symbol), range_str="5d")
                    if res and "chart" in res and res["chart"].get("result"):
                        result = res["chart"]["result"][0]
                        meta = result.get("meta", {})
                        indicators = result.get("indicators", {}).get("quote", [{}])[0]
                        closes = [c for c in indicators.get("close", []) if c is not None]
                        opens = [o for o in indicators.get("open", []) if o is not None]
                        highs = [h for h in indicators.get("high", []) if h is not None]
                        lows = [l for l in indicators.get("low", []) if l is not None]
                        volumes = [v for v in indicators.get("volume", []) if v is not None]
                        
                        price = closes[-1] if closes else meta.get("regularMarketPrice", 0.0)
                        open_val = opens[-1] if opens else meta.get("regularMarketOpen", 0.0)
                        high_val = highs[-1] if highs else price
                        low_val = lows[-1] if lows else price
                        volume = int(volumes[-1]) if volumes else 0
                        prev_close = closes[-2] if len(closes) >= 2 else meta.get("previousClose", price)
                        change = price - prev_close
                        change_percent = (change / prev_close) * 100 if prev_close != 0.0 else 0.0
                        
                        quote = {
                            "symbol":                local,
                            "name":                  _get_clean_name(local, {}),
                            "price":                 price,
                            "change":               change,
                            "change_percent":       change_percent,
                            "volume":               volume,
                            "market_cap":           meta.get("marketCap", 0),
                            "day_high":             high_val,
                            "day_low":              low_val,
                            "open":                 open_val,
                            "previous_close":       prev_close,
                            "fifty_two_week_high":  0.0,
                            "fifty_two_week_low":   0.0,
                            "currency":             meta.get("currency", "PKR"),
                        }
                    else:
                        return {"symbol": local, "error": f"No data found for {local} on PSX Portal, AskAnalyst, or Yahoo REST"}
                except Exception as exc:
                    return {"symbol": local, "error": f"Failed to fetch quote via Yahoo REST fallback: {exc}"}

        set_cached(cache_key, quote)
        return quote

    except Exception as exc:
        logger.error("Error fetching quote for %s: %s", local, exc)
        return {"symbol": local, "error": str(exc)}


# ── Price History ────────────────────────────────────────────────────


def get_history(
    symbol: str,
    period: str = HISTORY_PERIOD_DAILY,
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Fetch OHLCV price history for a PSX stock.

    Cached for ``CACHE_TTL_HISTORY`` seconds (default 15 min).

    Args:
        symbol:   PSX ticker without suffix.
        period:   yfinance period string (``'1mo'``, ``'6mo'``, ``'1y'``, etc.).
        interval: Bar interval (``'1d'``, ``'1h'``, ``'5m'``, etc.).

    Returns:
        pandas DataFrame with columns ``Open``, ``High``, ``Low``,
        ``Close``, ``Volume`` indexed by datetime.  Returns an empty
        DataFrame on error.
    """
    local = _local_symbol(symbol)
    cache_key = f"history:{local}:{period}:{interval}"

    cached = get_cached(cache_key, CACHE_TTL_HISTORY)
    if cached is not None:
        try:
            df = pd.DataFrame(cached)
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"])
                df.set_index("Date", inplace=True)
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            return df
        except Exception:
            pass  # fall through to fresh fetch

    try:
        if interval == "1d":
            try:
                from data import psx_portal
                df = psx_portal.fetch_eod_history(local)
                if not df.empty:
                    import datetime
                    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                    limit_date = None
                    if period == "1mo":
                        limit_date = now - datetime.timedelta(days=31)
                    elif period == "3mo":
                        limit_date = now - datetime.timedelta(days=92)
                    elif period == "6mo":
                        limit_date = now - datetime.timedelta(days=183)
                    elif period == "1y":
                        limit_date = now - datetime.timedelta(days=366)
                    elif period == "2y":
                        limit_date = now - datetime.timedelta(days=731)
                    elif period == "5y":
                        limit_date = now - datetime.timedelta(days=1826)

                    if limit_date:
                        df = df[df.index >= limit_date]

                    min_points = 15 if period == "1mo" else 30
                    if len(df) >= min_points:
                        df_reset = df.reset_index()
                        if "Date" in df_reset.columns:
                            df_reset["Date"] = df_reset["Date"].dt.strftime("%Y-%m-%d %H:%M:%S")
                        cache_data = df_reset.to_dict(orient="records")
                        set_cached(cache_key, cache_data)
                        if df.index.tz is not None:
                            df.index = df.index.tz_localize(None)
                        return df
                logger.info("PSX EOD history empty or insufficient for %s, trying Yahoo REST fallback", local)
            except Exception as p_exc:
                logger.warning("PSX EOD history failed for %s: %s, trying Yahoo REST fallback", local, p_exc)

        # Fallback to Yahoo REST API
        res = _fetch_yahoo_chart_rest(_yahoo_symbol(symbol), range_str=period, interval_str=interval)
        if not res or not res.get("chart", {}).get("result"):
            logger.warning("No history data for %s (period=%s, interval=%s)", local, period, interval)
            return pd.DataFrame()

        result = res["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        quote_indicators = result.get("indicators", {}).get("quote", [{}])[0]
        
        opens = quote_indicators.get("open", [])
        highs = quote_indicators.get("high", [])
        lows = quote_indicators.get("low", [])
        closes = quote_indicators.get("close", [])
        volumes = quote_indicators.get("volume", [])
        
        # Build pandas DataFrame
        records = []
        import datetime
        for i, ts in enumerate(timestamps):
            if i < len(closes) and closes[i] is not None:
                dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                records.append({
                    "Date": dt,
                    "Open": float(opens[i]) if i < len(opens) and opens[i] is not None else float(closes[i]),
                    "High": float(highs[i]) if i < len(highs) and highs[i] is not None else float(closes[i]),
                    "Low": float(lows[i]) if i < len(lows) and lows[i] is not None else float(closes[i]),
                    "Close": float(closes[i]),
                    "Volume": float(volumes[i]) if i < len(volumes) and volumes[i] is not None else 0.0
                })
                
        if not records:
            return pd.DataFrame()
            
        df = pd.DataFrame(records)
        df.set_index("Date", inplace=True)
        df.sort_index(inplace=True)

        # Cache as serialisable records
        df_reset = df.reset_index()
        if "Date" in df_reset.columns:
            if hasattr(df_reset["Date"].dt, "strftime"):
                df_reset["Date"] = df_reset["Date"].dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                df_reset["Date"] = df_reset["Date"].astype(str)
        cache_data = df_reset.to_dict(orient="records")
        set_cached(cache_key, cache_data)

        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df

    except Exception as exc:
        logger.error("Error fetching history for %s: %s", local, exc)
        return pd.DataFrame()


# ── Fundamentals ─────────────────────────────────────────────────────


def get_fundamentals(symbol: str) -> Dict[str, Any]:
    """
    Fetch fundamental metrics for a PSX stock.

    Cached for ``CACHE_TTL_FUNDAMENTALS`` seconds (default 24 h).

    Args:
        symbol: PSX ticker without suffix.

    Returns:
        Dict with keys such as ``pe_ratio``, ``pb_ratio``, ``roe``,
        ``eps``, ``dividend_yield``, ``debt_to_equity``, ``revenue``,
        ``net_income``, ``book_value``, ``beta``, ``sector``, etc.
    """
    local = _local_symbol(symbol)
    cache_key = f"fundamentals:{local}"

    cached = get_cached(cache_key, CACHE_TTL_FUNDAMENTALS)
    if cached is not None:
        return cached

    # 1. Try to fetch from Firestore first
    from config import firebase_db
    if firebase_db:
        try:
            doc_ref = firebase_db.collection("companies").document(local).collection("financials")
            doc = doc_ref.document("annual").get()
            if not doc.exists:
                doc = doc_ref.document("quarter").get()
                
            if doc.exists:
                data = doc.to_dict() or {}
                highlights = _parse_firestore_financials_to_highlights(data, local)
                
                # Fetch quote for price-dependent metrics
                quote = get_quote(symbol) or {}
                price = quote.get("price", 0.0)
                
                eps = highlights.get("eps", 0.0)
                pe_ratio = (price / eps) if eps > 0 else None
                
                total_assets = highlights.get("total_assets", 0.0)
                total_liabilities = highlights.get("total_liabilities", 0.0)
                equity = total_assets - total_liabilities
                
                # Retrieve shares outstanding from AskAnalyst if possible
                shares_outstanding = None
                try:
                    ask_id = _get_askanalyst_id(symbol)
                    if ask_id:
                        r_ask = requests.get(f"https://api.askanalyst.com.pk/api/sharepricedatanew/{ask_id}", timeout=5)
                        if r_ask.status_code == 200:
                            quote_raw = r_ask.json()
                            shares_outstanding = float(quote_raw.get("shares") or 0.0)
                except:
                    pass
                
                book_value = None
                pb_ratio = None
                if shares_outstanding and shares_outstanding > 0:
                    book_value = equity / (shares_outstanding / 1000000.0) # assuming statement units are millions (PKR mn)
                    if book_value > 0:
                        pb_ratio = price / book_value
                
                roe = highlights.get("roe")
                if roe is None and equity > 0:
                    roe = (highlights.get("net_income", 0.0) / equity) * 100
                
                debt_equity = None
                if equity > 0:
                    debt_equity = (total_liabilities / equity) * 100
                
                fundamentals = {
                    "symbol":               local,
                    "name":                 local,
                    "sector":               "N/A",
                    "pe_ratio":             pe_ratio,
                    "pb_ratio":             pb_ratio,
                    "roe":                  roe,
                    "eps":                  eps,
                    "dividend_yield":       highlights.get("dividend_yield"),
                    "debt_to_equity":       debt_equity,
                    "revenue":              highlights.get("revenue"),
                    "net_income":           highlights.get("net_income"),
                    "book_value":           book_value,
                    "currency":             "PKR",
                }
                
                # Merge with AskAnalyst / local metadata for missing name, sector
                try:
                    from data.psx_tickers import PSX_TICKERS
                    if local in PSX_TICKERS:
                        fundamentals["name"] = PSX_TICKERS[local].get("name", local)
                        fundamentals["sector"] = PSX_TICKERS[local].get("sector", "N/A")
                except:
                    pass
                    
                set_cached(cache_key, fundamentals)
                return fundamentals
        except Exception as e:
            logger.warning("Error fetching fundamentals from Firestore for %s: %s", local, e)

    # 2. Fallback to AskAnalyst / Yahoo REST
    try:
        ask_id = _get_askanalyst_id(symbol)
        if ask_id:
            r_ask = requests.get(f"https://api.askanalyst.com.pk/api/sharepricedatanew/{ask_id}", timeout=8)
            if r_ask.status_code == 200:
                info = r_ask.json()
                from data.psx_tickers import PSX_TICKERS
                name = PSX_TICKERS.get(local, {}).get("name", local)
                sector = PSX_TICKERS.get(local, {}).get("sector", "N/A")
                
                pe_val = info.get("pe")
                pb_val = info.get("pbv")
                dy_val = info.get("dividend_yield")
                
                pe_ratio = float(pe_val) if pe_val and pe_val != "None" else None
                pb_ratio = float(pb_val) if pb_val and pb_val != "None" else None
                dividend_yield = float(dy_val) if dy_val and dy_val != "None" else None
                
                fundamentals = {
                    "symbol":               local,
                    "name":                 name,
                    "sector":               sector,
                    "industry":             "N/A",
                    "pe_ratio":             pe_ratio,
                    "pb_ratio":             pb_ratio,
                    "dividend_yield":       dividend_yield,
                    "eps":                  None,
                    "roe":                  None,
                    "book_value":           None,
                    "shares_outstanding":   float(info.get("shares") or 0.0),
                    "market_cap":           float(info.get("market_cap") or 0.0) * 1_000_000,
                    "currency":             "PKR",
                }
                
                quote = get_quote(symbol) or {}
                price = quote.get("price", 0.0)
                if price > 0:
                    if pe_ratio and pe_ratio > 0:
                        fundamentals["eps"] = price / pe_ratio
                    if pb_ratio and pb_ratio > 0:
                        fundamentals["book_value"] = price / pb_ratio
                
                set_cached(cache_key, fundamentals)
                return fundamentals
                
        # Try Yahoo REST API secondary fallback
        res = _fetch_yahoo_chart_rest(_yahoo_symbol(symbol), range_str="1d")
        if res and "chart" in res and res["chart"].get("result"):
            meta = res["chart"]["result"][0].get("meta", {})
            fundamentals = {
                "symbol":               local,
                "name":                 local,
                "sector":               "N/A",
                "industry":             "N/A",
                "pe_ratio":             None,
                "pb_ratio":             None,
                "dividend_yield":       None,
                "shares_outstanding":   meta.get("sharesOutstanding"),
                "market_cap":           meta.get("marketCap"),
                "currency":             meta.get("currency", "PKR"),
            }
            set_cached(cache_key, fundamentals)
            return fundamentals
            
        return {"symbol": local, "error": f"No fundamental data for {local} on AskAnalyst or Yahoo REST"}
    except Exception as exc:
        logger.error("Error fetching fundamentals for %s: %s", local, exc)
        return {"symbol": local, "error": str(exc)}


# ── Financial Statements ─────────────────────────────────────────────


def get_financial_statements(symbol: str) -> Dict[str, Any]:
    """
    Fetch structured financial statements for a PSX stock.

    Returns the three core statements (income, balance sheet, cash flow)
    as nested dicts.  Cached for ``CACHE_TTL_FUNDAMENTALS`` seconds.

    Args:
        symbol: PSX ticker without suffix.

    Returns:
        Dict with keys ``income_statement``, ``balance_sheet``,
        ``cash_flow``, each containing annual data serialised as
        dicts of ``{date_str: value}``.
    """
    local = _local_symbol(symbol)
    cache_key = f"financials:{local}"

    cached = get_cached(cache_key, CACHE_TTL_FUNDAMENTALS)
    if cached is not None:
        return cached

    # 1. Try to fetch from Firestore first
    from config import firebase_db
    if firebase_db:
        try:
            doc_ref = firebase_db.collection("companies").document(local).collection("financials")
            doc = doc_ref.document("annual").get()
            if not doc.exists:
                doc = doc_ref.document("quarter").get()
                
            if doc.exists:
                data = doc.to_dict() or {}
                highlights = _parse_firestore_financials_to_highlights(data, local)
                statements = {
                    "symbol":           local,
                    "income_statement": data.get("income_statement", []),
                    "balance_sheet":    data.get("balance_sheet", []),
                    "cash_flow":        data.get("cash_flow", []),
                    **highlights
                }
                set_cached(cache_key, statements)
                return statements
        except Exception as e:
            logger.warning("Error fetching financial statements from Firestore for %s: %s", local, e)

    # 2. Fallback to AskAnalyst
    try:
        import sys
        from pathlib import Path
        base_dir = Path(__file__).resolve().parent.parent
        if str(base_dir) not in sys.path:
            sys.path.insert(0, str(base_dir))
        from scrape_askanalyst import fetch_statement_data, fetch_cash_flow, parse_statement_json, parse_cash_flow_json
        
        ask_id = _get_askanalyst_id(symbol)
        if not ask_id:
            return {"symbol": local, "error": f"No AskAnalyst ID found for {local}"}
            
        is_raw = fetch_statement_data("iss", ask_id, "annual")
        bs_raw = fetch_statement_data("bss", ask_id, "annual")
        cf_raw = fetch_cash_flow(ask_id)
        
        def _parse_to_dict(raw_data, is_cf=False):
            if not raw_data:
                return {}
            df = parse_cash_flow_json(raw_data) if is_cf else parse_statement_json(raw_data)
            if df is None or df.empty:
                return {}
            res = {}
            date_cols = [c for c in df.columns if c not in ["Metric", "Unit"]]
            for col in date_cols:
                res[str(col)] = {}
                for _, row in df.iterrows():
                    val = row[col]
                    try:
                        if pd.notna(val):
                            res[str(col)][str(row["Metric"])] = float(val)
                    except:
                        pass
            return res
            
        statements = {
            "symbol":           local,
            "income_statement": _parse_to_dict(is_raw),
            "balance_sheet":    _parse_to_dict(bs_raw),
            "cash_flow":        _parse_to_dict(cf_raw, is_cf=True)
        }
        
        # Parse highlights
        highlights = _parse_firestore_financials_to_highlights(statements, local)
        statements.update(highlights)
        
        set_cached(cache_key, statements)
        return statements

    except Exception as exc:
        logger.error("Error fetching financial statements for %s: %s", local, exc)
        return {"symbol": local, "error": str(exc)}


# ── Ticker Search ────────────────────────────────────────────────────


def search_ticker(query: str, limit: int = 10) -> List[Dict[str, str]]:
    """
    Search for PSX tickers matching a partial symbol or company name.

    This is a thin wrapper around the local PSX ticker database; it
    does **not** hit the network.

    Args:
        query: Partial string to search for.
        limit: Maximum results.

    Returns:
        List of dicts with ``symbol``, ``name``, ``sector``.
    """
    return _search_local(query, limit=limit)


# ── Batch Convenience ────────────────────────────────────────────────


def get_multiple_quotes(symbols: List[str]) -> List[Dict[str, Any]]:
    """
    Fetch quotes for several symbols at once.

    Args:
        symbols: List of PSX tickers without suffix.

    Returns:
        List of quote dicts (same shape as :func:`get_quote`).
    """
    return [get_quote(s) for s in symbols]
