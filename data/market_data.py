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
    """Parse a Firestore financial statement column header into a comparable datetime.

    Recognises:
      - Annual columns:  '%b-%y' (Jun-25), '%Y' (2025)
      - Interim columns: '9MFY26', '6MFY26', '3MFY26'
                         '1QFY26', '2QFY26', '3QFY26', '4QFY26'
                         '1HFY26', '2HFY26'

    Returns a datetime for sorting/comparison, or None if unrecognised.
    Interim labels are mapped to approximate calendar dates so they sort
    correctly relative to annual columns.
    PSX fiscal year convention: FY26 ends June 2026.
    """
    import datetime
    import re
    k = key.strip()

    # Try %b-%y (e.g. 'Jun-25', 'Dec-24')
    try:
        return datetime.datetime.strptime(k, "%b-%y")
    except ValueError:
        pass
    # Try %Y (e.g. '2025')
    try:
        return datetime.datetime.strptime(k, "%Y")
    except ValueError:
        pass

    # Interim labels: nMFYyy, nQFYyy, nHFYyy
    # PSX FY ends in June: FY26 = Jul 2025 – Jun 2026
    # Map each label to the approximate end-month of its period.
    m = re.match(r'^(\d{1,2})(M|Q|H)[-\s]?FY(\d{2})$', k, re.IGNORECASE)
    if m:
        n, period_type, yy = int(m.group(1)), m.group(2).upper(), int(m.group(3))
        fy_end_year = 2000 + yy  # e.g. 26 -> 2026
        fy_start_year = fy_end_year - 1  # FY26 starts Jul 2025
        if period_type == 'M':
            # nM = n months from FY start (July)
            month_offset = n  # 9M -> 9 months from Jul = Mar
        elif period_type == 'Q':
            # 1Q=Sep, 2Q=Dec, 3Q=Mar, 4Q=Jun
            month_offset = n * 3
        else:  # H
            month_offset = n * 6
        # Calendar month = July + offset - 1 (July is month 7)
        cal_month = 7 + month_offset - 1
        cal_year = fy_start_year + (cal_month - 1) // 12
        cal_month = (cal_month - 1) % 12 + 1
        try:
            return datetime.datetime(cal_year, cal_month, 28)
        except ValueError:
            return datetime.datetime(cal_year, cal_month, 1)

    return None


def _parse_period_type(key: str) -> str:
    """Return 'annual', 'interim', or 'unknown' for a period key.

    Heuristic:
      - ``%Y`` (2025) → annual.
      - ``%b-%y`` (Jun-25, Dec-24) → interim (these are quarterly columns in Firestore).
      - nMFYxx, nQFYxx, nHFYxx → interim.
      - Everything else → unknown.
    """
    import re
    k = key.strip()
    import datetime
    # Annual: %Y only (e.g. '2025')
    try:
        datetime.datetime.strptime(k, "%Y")
        return 'annual'
    except ValueError:
        pass
    # Interim: %b-%y (e.g. 'Jun-25' — quarterly Firestore columns)
    try:
        datetime.datetime.strptime(k, "%b-%y")
        return 'interim'
    except ValueError:
        pass
    if re.match(r'^\d{1,2}M[-\s]?FY\d{2}$', k, re.IGNORECASE):
        return 'interim'
    if re.match(r'^\dQ[-\s]?FY\d{2}$', k, re.IGNORECASE):
        return 'interim'
    if re.match(r'^\dH[-\s]?FY\d{2}$', k, re.IGNORECASE):
        return 'interim'
    return 'unknown'


# Metadata columns to skip when scanning for period keys
_FS_META_KEYS = frozenset(["Metric", "Unit", "symbol", "period", "last_updated"])


def _collect_period_keys(rows: list) -> list:
    """Extract (datetime, key_str) pairs from the first row of a statement."""
    if not rows:
        return []
    sample = rows[0]
    result = []
    for k in sample.keys():
        if k in _FS_META_KEYS:
            continue
        dt = _parse_period_key(k)
        if dt:
            result.append((dt, k))
    return result


def _parse_firestore_financials_to_highlights(
    data: Dict[str, Any],
    symbol: str,
    period_label_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract single-period highlights from one Firestore financial doc.

    Args:
        data: Raw Firestore doc dict (income_statement / balance_sheet / cash_flow lists).
        symbol: Ticker (for logging).
        period_label_override: If provided, use this as ``period_label`` in the
            result (e.g. 'TTM') rather than the raw column key.

    Returns:
        Dict of financial highlights including ``latest_period`` (raw column key)
        and ``period_label`` (display label — same as latest_period unless overridden).
    """
    import numpy as np

    result = {}

    # 1. Determine the latest period key (e.g., '2025', 'Sep-25', '9MFY26')
    period_keys = _collect_period_keys(data.get("income_statement", []))
    if not period_keys:
        period_keys = _collect_period_keys(data.get("balance_sheet", []))
    if not period_keys:
        return {}

    latest_dt, latest_period = max(period_keys)
    result["latest_period"] = latest_period
    result["period_label"] = period_label_override if period_label_override else latest_period

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

    # Paid-up (share) capital — enables a deterministic share count
    # (shares_mn = paid_up_capital / 10 at PKR 10 par value) when the live
    # AskAnalyst shares endpoint is unavailable. Same convention as
    # calculate_cashflows.py.
    paid_up_capital = get_metric_value("balance_sheet", ["equity - paid-up capital", "paid-up capital", "paid up capital", "share capital"])

    operating_cash_flow = get_metric_value("cash_flow", ["operating cash flow", "net cash generated from operating activities", "cash flow from operating activities"])

    # Free cash flow for the DCF. The engine is a 2-stage FCFE model, and the
    # AskAnalyst cash-flow statement carries an explicit FCFE line — prefer it.
    # Fall back to FCFF, an explicit "free cash flow" line, or a derivation:
    # FCFE = Operating Cash Flow + CAPEX + Net Borrowings (CAPEX is stored as a
    # negative outflow, so it is added). Verified to reproduce the FCFE line
    # exactly (e.g. ABOT 9978 - 3273 + 435 = 7140).
    free_cash_flow = get_metric_value("cash_flow", ["fcfe"])
    if not free_cash_flow:
        free_cash_flow = get_metric_value("cash_flow", ["fcff"])
    if not free_cash_flow:
        free_cash_flow = get_metric_value("cash_flow", ["free cash flow"])
    if not free_cash_flow and operating_cash_flow:
        capex = get_metric_value("cash_flow", ["capex", "capital expenditure", "purchase of property", "additions to fixed assets", "purchase of fixed assets"])
        net_borrowings = get_metric_value("cash_flow", ["net borrowings", "net borrowing"])
        free_cash_flow = operating_cash_flow + (capex or 0.0) + (net_borrowings or 0.0)

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
        "dividend_yield": dividend_yield,
        "paid_up_capital": paid_up_capital,
    })

    return result


def _compute_ttm_highlights(
    annual_data: Dict[str, Any],
    quarter_data: Dict[str, Any],
    symbol: str,
) -> Dict[str, Any]:
    """Compute true trailing-twelve-month financial highlights from annual + interim docs.

    Strategy: TTM = Latest Annual + Current YTD – Prior-Year YTD
    (standard TTM stitching formula). This requires the quarter doc to have
    at least the current YTD period AND the corresponding prior-year YTD.

    For balance-sheet (stock) metrics, always uses the most recent column
    from whichever doc is newer.

    Falls back to honest labeling when stitching is not possible:
      - If only current YTD exists but no prior-year YTD: label as the
        latest interim period (e.g. '9MFY26').
      - If no quarter doc: label as the latest annual period (e.g. 'FY25').

    Returns:
        Highlights dict with 'period_label' set to 'TTM' (when truly computed)
        or the actual period (when falling back), and 'latest_interim_headline'
        with the most recent available result.
    """
    import numpy as np
    import re

    def clean_val(val):
        if val is None:
            return 0.0
        try:
            if isinstance(val, float) and np.isnan(val):
                return 0.0
            return float(val)
        except:
            return 0.0

    def get_col(rows, synonyms, col_key):
        """Get a metric value from a specific column of statement rows."""
        for row in rows:
            name = row.get("Metric", "").strip()
            if any(syn.lower() in name.lower() for syn in synonyms):
                return clean_val(row.get(col_key))
        return 0.0

    # ── 1. Parse period keys from both docs ───────────────────────────────────
    annual_periods = _collect_period_keys(annual_data.get("income_statement", []))
    if not annual_periods:
        annual_periods = _collect_period_keys(annual_data.get("balance_sheet", []))

    quarter_periods = _collect_period_keys(quarter_data.get("income_statement", []))
    if not quarter_periods:
        quarter_periods = _collect_period_keys(quarter_data.get("balance_sheet", []))

    # Separate annual from interim keys in the quarter doc
    q_interim = [(dt, k) for dt, k in quarter_periods if _parse_period_type(k) == 'interim']
    q_annual  = [(dt, k) for dt, k in quarter_periods if _parse_period_type(k) == 'annual']

    # Best annual column: prefer the annual doc, fall back to any annual col in quarter doc
    if annual_periods:
        _, latest_annual_col = max(annual_periods)
        ann_rows_is = annual_data.get("income_statement", [])
        ann_rows_cf = annual_data.get("cash_flow", [])
    elif q_annual:
        _, latest_annual_col = max(q_annual)
        ann_rows_is = quarter_data.get("income_statement", [])
        ann_rows_cf = quarter_data.get("cash_flow", [])
    else:
        latest_annual_col = None
        ann_rows_is = []
        ann_rows_cf = []

    # Latest interim column in the quarter doc
    if q_interim:
        _, latest_interim_col = max(q_interim)
        q_rows_is = quarter_data.get("income_statement", [])
    else:
        latest_interim_col = None
        q_rows_is = []

    # ── 2. Find the prior-year YTD column (same period label, previous FY) ───
    # e.g. for '9MFY26' look for '9MFY25'
    prior_interim_col = None
    if latest_interim_col:
        m = re.match(r'^(\d{1,2}(?:M|Q|H))[-\s]?FY(\d{2})$', latest_interim_col, re.IGNORECASE)
        if m:
            prefix, yy = m.group(1), int(m.group(2))
            prior_label = f"{prefix.upper()}FY{(yy - 1):02d}"
            # Check both docs for the prior-year label
            all_q_keys = {k for _, k in quarter_periods}
            all_a_keys = {k for _, k in annual_periods}
            if prior_label in all_q_keys:
                prior_interim_col = prior_label
                prior_rows_is = quarter_data.get("income_statement", [])
            elif prior_label in all_a_keys:
                prior_interim_col = prior_label
                prior_rows_is = annual_data.get("income_statement", [])
            else:
                prior_rows_is = []

    # ── 3. IS synonyms ────────────────────────────────────────────────────────
    REV_SYNS  = ["total revenue", "revenue", "net sales", "markup/interest revenue", "mark-up/interest revenue"]
    NI_SYNS   = ["profit after tax", "net income", "net profit", "profit for the period"]
    OP_SYNS   = ["operating profit", "operating profit/ (loss)", "net mark-up/interest income", "net markup/interest income"]
    EPS_SYNS  = ["eps - basic", "eps", "earnings per share"]
    BS_ASSET  = ["total asset - total assets", "total assets", "total asset"]
    BS_LIAB   = ["total liabilities - total liabilities", "total liabilities", "total liability"]
    BS_CASH   = ["cash & bank balances", "cash and balances with treasury banks", "cash and cash equivalents"]
    BS_PUC    = ["equity - paid-up capital", "paid-up capital", "paid up capital", "share capital"]
    CF_OCF    = ["operating cash flow", "net cash generated from operating activities", "cash flow from operating activities"]
    CF_FCFE   = ["fcfe"]
    CF_FCFF   = ["fcff"]
    CF_FCF    = ["free cash flow"]
    CF_CAPEX  = ["capex", "capital expenditure", "purchase of property", "additions to fixed assets", "purchase of fixed assets"]
    CF_BORROW = ["net borrowings", "net borrowing"]
    ROE_SYNS  = ["return on equity", "roe"]
    DIV_SYNS  = ["dividend yield"]

    # ── 4. Attempt TTM stitching ──────────────────────────────────────────────
    can_stitch = (
        latest_annual_col is not None
        and latest_interim_col is not None
        and prior_interim_col is not None
    )

    if can_stitch:
        # TTM flow = Annual + Current YTD − Prior-Year YTD
        def ttm(annual_rows, q_rows, prior_rows, syns):
            ann = get_col(annual_rows, syns, latest_annual_col)
            cur = get_col(q_rows, syns, latest_interim_col)
            pri = get_col(prior_rows, syns, prior_interim_col)
            return ann + cur - pri

        revenue         = ttm(ann_rows_is, q_rows_is, prior_rows_is, REV_SYNS)
        net_income      = ttm(ann_rows_is, q_rows_is, prior_rows_is, NI_SYNS)
        operating_profit = ttm(ann_rows_is, q_rows_is, prior_rows_is, OP_SYNS)
        eps             = ttm(ann_rows_is, q_rows_is, prior_rows_is, EPS_SYNS)
        period_label    = "TTM"
        logger.info(
            "TTM stitched for %s: annual=%s + interim=%s − prior=%s",
            symbol, latest_annual_col, latest_interim_col, prior_interim_col,
        )
    elif latest_interim_col is not None:
        # Only current YTD available — use it, label honestly
        revenue          = get_col(q_rows_is, REV_SYNS, latest_interim_col)
        net_income       = get_col(q_rows_is, NI_SYNS, latest_interim_col)
        operating_profit = get_col(q_rows_is, OP_SYNS, latest_interim_col)
        eps              = get_col(q_rows_is, EPS_SYNS, latest_interim_col)
        period_label     = latest_interim_col
        logger.info(
            "TTM fallback for %s: using latest interim %s (no prior-year YTD for stitching)",
            symbol, latest_interim_col,
        )
    elif latest_annual_col is not None:
        # No interim data at all — use annual, label honestly
        revenue          = get_col(ann_rows_is, REV_SYNS, latest_annual_col)
        net_income       = get_col(ann_rows_is, NI_SYNS, latest_annual_col)
        operating_profit = get_col(ann_rows_is, OP_SYNS, latest_annual_col)
        eps              = get_col(ann_rows_is, EPS_SYNS, latest_annual_col)
        period_label     = latest_annual_col
        logger.info(
            "TTM fallback for %s: using latest annual %s (no interim data)",
            symbol, latest_annual_col,
        )
    else:
        logger.warning("No usable financial data found for %s", symbol)
        return {}

    # ── 5. Balance-sheet metrics: always use the most recent column ───────────
    # Determine which doc has the more recent balance-sheet reading
    all_bs_periods = []
    for dt, k in _collect_period_keys(annual_data.get("balance_sheet", [])):
        all_bs_periods.append((dt, k, annual_data))
    for dt, k in _collect_period_keys(quarter_data.get("balance_sheet", [])):
        all_bs_periods.append((dt, k, quarter_data))

    if all_bs_periods:
        bs_dt, bs_col, bs_src = max(all_bs_periods, key=lambda x: x[0])
        bs_rows = bs_src.get("balance_sheet", [])
        total_assets      = get_col(bs_rows, BS_ASSET, bs_col)
        total_liabilities = get_col(bs_rows, BS_LIAB, bs_col)
        cash              = get_col(bs_rows, BS_CASH, bs_col)
        paid_up_capital   = get_col(bs_rows, BS_PUC,  bs_col)
    else:
        total_assets = total_liabilities = cash = paid_up_capital = 0.0

    # ── 6. Cash flow: prefer annual (most complete); fall back to quarter ─────
    # AskAnalyst only serves cash flow at annual cadence, so quarter CF is often empty.
    cf_rows = annual_data.get("cash_flow", []) or quarter_data.get("cash_flow", [])
    cf_col  = latest_annual_col or latest_interim_col
    operating_cash_flow = 0.0
    free_cash_flow = 0.0
    if cf_rows and cf_col:
        operating_cash_flow = get_col(cf_rows, CF_OCF, cf_col)
        free_cash_flow = get_col(cf_rows, CF_FCFE, cf_col)
        if not free_cash_flow:
            free_cash_flow = get_col(cf_rows, CF_FCFF, cf_col)
        if not free_cash_flow:
            free_cash_flow = get_col(cf_rows, CF_FCF, cf_col)
        if not free_cash_flow and operating_cash_flow:
            capex = get_col(cf_rows, CF_CAPEX, cf_col)
            net_borrowings = get_col(cf_rows, CF_BORROW, cf_col)
            free_cash_flow = operating_cash_flow + (capex or 0.0) + (net_borrowings or 0.0)

    # ── 7. Derived ratios ─────────────────────────────────────────────────────
    operating_margin = (operating_profit / revenue * 100) if revenue > 0 else 0.0
    net_margin       = (net_income       / revenue * 100) if revenue > 0 else 0.0

    roe          = None  # computed upstream in get_fundamentals from equity
    dividend_yield = None  # quarterly docs often lack this

    # Latest interim headline for the report (always surface most recent result)
    if latest_interim_col and q_rows_is:
        interim_ni = get_col(q_rows_is, NI_SYNS, latest_interim_col)
        latest_interim_headline = (
            f"{latest_interim_col} Net Income: {interim_ni:+,.0f} mn PKR"
        )
    elif latest_annual_col and ann_rows_is:
        ann_ni = get_col(ann_rows_is, NI_SYNS, latest_annual_col)
        latest_interim_headline = (
            f"{latest_annual_col} Net Income: {ann_ni:+,.0f} mn PKR"
        )
    else:
        latest_interim_headline = ""

    return {
        "period_label":            period_label,
        "latest_period":           latest_interim_col or latest_annual_col,
        "latest_interim_headline": latest_interim_headline,
        "revenue":                 revenue,
        "net_income":              net_income,
        "operating_margin":        operating_margin,
        "net_margin":              net_margin,
        "total_assets":            total_assets,
        "total_liabilities":       total_liabilities,
        "cash":                    cash,
        "operating_cash_flow":     operating_cash_flow,
        "free_cash_flow":          free_cash_flow,
        "eps":                     eps,
        "roe":                     roe,
        "dividend_yield":          dividend_yield,
        "paid_up_capital":         paid_up_capital,
    }


# ── DCF Input Helpers (beta & historical growth) ──────────────────────


def get_beta(symbol: str) -> float:
    """
    Compute the equity beta of *symbol* against the KSE-100 index from
    daily price history (covariance of returns / market variance).

    Mirrors the Risk Analyst's calculation but lives in the data layer so
    the Fundamentals Analyst's DCF can obtain a real beta without depending
    on agent execution order (both agents run in parallel).

    Cached for ``CACHE_TTL_FUNDAMENTALS``. Returns 1.0 when history is
    insufficient or unavailable.
    """
    local = _local_symbol(symbol)
    cache_key = f"beta:{local}"
    cached = get_cached(cache_key, CACHE_TTL_FUNDAMENTALS)
    if cached is not None:
        return cached

    beta = 1.0
    try:
        import numpy as np

        stock_df = get_history(symbol, HISTORY_PERIOD_DAILY, "1d")
        if stock_df is None or stock_df.empty:
            return beta

        index_df = None
        for idx_sym in ("KSE100", "^KSE"):
            result = get_history(idx_sym, HISTORY_PERIOD_DAILY, "1d")
            if result is not None and not result.empty:
                index_df = result
                break

        if index_df is not None and not index_df.empty:
            combined = pd.DataFrame(
                {"stock": stock_df["Close"], "index": index_df["Close"]}
            ).dropna().pct_change().dropna()
            if len(combined) > 10:
                cov = np.cov(combined["stock"], combined["index"])
                market_var = cov[1, 1]
                if market_var > 0:
                    beta = float(cov[0, 1] / market_var)
    except Exception as e:
        logger.warning("Beta computation failed for %s: %s", local, e)
        return 1.0

    set_cached(cache_key, beta)
    return beta


def _metric_series(statement: Any, synonyms: List[str]) -> List[tuple]:
    """Return ``[(period_datetime, value), ...]`` sorted ascending for the first
    metric matching *synonyms*. Handles both statement shapes returned by
    ``get_financial_statements``: the Firestore list-of-rows format and the
    AskAnalyst ``{period: {metric: value}}`` dict format."""
    import numpy as np

    series: List[tuple] = []
    skip_keys = {"Metric", "Unit", "symbol", "period", "last_updated"}

    if isinstance(statement, list):
        target = None
        for row in statement:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Metric", "")).strip().lower()
            if any(syn in name for syn in synonyms):
                target = row
                break
        if target is None:
            return []
        for k, v in target.items():
            if k in skip_keys:
                continue
            dt = _parse_period_key(k)
            if dt is None:
                continue
            try:
                val = float(v)
                if np.isnan(val):
                    continue
            except (TypeError, ValueError):
                continue
            series.append((dt, val))

    elif isinstance(statement, dict):
        for period, metrics in statement.items():
            if not isinstance(metrics, dict):
                continue
            dt = _parse_period_key(period)
            if dt is None:
                continue
            for metric_name, v in metrics.items():
                if any(syn in str(metric_name).strip().lower() for syn in synonyms):
                    try:
                        val = float(v)
                        if np.isnan(val):
                            continue
                    except (TypeError, ValueError):
                        continue
                    series.append((dt, val))
                    break

    series.sort(key=lambda x: x[0])
    return series


def compute_historical_growth(
    financials: Dict[str, Any],
    default: float = 0.08,
    cap: float = 0.30,
    floor: float = -0.10,
) -> float:
    """
    Estimate a normalized historical growth rate (decimal) from filed
    financial statements, for use as the Stage-1 DCF growth assumption.

    Tries Free Cash Flow → Operating Cash Flow → Net Income → Revenue, using
    the first metric with at least two clean, positive-endpoint periods, and
    computes a CAGR across them. The result is clamped to ``[floor, cap]`` to
    keep the DCF guardrails intact. Falls back to *default* when no usable
    series exists.
    """
    if not isinstance(financials, dict):
        return default

    candidates = [
        ("cash_flow", ["free cash flow"]),
        ("cash_flow", ["operating cash flow", "net cash generated from operating", "cash flow from operating"]),
        ("income_statement", ["profit after tax", "net income", "net profit", "profit for the period"]),
        ("income_statement", ["total revenue", "net sales", "markup/interest revenue", "mark-up/interest revenue"]),
    ]

    for stmt_name, synonyms in candidates:
        synonyms = [s.lower() for s in synonyms]
        series = _metric_series(financials.get(stmt_name, []), synonyms)
        if len(series) < 2:
            continue
        first_val = series[0][1]
        last_val = series[-1][1]
        if first_val <= 0 or last_val <= 0:
            continue
        # Annualize over the actual elapsed time, not the number of data points
        # (periods can be unevenly spaced or quarterly), then clamp.
        years = (series[-1][0] - series[0][0]).days / 365.25
        if years < 0.5:
            continue
        cagr = (last_val / first_val) ** (1.0 / years) - 1.0
        return max(floor, min(cap, cagr))

    return default


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
        if interval == "1h":
            try:
                from config import firebase_db
                if firebase_db:
                    doc_ref = firebase_db.collection("companies").document(local).collection("market").document("history_hourly")
                    doc = doc_ref.get()
                    if doc.exists:
                        bars = (doc.to_dict() or {}).get("bars", [])
                        if bars:
                            records = []
                            for b in bars:
                                records.append({
                                    "Date": pd.to_datetime(b["date"]),
                                    "Open": float(b.get("open") or 0.0),
                                    "High": float(b.get("high") or 0.0),
                                    "Low": float(b.get("low") or 0.0),
                                    "Close": float(b.get("close") or 0.0),
                                    "Volume": float(b.get("volume") or 0.0)
                                })
                            df = pd.DataFrame(records)
                            df.set_index("Date", inplace=True)
                            df.sort_index(inplace=True)
                            
                            df_reset = df.reset_index()
                            if "Date" in df_reset.columns:
                                df_reset["Date"] = df_reset["Date"].dt.strftime("%Y-%m-%d %H:%M:%S")
                            cache_data = df_reset.to_dict(orient="records")
                            set_cached(cache_key, cache_data)
                            
                            return df
            except Exception as e:
                logger.warning("Failed to fetch hourly history from Firestore for %s: %s", local, e)
            return pd.DataFrame()

        elif interval == "1d":
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

        # Fallback 1: Yahoo REST API
        res = _fetch_yahoo_chart_rest(_yahoo_symbol(symbol), range_str=period, interval_str=interval)
        if not res or not res.get("chart", {}).get("result"):
            # Fallback 2: AskAnalyst chart endpoint
            range_map = {"1mo": "1M", "3mo": "3M", "6mo": "6M", "1y": "1Y", "2y": "3Y", "5y": "5Y"}
            ask_range = range_map.get(period, "1Y")
            df_ask = get_askanalyst_history(local, range_str=ask_range, interval="1D")
            if not df_ask.empty:
                logger.info("Got %d bars from AskAnalyst chart for %s", len(df_ask), local)
                df_reset = df_ask.reset_index()
                if "Date" in df_reset.columns:
                    df_reset["Date"] = df_reset["Date"].dt.strftime("%Y-%m-%d %H:%M:%S")
                set_cached(cache_key, df_reset.to_dict(orient="records"))
                return df_ask
            logger.warning("No history data for %s (period=%s, interval=%s) from PSX DPS, Yahoo, or AskAnalyst", local, period, interval)
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


def _roe_to_fraction(roe: Any) -> Any:
    """Normalize an ROE value to a *fraction* (0.15 == 15%).

    Sources are inconsistent: AskAnalyst /api/ratios and the financial
    statements sometimes report ROE as a percent (e.g. 15.3 or -2.63) and
    sometimes as a fraction (0.153). Downstream formatting (`_pct` in the PDF
    generator, and the analyst data-blob) expects a fraction. A genuine ROE
    fraction is essentially always within [-1.5, 1.5]; anything larger in
    magnitude is a percent and is scaled down.
    """
    if roe is None:
        return None
    try:
        v = float(roe)
    except (TypeError, ValueError):
        return None
    return v / 100.0 if abs(v) > 1.5 else v


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
            # Read BOTH annual and quarter docs — never prefer one as a simple fallback.
            # The TTM stitcher merges them to produce accurate trailing metrics.
            annual_doc  = doc_ref.document("annual").get()
            quarter_doc = doc_ref.document("quarter").get()
            annual_data  = annual_doc.to_dict()  or {} if annual_doc.exists  else {}
            quarter_data = quarter_doc.to_dict() or {} if quarter_doc.exists else {}

            if annual_data or quarter_data:
                highlights = _compute_ttm_highlights(annual_data, quarter_data, local)
                
                # Fetch quote for price-dependent metrics
                quote = get_quote(symbol) or {}
                price = quote.get("price", 0.0)
                
                eps = highlights.get("eps", 0.0)
                pe_ratio = (price / eps) if eps > 0 else None
                
                total_assets = highlights.get("total_assets", 0.0)
                total_liabilities = highlights.get("total_liabilities", 0.0)
                equity = total_assets - total_liabilities
                
                # Retrieve shares outstanding (in MILLIONS) from AskAnalyst if
                # possible — a single short attempt, since the AskAnalyst host is
                # frequently unreachable and a 3×15s retry loop just hangs the
                # whole analysis (~45s) before falling through anyway.
                shares_outstanding = None
                ask_id = _get_askanalyst_id(symbol)
                if ask_id:
                    try:
                        r_ask = requests.get(f"https://api.askanalyst.com.pk/api/sharepricedatanew/{ask_id}", timeout=8)
                        if r_ask.status_code == 200:
                            quote_raw = r_ask.json()
                            shares_outstanding = float(quote_raw.get("shares") or 0.0) or None
                    except Exception as e:
                        logger.warning("AskAnalyst shares fetch failed for %s: %s", local, e)

                # Deterministic fallback: derive shares (millions) from paid-up
                # capital already in the filed balance sheet, at PKR 10 par value
                # (shares_mn = paid_up_capital_mn / 10). This keeps the DCF alive
                # without depending on the flaky live shares endpoint.
                if not shares_outstanding or shares_outstanding <= 0:
                    paid_up = highlights.get("paid_up_capital") or 0.0
                    if paid_up > 0:
                        shares_outstanding = paid_up / 10.0
                        logger.info(
                            "Derived shares for %s from paid-up capital: %.2f mn",
                            local, shares_outstanding,
                        )

                # Both equity (statement) and shares_outstanding are in MILLIONS,
                # so book value per share = equity_mn / shares_mn (PKR/share).
                book_value = None
                pb_ratio = None
                if shares_outstanding and shares_outstanding > 0:
                    book_value = equity / shares_outstanding
                    if book_value > 0:
                        pb_ratio = price / book_value

                # Market cap (absolute PKR) = price × shares (mn) × 1e6 — same
                # units as the AskAnalyst fallback branch below. Populating it
                # here also lets the agent's mcap/price share fallback work.
                market_cap = None
                if price and shares_outstanding and shares_outstanding > 0:
                    market_cap = price * shares_outstanding * 1_000_000.0

                # ROE as a FRACTION (0.15 == 15%); _pct() and the analyst
                # data-blob format it to a percent at the edge.
                roe = highlights.get("roe")
                if roe is None and equity > 0:
                    roe = highlights.get("net_income", 0.0) / equity
                roe = _roe_to_fraction(roe)

                # Debt/Equity as a plain MULTIPLE (2.74 == 2.74x), not a percent.
                debt_equity = None
                if equity > 0:
                    debt_equity = total_liabilities / equity
                
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
                    "shares_outstanding":   shares_outstanding,  # in MILLIONS (matches FCFE units for the DCF)
                    "market_cap":           market_cap,
                    "free_cash_flow":       highlights.get("free_cash_flow"),
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
            info = None
            for _ in range(3):
                try:
                    r_ask = requests.get(f"https://api.askanalyst.com.pk/api/sharepricedatanew/{ask_id}", timeout=15)
                    if r_ask.status_code == 200:
                        info = r_ask.json()
                        break
                except:
                    import time
                    time.sleep(1)
            
            if info:
                from data.psx_tickers import PSX_TICKERS
                name = PSX_TICKERS.get(local, {}).get("name", local)
                sector = PSX_TICKERS.get(local, {}).get("sector", "N/A")
                
                pe_val = info.get("pe")
                pb_val = info.get("pbv")
                dy_val = info.get("dividend_yield")
                
                pe_ratio = float(pe_val) if pe_val and pe_val != "None" else None
                pb_ratio = float(pb_val) if pb_val and pb_val != "None" else None
                dividend_yield = float(dy_val) if dy_val and dy_val != "None" else None
                
                # Enrich with /api/ratios and /api/equity-profile
                ratios = get_valuation_ratios(local)
                equity_prof = get_equity_profile(local)
                consensus = get_analyst_consensus(local)

                eps = ratios.get("eps") or (float(info.get("eps") or 0.0) or None)
                roe = _roe_to_fraction(ratios.get("roe") or None)
                dps = ratios.get("dps")
                ev_ebitda = ratios.get("ev_ebitda")
                free_float_pct = equity_prof.get("free_float_pct")

                fundamentals = {
                    "symbol":               local,
                    "name":                 name,
                    "sector":               sector,
                    "industry":             "N/A",
                    "pe_ratio":             pe_ratio or ratios.get("pe"),
                    "pb_ratio":             pb_ratio or ratios.get("pb"),
                    "dividend_yield":       dividend_yield or ratios.get("dividend_yield"),
                    "ev_ebitda":            ev_ebitda,
                    "eps":                  eps,
                    "dps":                  dps,
                    "roe":                  roe,
                    "book_value":           None,
                    "shares_outstanding":   float(info.get("shares") or 0.0),
                    "market_cap":           float(info.get("market_cap") or 0.0) * 1_000_000,
                    "free_float_pct":       free_float_pct,
                    "analyst_recommendation": consensus.get("recommendation"),
                    "analyst_target_price": consensus.get("target_price_pkr"),
                    "analyst_upside":       consensus.get("upside_potential"),
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
            # Read BOTH annual and quarter docs simultaneously so the PDF generator
            # receives all period columns (annual + interim) for a populated IS table.
            annual_doc  = doc_ref.document("annual").get()
            quarter_doc = doc_ref.document("quarter").get()
            annual_data  = annual_doc.to_dict()  or {} if annual_doc.exists  else {}
            quarter_data = quarter_doc.to_dict() or {} if quarter_doc.exists else {}

            if annual_data or quarter_data:
                highlights = _compute_ttm_highlights(annual_data, quarter_data, local)

                # Merge statement rows from both docs so the PDF table shows all periods.
                # Strategy: combine row lists; later periods from the quarter doc are
                # appended as additional columns when the PDF renderer calls _parse_fs_rows.
                # Since both docs share the same Metric names, we merge by-metric.
                def _merge_stmt_rows(ann_rows, q_rows):
                    """Merge two lists of {Metric:..., col1:val, col2:val} dicts into one.
                    Each Metric appears once; all period columns from both lists are included."""
                    if not ann_rows:
                        return q_rows or []
                    if not q_rows:
                        return ann_rows or []
                    # Build a mapping from metric name to merged row dict
                    merged: Dict[str, Any] = {}
                    for row in ann_rows:
                        key = row.get("Metric", "").strip()
                        merged[key] = dict(row)
                    for row in q_rows:
                        key = row.get("Metric", "").strip()
                        if key in merged:
                            # Add any columns not already present
                            for col, val in row.items():
                                if col not in merged[key]:
                                    merged[key][col] = val
                        else:
                            merged[key] = dict(row)
                    return list(merged.values())

                merged_is = _merge_stmt_rows(
                    annual_data.get("income_statement", []),
                    quarter_data.get("income_statement", []),
                )
                merged_bs = _merge_stmt_rows(
                    annual_data.get("balance_sheet", []),
                    quarter_data.get("balance_sheet", []),
                )
                # Cash flow: annual only (AskAnalyst doesn't serve quarterly CF)
                merged_cf = annual_data.get("cash_flow", []) or quarter_data.get("cash_flow", [])

                statements = {
                    "symbol":           local,
                    "income_statement": merged_is,
                    "balance_sheet":    merged_bs,
                    "cash_flow":        merged_cf,
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


# ── Valuation Ratios (AskAnalyst /api/ratios/{id}) ──────────────────


def get_valuation_ratios(symbol: str) -> Dict[str, Any]:
    """
    Fetch valuation multiples and per-share metrics from AskAnalyst.

    Endpoint: GET /api/ratios/{company_id}

    Returns dict with keys: pe, pb, ev_ebitda, dividend_yield, eps, dps,
    price_to_sales, roa, roe, current_ratio.
    Returns empty dict on failure.
    """
    local = _local_symbol(symbol)
    cache_key = f"ratios:{local}"
    cached = get_cached(cache_key, CACHE_TTL_FUNDAMENTALS)
    if cached is not None:
        return cached

    ask_id = _get_askanalyst_id(local)
    if not ask_id:
        return {}

    for endpoint in [f"https://api.askanalyst.com.pk/api/ratios/{ask_id}",
                     f"https://api.askanalyst.com.pk/api/valuation/{ask_id}"]:
        try:
            r = requests.get(endpoint, timeout=8)
            if r.status_code == 200:
                raw = r.json()
                if isinstance(raw, dict) and raw:
                    def _f(v):
                        try:
                            return float(v) if v is not None and v != "None" else None
                        except (TypeError, ValueError):
                            return None

                    result = {
                        "pe":             _f(raw.get("pe") or raw.get("per")),
                        "pb":             _f(raw.get("pbv") or raw.get("pb")),
                        "ev_ebitda":      _f(raw.get("ev_ebitda")),
                        "dividend_yield": _f(raw.get("dividend_yield") or raw.get("dy")),
                        "eps":            _f(raw.get("eps")),
                        "dps":            _f(raw.get("dps")),
                        "price_to_sales": _f(raw.get("ps") or raw.get("price_to_sales")),
                        "roa":            _f(raw.get("roa")),
                        "roe":            _f(raw.get("roe")),
                        "current_ratio":  _f(raw.get("current_ratio")),
                    }
                    set_cached(cache_key, result)
                    return result
        except Exception as e:
            logger.debug("Ratios fetch failed for %s at %s: %s", local, endpoint, e)

    return {}


# ── Equity Profile / Free Float (AskAnalyst /api/equity-profile/{id}) ─


def get_equity_profile(symbol: str) -> Dict[str, Any]:
    """
    Fetch shareholding structure and free float data from AskAnalyst.

    Endpoint: GET /api/equity-profile/{company_id}

    Returns dict with keys: market_cap_mn, total_shares_mn, free_float_mn,
    free_float_pct, paid_up_capital.
    Returns empty dict on failure.
    """
    local = _local_symbol(symbol)
    cache_key = f"equity_profile:{local}"
    cached = get_cached(cache_key, CACHE_TTL_FUNDAMENTALS)
    if cached is not None:
        return cached

    ask_id = _get_askanalyst_id(local)
    if not ask_id:
        return {}

    try:
        r = requests.get(
            f"https://api.askanalyst.com.pk/api/equity-profile/{ask_id}",
            timeout=8
        )
        if r.status_code == 200:
            raw = r.json()
            if isinstance(raw, dict) and raw:
                def _f(v):
                    try:
                        return float(v) if v is not None and v != "None" else None
                    except (TypeError, ValueError):
                        return None

                result = {
                    "market_cap_mn":   _f(raw.get("market_cap") or raw.get("market_cap_mn")),
                    "total_shares_mn": _f(raw.get("shares") or raw.get("total_shares_mn")),
                    "free_float_mn":   _f(raw.get("free_float_mn") or raw.get("free_float")),
                    "free_float_pct":  _f(raw.get("free_float_pct") or raw.get("free_float_percentage")),
                    "paid_up_capital": _f(raw.get("paid_up_capital")),
                }
                set_cached(cache_key, result)
                return result
    except Exception as e:
        logger.debug("Equity profile fetch failed for %s: %s", local, e)

    return {}


# ── Institutional Flows (AskAnalyst /api/market/fipi-lipi) ────────────


def get_institutional_flows() -> Dict[str, Any]:
    """
    Fetch FIPI/LIPI institutional money flow data from AskAnalyst.

    Endpoint: GET /api/market/fipi-lipi

    Returns a dict with keys: flows (by investor type) and sector_fipi
    (foreign flows by sector).  Cached for 15 minutes.
    Returns empty dict on failure.
    """
    cache_key = "market:fipi_lipi"
    cached = get_cached(cache_key, 60 * 15)  # 15 min TTL
    if cached is not None:
        return cached

    try:
        r = requests.get(
            "https://api.askanalyst.com.pk/api/market/fipi-lipi",
            timeout=8
        )
        if r.status_code == 200:
            raw = r.json()
            if raw:
                set_cached(cache_key, raw)
                return raw
    except Exception as e:
        logger.debug("FIPI/LIPI fetch failed: %s", e)

    return {}


# ── Analyst Consensus (AskAnalyst /api/research/consensus/{id}) ───────


def get_analyst_consensus(symbol: str) -> Dict[str, Any]:
    """
    Fetch analyst consensus recommendation and target price from AskAnalyst.

    Endpoint: GET /api/research/consensus/{company_id}

    Returns dict with keys: recommendation, target_price_pkr,
    upside_potential, report_metadata.
    Returns empty dict on failure.
    """
    local = _local_symbol(symbol)
    cache_key = f"consensus:{local}"
    cached = get_cached(cache_key, CACHE_TTL_FUNDAMENTALS)
    if cached is not None:
        return cached

    ask_id = _get_askanalyst_id(local)
    if not ask_id:
        return {}

    try:
        r = requests.get(
            f"https://api.askanalyst.com.pk/api/research/consensus/{ask_id}",
            timeout=8
        )
        if r.status_code == 200:
            raw = r.json()
            if isinstance(raw, dict) and raw:
                result = {
                    "recommendation":  raw.get("recommendation"),
                    "target_price_pkr": float(raw.get("target_price", 0) or 0) or None,
                    "upside_potential": float(raw.get("upside_potential", 0) or 0) or None,
                    "report_metadata": raw.get("report_metadata") or raw.get("research_house"),
                    "last_updated":    raw.get("date") or raw.get("created_at"),
                }
                set_cached(cache_key, result)
                return result
    except Exception as e:
        logger.debug("Analyst consensus fetch failed for %s: %s", local, e)

    return {}


# ── AskAnalyst Historical Chart (AskAnalyst /api/chart/{id}) ──────────


def get_askanalyst_history(symbol: str, range_str: str = "1Y", interval: str = "1D") -> pd.DataFrame:
    """
    Fetch historical OHLCV data directly from AskAnalyst chart endpoint.

    Endpoint: GET /api/chart/{company_id}?range={range}&interval={interval}

    Useful as an additional fallback when PSX DPS and Yahoo REST both fail.
    Supports ranges: 1W, 1M, 3M, 6M, 1Y, 3Y, 5Y
    Intervals: 1D, 1W, 1M

    Returns pandas DataFrame with columns Open, High, Low, Close, Volume.
    Returns empty DataFrame on failure.
    """
    local = _local_symbol(symbol)
    ask_id = _get_askanalyst_id(local)
    if not ask_id:
        return pd.DataFrame()

    try:
        import datetime
        r = requests.get(
            f"https://api.askanalyst.com.pk/api/chart/{ask_id}",
            params={"range": range_str, "interval": interval},
            timeout=10
        )
        if r.status_code != 200:
            return pd.DataFrame()

        raw = r.json()
        # Try both direct array and nested data key
        items = raw if isinstance(raw, list) else raw.get("data", [])
        if not items:
            return pd.DataFrame()

        records = []
        for item in items:
            if isinstance(item, dict):
                date_val = item.get("date") or item.get("datetime")
                close = float(item.get("close", 0) or 0)
                if not close:
                    continue
                records.append({
                    "Date":   pd.to_datetime(date_val),
                    "Open":   float(item.get("open") or close),
                    "High":   float(item.get("high") or close),
                    "Low":    float(item.get("low") or close),
                    "Close":  close,
                    "Volume": float(item.get("volume") or 0),
                })
            elif isinstance(item, list) and len(item) >= 5:
                # Array format: [timestamp_ms, open, high, low, close, volume]
                ts = item[0]
                dt = datetime.datetime.fromtimestamp(ts / 1000 if ts > 1e10 else ts,
                                                     tz=datetime.timezone.utc)
                records.append({
                    "Date":   dt,
                    "Open":   float(item[1] or item[4]),
                    "High":   float(item[2] or item[4]),
                    "Low":    float(item[3] or item[4]),
                    "Close":  float(item[4]),
                    "Volume": float(item[5]) if len(item) > 5 else 0.0,
                })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df.set_index("Date", inplace=True)
        df.sort_index(inplace=True)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df

    except Exception as e:
        logger.debug("AskAnalyst history fetch failed for %s: %s", local, e)
        return pd.DataFrame()


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
