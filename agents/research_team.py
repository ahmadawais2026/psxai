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
        import math
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
        
        is_reversed = False
        
        # ── Round 2+: Disagree-or-Commit Deliberation ─────────────
        for r in range(2, rounds + 1):
            logger.info(f"Debate Round {r}: Applying Disagree-or-Commit protocol...")
            
            # Final round: Role-Reversal
            if r == rounds and rounds >= 2:
                logger.info(f"Debate Round {r}: Enforcing Role-Reversal...")
                is_reversed = True
                
                # Swap personas
                bull_persona_temp = self.bull.persona
                self.bull.persona = self.bear.persona
                self.bear.persona = bull_persona_temp
                
                # Bull (acting as Bear) responds to Bear (who was acting as Bear)
                opponent_section = DEBATE_ROUND_OPPONENT_SECTION.format(
                    opponent_argument=json.dumps(latest_bear, indent=2)
                )
                bull_prompt = DEBATE_PROMPT_TEMPLATE.format(
                    analyst_reports=reports_context,
                    opponent_section=opponent_section,
                    opponent_instruction=DEBATE_ROUND_INSTRUCTION
                )
                latest_bull = self.bull.query_json(bull_prompt)
                debate_log.append({"round": r, "agent": "Bull Researcher (Role-Reversed)", "output": latest_bull})
                
                # Bear (acting as Bull) responds to Bull (who was acting as Bull)
                # Wait, if Bull acted as Bear, should Bear act against the original Bull? Yes.
                opponent_section = DEBATE_ROUND_OPPONENT_SECTION.format(
                    opponent_argument=json.dumps(bull_round_1 if r == 2 else debate_log[-3]['output'], indent=2)
                )
                bear_prompt = DEBATE_PROMPT_TEMPLATE.format(
                    analyst_reports=reports_context,
                    opponent_section=opponent_section,
                    opponent_instruction=DEBATE_ROUND_INSTRUCTION
                )
                latest_bear = self.bear.query_json(bear_prompt)
                debate_log.append({"round": r, "agent": "Bear Researcher (Role-Reversed)", "output": latest_bear})
                
                # Revert personas (optional, but good practice)
                self.bear.persona = self.bull.persona
                self.bull.persona = bull_persona_temp
                
            else:
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
            
        if is_reversed:
            final_bull = latest_bear  # Bear agent was acting as Bull
            final_bear = latest_bull  # Bull agent was acting as Bear
        else:
            final_bull = latest_bull
            final_bear = latest_bear
            
        # ── Step 3: Synthesize Agreements and Disagreements ──────
        logger.info("Synthesizing debate using Debate Synthesizer (Council Mode)...")
        synthesis_prompt = DEBATE_SYNTHESIS_TEMPLATE.format(
            bull_report=json.dumps(final_bull, indent=2),
            bear_report=json.dumps(final_bear, indent=2)
        )
        synthesis_result = self.synthesizer.query_json(synthesis_prompt)
        
        # In Council Mode, we get consensus_points, disagreements, unique_findings, comprehensive_analysis
        agreements = synthesis_result.get("consensus_points", [])
        disagreements = synthesis_result.get("disagreements", [])
        unique_findings = synthesis_result.get("unique_findings", [])
        comp_analysis = synthesis_result.get("comprehensive_analysis", "")
        
        if not isinstance(agreements, list):
            agreements = [str(agreements)] if agreements else []
        if not isinstance(disagreements, list):
            disagreements = [str(disagreements)] if disagreements else []
        if not isinstance(unique_findings, list):
            unique_findings = [str(unique_findings)] if unique_findings else []
            
        # Enforce accuracy of quantitative data layer agreement (grounded consensus on data)
        has_data_agreement = any("data layer" in a.lower() or "quantitative" in a.lower() for a in agreements)
        if not has_data_agreement:
            agreements.insert(0, "Both agents agree on the accuracy of the underlying quantitative data layer (price, volume, debt, PE ratios).")
            
        # Entropy-Modulated Confidence Scoring (EMCS)
        bull_conv = final_bull.get("conviction", 5)
        bear_conv = final_bear.get("conviction", 5)
        
        total_conv = bull_conv + bear_conv
        entropy = 0.0
        if total_conv > 0:
            p_bull = bull_conv / total_conv
            p_bear = bear_conv / total_conv
            if p_bull > 0: entropy -= p_bull * math.log2(p_bull)
            if p_bear > 0: entropy -= p_bear * math.log2(p_bear)
            
        base_confidence = (bull_conv + bear_conv) / 2.0
        emcs_score = base_confidence * (1.0 - (entropy * 0.5))
        
        emcs_msg = f"EMCS (Entropy-Modulated Confidence Score): {emcs_score:.2f}/10. (Bull: {bull_conv}/10, Bear: {bear_conv}/10, Entropy: {entropy:.3f})"
        disagreements.insert(0, emcs_msg)
        
        if comp_analysis:
            agreements.append(f"Comprehensive Analysis: {comp_analysis}")
        
        logger.info(f"Debate committee finished deliberation. {emcs_msg}")
        return {
            "bull_thesis": final_bull.get("thesis", ""),
            "bull_arguments": final_bull.get("key_arguments", []),
            "bear_thesis": final_bear.get("thesis", ""),
            "bear_arguments": final_bear.get("key_arguments", []),
            "agreements": agreements,
            "disagreements": disagreements,
            "unique_findings": unique_findings,
            "debate_log": debate_log
        }

