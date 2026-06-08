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

from agents.technical_analyst import TechnicalAnalystAgent
from agents.fundamentals_analyst import FundamentalsAnalystAgent
from agents.sentiment_analyst import SentimentAnalystAgent
from agents.risk_analyst import RiskAnalystAgent
from agents.research_team import ResearchTeam
from agents.portfolio_manager import PortfolioManagerAgent
from agents.prompts import DISCLAIMER
from data.local_data import get_market_context, get_financials_text, get_research_reports, get_local_company_news

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
        
        # ── Cache Check ───────────────────────────────────────────
        from data.cache import get_cached, set_cached
        cache_key = f"analysis:{symbol}"
        cached_report = get_cached(cache_key, ttl_seconds=3600)  # 1 hour cache
        
        # Determine if user has active holdings context to inject
        has_active_holdings = (
            user_context is not None 
            and user_context.get("owns_stock", False) 
            and user_context.get("shares", 0.0) > 0.0
        )
        
        if cached_report:
            logger.info(f"Cache hit for analysis of symbol: {symbol}")
            if not has_active_holdings:
                # No custom portfolio context needed, return cached report directly
                return cached_report
            else:
                # Re-run only the Portfolio Manager using the cached reports to inject user context
                logger.info(f"Re-running Portfolio Manager for {symbol} with user context...")
                analyst_reports = {
                    "technical": cached_report["technical_report"],
                    "fundamental": cached_report["fundamental_report"],
                    "sentiment": cached_report["sentiment_report"],
                    "risk": cached_report["risk_report"]
                }
                final_recommendation = self.portfolio_manager.generate_recommendation(
                    symbol=symbol,
                    analyst_reports=analyst_reports,
                    debate_result=cached_report["debate"],
                    user_context=user_context
                )
                report_copy = dict(cached_report)
                report_copy["recommendation"] = final_recommendation
                report_copy["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                return report_copy

        # ── Cache Miss: Run Full Pipeline ────────────────────────
        logger.info(f"Cache miss for analysis of symbol: {symbol}. Running full pipeline...")

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

        # ── Step 2: Pre-fetch live data & build shared context ────
        # Fetch fresh company news from AskAnalyst (non-blocking — failure is OK)
        try:
            from data.live_scraper import fetch_company_news_live, refresh_market_news
            logger.info(f"Fetching live company news for {symbol}...")
            fetch_company_news_live(symbol)
            logger.info("Refreshing general market news...")
            refresh_market_news()
        except Exception as e:
            logger.warning(f"Live news pre-fetch failed (non-fatal): {e}")

        # Build the rich context bundle passed to every agent
        agent_context = {
            "sector": sector,
            "company_name": company_name,
            "market_context": get_market_context(sector=sector),
            "financials_text": get_financials_text(symbol),
            "research_reports": get_research_reports(symbol, sector=sector, max_reports=5),
            "company_news": get_local_company_news(symbol),
        }

        # To build a clean general cache entry, we run the analysts with empty portfolio context
        general_context = {
            "owns_stock": False,
            "shares": 0.0,
            "avg_cost": 0.0,
            "current_value": 0.0,
            "portfolio_pct": 0.0,
            "is_concentrated": False
        }

        # ── Step 3: Run Analyst Agents (Sequential due to rate limits) ──
        # Technical Analyst
        tech_report = self.technical_analyst.analyze(symbol, context=agent_context)

        # Fundamental Analyst
        fund_report = self.fundamentals_analyst.analyze(symbol, context=agent_context)

        # Sentiment Analyst
        sent_report = self.sentiment_analyst.analyze(symbol, context=agent_context)

        # Risk Analyst with general context
        risk_report = self.risk_analyst.analyze(symbol, portfolio_context=general_context, context=agent_context)
        
        # Compile analyst reports dictionary
        analyst_reports = {
            "technical": tech_report,
            "fundamental": fund_report,
            "sentiment": sent_report,
            "risk": risk_report
        }
        
        # ── Step 4: Run Researcher Committee Debate ──────────────
        debate_result = self.research_team.debate(analyst_reports, rounds=2)
        
        # ── Step 5: Run Portfolio Manager with general context ──
        general_recommendation = self.portfolio_manager.generate_recommendation(
            symbol=symbol,
            analyst_reports=analyst_reports,
            debate_result=debate_result,
            user_context=general_context
        )
        
        # ── Step 6: Compile overall general dossier ───────────────
        general_report = {
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
            "recommendation": general_recommendation,
            "disclaimer": DISCLAIMER
        }
        
        # Save to cache
        set_cached(cache_key, general_report)
        logger.info(f"General report cached for symbol: {symbol}")
        
        # If user has active holdings, generate recommendation for their custom context and return it
        if has_active_holdings:
            logger.info(f"Generating custom recommendation for user context...")
            final_recommendation = self.portfolio_manager.generate_recommendation(
                symbol=symbol,
                analyst_reports=analyst_reports,
                debate_result=debate_result,
                user_context=user_context
            )
            custom_report = dict(general_report)
            custom_report["recommendation"] = final_recommendation
            return custom_report
            
        logger.info(f"Orchestrator pipeline complete for symbol: {symbol}")
        return general_report

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
