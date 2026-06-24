"""
agents/business_analyst.py
═══════════════════════════════════════════════════════════════════════
Business Analyst Agent — Deep business intelligence via Google Search grounding.

Runs in parallel with Technical, Fundamental, Sentiment, and Risk analysts.
Focuses exclusively on WHAT the company does, its products/services, revenue
segments, near-term and long-term operational outlook, upcoming opportunities
and headwinds. Financial ratios and valuations are out of scope (handled by
FundamentalsAnalystAgent).

Google Search grounding is enabled via `self.use_grounding = True`, which
triggers the grounding branch in BaseAgent._invoke_gemini. This means
thinking_config is NOT used (mutually exclusive with grounding on Vertex AI).
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.prompts import BUSINESS_ANALYST_PERSONA, BUSINESS_ANALYST_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)


class BusinessAnalystAgent(BaseAgent):
    """Industry/sector specialist that produces a grounded business intelligence
    profile for any PSX-listed company using live Google Search grounding."""

    def __init__(self) -> None:
        super().__init__(
            name="Business Analyst",
            persona=BUSINESS_ANALYST_PERSONA,
            role="business",
        )
        # Enable Google Search grounding — this flag is read by BaseAgent._invoke_gemini
        # which then omits thinking_config (they are mutually exclusive on Vertex AI).
        self.use_grounding = True

    def analyze(
        self,
        symbol: str,
        company_name: str = "",
        sector: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run business intelligence analysis for *symbol*.

        Parameters
        ----------
        symbol : str
            PSX ticker symbol (e.g. "SSGC", "OGDC").
        company_name : str
            Full company name for richer search queries.
        sector : str
            PSX sector string (e.g. "Oil & Gas", "Cement").
        context : dict, optional
            Shared agent_context from the orchestrator (broker reports, news, etc.).
            Used as supplementary grounding context alongside live search results.

        Returns
        -------
        dict
            Business intelligence report conforming to the JSON schema in the persona.
        """
        symbol = symbol.strip().upper()
        ctx = context or {}

        # Build supplementary context string from what the orchestrator already fetched
        # (broker research, news headlines). The grounded model will combine these
        # with live search results for the most comprehensive picture.
        supplementary_lines = []

        research_reports = ctx.get("research_reports", [])
        if research_reports:
            supplementary_lines.append("=== BROKER RESEARCH EXCERPTS ===")
            for r in research_reports[:3]:
                if isinstance(r, dict):
                    title = r.get("title", "")
                    excerpt = r.get("content", r.get("summary", ""))[:600]
                    supplementary_lines.append(f"[{title}] {excerpt}")
                elif isinstance(r, str):
                    supplementary_lines.append(r[:600])

        company_news = ctx.get("company_news", [])
        if company_news:
            supplementary_lines.append("\n=== RECENT COMPANY NEWS ===")
            for n in company_news[:5]:
                if isinstance(n, dict):
                    title = n.get("title", "")
                    pub = n.get("published", "")
                    summary = n.get("summary", "")
                    supplementary_lines.append(f"[{pub}] {title}. {summary}")

        additional_context = "\n".join(supplementary_lines) if supplementary_lines else "No supplementary context available — rely entirely on Google Search."

        prompt = BUSINESS_ANALYST_PROMPT_TEMPLATE.format(
            company_name=company_name or symbol,
            symbol=symbol,
            sector=sector or "Unknown",
            additional_context=additional_context,
        )

        self._log(f"Analyzing business intelligence for {symbol} ({company_name}) — {sector} sector")

        try:
            result = self.query_json(prompt)
            if result.get("error"):
                logger.warning(f"Business Analyst JSON parse failed for {symbol}: {result}")
                return self._fallback(symbol, company_name, sector)
            result["symbol"] = symbol
            return result
        except Exception as exc:
            logger.error(f"Business Analyst analysis failed for {symbol}: {exc}")
            return self._fallback(symbol, company_name, sector, str(exc))

    @staticmethod
    def _fallback(
        symbol: str,
        company_name: str = "",
        sector: str = "",
        error: str = "",
    ) -> Dict[str, Any]:
        """Return a minimal safe dict when the analysis fails entirely."""
        return {
            "symbol": symbol,
            "company_overview": {
                "what_they_do": f"{company_name or symbol} — business profile unavailable.",
                "business_type": "unknown",
                "primary_geography": "Pakistan",
                "competitive_position": "unknown",
                "key_operational_metric": "Data unavailable",
            },
            "revenue_segments": [],
            "products_and_services": [],
            "upcoming_opportunities": [],
            "upcoming_headwinds": [],
            "industry_dynamics": {
                "near_term": "Data unavailable.",
                "long_term": "Data unavailable.",
            },
            "strategic_initiatives": [],
            "key_customers_or_markets": "Data unavailable.",
            "competitive_advantages": [],
            "key_risks_operational": [],
            "confidence": 0,
            "data_quality_note": f"Analysis failed. Error: {error}" if error else "Analysis unavailable.",
            "error": True,
        }
