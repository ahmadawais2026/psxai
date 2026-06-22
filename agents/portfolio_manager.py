"""
Portfolio Manager Agent — Final decision maker and advisory compiler.

Synthesizes analyst reports, debate findings, and user holdings to generate a
final position-aware investment recommendation and client-facing advisory dossier.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.prompts import FINAL_VERDICT_TEMPLATE, PORTFOLIO_MANAGER_PERSONA, DISCLAIMER

logger = logging.getLogger(__name__)


class PortfolioManagerAgent(BaseAgent):
    """Compiles individual advisor insights and runs final recommendation logic."""

    def __init__(self) -> None:
        super().__init__(
            name="Portfolio Manager",
            persona=PORTFOLIO_MANAGER_PERSONA,
            role="portfolio_manager",
        )

    def generate_recommendation(
        self,
        symbol: str,
        analyst_reports: Dict[str, Any],
        debate_result: Dict[str, Any],
        user_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Produce a final investment recommendation for *symbol*.
        
        Args:
            symbol: Ticker symbol.
            analyst_reports: Dictionary containing the reports from the 4 analyst agents.
            debate_result: Results from the Bull vs Bear debate committee.
            user_context: User holding context dict (from portfolio/manager).
            
        Returns:
            Dict: Final recommendation JSON report conforming to PORTFOLIO_MANAGER_PERSONA.
        """
        self._log(f"Compiling final recommendation for {symbol} …")

        # ── Step 1: Serialize inputs for LLM prompt ──────────────
        # Compact serialization (no indent) keeps the synthesis prompt lean —
        # indent=2 inflated the token count purely with whitespace. The verbose
        # raw_response blob from any analyst's JSON-fallback path is dropped too,
        # since it carries no decision-relevant structure for the final verdict.
        def _slim(report: Any) -> Any:
            if isinstance(report, dict):
                return {k: v for k, v in report.items() if k != "raw_response"}
            return report

        slim_reports = {k: _slim(v) for k, v in analyst_reports.items()}
        reports_json = json.dumps(slim_reports, separators=(",", ":"))
        debate_json = json.dumps({
            "bull_thesis": debate_result.get("bull_thesis", ""),
            "bear_thesis": debate_result.get("bear_thesis", ""),
            "agreements": debate_result.get("agreements", []),
            "disagreements": debate_result.get("disagreements", [])
        }, separators=(",", ":"))
        
        # Position-aware context construction
        if user_context and user_context.get("owns_stock", False):
            context_summary = (
                f"User currently OWNS this stock.\n"
                f"Shares Owned: {user_context.get('shares', 0.0)}\n"
                f"Average Acquisition Cost: PKR {user_context.get('avg_cost', 0.0)}\n"
                f"Current Holding Value: PKR {user_context.get('current_value', 0.0)}\n"
                f"Portfolio Concentration: {user_context.get('portfolio_pct', 0.0):.2f}%\n"
                f"Concentration Warning (>15% limit): {user_context.get('is_concentrated', False)}\n"
            )
            if user_context.get("is_concentrated", False):
                context_summary += (
                    "\nCRITICAL: The stock occupies more than 15% of the user's total portfolio. "
                    "You must prioritize advising risk reduction (TRIM or HOLD) due to concentration risk."
                )
        else:
            context_summary = "User does NOT currently own this stock. Evaluate pure market-entry potential."

        # ── Step 2: Query Gemini ──────────────────────────────────
        prompt = FINAL_VERDICT_TEMPLATE.format(
            all_reports=reports_json,
            debate_summary=debate_json,
            user_context=context_summary
        )
        
        report = self.query_json(prompt)
        
        # Attach standard disclaimer and symbol reference
        report["symbol"] = symbol.upper()
        report["disclaimer"] = DISCLAIMER
        report["agent"] = self.name
        
        self._log(f"Final recommendation compiled. Verdict: {report.get('recommendation', '?')} (Confidence: {report.get('confidence', 0)}/10)")
        return report
