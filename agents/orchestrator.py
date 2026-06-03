"""
Orchestrator Agent — Core controller and sequencer of the Multi-Agent Investment Advisor.

Validates the ticker, coordinates the parallel analysis passes (Tech, Fundamental,
Sentiment, Risk), sequences the dialectical debate, and prompts the Portfolio Manager
for the final recommendation dossier.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from data.psx_tickers import PSX_TICKERS
from data.market_data import get_quote
from portfolio.manager import PortfolioManager

from agents.technical_analyst import TechnicalAnalystAgent
from agents.fundamentals_analyst import FundamentalsAnalystAgent
from agents.sentiment_analyst import SentimentAnalystAgent
from agents.risk_analyst import RiskAnalystAgent
from agents.research_team import ResearchTeam
from agents.portfolio_manager import PortfolioManagerAgent
from agents.prompts import DISCLAIMER

logger = logging.getLogger(__name__)


class Orchestrator:
    """Core coordinator of the multi-agent analysis pipeline."""

    def __init__(self) -> None:
        logger.info("Initializing Orchestrator Agent and analyst pool...")
        self.technical_analyst = TechnicalAnalystAgent()
        self.fundamentals_analyst = FundamentalsAnalystAgent()
        self.sentiment_analyst = SentimentAnalystAgent()
        self.risk_analyst = RiskAnalystAgent()
        self.research_team = ResearchTeam()
        self.portfolio_manager = PortfolioManagerAgent()
        self.portfolio_db = PortfolioManager()

    def analyze(self, symbol: str, user_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Run the complete multi-agent analysis pipeline for a ticker.
        
        Args:
            symbol: Ticker symbol (e.g. 'OGDC')
            user_context: Optional manual override for user portfolio details.
            
        Returns:
            Dict: Comprehensive advisory report.
        """
        symbol = symbol.strip().upper()
        logger.info(f"Orchestrator pipeline started for symbol: {symbol}")
        
        # Fetch current quote first to validate the ticker
        quote = get_quote(symbol)
        if not quote or "error" in quote:
            err_msg = quote.get("error", "Unknown error") if quote else "No response from data layer"
            raise ValueError(f"Ticker symbol '{symbol}' could not be resolved on PSX. Error: {err_msg}")

        # ── Step 1: Validate Ticker & Retrieve metadata ───────────
        company_name = symbol
        sector = "Unknown"
        
        # Look up in curated PSX tickers
        if symbol in PSX_TICKERS:
            ticker_info = PSX_TICKERS[symbol]
            company_name = ticker_info.get("name", symbol)
            sector = ticker_info.get("sector", "Unknown")
        else:
            logger.warning(f"Symbol {symbol} not found in curated PSX tickers list. Fetching dynamically...")
            company_name = quote.get("name", symbol)
            # Clean up comma-separated yfinance fund names if present
            if "," in company_name and (".KA" in company_name or symbol in company_name):
                company_name = company_name.split(",")[0].replace(".KA", "").strip()
            
            try:
                from data.market_data import get_fundamentals
                fund = get_fundamentals(symbol)
                sector = fund.get("sector", "Unknown")
                if sector == "N/A":
                    sector = "Unknown"
            except Exception:
                sector = "Unknown"
            
        # ── Step 2: Resolve Portfolio Context ─────────────────────
        # If user_context wasn't passed directly (e.g. by API override), read from sqlite
        if user_context is None:
            user_context = self.portfolio_db.get_position_context(symbol)
            
        # ── Step 3: Run Analyst Agents (Sequential due to rate limits) ──
        # Technical Analyst
        tech_report = self.technical_analyst.analyze(symbol)
        
        # Fundamental Analyst
        fund_report = self.fundamentals_analyst.analyze(symbol)
        
        # Sentiment Analyst
        sent_report = self.sentiment_analyst.analyze(symbol)
        
        # Risk Analyst
        risk_report = self.risk_analyst.analyze(symbol, portfolio_context=user_context)
        
        # Compile analyst reports dictionary
        analyst_reports = {
            "technical": tech_report,
            "fundamental": fund_report,
            "sentiment": sent_report,
            "risk": risk_report
        }
        
        # ── Step 4: Run Researcher Committee Debate ──────────────
        debate_result = self.research_team.debate(analyst_reports, rounds=2)
        
        # ── Step 5: Run Portfolio Manager for final verdict ──────
        final_recommendation = self.portfolio_manager.generate_recommendation(
            symbol=symbol,
            analyst_reports=analyst_reports,
            debate_result=debate_result,
            user_context=user_context
        )
        
        # ── Step 6: Compile overall dossier ───────────────────────
        report = {
            "symbol": symbol,
            "company_name": company_name,
            "sector": sector,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "quote": quote,
            "technical_report": tech_report,
            "fundamental_report": fund_report,
            "sentiment_report": sent_report,
            "risk_report": risk_report,
            "debate": {
                "bull_thesis": debate_result.get("bull_thesis", ""),
                "bull_arguments": debate_result.get("bull_arguments", []),
                "bear_thesis": debate_result.get("bear_thesis", ""),
                "bear_arguments": debate_result.get("bear_arguments", []),
                "agreements": debate_result.get("agreements", []),
                "disagreements": debate_result.get("disagreements", [])
            },
            "recommendation": final_recommendation,
            "disclaimer": DISCLAIMER
        }
        
        logger.info(f"Orchestrator pipeline complete for symbol: {symbol}")
        return report

    def quick_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetch quick stock quote information."""
        symbol = symbol.strip().upper()
        quote = get_quote(symbol)
        if not quote:
            return {"error": True, "message": f"Quote for {symbol} unavailable."}
            
        company_name = symbol
        if symbol in PSX_TICKERS:
            company_name = PSX_TICKERS[symbol].get("name", symbol)
            
        return {
            "symbol": symbol,
            "company_name": company_name,
            "price": quote.get("price", 0.0),
            "change": quote.get("change", 0.0),
            "change_pct": quote.get("change_pct", 0.0),
            "volume": quote.get("volume", 0),
            "market_cap": quote.get("market_cap", 0)
        }
