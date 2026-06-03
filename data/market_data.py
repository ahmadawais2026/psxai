"""
data/market_data.py
═══════════════════════════════════════════════════════════════════════
yfinance wrapper for Pakistan Stock Exchange (PSX) equities.

Every public function transparently:
  1.  Appends the ``.KA`` suffix required by Yahoo Finance.
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
import yfinance as yf

_session = requests.Session()

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


def _safe_get(info: dict, key: str, default: Any = None) -> Any:
    """Safely extract a key from yfinance info dict."""
    val = info.get(key, default)
    # yfinance sometimes returns 'None' as a string
    if val is None or val == "None":
        return default
    return val


def _get_clean_name(local: str, info: dict) -> str:
    """Get a clean, professional name for the company."""
    from data.psx_tickers import PSX_TICKERS
    if local in PSX_TICKERS:
        return PSX_TICKERS[local].get("name", local)
    
    yf_name = _safe_get(info, "longName", _safe_get(info, "shortName", local))
    if "," in yf_name and (".KA" in yf_name or local in yf_name):
        return yf_name.split(",")[0].replace(".KA", "").strip()
    return yf_name


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
        # 1. Fetch yfinance history for fallback and previous close (LDCP)
        try:
            ticker = yf.Ticker(_yahoo_symbol(symbol))
            info = ticker.info or {}
            hist = ticker.history(period="5d")
        except Exception as exc:
            logger.warning("yfinance fetch failed for %s: %s", local, exc)
            info = {}
            hist = pd.DataFrame()

        # 2. Try to fetch real-time quote from the official PSX Data Portal
        psx_quote = None
        url = f"https://dps.psx.com.pk/timeseries/int/{local}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            r = _session.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                res = r.json()
                data = res.get("data", [])
                if data:
                    price = float(data[0][1])
                    day_high = float(max(x[1] for x in data))
                    day_low = float(min(x[1] for x in data))
                    open_val = float(data[-1][1])
                    volume = int(sum(x[2] for x in data))
                    
                    # Determine previous close (LDCP)
                    if not hist.empty:
                        if len(hist) >= 2:
                            import datetime
                            today_str = datetime.date.today().strftime("%Y-%m-%d")
                            last_hist_date = hist.index[-1].strftime("%Y-%m-%d")
                            if last_hist_date == today_str:
                                prev_close = float(hist["Close"].iloc[-2])
                            else:
                                prev_close = float(hist["Close"].iloc[-1])
                        else:
                            prev_close = float(hist["Close"].iloc[-1])
                    else:
                        prev_close = float(_safe_get(info, "regularMarketPreviousClose", price))

                    if prev_close == 0.0:
                        prev_close = price

                    change = price - prev_close
                    change_percent = (change / prev_close) * 100 if prev_close != 0.0 else 0.0

                    psx_quote = {
                        "symbol":                local,
                        "name":                  _get_clean_name(local, info),
                        "price":                 price,
                        "change":               change,
                        "change_percent":       change_percent,
                        "volume":               volume,
                        "market_cap":           _safe_get(info, "marketCap", 0),
                        "day_high":             day_high,
                        "day_low":              day_low,
                        "open":                 open_val,
                        "previous_close":       prev_close,
                        "fifty_two_week_high":  _safe_get(info, "fiftyTwoWeekHigh", 0),
                        "fifty_two_week_low":   _safe_get(info, "fiftyTwoWeekLow", 0),
                        "currency":             _safe_get(info, "currency", "PKR"),
                    }
        except Exception as exc:
            logger.warning("PSX Portal fetch failed for %s: %s", local, exc)

        # 3. Fallback to yfinance history/info overlay if PSX Portal fetch failed or was empty
        if psx_quote is not None:
            quote = psx_quote
        else:
            if hist.empty and (not info or info.get("regularMarketPrice") is None):
                return {"symbol": local, "error": f"No data found for {local} on yfinance or PSX Portal"}

            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                open_val = float(hist["Open"].iloc[-1])
                high_val = float(hist["High"].iloc[-1])
                low_val = float(hist["Low"].iloc[-1])
                volume = int(hist["Volume"].iloc[-1])
                
                if len(hist) >= 2:
                    prev_close = float(hist["Close"].iloc[-2])
                else:
                    prev_close = float(_safe_get(info, "regularMarketPreviousClose", price))
                    
                change = price - prev_close
                change_percent = (change / prev_close) * 100 if prev_close != 0.0 else 0.0
            else:
                price = _safe_get(info, "regularMarketPrice", 0)
                open_val = _safe_get(info, "regularMarketOpen", 0)
                high_val = _safe_get(info, "regularMarketDayHigh", 0)
                low_val = _safe_get(info, "regularMarketDayLow", 0)
                volume = _safe_get(info, "regularMarketVolume", 0)
                prev_close = _safe_get(info, "regularMarketPreviousClose", price)
                change = _safe_get(info, "regularMarketChange", 0)
                change_percent = _safe_get(info, "regularMarketChangePercent", 0)

            quote = {
                "symbol":                local,
                "name":                  _get_clean_name(local, info),
                "price":                 price,
                "change":               change,
                "change_percent":       change_percent,
                "volume":               volume,
                "market_cap":           _safe_get(info, "marketCap", 0),
                "day_high":             high_val,
                "day_low":              low_val,
                "open":                 open_val,
                "previous_close":       prev_close,
                "fifty_two_week_high":  _safe_get(info, "fiftyTwoWeekHigh", 0),
                "fifty_two_week_low":   _safe_get(info, "fiftyTwoWeekLow", 0),
                "currency":             _safe_get(info, "currency", "PKR"),
            }

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
            return df
        except Exception:
            pass  # fall through to fresh fetch

    try:
        ticker = yf.Ticker(_yahoo_symbol(symbol))
        df: pd.DataFrame = ticker.history(period=period, interval=interval)

        if df.empty:
            logger.warning("No history data for %s (period=%s, interval=%s)", local, period, interval)
            return pd.DataFrame()

        # Normalise column names (yfinance can vary)
        df = df.rename(columns={
            "Stock Splits": "Stock_Splits",
            "Capital Gains": "Capital_Gains",
        })

        # Keep only OHLCV columns
        keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[keep_cols]

        # Cache as serialisable records
        cache_data = df.reset_index().to_dict(orient="records")
        set_cached(cache_key, cache_data)

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

    try:
        ticker = yf.Ticker(_yahoo_symbol(symbol))
        info = ticker.info or {}

        if not info:
            return {"symbol": local, "error": f"No fundamental data for {local}"}

        fundamentals: Dict[str, Any] = {
            "symbol":               local,
            "name":                 _safe_get(info, "longName", _safe_get(info, "shortName", local)),
            "sector":               _safe_get(info, "sector", "N/A"),
            "industry":             _safe_get(info, "industry", "N/A"),
            # Valuation
            "pe_ratio":             _safe_get(info, "trailingPE"),
            "forward_pe":           _safe_get(info, "forwardPE"),
            "pb_ratio":             _safe_get(info, "priceToBook"),
            "ps_ratio":             _safe_get(info, "priceToSalesTrailing12Months"),
            "peg_ratio":            _safe_get(info, "pegRatio"),
            "enterprise_value":     _safe_get(info, "enterpriseValue"),
            "ev_to_ebitda":         _safe_get(info, "enterpriseToEbitda"),
            # Profitability
            "eps":                  _safe_get(info, "trailingEps"),
            "forward_eps":          _safe_get(info, "forwardEps"),
            "roe":                  _safe_get(info, "returnOnEquity"),
            "roa":                  _safe_get(info, "returnOnAssets"),
            "profit_margin":        _safe_get(info, "profitMargins"),
            "operating_margin":     _safe_get(info, "operatingMargins"),
            # Dividend
            "dividend_yield":       _safe_get(info, "dividendYield"),
            "dividend_rate":        _safe_get(info, "dividendRate"),
            "payout_ratio":         _safe_get(info, "payoutRatio"),
            # Balance Sheet
            "debt_to_equity":       _safe_get(info, "debtToEquity"),
            "current_ratio":        _safe_get(info, "currentRatio"),
            "book_value":           _safe_get(info, "bookValue"),
            "total_debt":           _safe_get(info, "totalDebt"),
            "total_cash":           _safe_get(info, "totalCash"),
            # Income
            "revenue":              _safe_get(info, "totalRevenue"),
            "revenue_growth":       _safe_get(info, "revenueGrowth"),
            "earnings_growth":      _safe_get(info, "earningsGrowth"),
            "net_income":           _safe_get(info, "netIncomeToCommon"),
            "ebitda":               _safe_get(info, "ebitda"),
            "free_cash_flow":       _safe_get(info, "freeCashflow"),
            # Risk
            "beta":                 _safe_get(info, "beta"),
            # Shares
            "shares_outstanding":   _safe_get(info, "sharesOutstanding"),
            "float_shares":         _safe_get(info, "floatShares"),
            "market_cap":           _safe_get(info, "marketCap"),
            "currency":             _safe_get(info, "currency", "PKR"),
        }

        set_cached(cache_key, fundamentals)
        return fundamentals

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

    def _df_to_dict(df: pd.DataFrame) -> Dict[str, Any]:
        """Convert a yfinance statement DataFrame to a JSON-safe dict."""
        if df is None or df.empty:
            return {}
        # Columns are dates, rows are line items
        result: Dict[str, Any] = {}
        for col in df.columns:
            col_label = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
            result[col_label] = {}
            for idx in df.index:
                val = df.at[idx, col]
                # Convert numpy types to native Python
                if pd.notna(val):
                    result[col_label][str(idx)] = float(val)
        return result

    try:
        ticker = yf.Ticker(_yahoo_symbol(symbol))

        statements: Dict[str, Any] = {
            "symbol":           local,
            "income_statement": _df_to_dict(ticker.income_stmt),
            "balance_sheet":    _df_to_dict(ticker.balance_sheet),
            "cash_flow":        _df_to_dict(ticker.cashflow),
        }

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
