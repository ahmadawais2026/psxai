"""
Risk Analyst Agent — Specialist in portfolio exposure control, volatility, and downside risk.

Offloads all mathematical computations to NumPy / Pandas / scikit-learn
(FinAgent arithmetic-separation principle), then sends the structured
numeric evidence to Gemini for qualitative reasoning and recommendations.

Advanced risk models integrated:
  - Conditional Value at Risk (CVaR / Expected Shortfall)
  - Higher-Order Moments (Skewness, Excess Kurtosis, Jarque-Bera)
  - Ledoit-Wolf Shrinkage Covariance
  - Black-Litterman Posterior Expected Returns
  - Adler-Dumas Currency Exposure (PKR/USD Gamma)
"""

from __future__ import annotations

import logging
import math
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Dict, Optional

# yfinance removed

from agents.base_agent import BaseAgent
from agents.prompts import ANALYSIS_PROMPT_TEMPLATE, RISK_ANALYST_PERSONA
from config import HISTORY_PERIOD_DAILY
from data.market_data import get_history, get_quote
from data.technical_indicators import compute_advanced_risk_suite

logger = logging.getLogger(__name__)

# Hard wall-clock deadline per network fetch — prevents LangGraph fan-in deadlock
FETCH_TIMEOUT_SECS: int = 8


def _fetch_with_timeout(fn, *args, timeout: int = FETCH_TIMEOUT_SECS, label: str = ""):
    """Run *fn(*args)* in a thread pool with a hard *timeout* seconds deadline.

    Returns the result of *fn* on success, or ``None`` if the call times out
    or raises any exception.  Never blocks the caller for longer than *timeout*.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, *args)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            logger.warning(f"Fetch '{label}' timed out after {timeout}s — continuing without it.")
            future.cancel()
            return None
        except Exception as exc:
            logger.warning(f"Fetch '{label}' failed: {exc}")
            return None



class RiskAnalystAgent(BaseAgent):
    """Calculates quantitative risk metrics and analyzes qualitative risk factors for a PSX stock."""

    def __init__(self) -> None:
        super().__init__(
            name="Risk Analyst",
            persona=RISK_ANALYST_PERSONA,
        )

    def analyze(self, symbol: str, portfolio_context: Optional[Dict[str, Any]] = None,
                context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run a full risk analysis for *symbol*.

        Pipeline:
        1. Fetch price history of stock and KSE-100 index.
        2. Compute beta, annualized volatility, and maximum drawdown.
        3. Compose data block incorporating portfolio context (if provided).
        4. Query Gemini to assess macro risk and recommend position limits.
        """
        self._log(f"Starting risk analysis for {symbol} …")

        # ── Step 1: Fetch price histories (hard timeout on every call) ──
        stock_df = _fetch_with_timeout(
            get_history, symbol, HISTORY_PERIOD_DAILY, "1d",
            label=f"stock_history:{symbol}"
        )
        if stock_df is None or stock_df.empty:
            self._log("Stock history unavailable — returning error report.")
            return self._error_report(symbol, "No historical data to compute risk metrics.")
        self._log(f"Fetched stock history ({len(stock_df)} bars).")

        # KSE-100 index — try two symbols, stop at first hit
        index_df = None
        for idx_sym in ["^KSE", "KSE100.KA"]:
            result = _fetch_with_timeout(
                get_history, idx_sym, HISTORY_PERIOD_DAILY, "1d",
                label=f"index_history:{idx_sym}"
            )
            if result is not None and not result.empty:
                index_df = result
                self._log(f"Fetched index history from {idx_sym} ({len(index_df)} bars).")
                break

        # USD/PKR exchange rate for Adler-Dumas FX exposure
        def _fetch_fx():
            import requests
            import pandas as pd
            import datetime
            url = "https://query1.finance.yahoo.com/v8/finance/chart/PKR=X"
            params = {"range": HISTORY_PERIOD_DAILY, "interval": "1d"}
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            try:
                r = requests.get(url, params=params, headers=headers, timeout=8)
                if r.status_code != 200:
                    return None
                res = r.json()["chart"]["result"][0]
                timestamps = res.get("timestamp", [])
                closes = res.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                
                records = []
                for i, ts in enumerate(timestamps):
                    if i < len(closes) and closes[i] is not None:
                        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                        records.append({
                            "Date": dt,
                            "Close": float(closes[i])
                        })
                if not records:
                    return None
                df = pd.DataFrame(records)
                df.set_index("Date", inplace=True)
                df.sort_index(inplace=True)
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                return df
            except Exception as e:
                logger.warning(f"REST FX history fetch failed for PKR=X: {e}")
            return None

        fx_df = _fetch_with_timeout(_fetch_fx, label="PKR=X FX history")
        if fx_df is not None:
            self._log(f"Fetched PKR=X FX history ({len(fx_df)} bars).")
        else:
            self._log("PKR=X FX fetch skipped or timed out — Adler-Dumas will be omitted.")

        # ── Step 2a: Classic risk metrics (vol, drawdown, beta, Sharpe) ──
        metrics = self._calculate_risk_metrics(stock_df, index_df)

        # ── Step 2b: Advanced quantitative risk suite ─────────────
        self._log("Computing advanced quantitative risk suite …")
        try:
            advanced_risk = compute_advanced_risk_suite(
                df=stock_df,
                index_df=index_df,
                fx_df=fx_df,
                bl_views_Q=None,
                bl_views_P=None,
            )
        except Exception as exc:
            self._log(f"Advanced risk suite failed (non-fatal): {exc}")
            advanced_risk = {"error": str(exc)}

        metrics["advanced"] = advanced_risk

        # ── Step 3: Current quote ────────────────────────────────
        quote = _fetch_with_timeout(get_quote, symbol, label=f"quote:{symbol}") or {}

        # ── Step 4: Format data blob ─────────────────────────────
        data_blob = self._build_data_blob(symbol, quote, metrics, portfolio_context, context or {})

        # ── Step 5: Query Gemini ──────────────────────────────────
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(data=data_blob)
        report = self.query_json(prompt)

        report["raw_risk_metrics"] = metrics
        report["agent"] = self.name
        report["symbol"] = symbol

        self._log(f"Risk analysis complete. Level: {report.get('risk_level', '?')} Score: {report.get('risk_score', 0)}")
        return report


    def _calculate_risk_metrics(
        self, stock_df: pd.DataFrame, index_df: Optional[pd.DataFrame]
    ) -> Dict[str, Any]:
        """Compute annualized volatility, max drawdown, and beta against KSE-100 index."""
        metrics = {
            "volatility_annualized": 0.0,
            "max_drawdown": 0.0,
            "beta": 1.0,  # Default fallback
            "sharpe_ratio_approx": 0.0
        }

        try:
            # 1. Volatility
            stock_close = stock_df["Close"]
            stock_returns = stock_close.pct_change().dropna()
            if len(stock_returns) > 0:
                daily_vol = stock_returns.std()
                metrics["volatility_annualized"] = float(daily_vol * math.sqrt(252))

            # 2. Maximum Drawdown
            roll_max = stock_close.cummax()
            drawdowns = (stock_close - roll_max) / roll_max
            metrics["max_drawdown"] = float(drawdowns.min())

            # 3. Beta calculation
            if index_df is not None and not index_df.empty:
                # Align dates
                combined = pd.DataFrame({
                    "stock": stock_close,
                    "index": index_df["Close"]
                }).dropna().pct_change().dropna()

                if len(combined) > 10:
                    cov_matrix = np.cov(combined["stock"], combined["index"])
                    market_variance = cov_matrix[1, 1]
                    if market_variance > 0:
                        metrics["beta"] = float(cov_matrix[0, 1] / market_variance)

            # 4. Approximate Sharpe Ratio (assuming risk-free rate of 12% for PKR region)
            rf_rate_daily = 0.12 / 252
            excess_returns = stock_returns - rf_rate_daily
            if len(stock_returns) > 0 and stock_returns.std() > 0:
                metrics["sharpe_ratio_approx"] = float(excess_returns.mean() / stock_returns.std() * math.sqrt(252))

        except Exception as e:
            logger.error(f"Error calculating risk metrics: {e}")

        return metrics

    def _build_data_blob(
        self,
        symbol: str,
        quote: Dict[str, Any],
        metrics: Dict[str, Any],
        portfolio_context: Optional[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Compose a human-readable risk description block for the Gemini prompt."""
        adv = metrics.get("advanced", {})

        lines = [
            f"SYMBOL: {symbol}",
            f"CURRENT PRICE: {quote.get('price', 'N/A')} PKR",
            "",
            "══ CLASSIC RISK METRICS (Computed — do NOT recompute) ══",
            f"  Annual Volatility      : {metrics['volatility_annualized']:.2%}",
            f"  Maximum Drawdown       : {metrics['max_drawdown']:.2%}",
            f"  Beta vs KSE-100        : {metrics['beta']:.3f}",
            f"  Sharpe Ratio (Rf=12%)  : {metrics['sharpe_ratio_approx']:.3f}",
            "",
        ]

        # ── CVaR ──────────────────────────────────────────────────
        cvar = adv.get("cvar", {})
        if cvar and not cvar.get("error"):
            lines += [
                "══ TAIL RISK — CVaR / Expected Shortfall (Computed) ══",
                f"  {cvar.get('interpretation', 'N/A')}",
                "",
            ]

        # ── Higher-Order Moments ──────────────────────────────────
        hom = adv.get("higher_moments", {})
        if hom and not hom.get("error") and hom.get("skewness") is not None:
            non_normal = "YES — returns are non-Gaussian" if hom.get("is_non_normal") else "No — returns approximately Gaussian"
            lines += [
                "══ DISTRIBUTION MOMENTS (Computed) ══",
                f"  Skewness            : {hom['skewness']}",
                f"  Excess Kurtosis     : {hom['excess_kurtosis']}",
                f"  Jarque-Bera Stat    : {hom['jarque_bera_stat']}",
                f"  Non-Normal Returns  : {non_normal}",
                f"  Interpretation      : {hom.get('interpretation', 'N/A')}",
                "",
            ]

        # ── Ledoit-Wolf Shrinkage ─────────────────────────────────
        lw = adv.get("ledoit_wolf", {})
        if lw and lw.get("shrinkage_coefficient") is not None:
            lines += [
                "══ COVARIANCE ESTIMATION — Ledoit-Wolf Shrinkage (Computed) ══",
                f"  Shrinkage coefficient (δ): {lw['shrinkage_coefficient']}",
                f"  Assets               : {lw.get('asset_names', ['stock', 'KSE-100'])}",
                f"  Interpretation       : {lw.get('interpretation', 'N/A')}",
                "",
            ]
        elif lw and lw.get("interpretation"):
            lines += [
                "══ COVARIANCE ESTIMATION — Ledoit-Wolf ══",
                f"  {lw['interpretation']}",
                "",
            ]

        # ── Black-Litterman ───────────────────────────────────────
        bl = adv.get("black_litterman", {})
        if bl and bl.get("posterior_mu"):
            pm = bl["posterior_mu"]
            pi = bl["implied_equilibrium_mu"]
            pw = bl["posterior_weights"]
            lines += [
                "══ BLACK-LITTERMAN POSTERIOR RETURNS (Computed) ══",
                f"  Implied Equilibrium μ (stock, index) : {pi}",
                f"  Posterior μ after views (stock, index): {pm}",
                f"  Optimal posterior weights             : {pw}",
                f"  Interpretation                        : {bl.get('interpretation', 'N/A')}",
                "",
            ]
        elif bl and bl.get("interpretation"):
            lines += [
                "══ BLACK-LITTERMAN ══",
                f"  {bl['interpretation']}",
                "",
            ]

        # ── Adler-Dumas FX Exposure ───────────────────────────────
        ad = adv.get("adler_dumas", {})
        if ad and ad.get("gamma_fx") is not None:
            lines += [
                "══ CURRENCY EXPOSURE — Adler-Dumas (PKR/USD) ══",
                f"  Alpha (excess return)   : {ad['alpha']}%/day",
                f"  Beta (market factor)    : {ad['beta_market']}",
                f"  Gamma (FX exposure γ)   : {ad['gamma_fx']}  ← KEY CURRENCY RISK METRIC",
                f"  R² (regression fit)     : {ad['r_squared']}",
                f"  Observations used       : {ad['n_observations']} trading days",
                f"  Interpretation          : {ad.get('interpretation', 'N/A')}",
                "",
            ]
        elif ad and ad.get("interpretation"):
            lines += [
                "══ CURRENCY EXPOSURE — Adler-Dumas ══",
                f"  {ad['interpretation']}",
                "",
            ]

        # ── Portfolio context ─────────────────────────────────────
        if portfolio_context:
            lines.extend([
                "══ USER PORTFOLIO CONTEXT ══",
                f"  Owns Stock                  : {portfolio_context.get('owns_stock', False)}",
                f"  Shares Owned                : {portfolio_context.get('shares', 0.0)}",
                f"  Average Acquisition Cost    : {portfolio_context.get('avg_cost', 0.0)}",
                f"  Current Holding Value       : {portfolio_context.get('current_value', 0.0)}",
                f"  Portfolio Concentration     : {portfolio_context.get('portfolio_pct', 0.0):.2f}%",
                f"  Concentration Danger (>15%) : {portfolio_context.get('is_concentrated', False)}",
                ""
            ])
        else:
            lines.extend([
                "══ USER PORTFOLIO CONTEXT ══",
                "  No current portfolio holdings context provided. Evaluate stock risk in isolation.",
                ""
            ])

        # ── Pakistan macro & sector context ──────────────────────
        if context:
            from data.local_data import format_market_context_text
            ctx_text = format_market_context_text(context.get("market_context", {}))
            if ctx_text:
                lines.extend(["══ PAKISTAN MACRO & SECTOR CONTEXT ══", ctx_text, ""])

        return "\n".join(lines)

    @staticmethod
    def _error_report(symbol: str, reason: str) -> Dict[str, Any]:
        """Return a minimal error report when analysis cannot proceed."""
        return {
            "error": True,
            "agent": "Risk Analyst",
            "symbol": symbol,
            "risk_level": "unknown",
            "risk_score": 0,
            "risk_factors": [{"factor": "Data access failed", "severity": "high", "detail": reason}],
            "volatility_assessment": "unknown",
            "max_position_pct": 0.0,
            "stop_loss_pct": 0.0,
            "key_risks": [reason],
            "mitigants": [],
            "confidence": 0,
            "summary": f"Risk analysis unavailable: {reason}",
        }
