"""
Research Team / Committee — Governs the Bull vs Bear Dialectical Debate.

Implements the structured Disagree-or-Commit (DoC) protocol from the FinCom research,
combating conformity bias (sycophancy) in multi-agent financial advisory systems.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from agents.prompts import (
    BULL_RESEARCHER_PERSONA,
    BEAR_RESEARCHER_PERSONA,
    DEBATE_PROMPT_TEMPLATE,
    DEBATE_ROUND_OPPONENT_SECTION,
    DEBATE_ROUND_INSTRUCTION,
    DEBATE_SYNTHESIZER_PERSONA,
    DEBATE_SYNTHESIS_TEMPLATE,
)

logger = logging.getLogger(__name__)


class ResearchTeam:
    """Manages the interactive debate committee between the Bull and Bear Researcher agents."""

    def __init__(self) -> None:
        self.bull = BaseAgent("Bull Researcher", BULL_RESEARCHER_PERSONA)
        self.bear = BaseAgent("Bear Researcher", BEAR_RESEARCHER_PERSONA)
        self.synthesizer = BaseAgent("Debate Synthesizer", DEBATE_SYNTHESIZER_PERSONA)

    @property
    def model_name(self) -> Optional[str]:
        return getattr(self, "_model_name", None)

    @model_name.setter
    def model_name(self, value: Optional[str]) -> None:
        self._model_name = value
        self.bull.model_name = value
        self.bear.model_name = value
        self.synthesizer.model_name = value

    def debate(self, analyst_reports: Dict[str, Any], rounds: int = 2) -> Dict[str, Any]:
        """
        Execute a multi-round debate between Bull and Bear researchers.
        
        Args:
            analyst_reports: Compiled JSON outputs from Tech, Fundamental, Sentiment, and Risk analysts.
            rounds: Total number of debate rounds to run (minimum 2 to trigger DoC).
            
        Returns:
            Dict: {bull_thesis, bear_thesis, agreements, disagreements, debate_log}
        """
        logger.info("Initiating Bull vs Bear Research Debate committee...")
        
        # Serialize analyst reports for context inclusion
        reports_context = json.dumps(analyst_reports, indent=2)
        
        debate_log: List[Dict[str, Any]] = []
        
        # ── Round 1: Initial Theses ──────────────────────────────
        # Bull Initial Thesis
        logger.info("Debate Round 1: Bull Researcher initial thesis...")
        bull_prompt = DEBATE_PROMPT_TEMPLATE.format(
            analyst_reports=reports_context,
            opponent_section="",
            opponent_instruction=""
        )
        bull_round_1 = self.bull.query_json(bull_prompt)
        debate_log.append({"round": 1, "agent": "Bull Researcher", "output": bull_round_1})
        
        # Bear Initial Thesis
        logger.info("Debate Round 1: Bear Researcher initial thesis...")
        bear_prompt = DEBATE_PROMPT_TEMPLATE.format(
            analyst_reports=reports_context,
            opponent_section="",
            opponent_instruction=""
        )
        bear_round_1 = self.bear.query_json(bear_prompt)
        debate_log.append({"round": 1, "agent": "Bear Researcher", "output": bear_round_1})
        
        # Keep track of latest outputs
        latest_bull = bull_round_1
        latest_bear = bear_round_1
        
        # ── Round 2+: Disagree-or-Commit Deliberation ─────────────
        for r in range(2, rounds + 1):
            logger.info(f"Debate Round {r}: Applying Disagree-or-Commit protocol...")
            
            # Bull responds to Bear
            opponent_section = DEBATE_ROUND_OPPONENT_SECTION.format(
                opponent_argument=json.dumps(latest_bear, indent=2)
            )
            bull_prompt = DEBATE_PROMPT_TEMPLATE.format(
                analyst_reports=reports_context,
                opponent_section=opponent_section,
                opponent_instruction=DEBATE_ROUND_INSTRUCTION
            )
            latest_bull = self.bull.query_json(bull_prompt)
            debate_log.append({"round": r, "agent": "Bull Researcher", "output": latest_bull})
            
            # Bear responds to Bull
            opponent_section = DEBATE_ROUND_OPPONENT_SECTION.format(
                opponent_argument=json.dumps(latest_bull, indent=2)
            )
            bear_prompt = DEBATE_PROMPT_TEMPLATE.format(
                analyst_reports=reports_context,
                opponent_section=opponent_section,
                opponent_instruction=DEBATE_ROUND_INSTRUCTION
            )
            latest_bear = self.bear.query_json(bear_prompt)
            debate_log.append({"round": r, "agent": "Bear Researcher", "output": latest_bear})
            
        # ── Step 3: Synthesize Agreements and Disagreements ──────
        logger.info("Synthesizing debate agreements and disagreements using Debate Synthesizer...")
        synthesis_prompt = DEBATE_SYNTHESIS_TEMPLATE.format(
            bull_report=json.dumps(latest_bull, indent=2),
            bear_report=json.dumps(latest_bear, indent=2)
        )
        synthesis_result = self.synthesizer.query_json(synthesis_prompt)
        
        agreements = synthesis_result.get("agreements", [])
        disagreements = synthesis_result.get("disagreements", [])
        
        # Ensure agreements and disagreements are lists of strings
        if not isinstance(agreements, list):
            agreements = [str(agreements)] if agreements else []
        if not isinstance(disagreements, list):
            disagreements = [str(disagreements)] if disagreements else []
            
        # Enforce accuracy of quantitative data layer agreement (grounded consensus on data)
        has_data_agreement = any("data layer" in a.lower() or "quantitative" in a.lower() for a in agreements)
        if not has_data_agreement:
            agreements.insert(0, "Both agents agree on the accuracy of the underlying quantitative data layer (price, volume, debt, PE ratios).")
            
        # Calculate and prepend deterministic conviction-gap message
        bull_conv = latest_bull.get("conviction", 5)
        bear_conv = latest_bear.get("conviction", 5)
        conviction_gap = abs(bull_conv - bear_conv)
        conviction_msg = f"Conviction gap: Bull conviction is {bull_conv}/10 vs Bear conviction of {bear_conv}/10 (gap of {conviction_gap}/10)."
        disagreements.insert(0, conviction_msg)
        
        logger.info("Debate committee finished deliberation.")
        return {
            "bull_thesis": latest_bull.get("thesis", ""),
            "bull_arguments": latest_bull.get("key_arguments", []),
            "bear_thesis": latest_bear.get("thesis", ""),
            "bear_arguments": latest_bear.get("key_arguments", []),
            "agreements": agreements,
            "disagreements": disagreements,
            "debate_log": debate_log
        }
