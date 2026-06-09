"""
fetch_macro_data.py
===================
Aggregates key Pakistan macroeconomic indicators from multiple sources and
saves a clean JSON snapshot that the AI agents can use as macro context.

Sources:
  1. yfinance          — PKR/USD, Brent crude, WTI crude
  2. Morning briefing  — KSE-100 level & changes, LIPI/FIPI flows, indices
  3. Economy .md files — SBP policy rate, CPI/inflation

Output files:
  market_data/macroeconomics/macro_data.json        — structured macro snapshot
  market_data/summary/morning_briefing_summary.json — latest briefing summary

Usage:
  python fetch_macro_data.py
  python fetch_macro_data.py --no-live   # skip yfinance, local files only
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

BASE_DIR      = Path(__file__).parent
EXTRACTED_DIR = BASE_DIR / "market_data" / "briefings" / "extracted"
MACRO_PATH    = BASE_DIR / "market_data" / "macroeconomics" / "macro_data.json"
BRIEFING_PATH = BASE_DIR / "market_data" / "summary" / "morning_briefing_summary.json"


# ── helpers ────────────────────────────────────────────────────────────────

def _float(text: str) -> Optional[float]:
    """Parse a float from a possibly messy string."""
    try:
        return float(re.sub(r"[,%\s]", "", text))
    except (ValueError, TypeError):
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"  [saved] {path}")


# ── 1. yfinance live prices ────────────────────────────────────────────────

def fetch_live_prices() -> dict:
    """Fetch PKR/USD, Brent, WTI from yfinance."""
    result: dict = {}
    try:
        import yfinance as yf
        SYMBOLS = {
            "pkr_per_usd":   "PKR=X",    # USD quoted in PKR (PKR per 1 USD)
            "brent_usd_bbl": "BZ=F",
            "wti_usd_bbl":   "CL=F",
        }
        for key, sym in SYMBOLS.items():
            try:
                price = yf.Ticker(sym).fast_info.last_price
                if price:
                    result[key] = round(float(price), 2)
                    print(f"  [live] {key} = {result[key]}")
            except Exception as e:
                print(f"  [warn] {sym} fetch failed: {e}")
    except ImportError:
        print("  [warn] yfinance not installed — skipping live prices")
    return result


# ── 2. Morning briefing parser ─────────────────────────────────────────────

def _latest_mb_file() -> Optional[Path]:
    """Return the most recent MB_DD-MM-YYYY.md file."""
    files = sorted(EXTRACTED_DIR.glob("MB_*.md"), reverse=True)
    return files[0] if files else None


def parse_morning_briefing() -> dict:
    """
    Extract KSE-100 level, change, LIPI/FIPI flows, and top headlines
    from the latest morning briefing .md file.
    """
    mb = _latest_mb_file()
    if not mb:
        print("  [warn] no morning briefing .md found")
        return {}

    text = _read(mb)
    result: dict = {"source_file": mb.name}

    # KSE-100 — look for: KSE-100 170,479 -0.4% +33.0% -2.1%
    m = re.search(r"KSE-100\s+([\d,]+)\s+([+-][\d.]+%)\s+([+-][\d.]+%)\s+([+-][\d.]+%)", text)
    if m:
        result["kse100_last"]      = _float(m.group(1))
        result["kse100_chg_pct"]   = m.group(2)
        result["kse100_fytd_pct"]  = m.group(3)
        result["kse100_cytd_pct"]  = m.group(4)
        print(f"  [mb] KSE-100 = {result['kse100_last']} ({result['kse100_chg_pct']})")

    # LIPI/FIPI table — lines like: Foreign 0.52 -420.45
    lipi_fipi: dict = {}
    for cat in ("Foreign", "Individuals", "Companies", "Banks/DFIs", "MF", "Broker", "Insurance"):
        pattern = rf"{cat}\s+([+-]?[\d.]+)\s+([+-]?[\d.]+)"
        m = re.search(pattern, text)
        if m:
            lipi_fipi[cat] = {"daily_usd_mn": _float(m.group(1)), "cytd_usd_mn": _float(m.group(2))}
    if lipi_fipi:
        result["lipi_fipi"] = lipi_fipi
        foreign_cytd = lipi_fipi.get("Foreign", {}).get("cytd_usd_mn")
        print(f"  [mb] FIPI CYTD (Foreign) = {foreign_cytd} USD mn")

    # FIPI sector-wise — lines like: EP 0.58
    sectors_fipi: dict = {}
    for sec in ("EP", "OMC", "Banks", "Tech", "Cement", "Fertilizer", "Autos", "Pharma"):
        m = re.search(rf"\b{sec}\s+([+-]?[\d.]+)\b", text)
        if m:
            sectors_fipi[sec] = _float(m.group(1))
    if sectors_fipi:
        result["fipi_by_sector_usd_mn"] = sectors_fipi

    # Top 5 headlines — lines ending with "Click here for more"
    headlines = re.findall(r"([A-Z][^:\n]{20,120}):\s*\n", text)
    result["top_headlines"] = headlines[:6]

    # Briefing date
    m = re.search(r"(\d{1,2}\s+\w+\s+\d{4})\s*\n\s*Morning Briefing", text)
    result["date"] = m.group(1) if m else mb.stem.replace("MB_", "")

    return result


# ── 3. Economy reports parser ──────────────────────────────────────────────

def _md_date(path: Path) -> date:
    """Extract date from markdown frontmatter **Date:** line."""
    m = re.search(r"\*\*Date:\*\*\s*([\d]{4}-[\d]{2}-[\d]{2})", _read(path))
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.min


def parse_sbp_rate() -> dict:
    """
    Find the most recent SBP rate decision from extracted economy .md files.
    Handles:
      - 'increases policy rate by Nbps to X%'
      - 'maintains status quo at X%'
      - Derived: last_status_quo + sum_of_changes (handles OCR-garbled PDF text)
    """
    result: dict = {}
    candidates = sorted(
        [f for f in EXTRACTED_DIR.glob("*.md") if "sbp" in f.name.lower() or "mpc" in f.name.lower()],
        key=_md_date,
        reverse=True,
    )

    last_known_rate: Optional[float] = None
    total_bps_change: int = 0
    last_decision_date: str = ""
    last_decision_type: str = "unchanged"

    for md in sorted(candidates, key=_md_date):  # oldest first to accumulate changes
        full_text = _read(md)
        d         = _md_date(md)
        # Use only the first ~1000 chars to avoid OCR-embedded older reports in the body
        head = full_text[:1000]

        # Priority 1: "increases the policy rate by 100bps to 11.5%" — direct absolute value
        m = re.search(r"policy\s+rate\s+by\s+(\d+)\s*bps\s+to\s+([\d.]+)%", head, re.I)
        if m:
            last_known_rate   = float(m.group(2))
            total_bps_change  = 0
            last_decision_date = str(d)
            last_decision_type = f"raised +{m.group(1)}bps"
            continue

        # Priority 2: "increases/cuts policy rate by Nbps" without explicit target (OCR garble)
        m = re.search(r"(?:increases?|cuts?|reduced?|raised?)\s+(?:the\s+)?policy\s+rate\s+by\s+(\d+)\s*bps", head, re.I)
        if m:
            bps = int(m.group(1))
            direction = +1 if re.search(r"\b(increas|rais)", m.group(0), re.I) else -1
            total_bps_change += direction * bps
            last_decision_date = str(d)
            last_decision_type = f"{'raised' if direction > 0 else 'cut'} {direction*bps:+d}bps"
            continue

        # Priority 3: "maintains status quo at 10.5%" (no change)
        m = re.search(r"(?:status\s+quo|maintains?\s+.*policy\s+rate)\s+at\s+([\d.]+)%", head, re.I)
        if m:
            last_known_rate   = float(m.group(1))
            total_bps_change  = 0
            last_decision_date = str(d)
            last_decision_type = "unchanged"
            continue

    # Compute final rate
    if last_known_rate is not None:
        final_rate = last_known_rate + total_bps_change / 100
        result = {
            "rate_pct": round(final_rate, 2),
            "decision": last_decision_type,
            "decision_date": last_decision_date,
        }
    # If only bps changes found with no anchor, report what we know
    elif total_bps_change != 0:
        result = {
            "rate_pct": None,
            "decision": f"net {total_bps_change:+d}bps from unknown base",
            "decision_date": last_decision_date,
        }

    if result and result.get("rate_pct"):
        print(f"  [sbp] policy rate = {result['rate_pct']}% ({result['decision']} on {result['decision_date']})")
    else:
        print("  [warn] SBP rate not found in extracted reports")

    return result


def parse_cpi() -> dict:
    """
    Find the most recent CPI/inflation report and extract the expected YoY figure.
    """
    result: dict = {}
    inflation_mds = sorted(
        [f for f in EXTRACTED_DIR.glob("*.md") if "inflation" in f.name.lower()],
        key=_md_date,
        reverse=True,
    )

    for md in inflation_mds:
        text  = _read(md)
        d     = _md_date(md)
        title_m = re.search(r"#\s+(.+)", text)
        title   = title_m.group(1).strip() if title_m else md.stem

        # "projected to increase by 11.9% YoY"
        m = re.search(r"(?:increase|rise|stand)\s+(?:to|by|at)?\s*([\d.]+)%\s*YoY", text, re.I)
        if not m:
            m = re.search(r"([\d.]+)%\s*YoY", text, re.I)

        if m:
            result = {
                "cpi_yoy_pct": float(m.group(1)),
                "report_title": title,
                "report_date": str(d),
                "source": md.name,
            }
            break

    if result:
        print(f"  [cpi] {result.get('cpi_yoy_pct')}% YoY ({result.get('report_date')})")
    else:
        print("  [warn] CPI not found in extracted reports")

    return result


def _inflation_trend(cpi_files: list[Path]) -> str:
    """Determine trend from last 3 CPI readings."""
    values: list[float] = []
    for md in sorted(cpi_files, key=_md_date)[-3:]:
        text = _read(md)
        m = re.search(r"([\d.]+)%\s*YoY", text, re.I)
        if m:
            values.append(float(m.group(1)))
    if len(values) < 2:
        return "unknown"
    if values[-1] > values[-2]:
        return "rising"
    if values[-1] < values[-2]:
        return "falling"
    return "stable"


# ── main ───────────────────────────────────────────────────────────────────

def main(no_live: bool = False) -> None:
    now = datetime.utcnow().isoformat()
    today = str(date.today())

    print("\n== Macro Data Fetcher ==")
    print(f"   As of: {today}\n")

    macro: dict[str, Any] = {"as_of": today, "last_updated": now}

    # Live prices
    if not no_live:
        print("[1] Fetching live prices (yfinance)...")
        macro.update(fetch_live_prices())
    else:
        print("[1] Skipping live prices (--no-live)")

    # Morning briefing
    print("\n[2] Parsing morning briefing...")
    briefing = parse_morning_briefing()
    if briefing:
        # Hoist KSE-100 into top-level macro dict
        for key in ("kse100_last", "kse100_chg_pct", "kse100_fytd_pct", "kse100_cytd_pct"):
            if key in briefing:
                macro[key] = briefing[key]
        macro["lipi_fipi"]              = briefing.get("lipi_fipi", {})
        macro["fipi_by_sector_usd_mn"]  = briefing.get("fipi_by_sector_usd_mn", {})
        macro["briefing_date"]          = briefing.get("date", "")
        _save_json(BRIEFING_PATH, briefing)

    # SBP rate
    print("\n[3] Parsing SBP policy rate...")
    sbp = parse_sbp_rate()
    if sbp:
        macro["sbp_policy_rate_pct"]  = sbp.get("rate_pct")
        macro["sbp_decision"]         = sbp.get("decision")
        macro["sbp_decision_date"]    = sbp.get("decision_date")

    # CPI
    print("\n[4] Parsing CPI / inflation...")
    cpi = parse_cpi()
    if cpi:
        macro["cpi_yoy_pct"]          = cpi.get("cpi_yoy_pct")
        macro["cpi_report_date"]      = cpi.get("report_date")

    # Inflation trend
    inflation_files = [f for f in EXTRACTED_DIR.glob("*.md") if "inflation" in f.name.lower()]
    macro["inflation_trend"] = _inflation_trend(inflation_files)

    # Derived: real interest rate
    if macro.get("sbp_policy_rate_pct") and macro.get("cpi_yoy_pct"):
        macro["real_interest_rate_pct"] = round(
            macro["sbp_policy_rate_pct"] - macro["cpi_yoy_pct"], 2
        )
        print(f"\n  [derived] Real interest rate = {macro['real_interest_rate_pct']}%")

    # Save
    print("\n[5] Saving...")
    _save_json(MACRO_PATH, macro)

    print("\n== Summary ==")
    for k, v in macro.items():
        if k not in ("lipi_fipi", "fipi_by_sector_usd_mn"):
            print(f"  {k:<30} {v}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Pakistan macro data snapshot")
    parser.add_argument("--no-live", action="store_true", help="Skip yfinance live prices")
    args = parser.parse_args()
    main(no_live=args.no_live)
