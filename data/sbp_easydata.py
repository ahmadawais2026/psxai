"""
data/sbp_easydata.py
════════════════════════════════════════════════════════════════════════
State Bank of Pakistan (SBP) EasyData API client.

Fetches macroeconomic time-series data directly from the official SBP
portal (easydata.sbp.org.pk). Free, official, structured JSON output.

Key datasets queried:
  - Policy / repo / reverse-repo rates
  - Broad Money (M2) & Reserve Money
  - Foreign Exchange Reserves
  - Exports / Imports / Current Account
  - Roshan Digital Account (diaspora flows)
  - MTB / PIB auction yields (risk-free rate curve)

These indicators are used by the risk and fundamental agents to detect
macroeconomic regime changes (tightening vs. easing cycles) and to
adjust discount rates and portfolio beta dynamically.
════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

from data.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

# SBP EasyData base URL
_SBP_BASE = "https://easydata.sbp.org.pk"

# Cache TTL: macro data changes slowly
_CACHE_TTL = 3600 * 6  # 6 hours

# ---------------------------------------------------------------------------
# Dataset IDs on SBP EasyData (resolved via metadata endpoint)
# These are stable identifiers used to pull time-series.
# ---------------------------------------------------------------------------
_DATASETS = {
    "policy_rate":    "PR",       # SBP Policy (Target) Rate
    "m2":             "M2",       # Broad Money M2
    "fx_reserves":    "FXR",      # Foreign Exchange Reserves (SBP + Banks)
    "exports":        "EXP",      # Exports of Goods & Services
    "imports":        "IMP",      # Imports of Goods & Services
    "cpi":            "CPI",      # Consumer Price Index / Inflation
    "rda_flows":      "RDA",      # Roshan Digital Account inflows
    "mtb_yields":     "MTB",      # Market Treasury Bill auction yields
}

import os

_API_KEY = os.environ.get("SBP_EASYDATA_API_KEY")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://easydata.sbp.org.pk/",
}

if _API_KEY:
    _HEADERS["apikey"] = _API_KEY
    _HEADERS["ApiKey"] = _API_KEY
    _HEADERS["x-api-key"] = _API_KEY
    _HEADERS["Authorization"] = f"Bearer {_API_KEY}"



def _get(path: str, params: Optional[Dict] = None) -> Optional[Any]:
    """Safe GET wrapper for SBP EasyData endpoints."""
    try:
        r = requests.get(
            f"{_SBP_BASE}{path}",
            params=params,
            headers=_HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        logger.debug("SBP EasyData %s → HTTP %s", path, r.status_code)
    except Exception as e:
        logger.debug("SBP EasyData request failed for %s: %s", path, e)
    return None


def _latest_value(series: List[Dict]) -> Optional[float]:
    """Extract the most recent non-null value from a sorted time-series list."""
    for item in reversed(series):
        val = item.get("value") or item.get("val") or item.get("data")
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


def _latest_two(series: List[Dict]) -> tuple[Optional[float], Optional[float]]:
    """Return the two most recent values for MoM / YoY calculations."""
    values = []
    for item in reversed(series):
        val = item.get("value") or item.get("val") or item.get("data")
        if val is not None:
            try:
                values.append(float(val))
            except (TypeError, ValueError):
                pass
        if len(values) == 2:
            break
    latest = values[0] if values else None
    prev = values[1] if len(values) > 1 else None
    return latest, prev


def get_policy_rate() -> Dict[str, Any]:
    """
    Fetch the current SBP Policy (Target) Rate.

    Returns:
        {policy_rate_pct, repo_rate_pct, reverse_repo_pct, trend}
        trend: 'easing' | 'tightening' | 'stable'
    """
    cache_key = "sbp:policy_rate"
    cached = get_cached(cache_key, _CACHE_TTL)
    if cached is not None:
        return cached

    # Try EasyData API endpoint for interest rate structure
    data = _get("/api/datasets/PR") or _get("/api/datasets/interest-rates")

    result: Dict[str, Any] = {}

    if data:
        series = data if isinstance(data, list) else data.get("data", [])
        latest, prev = _latest_two(series)
        if latest is not None:
            result["policy_rate_pct"] = latest
            if prev is not None:
                if latest < prev:
                    result["trend"] = "easing"
                elif latest > prev:
                    result["trend"] = "tightening"
                else:
                    result["trend"] = "stable"

    # Fallback: scrape the SBP monetary policy page
    if not result:
        try:
            r = requests.get(
                "https://www.sbp.org.pk/mpc/",
                headers=_HEADERS,
                timeout=8,
            )
            if r.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, "html.parser")
                # Look for rate table or highlighted rate text
                for tag in soup.find_all(["td", "th", "p", "h3", "h4"]):
                    text = tag.get_text(strip=True)
                    if "%" in text and any(
                        kw in text.lower() for kw in ["policy rate", "target rate"]
                    ):
                        import re
                        m = re.search(r"(\d+\.?\d*)\s*%", text)
                        if m:
                            result["policy_rate_pct"] = float(m.group(1))
                            break
        except Exception as e:
            logger.debug("SBP MPC page fallback failed: %s", e)

    if result:
        set_cached(cache_key, result)
    return result


def get_risk_free_rate() -> Optional[float]:
    """
    Fetch a risk-free rate proxy for CAPM, returned as a **decimal**
    (e.g. 0.1475 for 14.75%).

    Preference order:
      1. Short T-bill (MTB) cut-off yield — the truest short risk-free proxy
      2. SBP Policy (Target) Rate — fallback when no auction yield is available

    Returns ``None`` when neither source resolves, leaving the caller to apply
    its own default.
    """
    cache_key = "sbp:risk_free_rate"
    cached = get_cached(cache_key, _CACHE_TTL)
    if cached is not None:
        return cached

    rate_pct: Optional[float] = None

    # 1. T-bill (MTB) auction yields
    data = _get("/api/datasets/MTB") or _get("/api/datasets/tbill-yields")
    if data:
        series = data if isinstance(data, list) else data.get("data", [])
        val = _latest_value(series)
        if val is not None:
            rate_pct = val

    # 2. Fall back to the policy rate
    if rate_pct is None:
        rate_pct = get_policy_rate().get("policy_rate_pct")

    if rate_pct is None:
        return None

    # Datasets report percent (e.g. 14.75) — normalise to a decimal.
    rate = float(rate_pct)
    if rate > 1.0:
        rate /= 100.0
    result = round(rate, 4)

    set_cached(cache_key, result)
    return result


def get_fx_reserves() -> Dict[str, Any]:
    """
    Fetch Pakistan's total foreign exchange reserves (SBP + commercial banks).

    Returns:
        {sbp_reserves_usd_bn, total_reserves_usd_bn, weeks_of_import_cover}
    """
    cache_key = "sbp:fx_reserves"
    cached = get_cached(cache_key, _CACHE_TTL)
    if cached is not None:
        return cached

    data = _get("/api/datasets/FXR") or _get("/api/datasets/foreign-exchange-reserves")
    result: Dict[str, Any] = {}

    if data:
        series = data if isinstance(data, list) else data.get("data", [])
        latest, prev = _latest_two(series)
        if latest is not None:
            result["total_reserves_usd_bn"] = round(latest / 1000, 2) if latest > 1000 else latest
            if prev is not None:
                result["reserves_wow_change_usd_bn"] = round(
                    (result["total_reserves_usd_bn"] - (round(prev / 1000, 2) if prev > 1000 else prev)), 2
                )

    # Fallback: parse SBP weekly reserves press release page
    if not result:
        try:
            r = requests.get(
                "https://www.sbp.org.pk/ecodata/index2.asp",
                headers=_HEADERS,
                timeout=8,
            )
            if r.status_code == 200:
                from bs4 import BeautifulSoup
                import re
                soup = BeautifulSoup(r.text, "html.parser")
                text = soup.get_text()
                # Look for USD billion figures (e.g. "9.12 billion")
                m = re.search(r"total.*?(\d+\.?\d*)\s*(?:billion|bn)", text, re.IGNORECASE)
                if m:
                    result["total_reserves_usd_bn"] = float(m.group(1))
        except Exception as e:
            logger.debug("SBP reserves page fallback failed: %s", e)

    if result:
        set_cached(cache_key, result)
    return result


def get_m2_money_supply() -> Dict[str, Any]:
    """
    Fetch Broad Money (M2) supply and month-over-month growth rate.
    High M2 growth → liquidity expansion → equity rally signal.

    Returns:
        {m2_pkr_bn, m2_mom_growth_pct, m2_yoy_growth_pct}
    """
    cache_key = "sbp:m2"
    cached = get_cached(cache_key, _CACHE_TTL)
    if cached is not None:
        return cached

    data = _get("/api/datasets/M2") or _get("/api/datasets/broad-money")
    result: Dict[str, Any] = {}

    if data:
        series = data if isinstance(data, list) else data.get("data", [])
        if len(series) >= 2:
            latest, prev = _latest_two(series)
            if latest and prev:
                result["m2_pkr_bn"] = latest
                result["m2_mom_growth_pct"] = round((latest - prev) / prev * 100, 2)

    if result:
        set_cached(cache_key, result)
    return result


def get_trade_balance() -> Dict[str, Any]:
    """
    Fetch export and import data to compute trade balance.
    Used to model PKR pressure and import-dependent sector headwinds.

    Returns:
        {exports_usd_mn, imports_usd_mn, trade_deficit_usd_mn}
    """
    cache_key = "sbp:trade_balance"
    cached = get_cached(cache_key, _CACHE_TTL)
    if cached is not None:
        return cached

    exp_data = _get("/api/datasets/EXP") or _get("/api/datasets/exports")
    imp_data = _get("/api/datasets/IMP") or _get("/api/datasets/imports")

    result: Dict[str, Any] = {}

    if exp_data:
        series = exp_data if isinstance(exp_data, list) else exp_data.get("data", [])
        val = _latest_value(series)
        if val:
            result["exports_usd_mn"] = val

    if imp_data:
        series = imp_data if isinstance(imp_data, list) else imp_data.get("data", [])
        val = _latest_value(series)
        if val:
            result["imports_usd_mn"] = val

    if "exports_usd_mn" in result and "imports_usd_mn" in result:
        result["trade_deficit_usd_mn"] = round(
            result["imports_usd_mn"] - result["exports_usd_mn"], 1
        )

    if result:
        set_cached(cache_key, result)
    return result


def get_macro_snapshot() -> Dict[str, Any]:
    """
    Aggregate all key SBP macro indicators into a single snapshot dict.

    Used by the Risk and Fundamental agents to detect macro regime:
    - Easing cycle + high reserves + M2 expansion → increase beta
    - Tightening + low reserves + trade deficit widening → defensive posture

    Returns:
        Combined dict of all available macro indicators.
    """
    cache_key = "sbp:macro_snapshot"
    cached = get_cached(cache_key, _CACHE_TTL)
    if cached is not None:
        return cached

    snapshot: Dict[str, Any] = {"source": "SBP EasyData"}

    try:
        snapshot.update(get_policy_rate())
    except Exception as e:
        logger.debug("Policy rate fetch failed: %s", e)

    try:
        rf = get_risk_free_rate()
        if rf is not None:
            snapshot["risk_free_rate"] = rf  # decimal, e.g. 0.1475
    except Exception as e:
        logger.debug("Risk-free rate fetch failed: %s", e)

    try:
        snapshot.update(get_fx_reserves())
    except Exception as e:
        logger.debug("FX reserves fetch failed: %s", e)

    try:
        snapshot.update(get_m2_money_supply())
    except Exception as e:
        logger.debug("M2 fetch failed: %s", e)

    try:
        snapshot.update(get_trade_balance())
    except Exception as e:
        logger.debug("Trade balance fetch failed: %s", e)

    # Derive macro regime label for AI agents
    policy_rate = snapshot.get("policy_rate_pct")
    reserves = snapshot.get("total_reserves_usd_bn")
    trend = snapshot.get("trend", "stable")

    if policy_rate is not None:
        if trend == "easing" and reserves and reserves > 10:
            snapshot["macro_regime"] = "RISK_ON"
            snapshot["macro_signal"] = (
                f"Easing cycle (policy rate {policy_rate}%) with adequate reserves "
                f"(${reserves}B). Increase equity beta; favour cyclicals."
            )
        elif trend == "tightening" or (reserves and reserves < 8):
            snapshot["macro_regime"] = "RISK_OFF"
            snapshot["macro_signal"] = (
                f"Tightening cycle (policy rate {policy_rate}%) or low reserves "
                f"(${reserves}B). Reduce beta; favour defensives and cash."
            )
        else:
            snapshot["macro_regime"] = "NEUTRAL"
            snapshot["macro_signal"] = (
                f"Stable rate environment (policy rate {policy_rate}%). "
                "Maintain balanced allocation."
            )

    if snapshot:
        set_cached(cache_key, snapshot)
    return snapshot
