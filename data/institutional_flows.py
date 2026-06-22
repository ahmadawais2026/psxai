"""
data/institutional_flows.py
════════════════════════════════════════════════════════════════════════
Institutional money flow tracking for PSX.

Sources (all free, no API key required):
  1. AskAnalyst  → /api/market/fipi-lipi  (primary, already integrated)
  2. SCSTrade     → HTML table scraping    (sector-level FIPI/LIPI breakdown)
  3. MUFAP        → Mutual fund NAV/AUM    (risk appetite signal)

FIPI/LIPI Logic:
  - Foreign (FIPI) net outflows + Local MF absorption = potential bottom
  - Foreign inflows + declining MF participation = distribution phase
  - Used by Risk Agent to adjust beta and sector weights

MUFAP Logic:
  - AUM shift from Equity → Money Market = risk-off signal
  - AUM shift from Income → Equity = institutional confidence signal
════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import requests

from data.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.9",
}

_TTL_FLOWS = 60 * 30   # 30 min — flows update EOD
_TTL_MUFAP = 3600 * 4  # 4 hours — NAVs update daily


# ── SCSTrade FIPI/LIPI Scraping ────────────────────────────────────────────

def _parse_flow_table(soup, table_index: int = 0) -> List[Dict[str, Any]]:
    """Parse an HTML table of FIPI/LIPI rows into structured dicts."""
    from bs4 import BeautifulSoup  # noqa: F401 — imported for type context
    tables = soup.find_all("table")
    if not tables or table_index >= len(tables):
        return []

    rows = []
    header = []
    for i, row in enumerate(tables[table_index].find_all("tr")):
        cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
        if i == 0:
            header = cells
            continue
        if cells:
            row_dict = {}
            for j, val in enumerate(cells):
                key = header[j].lower().replace(" ", "_") if j < len(header) else f"col_{j}"
                # Attempt numeric conversion
                cleaned = val.replace(",", "").replace("(", "-").replace(")", "")
                try:
                    row_dict[key] = float(cleaned)
                except ValueError:
                    row_dict[key] = val
            rows.append(row_dict)
    return rows


def get_fipi_lipi_scstrade() -> Dict[str, Any]:
    """
    Scrape FIPI/LIPI daily flow data from SCSTrade.com.

    SCSTrade mirrors NCCPL data in clean HTML tables, providing sector-level
    breakdown of foreign and local investor flows.

    Returns:
        {
          flows: [{participant, net_daily_usd_mn, net_cytd_usd_mn}, ...],
          sector_flows: [{sector, net_foreign_usd_mn}, ...],
          signal: str  — human-readable institutional flow interpretation
        }
    """
    cache_key = "institutional:fipi_lipi_scstrade"
    cached = get_cached(cache_key, _TTL_FLOWS)
    if cached is not None:
        return cached

    result: Dict[str, Any] = {}

    try:
        from bs4 import BeautifulSoup

        import cloudscraper
        scraper = cloudscraper.create_scraper()

        # SCSTrade FIPI/LIPI page
        r = scraper.get(
            "https://scstrade.com/stockscreener/SS_ShareTradeHistory.aspx",
            headers=_HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            # Try alternative page
            r = scraper.get(
                "https://www.scstrade.com/market/MS_FIPIAndLIPI.aspx",
                headers=_HEADERS,
                timeout=15,
            )

        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            flows = _parse_flow_table(soup, 0)

            if flows:
                result["flows"] = flows

                # Derive signal from net foreign flow
                foreign_net = next(
                    (
                        row.get("net_daily", row.get("net", row.get("daily_net")))
                        for row in flows
                        if "foreign" in str(row).lower()
                    ),
                    None,
                )
                mf_net = next(
                    (
                        row.get("net_daily", row.get("net"))
                        for row in flows
                        if "mutual" in str(row).lower() or "mf" in str(row).lower()
                    ),
                    None,
                )

                if foreign_net is not None and mf_net is not None:
                    if foreign_net < 0 and mf_net > 0:
                        result["signal"] = (
                            f"ACCUMULATION: Foreign selling (${abs(foreign_net):.1f}M) being "
                            f"absorbed by local mutual funds (${mf_net:.1f}M) — potential bottom."
                        )
                    elif foreign_net > 0:
                        result["signal"] = (
                            f"INFLOW: Net foreign buying (${foreign_net:.1f}M) — bullish."
                        )
                    elif foreign_net < 0 and mf_net < 0:
                        result["signal"] = (
                            f"DISTRIBUTION: Both foreign (${foreign_net:.1f}M) and local MFs "
                            f"(${mf_net:.1f}M) net sellers — bearish pressure."
                        )

    except Exception as e:
        logger.warning("SCSTrade FIPI/LIPI scrape failed: %s", e)

    # Fallback: try AskAnalyst fipi-lipi endpoint
    if not result:
        try:
            from data.market_data import get_institutional_flows
            ask_flows = get_institutional_flows()
            if ask_flows:
                result = ask_flows
                result["source"] = "AskAnalyst"
        except Exception as e:
            logger.debug("AskAnalyst FIPI/LIPI fallback failed: %s", e)

    if result:
        set_cached(cache_key, result)
    return result


# ── MUFAP Mutual Fund AUM Tracking ────────────────────────────────────────

def get_mufap_aum_snapshot() -> Dict[str, Any]:
    """
    Scrape MUFAP daily NAV and AUM data for institutional risk-appetite gauge.

    Key signals:
    - Equity fund AUM growing → institutions buying equities (risk-on)
    - Money market AUM surging → risk-off flight to safety
    - Income fund AUM declining → capital rotating into equities

    Returns:
        {
          equity_aum_pkr_bn, money_market_aum_pkr_bn, income_aum_pkr_bn,
          total_aum_pkr_bn, equity_share_pct, risk_appetite: 'HIGH'|'LOW'|'NEUTRAL'
        }
    """
    cache_key = "institutional:mufap_aum"
    cached = get_cached(cache_key, _TTL_MUFAP)
    if cached is not None:
        return cached

    result: Dict[str, Any] = {}

    try:
        from bs4 import BeautifulSoup

        import cloudscraper
        scraper = cloudscraper.create_scraper()

        r = scraper.get(
            "https://mufap.com.pk/payout-report.php",
            headers=_HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            r = scraper.get(
                "https://mufap.com.pk/nav-report.php",
                headers=_HEADERS,
                timeout=15,
            )

        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")

            equity_aum = 0.0
            mm_aum = 0.0
            income_aum = 0.0

            # Scan all table rows for fund category and AUM
            for row in soup.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                row_text = " ".join(cells).lower()
                # Try to find AUM column (usually last numeric column)
                nums = []
                for cell in cells:
                    cleaned = cell.replace(",", "")
                    try:
                        nums.append(float(cleaned))
                    except ValueError:
                        pass

                if not nums:
                    continue
                aum_val = nums[-1]

                if "equity" in row_text or "stock" in row_text:
                    equity_aum += aum_val
                elif "money market" in row_text or "cash" in row_text:
                    mm_aum += aum_val
                elif "income" in row_text or "fixed" in row_text:
                    income_aum += aum_val

            total = equity_aum + mm_aum + income_aum
            if total > 0:
                result = {
                    "equity_aum_pkr_bn":      round(equity_aum / 1000, 1),
                    "money_market_aum_pkr_bn": round(mm_aum / 1000, 1),
                    "income_aum_pkr_bn":      round(income_aum / 1000, 1),
                    "total_aum_pkr_bn":        round(total / 1000, 1),
                    "equity_share_pct":        round(equity_aum / total * 100, 1),
                    "source": "MUFAP",
                }

                # Risk appetite signal
                eq_share = result["equity_share_pct"]
                if eq_share > 35:
                    result["risk_appetite"] = "HIGH"
                    result["mufap_signal"] = (
                        f"Equity funds hold {eq_share}% of total AUM — institutions are "
                        "risk-on. Favour growth and cyclical equities."
                    )
                elif eq_share < 20:
                    result["risk_appetite"] = "LOW"
                    result["mufap_signal"] = (
                        f"Equity funds hold only {eq_share}% of total AUM — capital "
                        "in money market/income. Defensive posture warranted."
                    )
                else:
                    result["risk_appetite"] = "NEUTRAL"
                    result["mufap_signal"] = (
                        f"Equity funds at {eq_share}% of AUM — balanced allocation."
                    )

    except Exception as e:
        logger.warning("MUFAP AUM scrape failed: %s", e)

    if result:
        set_cached(cache_key, result)
    return result


# ── Combined Institutional Flow Snapshot ──────────────────────────────────

def get_full_flow_context() -> Dict[str, Any]:
    """
    Aggregate FIPI/LIPI + MUFAP into a single institutional context block.
    Used by the Risk Agent and Fundamental Agent as macro overlay data.

    Returns:
        Combined dict with flows, mufap AUM, and unified signal.
    """
    cache_key = "institutional:full_context"
    cached = get_cached(cache_key, _TTL_FLOWS)
    if cached is not None:
        return cached

    context: Dict[str, Any] = {}

    try:
        flows = get_fipi_lipi_scstrade()
        context["fipi_lipi"] = flows
    except Exception as e:
        logger.debug("FIPI/LIPI context failed: %s", e)

    try:
        mufap = get_mufap_aum_snapshot()
        context["mufap"] = mufap
    except Exception as e:
        logger.debug("MUFAP context failed: %s", e)

    if context:
        set_cached(cache_key, context)
    return context
