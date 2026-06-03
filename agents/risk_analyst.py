"""
Risk Analyst Agent — Specialist in portfolio exposure control, volatility, and downside risk.

Offloads mathematical computations (drawdown, beta, volatility) to NumPy/Pandas,
and sends the risk metrics to Gemini for qualitative assessment of macro and corporate risk.
"""

from __future__ import annotations

import json
import logging
import math
import numpy as np
import pandas as pd
import yfinance as yf
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.prompts import ANALYSIS_PROMPT_TEMPLATE, RISK_ANALYST_PERSONA
from config import HISTORY_PERIOD_DAILY, PSX_SUFFIX
from data.market_data import get_history, get_quote

logger = logging.getLogger(__name__)


class RiskAnalystAgent(BaseAgent):
    """Calculates quantitative risk metrics and analyzes qualitative risk factors for a PSX stock."""

    def __init__(self) -> None:
        super().__init__(
            name="Risk Analyst",
            persona=RISK_ANALYST_PERSONA,
        )

    def analyze(self, symbol: str, portfolio_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run a full risk analysis for *symbol*.

        Pipeline:
        1. Fetch price history of stock and KSE-100 index.
        2. Compute beta, annualized volatility, and maximum drawdown.
        3. Compose data block incorporating portfolio context (if provided).
        4. Query Gemini to assess macro risk and recommend position limits.
        """
        self._log(f"Starting risk analysis for {symbol} …")

        # ── Step 1: Fetch histories ──────────────────────────────
        try:
            stock_df = get_history(symbol, period=HISTORY_PERIOD_DAILY, interval="1d")
            if stock_df is None or stock_df.empty:
                return self._error_report(symbol, "No historical data to compute risk metrics.")
            self._log(f"Fetched stock history ({len(stock_df)} bars).")
        except Exception as exc:
            self._log(f"Stock history fetch failed: {exc}")
            return self._error_report(symbol, f"Data fetch error: {exc}")

        # Fetch index history for beta calculation
        index_df = None
        for index_symbol in ["^KSE", "KSE100.KA"]:
            try:
                index_df = get_history(index_symbol, period=HISTORY_PERIOD_DAILY, interval="1d")
                if index_df is not None and not index_df.empty:
                    self._log(f"Fetched index history from {index_symbol}.")
                    break
            except Exception as exc:
                self._log(f"Index fetch from {index_symbol} failed: {exc}")

        # ── Step 2: Compute quantitative risk metrics ────────────
        metrics = self._calculate_risk_metrics(stock_df, index_df)

        # ── Step 3: Fetch current quote ───────────────────────────
        try:
            quote = get_quote(symbol) or {}
        except Exception:
            quote = {}

        # ── Step 4: Format data blob ─────────────────────────────
        data_blob = self._build_data_blob(symbol, quote, metrics, portfolio_context)

        # ── Step 5: Query Gemini ──────────────────────────────────
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(data=data_blob)
        report = self.query_json(prompt)

        # Attach computed risk metrics
        report["raw_risk_metrics"] = metrics
        report["agent"] = self.name
        report["symbol"] = symbol

        self._log(f"Risk analysis complete. Risk level: {report.get('risk_level', '?')} (Score: {report.get('risk_score', 0)})")
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
        portfolio_context: Optional[Dict[str, Any]]
    ) -> str:
        """Compose a human-readable risk description block."""
        lines = [
            f"SYMBOL: {symbol}",
            f"CURRENT PRICE: {quote.get('price', 'N/A')}",
            "",
            "── QUANTITATIVE RISK METRICS (Computed) ──",
            f"  Annual Volatility: {metrics['volatility_annualized']:.2%} (standard deviation of daily returns, annualized)",
            f"  Maximum Drawdown: {metrics['max_drawdown']:.2%} (worst peak-to-trough drop in historical period)",
            f"  Beta against KSE-100: {metrics['beta']:.2f} (sensitivity relative to the benchmark index)",
            f"  Approx. Sharpe Ratio: {metrics['sharpe_ratio_approx']:.2f} (risk-adjusted returns assuming 12% Rf rate)",
            ""
        ]

        if portfolio_context:
            lines.extend([
                "── USER PORTFOLIO CONTEXT ──",
                f"  Owns Stock: {portfolio_context.get('owns_stock', False)}",
                f"  Shares Owned: {portfolio_context.get('shares', 0.0)}",
                f"  Average Acquisition Cost: {portfolio_context.get('avg_cost', 0.0)}",
                f"  Current Holding Value: {portfolio_context.get('current_value', 0.0)}",
                f"  Portfolio Concentration Percentage: {portfolio_context.get('portfolio_pct', 0.0):.2f}%",
                f"  Concentration Danger (>15% limit): {portfolio_context.get('is_concentrated', False)}",
                ""
            ])
        else:
            lines.extend([
                "── USER PORTFOLIO CONTEXT ──",
                "  No current portfolio holdings context provided. Evaluate stock risk in isolation.",
                ""
            ])

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
