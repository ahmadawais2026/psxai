"""
Fundamentals Analyst Agent — Specialist in company financial statements and valuation ratios.

Fetches fundamental data and financial statements, offloads calculation to the data layer,
and passes structured financial metrics to Gemini for qualitative valuation.
"""

from __future__ import annotations

import json
import traceback
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.prompts import ANALYSIS_PROMPT_TEMPLATE, FUNDAMENTALS_ANALYST_PERSONA
from data.market_data import get_fundamentals, get_financial_statements, get_quote


class FundamentalsAnalystAgent(BaseAgent):
    """Interprets company financial statements and key valuation metrics for a PSX stock."""

    def __init__(self) -> None:
        super().__init__(
            name="Fundamentals Analyst",
            persona=FUNDAMENTALS_ANALYST_PERSONA,
        )

    def analyze(self, symbol: str) -> Dict[str, Any]:
        """Run a full fundamental analysis for *symbol*.

        Pipeline:
        1. Fetch fundamentals (P/E, P/B, ROE, debt/equity, dividend yield).
        2. Fetch key financial statements (balance sheet, income, cash flow summaries).
        3. Format data block for Gemini.
        4. Query Gemini and obtain structured JSON report.
        """
        self._log(f"Starting fundamental analysis for {symbol} …")

        # ── Step 1: Fetch fundamentals ────────────────────────────
        try:
            fundamentals = get_fundamentals(symbol)
            if not fundamentals:
                return self._error_report(symbol, "Fundamentals data unavailable.")
            self._log("Fetched fundamentals data.")
        except Exception as exc:
            self._log(f"Fundamentals fetch failed: {exc}")
            return self._error_report(symbol, f"Data fetch error: {exc}")

        # ── Step 2: Fetch financial statements ────────────────────
        try:
            financials = get_financial_statements(symbol) or {}
            self._log("Fetched financial statements.")
        except Exception as exc:
            self._log(f"Financial statements fetch failed: {exc}")
            financials = {}

        # ── Step 3: Fetch current quote ───────────────────────────
        try:
            quote = get_quote(symbol) or {}
        except Exception:
            quote = {}

        # ── Step 4: Compose data blob ─────────────────────────────
        data_blob = self._build_data_blob(symbol, quote, fundamentals, financials)

        # ── Step 5: Query Gemini ──────────────────────────────────
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(data=data_blob)
        report = self.query_json(prompt)

        # Attach raw fundamentals for downstream committee review
        report["raw_fundamentals"] = fundamentals
        report["raw_financials"] = financials
        report["agent"] = self.name
        report["symbol"] = symbol

        self._log(f"Fundamental analysis complete. Valuation verdict: {report.get('valuation_verdict', '?')}")
        return report

    def _build_data_blob(
        self,
        symbol: str,
        quote: Dict[str, Any],
        fundamentals: Dict[str, Any],
        financials: Dict[str, Any]
    ) -> str:
        """Format fundamental data into a readable text block."""
        lines = [
            f"SYMBOL: {symbol}",
            f"COMPANY NAME: {fundamentals.get('name', symbol)}",
            f"SECTOR: {fundamentals.get('sector', 'N/A')}",
            f"CURRENT PRICE: {quote.get('price', 'N/A')}",
            f"MARKET CAP: {fundamentals.get('market_cap', 'N/A')}",
            "",
            "── KEY VALUATION & EFFICIENCY METRICS ──",
            f"  Trailing P/E: {fundamentals.get('pe_ratio', 'N/A')}",
            f"  Price to Book (P/B): {fundamentals.get('pb_ratio', 'N/A')}",
            f"  Return on Equity (ROE): {fundamentals.get('roe', 'N/A')}",
            f"  Earnings Per Share (EPS): {fundamentals.get('eps', 'N/A')}",
            f"  Dividend Yield: {fundamentals.get('dividend_yield', 'N/A')}",
            f"  Debt to Equity: {fundamentals.get('debt_equity', 'N/A')}",
            f"  Beta: {fundamentals.get('beta', 'N/A')}",
            "",
            "── INCOME STATEMENT HIGHLIGHTS ──",
            f"  Revenue (TTM): {financials.get('revenue', 'N/A')}",
            f"  Net Income (TTM): {financials.get('net_income', 'N/A')}",
            f"  Operating Margin: {financials.get('operating_margin', 'N/A')}",
            f"  Net Margin: {financials.get('net_margin', 'N/A')}",
            "",
            "── BALANCE SHEET HIGHLIGHTS ──",
            f"  Total Assets: {financials.get('total_assets', 'N/A')}",
            f"  Total Liabilities: {financials.get('total_liabilities', 'N/A')}",
            f"  Cash and Cash Equivalents: {financials.get('cash', 'N/A')}",
            "",
            "── CASH FLOW HIGHLIGHTS ──",
            f"  Operating Cash Flow: {financials.get('operating_cash_flow', 'N/A')}",
            f"  Free Cash Flow: {financials.get('free_cash_flow', 'N/A')}",
        ]
        
        return "\n".join(lines)

    @staticmethod
    def _error_report(symbol: str, reason: str) -> Dict[str, Any]:
        """Return a minimal error report when analysis cannot proceed."""
        return {
            "error": True,
            "agent": "Fundamentals Analyst",
            "symbol": symbol,
            "valuation_verdict": "unknown",
            "financial_health": "unknown",
            "growth_outlook": "unknown",
            "moat": "none",
            "strengths": [],
            "concerns": [reason],
            "fair_value_range": {"low": 0.0, "high": 0.0},
            "confidence": 0,
            "summary": f"Fundamental analysis unavailable: {reason}",
        }
