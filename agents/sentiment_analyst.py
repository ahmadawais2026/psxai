"""
Sentiment Analyst Agent — Specialist in market sentiment and news narrative classification.

Fetches recent news articles and uses Gemini to classify market sentiment, narrative trends,
and potential sentiment catalysts specific to the Pakistani market.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from agents.prompts import ANALYSIS_PROMPT_TEMPLATE, SENTIMENT_ANALYST_PERSONA
from data.news_data import get_stock_news


class SentimentAnalystAgent(BaseAgent):
    """Analyzes market news and sentiment trends for a PSX stock."""

    def __init__(self) -> None:
        super().__init__(
            name="Sentiment Analyst",
            persona=SENTIMENT_ANALYST_PERSONA,
        )

    def analyze(self, symbol: str) -> Dict[str, Any]:
        """Run sentiment analysis for *symbol*.

        Pipeline:
        1. Fetch news articles from the data layer.
        2. Format headlines and sources.
        3. Query Gemini to classify overall sentiment, key catalysts, and narratives.
        """
        self._log(f"Starting sentiment analysis for {symbol} …")

        try:
            articles = get_stock_news(symbol, max_articles=10)
            self._log(f"Fetched {len(articles)} news articles.")
        except Exception as exc:
            self._log(f"News fetch failed: {exc}")
            articles = []

        if not articles:
            return {
                "agent": self.name,
                "symbol": symbol,
                "overall_sentiment": "neutral",
                "sentiment_score": 0,
                "news_volume": "low",
                "key_narratives": ["No recent news found for this ticker."],
                "catalysts_positive": [],
                "catalysts_negative": [],
                "institutional_signals": "No significant media mentions detected.",
                "confidence": 5,
                "summary": "No recent news headlines available to parse. Sentiment defaults to neutral.",
                "articles": []
            }

        # ── Step 2: Compose data blob ─────────────────────────────
        data_blob = self._build_data_blob(symbol, articles)

        # ── Step 3: Query Gemini ──────────────────────────────────
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(data=data_blob)
        report = self.query_json(prompt)

        # Attach source articles and metadata
        report["articles"] = articles
        report["agent"] = self.name
        report["symbol"] = symbol

        self._log(
            f"Sentiment analysis complete. Overall sentiment: {report.get('overall_sentiment', '?')} "
            f"(Score: {report.get('sentiment_score', 0)})"
        )
        return report

    def _build_data_blob(self, symbol: str, articles: List[Dict[str, Any]]) -> str:
        """Format news articles into a readable text block."""
        lines = [
            f"SYMBOL: {symbol}",
            "RECENT NEWS ARTICLES:",
            ""
        ]
        
        for idx, art in enumerate(articles, start=1):
            lines.extend([
                f"Article #{idx}:",
                f"  Title: {art.get('title', 'N/A')}",
                f"  Published: {art.get('published', 'N/A')}",
                f"  Source: {art.get('source', 'N/A')}",
                f"  Link: {art.get('link', 'N/A')}",
                ""
            ])
            
        return "\n".join(lines)
