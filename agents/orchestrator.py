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


from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated, Dict, Any, Optional
import operator

class FinancialConsensusState(TypedDict):
    symbol: str
    model_name: Optional[str]
    user_context: Optional[Dict[str, Any]]
    agent_context: Dict[str, Any]
    technical_report: Optional[Dict[str, Any]]
    fundamental_report: Optional[Dict[str, Any]]
    sentiment_report: Optional[Dict[str, Any]]
    risk_report: Optional[Dict[str, Any]]
    debate_result: Optional[Dict[str, Any]]
    recommendation: Optional[Dict[str, Any]]


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
        self.graph = self._build_graph()

    def _build_graph(self):
        """Build the LangGraph parallel execution graph."""
        workflow = StateGraph(FinancialConsensusState)

        def node_technical(state: FinancialConsensusState):
            logger.info(f"LangGraph: Running Technical Analyst for {state['symbol']}...")
            rep = self.technical_analyst.analyze(state["symbol"], context=state["agent_context"])
            return {"technical_report": rep}

        def node_fundamental(state: FinancialConsensusState):
            logger.info(f"LangGraph: Running Fundamental Analyst for {state['symbol']}...")
            rep = self.fundamentals_analyst.analyze(state["symbol"], context=state["agent_context"])
            return {"fundamental_report": rep}

        def node_sentiment(state: FinancialConsensusState):
            logger.info(f"LangGraph: Running Sentiment Analyst for {state['symbol']}...")
            rep = self.sentiment_analyst.analyze(state["symbol"], context=state["agent_context"])
            return {"sentiment_report": rep}

        def node_risk(state: FinancialConsensusState):
            logger.info(f"LangGraph: Running Risk Analyst for {state['symbol']}...")
            # Use the general context (user_context is the general one here)
            rep = self.risk_analyst.analyze(state["symbol"], portfolio_context=state["user_context"], context=state["agent_context"])
            return {"risk_report": rep}

        def node_debate(state: FinancialConsensusState):
            logger.info(f"LangGraph: Running Research Team Debate for {state['symbol']}...")
            reports = {
                "technical": state["technical_report"],
                "fundamental": state["fundamental_report"],
                "sentiment": state["sentiment_report"],
                "risk": state["risk_report"]
            }
            res = self.research_team.debate(reports, rounds=2)
            return {"debate_result": res}

        def node_portfolio(state: FinancialConsensusState):
            logger.info(f"LangGraph: Running Portfolio Manager for {state['symbol']}...")
            reports = {
                "technical": state["technical_report"],
                "fundamental": state["fundamental_report"],
                "sentiment": state["sentiment_report"],
                "risk": state["risk_report"]
            }
            rec = self.portfolio_manager.generate_recommendation(
                symbol=state["symbol"],
                analyst_reports=reports,
                debate_result=state["debate_result"],
                user_context=state["user_context"]
            )
            return {"recommendation": rec}

        workflow.add_node("technical", node_technical)
        workflow.add_node("fundamental", node_fundamental)
        workflow.add_node("sentiment", node_sentiment)
        workflow.add_node("risk", node_risk)
        workflow.add_node("debate", node_debate)
        workflow.add_node("portfolio", node_portfolio)

        # Parallel fan-out
        workflow.add_edge(START, "technical")
        workflow.add_edge(START, "fundamental")
        workflow.add_edge(START, "sentiment")
        workflow.add_edge(START, "risk")

        # Fan-in to debate
        workflow.add_edge(["technical", "fundamental", "sentiment", "risk"], "debate")

        # Debate to Portfolio
        workflow.add_edge("debate", "portfolio")
        workflow.add_edge("portfolio", END)

        return workflow.compile()

    def analyze(self, symbol: str, user_context: Optional[Dict[str, Any]] = None, model_name: Optional[str] = None) -> Dict[str, Any]:
        """Backward compatible non-streaming wrapper."""
        final_report = None
        for event in self.analyze_stream(symbol, user_context, model_name):
            if event.get("event") == "complete":
                final_report = event.get("report")
        if not final_report:
            raise ValueError("Pipeline did not complete successfully.")
        return final_report

    def analyze_stream(self, symbol: str, user_context: Optional[Dict[str, Any]] = None, model_name: Optional[str] = None):
        """
        Run the complete multi-agent pipeline using LangGraph, yielding status updates as SSE dicts.
        """
        symbol = symbol.strip().upper()
        
        # Set the model_name on the analysts dynamically
        if model_name:
            self.technical_analyst.model_name = model_name
            self.fundamentals_analyst.model_name = model_name
            self.sentiment_analyst.model_name = model_name
            self.risk_analyst.model_name = model_name
            self.research_team.model_name = model_name
            self.portfolio_manager.model_name = model_name
        
        # ── Cache Check ───────────────────────────────────────────
        from data.cache import get_cached, set_cached
        cache_key = f"analysis:{symbol}:{model_name}" if model_name else f"analysis:{symbol}"
        cached_report = get_cached(cache_key, ttl_seconds=3600)  # 1 hour cache
        
        has_active_holdings = (
            user_context is not None 
            and user_context.get("owns_stock", False) 
            and user_context.get("shares", 0.0) > 0.0
        )
        
        if cached_report:
            logger.info(f"Cache hit for analysis of symbol: {symbol}")
            yield {"event": "cache_hit"}
            
            if not has_active_holdings:
                yield {"event": "complete", "report": cached_report}
                return
            else:
                logger.info(f"Re-running Portfolio Manager for {symbol} with user context...")
                yield {"event": "node_finish", "node": "portfolio_custom"}
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
                yield {"event": "complete", "report": report_copy}
                return

        # ── Cache Miss: Run Full Pipeline ────────────────────────
        logger.info(f"Cache miss for analysis of symbol: {symbol}. Running full pipeline...")
        yield {"event": "start"}

        quote = get_quote(symbol)
        if not quote or "error" in quote:
            err_msg = quote.get("error", "Unknown error") if quote else "No response from data layer"
            yield {"event": "error", "message": f"Ticker symbol '{symbol}' could not be resolved on PSX. Error: {err_msg}"}
            return

        # ── Step 1: Validate Ticker & Retrieve metadata ───────────
        company_name = symbol
        sector = "Unknown"

        if symbol in PSX_TICKERS:
            ticker_info = PSX_TICKERS[symbol]
            company_name = ticker_info.get("name", symbol)
            sector = ticker_info.get("sector", "Unknown")
        else:
            company_name = quote.get("name", symbol)
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
        yield {"event": "data_fetch", "status": "working"}
        try:
            from data.live_scraper import fetch_company_news_live, refresh_market_news
            fetch_company_news_live(symbol)
            refresh_market_news()
        except Exception as e:
            logger.warning(f"Live news pre-fetch failed (non-fatal): {e}")

        agent_context = {
            "sector": sector,
            "company_name": company_name,
            "market_context": get_market_context(sector=sector),
            "financials_text": get_financials_text(symbol),
            "research_reports": get_research_reports(symbol, sector=sector, max_reports=5),
            "company_news": get_local_company_news(symbol),
        }
        
        yield {"event": "data_fetch", "status": "done"}

        general_context = {
            "owns_stock": False,
            "shares": 0.0,
            "avg_cost": 0.0,
            "current_value": 0.0,
            "portfolio_pct": 0.0,
            "is_concentrated": False
        }

        # ── Step 3: Run LangGraph Pipeline ────────────────────────
        initial_state = {
            "symbol": symbol,
            "model_name": model_name,
            "user_context": general_context,
            "agent_context": agent_context,
            "technical_report": None,
            "fundamental_report": None,
            "sentiment_report": None,
            "risk_report": None,
            "debate_result": None,
            "recommendation": None
        }

        current_state = initial_state.copy()

        # Stream events from LangGraph
        for step_event in self.graph.stream(initial_state, {"recursion_limit": 50}):
            for node_name, updates in step_event.items():
                yield {"event": "node_finish", "node": node_name}
                current_state.update(updates)

        # ── Step 6: Compile overall general dossier ───────────────
        debate_res = current_state.get("debate_result", {})
        
        general_report = {
            "symbol": symbol,
            "company_name": company_name,
            "sector": sector,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "quote": quote,
            "technical_report": current_state.get("technical_report"),
            "fundamental_report": current_state.get("fundamental_report"),
            "sentiment_report": current_state.get("sentiment_report"),
            "risk_report": current_state.get("risk_report"),
            "debate": {
                "bull_thesis": debate_res.get("bull_thesis", ""),
                "bull_arguments": debate_res.get("bull_arguments", []),
                "bear_thesis": debate_res.get("bear_thesis", ""),
                "bear_arguments": debate_res.get("bear_arguments", []),
                "agreements": debate_res.get("agreements", []),
                "disagreements": debate_res.get("disagreements", [])
            },
            "recommendation": current_state.get("recommendation"),
            "disclaimer": DISCLAIMER
        }
        
        set_cached(cache_key, general_report)
        logger.info(f"General report cached for symbol: {symbol}")
        
        if has_active_holdings:
            logger.info(f"Generating custom recommendation for user context...")
            yield {"event": "node_finish", "node": "portfolio_custom"}
            analyst_reports = {
                "technical": general_report["technical_report"],
                "fundamental": general_report["fundamental_report"],
                "sentiment": general_report["sentiment_report"],
                "risk": general_report["risk_report"]
            }
            final_recommendation = self.portfolio_manager.generate_recommendation(
                symbol=symbol,
                analyst_reports=analyst_reports,
                debate_result=debate_res,
                user_context=user_context
            )
            custom_report = dict(general_report)
            custom_report["recommendation"] = final_recommendation
            yield {"event": "complete", "report": custom_report}
        else:
            yield {"event": "complete", "report": general_report}

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
