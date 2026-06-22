"""
Technical Analyst Agent — Chart-reading specialist for PSX equities.

Fetches OHLCV history, runs deterministic indicator computations via the
data layer, then sends the pre-computed numbers to Gemini for strategic
interpretation.  The LLM never touches raw math.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.prompts import ANALYSIS_PROMPT_TEMPLATE, TECHNICAL_ANALYST_PERSONA
from config import HISTORY_PERIOD_DAILY
from data.market_data import get_history, get_quote
from data.technical_indicators import compute_all_indicators, get_trend_summary


class TechnicalAnalystAgent(BaseAgent):
    """Interprets pre-computed technical indicators for a PSX ticker."""

    def __init__(self) -> None:
        super().__init__(
            name="Technical Analyst",
            persona=TECHNICAL_ANALYST_PERSONA,
            role="technical",
        )

    # ── Public API ────────────────────────────────────────────────

    def analyze(self, symbol: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run a full technical analysis for *symbol*.

        Pipeline
        --------
        1. Fetch OHLCV history (1 year daily).
        2. Compute indicators deterministically (``data.technical_indicators``).
        3. Build a trend summary (text).
        4. Send everything to Gemini for qualitative interpretation.
        5. Return structured JSON report.

        Returns
        -------
        dict
            JSON report keyed by ``trend``, ``signals``, ``key_levels``,
            ``confidence``, ``summary``, plus raw ``indicators`` for
            downstream consumers.
        """
        self._log(f"Starting technical analysis for {symbol} …")

        # ── Step 1: Fetch price history ───────────────────────────
        try:
            df = get_history(symbol, period=HISTORY_PERIOD_DAILY, interval="1d")
            if df is None or df.empty:
                return self._error_report(
                    symbol, "No OHLCV history available for this ticker."
                )
            self._log(f"Fetched {len(df)} daily bars.")
        except Exception as exc:
            self._log(f"History fetch failed: {exc}")
            return self._error_report(symbol, f"Data fetch error: {exc}")

        # ── Step 2: Compute indicators (deterministic) ────────────
        try:
            indicators: Dict[str, Any] = compute_all_indicators(df)
            self._log("Indicators computed successfully.")
        except Exception as exc:
            self._log(f"Indicator computation failed: {exc}")
            return self._error_report(symbol, f"Indicator error: {exc}")

        # ── Step 3: Build trend summary (text) ────────────────────
        try:
            trend_text: str = get_trend_summary(indicators)
        except Exception:
            trend_text = "Trend summary unavailable."

        # ── Step 4: Fetch current quote for context ───────────────
        try:
            quote: Dict[str, Any] = get_quote(symbol) or {}
        except Exception:
            quote = {}

        # ── Step 5: Prepare data blob for the LLM ────────────────
        data_blob = self._build_data_blob(symbol, quote, indicators, trend_text, df, context or {})

        # ── Step 6: Query Gemini ──────────────────────────────────
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(data=data_blob)
        report: Dict[str, Any] = self.query_json(prompt)

        # Attach raw indicators so downstream agents can reference them.
        report["raw_indicators"] = self._serializable_indicators(indicators)
        report["agent"] = self.name
        report["symbol"] = symbol

        self._log(f"Technical analysis complete. Confidence: {report.get('confidence', '?')}")
        return report

    # ── Private helpers ───────────────────────────────────────────

    def _build_data_blob(
        self,
        symbol: str,
        quote: Dict[str, Any],
        indicators: Dict[str, Any],
        trend_text: str,
        df: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Compose a human-readable text block from all data sources."""
        lines = [
            f"SYMBOL: {symbol}",
            f"CURRENT PRICE: {quote.get('price', 'N/A')}",
            f"DAY CHANGE: {quote.get('change', 'N/A')} ({quote.get('change_pct', 'N/A')}%)",
            f"VOLUME: {quote.get('volume', 'N/A')}",
            "",
            "── PRICE CONTEXT (last 5 closes) ──",
        ]

        # Last 5 closes for quick visual context.
        if not df.empty:
            recent = df.tail(5)
            for idx, row in recent.iterrows():
                date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
                lines.append(
                    f"  {date_str}  O={row.get('Open','?'):.2f}  "
                    f"H={row.get('High','?'):.2f}  L={row.get('Low','?'):.2f}  "
                    f"C={row.get('Close','?'):.2f}  V={row.get('Volume','?')}"
                )

        # 52-week high / low.
        if not df.empty and "High" in df.columns and "Low" in df.columns:
            high_52w = df["High"].max()
            low_52w = df["Low"].min()
            last_close = df["Close"].iloc[-1]
            pct_from_high = ((last_close - high_52w) / high_52w) * 100
            pct_from_low = ((last_close - low_52w) / low_52w) * 100
            lines.extend([
                "",
                f"52-WEEK HIGH: {high_52w:.2f}  ({pct_from_high:+.1f}% from high)",
                f"52-WEEK LOW:  {low_52w:.2f}  ({pct_from_low:+.1f}% from low)",
            ])

        # Indicator values.
        lines.extend(["", "── TECHNICAL INDICATORS ──"])
        for key, value in indicators.items():
            if key == "interpretations":
                continue  # Added separately below.
            lines.append(f"  {key}: {self._format_value(value)}")

        # Interpretations (if provided by the data layer).
        interpretations = indicators.get("interpretations", {})
        if interpretations:
            lines.extend(["", "── INDICATOR INTERPRETATIONS ──"])
            for k, v in interpretations.items():
                lines.append(f"  {k}: {v}")

        # Trend summary.
        lines.extend(["", "── TREND SUMMARY ──", trend_text])

        # Market briefing and macro context
        if context:
            from data.local_data import format_market_context_text
            ctx_text = format_market_context_text(context.get("market_context", {}))
            if ctx_text:
                lines.extend(["", "── BROADER MARKET CONTEXT ──", ctx_text])
                
            # Add Institutional FIPI/LIPI flows for momentum context
            flows = context.get("institutional_flows") or {}
            fipi_lipi = flows.get("fipi_lipi") or {}
            if fipi_lipi:
                lines.extend([
                    "",
                    "── INSTITUTIONAL FLOWS (FIPI/LIPI) ──",
                    f"  Foreign Institutional (FIPI): {fipi_lipi.get('fipi_net_usd_mn', 'N/A')} USD Mn",
                    f"  Local Institutional (LIPI): {fipi_lipi.get('lipi_net_usd_mn', 'N/A')} USD Mn",
                    f"  Flow Signal: {fipi_lipi.get('flow_signal', 'N/A')}"
                ])

        return "\n".join(lines)

    @staticmethod
    def _format_value(value: Any) -> str:
        """Render an indicator value as a compact string."""
        if isinstance(value, dict):
            parts = [f"{k}={v}" for k, v in value.items()]
            return "{ " + ", ".join(parts) + " }"
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    @staticmethod
    def _serializable_indicators(indicators: Dict[str, Any]) -> Dict[str, Any]:
        """Make indicator dict JSON-safe (convert numpy/pandas types)."""
        clean: Dict[str, Any] = {}
        for key, value in indicators.items():
            try:
                json.dumps(value)  # test serializability
                clean[key] = value
            except (TypeError, ValueError):
                clean[key] = str(value)
        return clean

    @staticmethod
    def _error_report(symbol: str, reason: str) -> Dict[str, Any]:
        """Return a minimal error report when analysis cannot proceed."""
        return {
            "error": True,
            "agent": "Technical Analyst",
            "symbol": symbol,
            "trend": "unknown",
            "signals": [],
            "key_levels": {"support": [], "resistance": []},
            "confidence": 0,
            "summary": f"Technical analysis unavailable: {reason}",
        }
