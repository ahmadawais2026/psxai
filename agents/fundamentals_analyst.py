"""
Fundamentals Analyst Agent — Specialist in company financial statements and valuation ratios.

Fetches fundamental data and financial statements, offloads calculation to the data layer,
and passes structured financial metrics to Gemini for qualitative valuation.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.prompts import ANALYSIS_PROMPT_TEMPLATE, FUNDAMENTALS_ANALYST_PERSONA
from data.market_data import get_fundamentals, get_financial_statements, get_quote
from data.local_data import format_market_context_text
from data.dcf_engine import DCFEngine
import json


def _fmt_pct_frac(v: Any) -> str:
    """Format a ratio stored as a fraction (0.15) into a percent string (15.00%)."""
    if v is None:
        return "N/A"
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_multiple(v: Any) -> str:
    """Format a plain ratio (2.74) into a multiple string (2.74x)."""
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.2f}x"
    except (TypeError, ValueError):
        return "N/A"


class FundamentalsAnalystAgent(BaseAgent):
    """Interprets company financial statements and key valuation metrics for a PSX stock."""

    def __init__(self) -> None:
        super().__init__(
            name="Fundamentals Analyst",
            persona=FUNDAMENTALS_ANALYST_PERSONA,
            role="fundamentals",
        )

    def analyze(self, symbol: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run a full fundamental analysis for *symbol*.

        Pipeline:
        1. Fetch fundamentals (P/E, P/B, ROE, debt/equity, dividend yield).
        2. Fetch key financial statements (balance sheet, income, cash flow summaries).
        3. Enrich with multi-period local financials and research reports from context.
        4. Format data block for Gemini.
        5. Query Gemini and obtain structured JSON report.
        """
        self._log(f"Starting fundamental analysis for {symbol} …")

        # ── Step 1: Fetch fundamentals ────────────────────────────
        try:
            fundamentals = get_fundamentals(symbol)
            if not fundamentals:
                return self._error_report(symbol, "Fundamentals data unavailable.")
            self._log("Fetched fundamentals data.")
        except Exception as exc:
            self._log(f"Fundamentals fetch failed: {exc}")
            return self._error_report(symbol, f"Data fetch error: {exc}")

        # ── Step 2: Fetch financial statements ────────────────────
        try:
            financials = get_financial_statements(symbol) or {}
            self._log("Fetched financial statements.")
        except Exception as exc:
            self._log(f"Financial statements fetch failed: {exc}")
            financials = {}

        # ── Step 3: Fetch current quote ───────────────────────────
        # Prefer the orchestrator's shared snapshot so the fundamental and
        # technical sections quote ONE price; only fetch live if standalone.
        quote = (context or {}).get("quote") or {}
        if not quote:
            try:
                quote = get_quote(symbol) or {}
            except Exception:
                quote = {}

        # ── Step 4: Compose data blob ─────────────────────────────
        data_blob = self._build_data_blob(symbol, quote, fundamentals, financials, context or {})

        # ── Step 5: Query Gemini ──────────────────────────────────
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(data=data_blob)
        report = self.query_json(prompt)

        # Attach raw fundamentals for downstream committee review
        report["raw_fundamentals"] = fundamentals
        report["raw_financials"] = financials
        report["agent"] = self.name
        report["symbol"] = symbol

        self._log(f"Fundamental analysis complete. Valuation verdict: {report.get('valuation_verdict', '?')}")
        return report

    def _build_data_blob(
        self,
        symbol: str,
        quote: Dict[str, Any],
        fundamentals: Dict[str, Any],
        financials: Dict[str, Any],
        context: Dict[str, Any],
    ) -> str:
        """Format fundamental data into a readable text block."""
        # Explicit solvency hint so the narrative does not conflate illiquidity
        # (low cash) with balance-sheet insolvency (negative equity).
        try:
            _ta = float(financials.get("total_assets") or 0)
            _tl = float(financials.get("total_liabilities") or 0)
            if _ta and _tl:
                _eq = _ta - _tl
                _state = ("NEGATIVE EQUITY — balance-sheet insolvent"
                          if _eq < 0 else
                          "positive equity — solvent on a balance-sheet basis "
                          "(any distress here is ILLIQUIDITY / cash-flow risk, not insolvency)")
                _equity_str = f"{_eq:,.0f} ({_state})"
            else:
                _equity_str = "N/A"
        except (TypeError, ValueError):
            _equity_str = "N/A"

        # Basis period for the trailing P/E / EPS (full-year: 'TTM' or e.g. 'FY25').
        _eps_period = fundamentals.get('eps_period') or financials.get('period_label') or 'Latest'
        _pe = fundamentals.get('pe_ratio')
        _de = fundamentals.get('debt_to_equity')

        lines = [
            f"SYMBOL: {symbol}",
            f"COMPANY NAME: {fundamentals.get('name', symbol)}",
            f"SECTOR: {fundamentals.get('sector', 'N/A')}",
            f"CURRENT PRICE: {quote.get('price', 'N/A')}",
            f"MARKET CAP: {fundamentals.get('market_cap', 'N/A')}",
            "",
            "── KEY VALUATION & EFFICIENCY METRICS ──",
            f"  Trailing P/E ({_eps_period} basis): {_pe if _pe is not None else 'N/A'}",
            f"  Price to Book (P/B): {fundamentals.get('pb_ratio', 'N/A')}",
            f"  Return on Equity (ROE): {_fmt_pct_frac(fundamentals.get('roe'))}",
            f"  Earnings Per Share (EPS, {_eps_period}): {fundamentals.get('eps', 'N/A')}",
            f"  Dividend Yield: {fundamentals.get('dividend_yield', 'N/A')}",
            f"  Debt to Equity: {_fmt_multiple(_de)}",
            f"  Beta: {fundamentals.get('beta', 'N/A')}",
            "",
            "  NOTES ON THE METRICS ABOVE:",
            "  • Trailing P/E and EPS are on a FULL-YEAR basis (TTM or the latest "
            "annual year), NOT a single quarter. If P/E is N/A, the company has no "
            "positive trailing earnings — do NOT describe it as 'overvalued on P/E'; "
            "value it on P/B / EV-EBITDA / asset basis instead.",
            "  • Debt to Equity here uses INTEREST-BEARING DEBT (borrowings) only. "
            "N/A means borrowings could not be isolated from the filings — in that "
            "case do NOT treat total liabilities or trade/circular-debt payables as "
            "leverage, and do not invent a debt-to-equity number.",
            "",
        ]

        # Period label: 'TTM' when truly computed via stitching,
        # otherwise the actual period (e.g. '9MFY26', 'FY25') so the
        # LLM never misrepresents stale annual data as TTM.
        _period = financials.get('period_label') or financials.get('latest_period') or 'Latest'

        lines += [
            "── INCOME STATEMENT HIGHLIGHTS (Firestore/AskAnalyst) ──",
            f"  Revenue ({_period}): {financials.get('revenue', 'N/A')}",
            f"  Net Income ({_period}): {financials.get('net_income', 'N/A')}",
            f"  Operating Margin: {financials.get('operating_margin', 'N/A')}",
            f"  Net Margin: {financials.get('net_margin', 'N/A')}",
            "",
            "── BALANCE SHEET HIGHLIGHTS ──",
            f"  Total Assets: {financials.get('total_assets', 'N/A')}",
            f"  Total Liabilities: {financials.get('total_liabilities', 'N/A')}",
            f"  Shareholder Equity (Assets - Liabilities): {_equity_str}",
            f"  Cash and Cash Equivalents: {financials.get('cash', 'N/A')}",
            "",
            "── CASH FLOW HIGHLIGHTS ──",
            f"  Operating Cash Flow: {financials.get('operating_cash_flow', 'N/A')}",
            f"  Free Cash Flow: {financials.get('free_cash_flow', 'N/A')}",
        ]

        # Surface the latest interim result so the LLM is never unaware of
        # the most recent reported period (even when full TTM can't be stitched).
        _interim_headline = financials.get('latest_interim_headline', '')
        if _interim_headline:
            lines.append(f"  Latest reported result: {_interim_headline}")

        # ── Automated DCF Valuations ──
        try:
            # Parse metrics for DCF
            def _parse_val(val_str):
                if not val_str or val_str == 'N/A': return 0.0
                val_str = str(val_str).replace('PKR', '').replace('$', '').replace('%', '').strip()
                multiplier = 1
                if 'T' in val_str: multiplier = 1e12; val_str = val_str.replace('T', '')
                elif 'B' in val_str: multiplier = 1e9; val_str = val_str.replace('B', '')
                elif 'M' in val_str: multiplier = 1e6; val_str = val_str.replace('M', '')
                elif 'K' in val_str: multiplier = 1e3; val_str = val_str.replace('K', '')
                try: return float(val_str.strip()) * multiplier
                except ValueError: return 0.0

            fcf = _parse_val(financials.get('free_cash_flow'))
            mcap = _parse_val(fundamentals.get('market_cap'))
            price = quote.get('price', 0.0)

            # ── Live, company-specific DCF inputs ──
            # Beta: computed vs KSE-100 from price history (the old
            # fundamentals['beta'] key never existed → always defaulted to 1.0).
            from data.market_data import get_beta, compute_historical_growth
            beta = get_beta(symbol)

            # Stage-1 growth: CAGR from filed statements instead of a flat 8%.
            hist_growth = compute_historical_growth(financials)

            # Risk-free rate: SBP-sourced (T-bill / policy rate), preferring the
            # value already in the shared macro snapshot, else a direct fetch.
            rf_rate = None
            macro = context.get("macro_context") or {}
            # Prefer the FRESH market-context policy rate (same source as the
            # live-macro preamble) so the DCF cost of equity isn't built off the
            # stale cached snapshot rate.
            _mkt_macro_rf = (context.get("market_context") or {}).get("macro") or {}
            rf_candidate = macro.get("risk_free_rate")
            if rf_candidate is None:
                pr = (_mkt_macro_rf.get("sbp_policy_rate_pct")
                      or macro.get("policy_rate_pct"))
                if pr is not None:
                    try:
                        pr = float(pr)
                        rf_candidate = pr / 100.0 if pr > 1.0 else pr
                    except (TypeError, ValueError):
                        rf_candidate = None
            if rf_candidate is None:
                try:
                    from data.sbp_easydata import get_risk_free_rate
                    rf_candidate = get_risk_free_rate()
                except Exception:
                    rf_candidate = None
            if rf_candidate:
                try:
                    rf_rate = float(rf_candidate)
                except (TypeError, ValueError):
                    rf_rate = None

            # Share count in MILLIONS. AskAnalyst reports shares outstanding in
            # millions, which matches FCFE (also PKR millions), so the per-share
            # intrinsic value comes out directly in PKR. Fall back to deriving
            # from market_cap (stored absolute upstream) / price when missing.
            shares = _parse_val(fundamentals.get('shares_outstanding'))  # millions
            if shares <= 0 and price > 0 and mcap > 0:
                shares = (mcap / price) / 1_000_000.0

            ocf_val = _parse_val(financials.get('operating_cash_flow'))
            if fcf > 0 and shares > 0:
                engine = DCFEngine(risk_free_rate=rf_rate) if rf_rate else DCFEngine()
                # Compute book value per share for DCF sanity check.
                # equity (total_assets - total_liabilities) and shares are both in
                # PKR millions, so equity_mn / shares_mn gives PKR per share.
                _total_assets = _parse_val(financials.get('total_assets'))
                _total_liab   = _parse_val(financials.get('total_liabilities'))
                _equity_mn    = _total_assets - _total_liab
                _bvps = (_equity_mn / shares) if shares > 0 else None
                dcf_results = engine.generate_scenarios(
                    base_fcf=fcf,
                    levered_beta=beta,
                    shares_outstanding=shares,
                    historical_growth=hist_growth,
                    current_price=float(price) if price else None,
                    book_value_per_share=_bvps if (_bvps and _bvps > 0) else None,
                    operating_cash_flow=ocf_val if ocf_val else None,
                )
                if dcf_results.get("credible", True) and "error" not in dcf_results:
                    lines.extend([
                        "",
                        "── AUTOMATED DCF ENGINE (FCFE) ──",
                        f"Live inputs → Risk-free (SBP): {engine.risk_free_rate:.2%} | "
                        f"Beta (vs KSE-100): {beta:.2f} | Historical growth (CAGR): {hist_growth:.2%} | "
                        f"Base FCFE: {fcf:,.0f}mn | Shares: {shares:,.1f}mn",
                        "The system has computed an intrinsic valuation based on free cash flows (FCFE).",
                        "Review the bounded scenarios and sensitivity matrix to inform your valuation verdict.",
                        json.dumps(dcf_results, indent=2)
                    ])
                else:
                    # DCF flagged non-credible (debt-funded FCF, or value far outside
                    # book/price). Discard it entirely so the LLM cannot quote a phantom
                    # intrinsic value or set a price target from it.
                    lines.extend([
                        "",
                        "── AUTOMATED DCF ENGINE (FCFE) — DISCARDED ──",
                        "[!] The DCF was flagged NON-CREDIBLE and has been discarded:",
                        f"  {dcf_results.get('credibility_note') or 'Base value outside the plausible range.'}",
                        "  CRITICAL: Do NOT cite any DCF intrinsic value and do NOT use one as a price",
                        "  target. Use RELATIVE VALUATION (P/B vs ROE, P/E vs peers/history, EV/EBITDA),",
                        "  set implied_valuation_range from those multiples, and state explicitly that",
                        "  the DCF was non-credible for this name.",
                    ])
            else:
                lines.extend([
                    "",
                    "── AUTOMATED DCF ENGINE (FCFE) ──",
                    "[!] DCF Engine aborted: non-positive FCFE or unknown share count "
                    "(common for banks/financials, whose cash-flow statements lack a clean FCFE).",
                    "Fallback to Relative Valuation (EV/EBITDA, P/E, P/B) or DDM required."
                ])
        except Exception as e:
            self._log(f"DCF calculation failed: {e}")

        # Multi-period local financials (8 quarters of trend data)
        financials_text = context.get("financials_text", "")
        if financials_text:
            lines.append("")
            lines.append(financials_text)

        # Add Macro Context if available
        macro = context.get("macro_context") or {}
        if macro:
            lines.extend([
                "",
                "── SBP MACROECONOMIC CONTEXT ──",
            ])
            pr = macro.get("policy_rate", {})
            # Authoritative CURRENT policy rate — PREFER the fresh market-context
            # macro (the same source as the report's live-macro preamble, e.g.
            # 11.5%) over the macro_context snapshot, whose Firestore-cached
            # `sbp:policy_rate` can be stale (it served 10.5% post-April-hike).
            _mkt_macro = (context.get("market_context") or {}).get("macro") or {}
            _rate = _mkt_macro.get("sbp_policy_rate_pct")
            if _rate is None:
                _rate = (pr.get("policy_rate_pct") if isinstance(pr, dict) else None)
            if _rate is None:
                _rate = macro.get("sbp_policy_rate_pct")
            if _rate is not None:
                _decdate = (_mkt_macro.get("sbp_decision_date")
                            or macro.get("sbp_decision_date")
                            or (pr.get("as_of") if isinstance(pr, dict) else ""))
                lines.append(
                    f"  AUTHORITATIVE: the CURRENT SBP policy rate is {_rate}%"
                    + (f" (as of {_decdate})" if _decdate else "")
                    + ". Treat this as the present rate; any broker note or recollection "
                    "citing a different current rate is STALE history, not the current rate."
                )
            # Only show the trend here — never re-print a (possibly stale) rate that
            # could contradict the authoritative line above.
            if isinstance(pr, dict) and pr.get("trend"):
                lines.append(f"  Policy Rate Trend: {pr.get('trend')}")
            m2 = macro.get("m2", {})
            if m2:
                lines.append(f"  Broad Money M2 (PKR Bn): {m2.get('m2_pkr_bn')} (Growth: {m2.get('growth_pct')}%)")
            fx = macro.get("fx_reserves", {})
            if fx:
                lines.append(f"  FX Reserves (USD Bn): {fx.get('total_reserves_usd_bn')}")
            lines.append(f"  Signal: {macro.get('macro_signal', 'N/A')}")
            
        # Add Institutional MUFAP flows
        flows = context.get("institutional_flows") or {}
        mufap = flows.get("mufap") or {}
        if mufap:
            lines.extend([
                "",
                "── INSTITUTIONAL RISK APPETITE (MUFAP) ──",
                f"  Equity AUM Share: {mufap.get('equity_share_pct')}%",
                f"  Risk Appetite: {mufap.get('risk_appetite')}",
                f"  Signal: {mufap.get('mufap_signal')}"
            ])

        # Broker research reports mentioning this company or sector
        reports = context.get("research_reports", [])
        if reports:
            lines.append("")
            lines.append("── BROKER RESEARCH REPORTS ──")
            for report in reports:
                if isinstance(report, str):
                    lines.append(report)
                    lines.append("")
                else:
                    lines.append(str(report))

        return "\n".join(lines)

    @staticmethod
    def _error_report(symbol: str, reason: str) -> Dict[str, Any]:
        """Return a minimal error report when analysis cannot proceed."""
        return {
            "error": True,
            "agent": "Fundamentals Analyst",
            "symbol": symbol,
            "valuation_verdict": "unknown",
            "financial_health": "unknown",
            "growth_outlook": "unknown",
            "moat": "none",
            "strengths": [],
            "concerns": [reason],
            "fair_value_range": {"low": 0.0, "high": 0.0},
            "confidence": 0,
            "summary": f"Fundamental analysis unavailable: {reason}",
        }
