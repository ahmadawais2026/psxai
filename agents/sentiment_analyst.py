"""
Sentiment Analyst Agent — Specialist in market sentiment and news narrative classification.

Fetches recent news articles and uses Gemini to classify market sentiment, narrative trends,
and potential sentiment catalysts specific to the Pakistani market.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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

    def analyze(self, symbol: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run sentiment analysis for *symbol*.

        Pipeline:
        1. Fetch news from local files (company-specific + general PSX news).
        2. Fall back to yfinance news if nothing found locally.
        3. Enrich with broker research reports from context.
        4. Query Gemini to classify sentiment, catalysts, and narratives.
        """
        self._log(f"Starting sentiment analysis for {symbol} …")
        ctx = context or {}

        # ── Step 1: Collect news from all sources ─────────────────
        # Local company-specific news (freshly scraped by live_scraper)
        local_news = ctx.get("company_news", [])

        # yfinance fallback
        yf_articles: List[Dict[str, Any]] = []
        if not local_news:
            try:
                yf_articles = get_stock_news(symbol, max_articles=10)
                self._log(f"Fetched {len(yf_articles)} articles from yfinance.")
            except Exception as exc:
                self._log(f"yfinance news fetch failed: {exc}")

        articles = local_news if local_news else yf_articles
        self._log(f"Total news items for sentiment: {len(articles)}")

        research_reports = ctx.get("research_reports", [])

        if not articles and not research_reports:
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
        data_blob = self._build_data_blob(symbol, articles, research_reports)

        # ── Step 3: Query Gemini ──────────────────────────────────
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(data=data_blob)
        report = self.query_json(prompt)

        report["articles"] = articles
        report["agent"] = self.name
        report["symbol"] = symbol

        self._log(
            f"Sentiment analysis complete. Overall sentiment: {report.get('overall_sentiment', '?')} "
            f"(Score: {report.get('sentiment_score', 0)})"
        )
        return report

    def _build_data_blob(self, symbol: str, articles: List[Dict[str, Any]],
                         research_reports: Optional[List[str]] = None) -> str:
        """Format news articles and broker reports into a readable text block."""
        lines = [f"SYMBOL: {symbol}", ""]

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

        if research_reports:
            lines.append("── BROKER RESEARCH REPORTS ──")
            for report in research_reports:
                lines.append(report)
                lines.append("")

        return "\n".join(lines)
