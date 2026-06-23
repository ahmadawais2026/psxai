"""
data/valuation.py
═══════════════════════════════════════════════════════════════════════
Reusable FCFE-DCF scenario computation.

The Fundamentals Analyst computes Base/Bull/Bear intrinsic values inline and
folds them into its free-text prompt (agents/fundamentals_analyst.py). The
forecast engine needs those same anchors as STRUCTURED numbers to pull its
medium-term drift toward intrinsic value — so this module factors out the
exact input derivation (FCFE, beta, growth, risk-free, share count) and returns
``DCFEngine.generate_scenarios()`` directly.

Returns ``None`` when DCF is not applicable (non-positive FCFE / unknown shares
— common for banks & financials), in which case the forecast degrades to a
damped-drift cone with no fundamental anchor.
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from data.dcf_engine import DCFEngine

logger = logging.getLogger(__name__)


def _parse_val(val_str: Any) -> float:
    """Parse a possibly-suffixed financial string ('1.2B', 'PKR 340M') to float.

    Mirrors the parser in agents/fundamentals_analyst.py so DCF inputs match.
    """
    if not val_str or val_str == "N/A":
        return 0.0
    s = str(val_str).replace("PKR", "").replace("$", "").replace("%", "").strip()
    multiplier = 1.0
    if "T" in s:
        multiplier, s = 1e12, s.replace("T", "")
    elif "B" in s:
        multiplier, s = 1e9, s.replace("B", "")
    elif "M" in s:
        multiplier, s = 1e6, s.replace("M", "")
    elif "K" in s:
        multiplier, s = 1e3, s.replace("K", "")
    try:
        return float(s.strip()) * multiplier
    except ValueError:
        return 0.0


def _resolve_risk_free(macro_context: Optional[Dict[str, Any]]) -> Optional[float]:
    """SBP risk-free rate, preferring the shared macro snapshot, else a direct fetch."""
    macro = macro_context or {}
    rf = macro.get("risk_free_rate")
    if rf is None:
        pr = macro.get("policy_rate_pct")
        if pr is not None:
            try:
                pr = float(pr)
                rf = pr / 100.0 if pr > 1.0 else pr
            except (TypeError, ValueError):
                rf = None
    if rf is None:
        try:
            from data.sbp_easydata import get_risk_free_rate
            rf = get_risk_free_rate()
        except Exception:
            rf = None
    try:
        return float(rf) if rf else None
    except (TypeError, ValueError):
        return None


def compute_dcf_scenarios(
    symbol: str,
    fundamentals: Optional[Dict[str, Any]] = None,
    financials: Optional[Dict[str, Any]] = None,
    quote: Optional[Dict[str, Any]] = None,
    macro_context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Recompute the FCFE DCF Base/Bull/Bear scenarios for *symbol*.

    Pass the already-fetched ``raw_fundamentals`` / ``raw_financials`` from the
    fundamental report to avoid a refetch; any missing input is fetched lazily.
    Returns the ``generate_scenarios()`` dict, or ``None`` when DCF can't run.
    """
    try:
        from data.market_data import get_beta, compute_historical_growth

        if fundamentals is None or financials is None or quote is None:
            from data.market_data import get_fundamentals, get_financial_statements, get_quote
            fundamentals = fundamentals or get_fundamentals(symbol) or {}
            financials = financials or get_financial_statements(symbol) or {}
            quote = quote or get_quote(symbol) or {}

        fcf = _parse_val(financials.get("free_cash_flow"))
        mcap = _parse_val(fundamentals.get("market_cap"))
        price = float(quote.get("price", 0.0) or 0.0)

        beta = get_beta(symbol)
        hist_growth = compute_historical_growth(financials)
        rf_rate = _resolve_risk_free(macro_context)

        # Share count in MILLIONS (matches FCFE units → per-share value in PKR).
        shares = _parse_val(fundamentals.get("shares_outstanding"))
        if shares <= 0 and price > 0 and mcap > 0:
            shares = (mcap / price) / 1_000_000.0

        if fcf <= 0 or shares <= 0:
            return None  # DCF not applicable (banks / negative FCFE)

        # Derive book value per share for DCF sanity check.
        # get_fundamentals stores this as 'book_value' (equity_mn / shares_mn = PKR/share).
        bvps = None
        raw_bv = fundamentals.get("book_value")
        if raw_bv:
            try:
                bvps = float(raw_bv)
            except (TypeError, ValueError):
                bvps = None

        engine = DCFEngine(risk_free_rate=rf_rate) if rf_rate else DCFEngine()
        scenarios = engine.generate_scenarios(
            base_fcf=fcf,
            levered_beta=beta,
            shares_outstanding=shares,
            historical_growth=hist_growth,
            current_price=price if price > 0 else None,
            book_value_per_share=bvps if (bvps and bvps > 0) else None,
        )
        if isinstance(scenarios, dict) and "error" in scenarios:
            return None
        return scenarios
    except Exception as exc:
        logger.warning("DCF scenario computation failed for %s: %s", symbol, exc)
        return None


def extract_anchors(scenarios: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Flatten scenarios to ``{base, bull, bear}`` per-share values (or Nones)."""
    def val(key: str) -> Optional[float]:
        try:
            v = (scenarios or {}).get(key, {}).get("value")
            return float(v) if v is not None else None
        except (TypeError, ValueError, AttributeError):
            return None

    return {"base": val("base"), "bull": val("bull"), "bear": val("bear")}
