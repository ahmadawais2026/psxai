"""
data/local_data.py
Reads market_data/ and company_data/ local files to build rich context
for the AI agent pipeline — macro indicators, sector data, company
financials (multi-period), research reports, and local news.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
MARKET_DATA_DIR = BASE_DIR / "market_data"
COMPANY_DATA_DIR = BASE_DIR / "company_data"

# PSX sector name → local sector data subfolder
SECTOR_FOLDER_MAP = {
    "automobile": "autos",
    "auto":       "autos",
    "cement":     "cement",
    "building":   "cement",
    "fertilizer": "fertilizer",
    "chemical":   "fertilizer",
    "oil & gas":  "omc",
    "oil and gas": "omc",
    "energy":     "circulardebt",
    "power":      "circulardebt",
    "electric":   "circulardebt",
}

# Key income-statement metrics to surface (fuzzy match on row index)
_IS_KEYWORDS = [
    "total revenue", "net sales", "markup/interest revenue",
    "profit after tax", "net profit", "profit for the period",
    "operating profit", "gross profit",
    "eps - basic", "eps",
]

# Key balance-sheet metrics
_BS_KEYWORDS = [
    "total assets",
    "total liabilities",
    "total equity",
    "cash & bank", "cash and bank", "cash and cash equivalents",
    "total current assets",
    "total non-current assets",
]


# ── Helpers ──────────────────────────────────────────────────────────


def _sector_folder(sector: str) -> Optional[str]:
    s = sector.lower().strip()
    for key, folder in SECTOR_FOLDER_MAP.items():
        if key in s:
            return folder
    return None


def _read_json_safe(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Could not read %s: %s", path, e)
        return None


def _find_row(df: pd.DataFrame, keywords: List[str]) -> Optional[pd.Series]:
    """Return the first row whose index contains any keyword (case-insensitive)."""
    idx_lower = [str(i).lower() for i in df.index]
    for kw in keywords:
        kw_l = kw.lower()
        for i, label in enumerate(idx_lower):
            if kw_l in label:
                row = df.iloc[i]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                return row
    return None


def _format_trend_table(df: pd.DataFrame, keywords: List[str], n_periods: int = 8) -> str:
    """
    Extract matching rows from a statement DataFrame and format as a compact
    multi-period text table showing the last n_periods columns.
    """
    # Drop non-date columns (Unit, symbol, etc.)
    date_cols = [c for c in df.columns if c not in ("Unit", "symbol", "period", "last_updated")]
    date_cols = date_cols[-n_periods:]  # most-recent N periods

    lines = []
    for kw in keywords:
        row = _find_row(df[date_cols] if date_cols else df, [kw])
        if row is None:
            continue
        metric_name = str(row.name) if hasattr(row, "name") else kw
        values = []
        for col in date_cols:
            val = row.get(col, None) if hasattr(row, "get") else None
            try:
                val = float(val)
                values.append(f"{col}:{val:,.1f}")
            except (TypeError, ValueError):
                values.append(f"{col}:N/A")
        if values:
            lines.append(f"  {metric_name[:40]:<40} | {' | '.join(values)}")

    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────


def get_market_context(sector: Optional[str] = None) -> Dict[str, Any]:
    """
    Load macro indicators, latest PSX news, morning briefing summary,
    and (when sector is given) the matching sector volume/price data.

    Returns a dict with keys: macro, market_news, morning_briefing,
    sector_data (optional).
    """
    context: Dict[str, Any] = {}

    # 1. Macro indicators (last 12 data rows)
    macro_path = MARKET_DATA_DIR / "macroeconomics" / "macro_indicators_index.xlsx"
    if macro_path.exists():
        try:
            df = pd.read_excel(macro_path)
            context["macro"] = df.tail(12).fillna("").to_dict(orient="records")
        except Exception as e:
            logger.warning("Macro indicators read failed: %s", e)

    # 2. Latest general PSX news (top 20 items)
    news_path = MARKET_DATA_DIR / "news" / "latest_news.json"
    data = _read_json_safe(news_path)
    if isinstance(data, list):
        context["market_news"] = data[:20]

    # 3. Morning briefing summary
    briefing_path = MARKET_DATA_DIR / "summary" / "morning_briefing_summary.json"
    data = _read_json_safe(briefing_path)
    if data:
        context["morning_briefing"] = data

    # 4. Sector-specific data (last 12 months per file)
    if sector:
        folder = _sector_folder(sector)
        if folder:
            sector_dir = MARKET_DATA_DIR / "sectors" / folder
            if sector_dir.exists():
                sector_data: Dict[str, Any] = {}
                for jf in sector_dir.glob("*.json"):
                    if "metadata" in jf.name:
                        continue
                    rows = _read_json_safe(jf)
                    if isinstance(rows, list) and rows:
                        sector_data[jf.stem] = rows[-12:]
                if sector_data:
                    context["sector_data"] = sector_data

    return context


def get_company_financials_local(ticker: str) -> Dict[str, pd.DataFrame]:
    """
    Read the company's quarterly Excel file from company_data/{ticker}/.
    Returns a dict of {sheet_name: DataFrame} for all statement sheets.
    Prefers the quarterly file; falls back to the annual file.
    """
    ticker = ticker.upper()
    company_dir = COMPANY_DATA_DIR / ticker
    quarter_file = company_dir / f"{ticker}_quarter_financials.xlsx"
    annual_file = company_dir / f"{ticker}_financials.xlsx"

    filepath = quarter_file if quarter_file.exists() else (annual_file if annual_file.exists() else None)
    if filepath is None:
        logger.warning("No local financial file for %s", ticker)
        return {}

    sheets: Dict[str, pd.DataFrame] = {}
    try:
        xl = pd.ExcelFile(filepath)
        for sheet in xl.sheet_names:
            df = xl.parse(sheet, index_col=0)
            if not df.empty:
                sheets[sheet] = df
    except Exception as e:
        logger.error("Error reading %s: %s", filepath, e)

    return sheets


def get_financials_text(ticker: str, n_periods: int = 8) -> str:
    """
    Build a compact multi-period text block from the company's local
    Excel file — Income Statement and Balance Sheet key metrics across
    the last n_periods quarters. Used by FundamentalsAnalystAgent.
    """
    sheets = get_company_financials_local(ticker)
    if not sheets:
        return ""

    lines = [f"── LOCAL QUARTERLY FINANCIALS: {ticker} (last {n_periods} quarters) ──"]

    # Income Statement
    is_df = None
    for name, df in sheets.items():
        if "income" in name.lower():
            is_df = df
            break

    if is_df is not None:
        lines.append("\nINCOME STATEMENT (PKR mn):")
        block = _format_trend_table(is_df, _IS_KEYWORDS, n_periods)
        lines.append(block if block else "  (no matching rows found)")

    # Balance Sheet
    bs_df = None
    for name, df in sheets.items():
        if "balance" in name.lower():
            bs_df = df
            break

    if bs_df is not None:
        lines.append("\nBALANCE SHEET (PKR mn):")
        block = _format_trend_table(bs_df, _BS_KEYWORDS, n_periods)
        lines.append(block if block else "  (no matching rows found)")

    # Simplified CF (our derived sheet)
    cf_df = None
    for name, df in sheets.items():
        if "simplified" in name.lower():
            cf_df = df
            break

    if cf_df is not None:
        lines.append("\nDERIVED CASH FLOWS (PKR mn, simplified):")
        cf_keywords = ["operating", "investing", "financing", "net change", "cash", "other/non"]
        block = _format_trend_table(cf_df, cf_keywords, n_periods)
        lines.append(block if block else "  (no matching rows found)")

    return "\n".join(lines)


def get_research_reports(ticker: str, sector: Optional[str] = None,
                         max_reports: int = 5) -> List[str]:
    """
    Find and read the most relevant .md broker research reports from
    market_data/briefings/extracted/ that mention the ticker or sector.
    Returns a list of markdown strings (capped at 2 500 chars each).
    """
    extracted_dir = MARKET_DATA_DIR / "briefings" / "extracted"
    if not extracted_dir.exists():
        return []

    ticker_upper = ticker.upper()
    scored: List[tuple] = []

    for md_file in extracted_dir.glob("*.md"):
        name_upper = md_file.name.upper()
        score = 0
        if ticker_upper in name_upper:
            score += 10
        if sector:
            for word in sector.split():
                if len(word) > 3 and word.upper() in name_upper:
                    score += 3
        # General market reports are weakly relevant
        if any(kw in name_upper for kw in ("KSE", "ECONOMY", "MARKET", "INFLATION", "SBP")):
            score += 1
        if score > 0:
            scored.append((score, md_file))

    scored.sort(key=lambda x: x[0], reverse=True)

    reports: List[str] = []
    for _, md_file in scored[:max_reports]:
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore").strip()
            if content:
                reports.append(f"=== {md_file.stem} ===\n{content[:2500]}")
        except Exception as e:
            logger.warning("Could not read %s: %s", md_file.name, e)

    return reports


def get_local_company_news(ticker: str) -> List[Dict[str, Any]]:
    """
    Return news items from market_data/news/ that mention this ticker.
    Checks both the general latest_news.json and the per-company file
    (saved by live_scraper when it runs).
    """
    ticker_upper = ticker.upper()
    results: List[Dict[str, Any]] = []

    # Per-company file saved by live_scraper
    per_company = MARKET_DATA_DIR / "news" / f"{ticker_upper}_news.json"
    data = _read_json_safe(per_company)
    if isinstance(data, list):
        results.extend(data[:15])

    # General news filtered by ticker mention
    general = MARKET_DATA_DIR / "news" / "latest_news.json"
    data = _read_json_safe(general)
    if isinstance(data, list):
        for item in data:
            title = str(item.get("title", "") or item.get("Title", "")).upper()
            body = str(item.get("description", "") or item.get("body", "")).upper()
            if ticker_upper in title or ticker_upper in body:
                results.append(item)

    # Deduplicate by title
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for item in results:
        key = str(item.get("title", "") or item.get("Title", ""))
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique[:20]


def format_market_context_text(ctx: Dict[str, Any]) -> str:
    """
    Convert the dict returned by get_market_context() into a compact
    human-readable text block suitable for LLM prompts.
    """
    lines: List[str] = []

    # Morning briefing
    briefing = ctx.get("morning_briefing")
    if briefing:
        if isinstance(briefing, dict):
            lines.append("── MORNING MARKET BRIEFING ──")
            for k, v in list(briefing.items())[:10]:
                lines.append(f"  {k}: {v}")
        elif isinstance(briefing, str):
            lines.append("── MORNING MARKET BRIEFING ──")
            lines.append(briefing[:800])

    # Macro indicators (latest row only for brevity)
    macro = ctx.get("macro")
    if macro and isinstance(macro, list) and macro:
        lines.append("\n── MACRO INDICATORS (latest) ──")
        latest = macro[-1]
        for k, v in list(latest.items())[:15]:
            if v not in ("", None):
                lines.append(f"  {k}: {v}")

    # Sector data (last 3 months per series)
    sector_data = ctx.get("sector_data", {})
    if sector_data:
        lines.append("\n── SECTOR DATA (recent months) ──")
        for series_name, rows in sector_data.items():
            lines.append(f"  {series_name}:")
            for row in (rows[-3:] if isinstance(rows, list) else []):
                lines.append(f"    {row}")

    # General market news (top 5 headlines)
    news = ctx.get("market_news", [])
    if news:
        lines.append("\n── LATEST PSX MARKET NEWS ──")
        for item in news[:5]:
            title = item.get("title") or item.get("Title") or ""
            date = item.get("date") or item.get("Date") or item.get("published") or ""
            lines.append(f"  [{date}] {title}")

    return "\n".join(lines)
