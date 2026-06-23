"""
Sentiment Analyst Agent — Specialist in market sentiment and news narrative classification.

Fetches recent news articles and uses Gemini to classify market sentiment, narrative trends,
and potential sentiment catalysts specific to the Pakistani market, using an advanced NLP pipeline.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from agents.prompts import ANALYSIS_PROMPT_TEMPLATE, SENTIMENT_ANALYST_PERSONA
from data.news_data import get_stock_news

CATEGORY_WEIGHTS = {
    "Sovereign & IMF Policy": 2.00,
    "Regulatory & Fiscal": 1.75,
    "Commodity & Macroeconomic": 1.25,
    "Microeconomic: Earnings": 1.00,
    "Microeconomic: Operations": 0.85,
    "Retail & Market Rumors": 0.50
}

def is_duplicate(text1: str, text2: str, threshold: float = 0.85) -> bool:
    """Uses difflib as a lightweight deterministic semantic duplicate filter (simulating LSH)."""
    if not text1 or not text2:
        return False
    # Use SequenceMatcher on a truncated chunk to save compute on large texts
    matcher = SequenceMatcher(None, text1.lower()[:1000], text2.lower()[:1000])
    return matcher.ratio() >= threshold

class SentimentAnalystAgent(BaseAgent):
    """Analyzes market news and sentiment trends for a PSX stock."""

    def __init__(self) -> None:
        super().__init__(
            name="Sentiment Analyst",
            persona=SENTIMENT_ANALYST_PERSONA,
            role="sentiment",
        )

    def analyze(self, symbol: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run sentiment analysis for *symbol* with deterministic anti-hallucination verification."""
        self._log(f"Starting sentiment analysis for {symbol} …")
        ctx = context or {}

        # ── Step 1: Collect news from all sources ─────────────────
        local_news = ctx.get("company_news", [])
        
        rss_articles: List[Dict[str, Any]] = []
        if not local_news:
            try:
                rss_articles = get_stock_news(symbol, max_articles=15)
                self._log(f"Fetched {len(rss_articles)} articles from local RSS.")
            except Exception as exc:
                self._log(f"RSS news fetch failed: {exc}")

        raw_articles = local_news if local_news else rss_articles
        
        # ── Step 2: Semantic Deduplication ────────────────────────
        unique_articles = []
        for art in raw_articles:
            body = str(art.get("title", "")) + " " + str(art.get("content", art.get("description", "")))
            is_dup = False
            for u_art in unique_articles:
                u_body = str(u_art.get("title", "")) + " " + str(u_art.get("content", u_art.get("description", "")))
                if is_duplicate(body, u_body):
                    is_dup = True
                    break
            if not is_dup:
                unique_articles.append(art)
                
        self._log(f"Total news items for sentiment after deduplication: {len(unique_articles)}")
        
        research_reports = ctx.get("research_reports", [])

        if not unique_articles and not research_reports:
            return {
                "agent": self.name,
                "symbol": symbol,
                "analytical_reasoning": "No recent news headlines available to parse. Sentiment defaults to neutral.",
                "news_category": "Retail & Market Rumors",
                "primary_entities": [symbol],
                "sentiment_direction": 0,
                "sentiment_magnitude": 0.0,
                "verbatim_citations": [],
                "adjusted_score": 0,
                "articles": []
            }

        data_blob = self._build_data_blob(symbol, unique_articles, ctx)
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(data=data_blob)

        # ── Step 3: LLM Query with Anti-Hallucination Loop ────────
        max_retries = 2
        report = {}
        for attempt in range(max_retries + 1):
            try:
                report = self.query_json(prompt)
                
                # Verify Verbatim Citations
                citations = report.get("verbatim_citations", [])
                hallucinated = False
                for cite in citations:
                    if not cite: continue
                    # Exact match first
                    if cite.lower() in data_blob.lower():
                        continue
                        
                    # Fuzzy fallback
                    best_ratio = 0.0
                    blob_lower = data_blob.lower()
                    cite_len = len(cite)
                    # Simple sliding window for fuzzy match if it's small enough, else just fail it
                    # To keep it efficient, if it's not exact and cite is > 30 chars, we assume hallucinated if simple difflib ratio is low
                    if cite_len > 10:
                        # Find potential fuzzy matches by splitting the blob
                        hallucinated = True
                        for i in range(0, max(1, len(blob_lower) - cite_len), max(1, cite_len // 2)):
                            window = blob_lower[i:i+cite_len+20]
                            if SequenceMatcher(None, cite.lower(), window).ratio() > 0.8:
                                hallucinated = False
                                break
                    else:
                        hallucinated = True
                        
                    if hallucinated:
                        self._log(f"Warning: Hallucinated citation detected: '{cite[:50]}...'")
                        break
                        
                if not hallucinated:
                    break # Success!
                elif attempt < max_retries:
                    self._log("Retrying LLM due to hallucinated citations...")
                    # Optionally append a stern warning to the prompt for the retry
                    prompt += "\n\nWARNING: Your previous response contained hallucinated citations. You MUST extract EXACT substrings from the text."
                else:
                    self._log("Max retries reached. Nullifying citations.")
                    report["verbatim_citations"] = ["FAILED_VERIFICATION: Citations removed to prevent hallucination propagation."]
            except Exception as e:
                self._log(f"Error querying sentiment LLM: {e}")
                if attempt == max_retries:
                    report = {
                        "analytical_reasoning": "Failed to generate sentiment analysis.",
                        "news_category": "Retail & Market Rumors",
                        "primary_entities": [symbol],
                        "sentiment_direction": 0,
                        "sentiment_magnitude": 0.0,
                        "verbatim_citations": []
                    }

        # ── Step 4: Calculate Adjusted Score ───────────────────────
        direction = report.get("sentiment_direction", 0)
        magnitude = report.get("sentiment_magnitude", 0.0)
        category = report.get("news_category", "Retail & Market Rumors")
        weight = CATEGORY_WEIGHTS.get(category, 1.0)
        
        # Scale back to -100 to 100 for backwards compatibility with UI/Portfolio Manager
        adjusted_score = int(direction * magnitude * weight * 50)
        # Cap between -100 and 100
        adjusted_score = max(-100, min(100, adjusted_score))

        report["articles"] = unique_articles
        report["agent"] = self.name
        report["symbol"] = symbol
        report["adjusted_score"] = adjusted_score
        report["weight_multiplier"] = weight

        # ── Presentation contract for the PDF/UI summary banner ────
        # The sentiment LLM emits direction/magnitude only; the report banner
        # (report/pdf_generator.py) expects overall_sentiment / sentiment_score
        # / news_volume / confidence / summary. Derive them here so the banner
        # reflects the real analysis instead of falling back to empty/0.
        _dir = int(direction or 0)
        _label = {1: "Positive", 0: "Neutral", -1: "Negative"}.get(_dir, "Neutral")
        if magnitude < 0.15:
            _label = "Neutral"
        report["overall_sentiment"] = _label
        report["sentiment_score"] = adjusted_score
        report["news_volume"] = str(len(unique_articles))
        report["confidence"] = int(round(magnitude * 10))
        if not report.get("summary"):
            report["summary"] = report.get("analytical_reasoning", "")

        self._log(
            f"Sentiment analysis complete. Category: {category} (x{weight}). "
            f"Direction: {direction}, Mag: {magnitude}. Adjusted Score: {adjusted_score}"
        )
        return report

    def _build_data_blob(self, symbol: str, articles: List[Dict[str, Any]], context: Dict[str, Any]) -> str:
        """Format news articles and broker reports into a readable text block."""
        lines = [f"SYMBOL: {symbol}", ""]
        
        retail = context.get("retail_sentiment") or {}
        if retail:
            lines.extend([
                "── RETAIL SENTIMENT (REDDIT & GOOGLE TRENDS) ──",
                f"  Reddit Score: {retail.get('reddit', {}).get('sentiment_score', 'N/A')}",
                f"  Google Trends Attention: {retail.get('google_trends', {}).get('signal', 'N/A')}",
                f"  Combined Retail Label: {retail.get('combined_label', 'N/A')}",
                ""
            ])

        if articles:
            lines.append("── COMPANY NEWS & ANNOUNCEMENTS ──")
            for idx, art in enumerate(articles[:20], start=1):
                title = art.get("title") or art.get("Title") or art.get("headline") or "N/A"
                published = art.get("published") or art.get("date") or art.get("Date") or "N/A"
                source = art.get("source") or art.get("Source") or art.get("publisher") or "Unknown"
                body = art.get("content") or art.get("description") or art.get("body") or ""
                lines.extend([
                    f"  [{idx}] {title}",
                    f"       Date: {published} | Source: {source}",
                ])
                if body and len(str(body).strip()) > 30:
                    lines.append(f"       {str(body).strip()[:600]}")
                lines.append("")

        research_reports = context.get("research_reports", [])
        if research_reports:
            lines.append("── BROKER RESEARCH REPORTS ──")
            for report in research_reports:
                if isinstance(report, str):
                    lines.append(report)
                    lines.append("")
                else:
                    lines.append(str(report))

        return "\n".join(lines)

