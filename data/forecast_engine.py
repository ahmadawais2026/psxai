"""
data/forecast_engine.py
═══════════════════════════════════════════════════════════════════════
Multi-horizon probabilistic price forecast — Ornstein-Uhlenbeck Monte-Carlo
baseline (pure numpy, no heavy deps).

Design notes (see the plan & research):
- Work in LOG space (stationarity); equity price levels are non-stationary.
- The honest deliverable is a VOLATILITY CONE, not a point prediction —
  short-horizon direction is ~random-walk. Value comes from (a) predictable
  volatility and (b) medium-term pull toward fundamental (DCF) intrinsic value.
- When a DCF Base anchor is available, the log price mean-reverts toward
  ``log(base)`` (Ornstein-Uhlenbeck). Otherwise we fall back to a damped-drift
  GBM cone (near-zero drift — an honest random walk).
- Frontier-market guard: illiquid / "stale" zero-volume days are excluded from
  the volatility estimate, and a variance floor stops the cone collapsing to a
  flat line on thin names.

Returns a dict shaped for both the ledger and the Chart.js fan chart, or
``None`` when there isn't enough history to model.
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# RiskMetrics EWMA decay for daily volatility.
_EWMA_LAMBDA = 0.94
# Mean-reversion half-life toward the DCF anchor, in trading days (~1 year):
# gentle enough that the near-term cone still behaves like a random walk while
# the 3–6 month bands skew toward intrinsic value.
_REVERSION_HALFLIFE = 252
# Daily volatility floors (log-return std). The stale floor is wider so an
# illiquid name still shows an opening cone instead of a deceptive flat line.
_VOL_FLOOR = 0.005
_VOL_FLOOR_STALE = 0.012
# Damped-drift cap when there's no DCF anchor — keep it close to a random walk.
_DRIFT_CAP = 0.0008

_DISCLAIMER = (
    "Probability bands are a statistical projection of historical volatility "
    "anchored to fundamental value — NOT a price guarantee or financial advice. "
    "Frontier-market shocks (currency, policy, circuit breakers) can fall well "
    "outside the cone."
)


def _ewma_daily_vol(log_returns: np.ndarray) -> float:
    """RiskMetrics EWMA volatility of daily log-returns."""
    if log_returns.size == 0:
        return _VOL_FLOOR
    weights = (1 - _EWMA_LAMBDA) * _EWMA_LAMBDA ** np.arange(log_returns.size)[::-1]
    weights /= weights.sum()
    mean = np.average(log_returns, weights=weights)
    var = np.average((log_returns - mean) ** 2, weights=weights)
    return float(np.sqrt(max(var, 0.0)))


def forecast_price_cone(
    history_df: pd.DataFrame,
    current_price: Optional[float] = None,
    dcf_anchors: Optional[Dict[str, Optional[float]]] = None,
    horizons: Sequence[int] = (5, 21, 63, 126),
    n_paths: int = 1000,
    seed: Optional[int] = 7,
) -> Optional[Dict[str, Any]]:
    """Generate a multi-horizon probability cone for a single ticker.

    Args:
        history_df:    OHLCV DataFrame (datetime index, ``Close``/``Volume`` cols)
                       as returned by ``data.market_data.get_history``.
        current_price: Spot price; defaults to the last ``Close``.
        dcf_anchors:   ``{base, bull, bear}`` per-share values (or Nones) from
                       ``data.valuation.extract_anchors``. Drives mean reversion.
        horizons:      Forecast points in trading days.
        n_paths:       Monte-Carlo paths.
        seed:          RNG seed for reproducible cones (forecasts shouldn't jitter
                       between identical runs).

    Returns:
        ``{horizons:[{t_days,date,p10,p25,p50,p75,p90}], anchors, current_price,
        vol_daily, stale_fraction, model, disclaimer}`` or ``None``.
    """
    if history_df is None or len(history_df) < 20 or "Close" not in history_df.columns:
        return None

    close = pd.to_numeric(history_df["Close"], errors="coerce").dropna()
    if len(close) < 20:
        return None

    spot = float(current_price) if current_price else float(close.iloc[-1])
    if spot <= 0:
        return None

    # ── Stale-price / illiquidity guard ──────────────────────────────
    # Drop days with no price move AND (when available) zero volume from the
    # volatility estimate — those are non-trading "stale" prints, not real
    # zero-variance signal.
    raw_log_ret = np.log(close / close.shift(1)).dropna()
    stale_mask = raw_log_ret == 0.0
    if "Volume" in history_df.columns:
        vol = pd.to_numeric(history_df["Volume"], errors="coerce").reindex(raw_log_ret.index)
        stale_mask = stale_mask | (vol.fillna(0) <= 0)
    stale_fraction = float(stale_mask.mean()) if len(stale_mask) else 0.0
    clean_log_ret = raw_log_ret[~stale_mask].to_numpy()
    if clean_log_ret.size < 10:
        clean_log_ret = raw_log_ret.to_numpy()

    sigma = _ewma_daily_vol(clean_log_ret)
    floor = _VOL_FLOOR_STALE if stale_fraction > 0.05 else _VOL_FLOOR
    sigma = max(sigma, floor)

    # ── Drift / mean-reversion specification ─────────────────────────
    log_spot = np.log(spot)
    base_anchor = (dcf_anchors or {}).get("base") if dcf_anchors else None
    if base_anchor and base_anchor > 0:
        kappa = np.log(2) / _REVERSION_HALFLIFE
        theta = np.log(float(base_anchor))
        base_drift = 0.0
        model = "ou_dcf_anchored"
    else:
        kappa = 0.0
        theta = 0.0
        hist_mean = float(np.mean(clean_log_ret)) if clean_log_ret.size else 0.0
        base_drift = float(np.clip(0.3 * hist_mean, -_DRIFT_CAP, _DRIFT_CAP))
        model = "gbm_damped"

    # ── Vectorized simulation over the longest horizon ───────────────
    n_steps = int(max(horizons))
    rng = np.random.default_rng(seed)
    shocks = rng.standard_normal((n_paths, n_steps)) * sigma
    x = np.empty((n_paths, n_steps + 1))
    x[:, 0] = log_spot
    for t in range(n_steps):
        x[:, t + 1] = x[:, t] + kappa * (theta - x[:, t]) + base_drift + shocks[:, t]
    paths = np.exp(x)  # back to price levels (lognormal → no negative prices)

    last_date = history_df.index[-1]
    qs = [10, 25, 50, 75, 90]
    horizon_out: List[Dict[str, Any]] = []
    for h in horizons:
        col = paths[:, int(h)]
        pcts = np.percentile(col, qs)
        try:
            fdate = (pd.Timestamp(last_date) + pd.tseries.offsets.BDay(int(h))).strftime("%Y-%m-%d")
        except Exception:
            fdate = None
        horizon_out.append({
            "t_days": int(h),
            "date": fdate,
            "p10": round(float(pcts[0]), 2),
            "p25": round(float(pcts[1]), 2),
            "p50": round(float(pcts[2]), 2),
            "p75": round(float(pcts[3]), 2),
            "p90": round(float(pcts[4]), 2),
        })

    return {
        "horizons": horizon_out,
        "anchors": dcf_anchors or {"base": None, "bull": None, "bear": None},
        "current_price": round(spot, 2),
        "as_of_date": pd.Timestamp(last_date).strftime("%Y-%m-%d"),
        "vol_daily": round(sigma, 5),
        "vol_annualized_pct": round(sigma * np.sqrt(252) * 100, 1),
        "stale_fraction": round(stale_fraction, 3),
        "model": model,
        "disclaimer": _DISCLAIMER,
    }
