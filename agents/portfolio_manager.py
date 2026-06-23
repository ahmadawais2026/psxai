"""
Portfolio Manager Agent — Final decision maker and advisory compiler.

Synthesizes analyst reports, debate findings, and user holdings to generate a
final position-aware investment recommendation and client-facing advisory dossier.
"""

from __future__ import annotations

import json
import logging
import math
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
        user_context: Optional[Dict[str, Any]] = None,
        calibration_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Produce a final investment recommendation for *symbol*.

        Args:
            symbol: Ticker symbol.
            analyst_reports: Dictionary containing the reports from the 4 analyst agents.
            debate_result: Results from the Bull vs Bear debate committee.
            user_context: User holding context dict (from portfolio/manager).
            calibration_context: Distilled track-record block (learning/calibration)
                injected as a prior — how this system's past calls on this name/
                sector actually fared vs the KSE-100.

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
            "disagreements": debate_result.get("disagreements", []),
            # Entropy-Modulated Confidence Score (0-10): low value == the Bull
            # and Bear disagree sharply, so the final conviction must be modest.
            "emcs_score": debate_result.get("emcs_score"),
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

        # ── Step 2: Extract structured sub-scores for the coherence guard ──────
        # These are extracted deterministically from the structured analyst output
        # fields before the LLM call so the guard has clean numbers to check,
        # independent of what the LLM decides to say in its synthesis.
        risk_report = analyst_reports.get("risk") or {}
        tech_report = analyst_reports.get("technical") or {}
        sent_report = analyst_reports.get("sentiment") or {}

        risk_score       = float(risk_report.get("risk_score") or 0)
        bull_conviction  = float(debate_result.get("bull_conviction") or
                                 debate_result.get("bull_score") or 0)
        bear_conviction  = float(debate_result.get("bear_conviction") or
                                 debate_result.get("bear_score") or 0)
        emcs_score       = debate_result.get("emcs_score")
        sentiment_score  = float(sent_report.get("sentiment_score") or
                                 sent_report.get("confidence") or 5)
        tech_trend       = (tech_report.get("trend") or "neutral").lower()

        # ── Step 3: Query Gemini ──────────────────────────────────────────────
        sub_score_block = (
            f"Risk Score: {risk_score:.0f}/10\n"
            f"Bull Conviction: {bull_conviction:.0f}/10\n"
            f"Bear Conviction: {bear_conviction:.0f}/10\n"
            f"EMCS Score: {emcs_score if emcs_score is not None else 'N/A'}\n"
            f"Sentiment Score: {sentiment_score:.0f}/10\n"
            f"Technical Trend: {tech_trend}"
        )
        prompt = FINAL_VERDICT_TEMPLATE.format(
            all_reports=reports_json,
            debate_summary=debate_json,
            user_context=context_summary,
            calibration_context=(calibration_context or
                "TRACK RECORD: not available for this run."),
            sub_scores=sub_score_block,
        )

        report = self.query_json(prompt)

        # ── Step 3b: Calibrate conviction against disagreement & failures ──────
        # The LLM tends to over-anchor on a headline call; enforce a ceiling
        # deterministically. EMCS caps conviction when the debate was divided,
        # and each failed/timed-out analyst module (confidence == 0) shaves a
        # further point so a half-blind verdict can't claim high conviction.
        try:
            emcs = debate_result.get("emcs_score")
            failed_modules = sum(
                1 for r in analyst_reports.values()
                if isinstance(r, dict) and (r.get("confidence") == 0 or r.get("error"))
            )
            raw_conf = float(report.get("confidence", 0) or 0)
            ceiling = raw_conf
            if emcs is not None:
                ceiling = min(ceiling, math.ceil(float(emcs)))
            ceiling = max(1.0, ceiling - failed_modules)
            capped = int(min(raw_conf, ceiling))
            if capped != int(raw_conf):
                self._log(
                    f"Conviction capped {int(raw_conf)} -> {capped} "
                    f"(EMCS={emcs}, failed_modules={failed_modules})."
                )
            report["confidence"] = capped
        except Exception as exc:
            self._log(f"Conviction calibration skipped: {exc}")

        # ── Step 3c: Direction coherence guard ───────────────────────────────
        # Conservative: only fires when ALL THREE conditions are simultaneously true.
        # Only downgrades to HOLD (never to SELL). Does not override HOLD or bearish calls.
        try:
            rec = (report.get("recommendation") or "HOLD").upper().strip()
            emcs_val = debate_result.get("emcs_score")
            contradicted = (
                rec in ("STRONG BUY", "BUY", "ACCUMULATE")
                and emcs_val is not None and float(emcs_val) < 5.0
                and bear_conviction > bull_conviction
                and risk_score >= 8
            )
            if contradicted:
                report["original_recommendation"] = rec
                report["recommendation"] = "HOLD"
                report["coherence_override"] = True
                self._log(
                    f"Coherence guard: {rec} → HOLD "
                    f"(EMCS={emcs_val:.2f}, bear={bear_conviction:.0f} > "
                    f"bull={bull_conviction:.0f}, risk={risk_score:.0f}/10)"
                )
        except Exception as exc:
            self._log(f"Coherence guard skipped: {exc}")

        # Attach standard disclaimer and symbol reference
        report["symbol"] = symbol.upper()
        report["disclaimer"] = DISCLAIMER
        report["agent"] = self.name
        
        self._log(f"Final recommendation compiled. Verdict: {report.get('recommendation', '?')} (Confidence: {report.get('confidence', 0)}/10)")
        return report
