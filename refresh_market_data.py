"""
refresh_market_data.py
======================
Master script — refreshes all market-level data used by the AI agents.

Sections (run in order; use --only / --skip to control):
  news       — fetch latest 50 PSX news headlines from AskAnalyst
  content    — scrape full article bodies for each news item
  briefings  — download latest Morning Briefing, Technical Research, Market Roundup PDFs
  reports    — download & extract research reports (last N months from catalog)
  sectors    — fetch sector fundamentals: cement, fertilizer, omc, autos, circulardebt
  macro      — compute macro snapshot: SBP rate, CPI, KSE-100, PKR/USD, crude
  intel      — fetch market intelligence: World Bank, forex, commodities, indices, RSS
  tickers    — refresh PSX tickers database from AskAnalyst (slow; run weekly)

Usage:
  python refresh_market_data.py                       # all sections
  python refresh_market_data.py --only news content   # only news + content
  python refresh_market_data.py --skip intel tickers  # skip slow sections
  python refresh_market_data.py --reports-months 3    # 3 months of research reports
  python refresh_market_data.py --no-live             # skip yfinance live prices
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

ALL_SECTIONS = ["news", "content", "briefings", "reports", "sectors", "macro", "intel", "tickers", "hourly"]


# ── timing helper ──────────────────────────────────────────────────────────────

class _Timer:
    def __init__(self, name: str):
        self.name = name
        self._start = time.time()

    def done(self, ok: bool = True):
        elapsed = time.time() - self._start
        status = "OK" if ok else "FAILED"
        print(f"  [{status}] {self.name} ({elapsed:.1f}s)\n")


def _header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


# ── section functions ──────────────────────────────────────────────────────────

def run_news():
    t = _Timer("news fetch")
    try:
        from scrape_askanalyst_market import fetch_news, setup_dirs
        setup_dirs()
        fetch_news(count=50)
        t.done()
    except Exception as e:
        print(f"  [!] news failed: {e}")
        t.done(ok=False)


def run_content(force: bool = False):
    t = _Timer("article content scraping")
    try:
        from fetch_news_content import enrich_news
        enrich_news(force=force)
        t.done()
    except Exception as e:
        print(f"  [!] content failed: {e}")
        t.done(ok=False)


def run_briefings():
    t = _Timer("briefing PDFs")
    try:
        from scrape_askanalyst_market import fetch_pdf_catalog, fetch_morning_briefing_chart, setup_dirs
        setup_dirs()
        fetch_morning_briefing_chart()
        fetch_pdf_catalog("morningbriefing",   "morning_briefing_pdfs.xlsx",    max_pdfs=3)
        fetch_pdf_catalog("technicalresearch", "technical_research_pdfs.xlsx",  max_pdfs=3)
        fetch_pdf_catalog("marketroundup",     "market_roundup_pdfs.xlsx",      max_pdfs=3)
        t.done()
    except Exception as e:
        print(f"  [!] briefings failed: {e}")
        t.done(ok=False)


def run_reports(months: int = 6):
    t = _Timer(f"research reports (last {months} months)")
    try:
        from scrape_askanalyst_market import fetch_pdf_catalog, setup_dirs
        setup_dirs()
        # First refresh the catalog index
        fetch_pdf_catalog("reports/1000", "research_reports_catalog.xlsx", max_pdfs=0)
        # Then download & extract from the catalog
        import subprocess
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "download_research_reports.py"),
             "--months", str(months)],
            cwd=str(BASE_DIR),
        )
        t.done(ok=result.returncode == 0)
    except Exception as e:
        print(f"  [!] reports failed: {e}")
        t.done(ok=False)


def run_sectors():
    t = _Timer("sector fundamentals")
    try:
        from scrape_askanalyst_market import fetch_sector, setup_dirs
        setup_dirs()
        for sector in ["cement", "fertilizer", "omc", "autos", "circulardebt"]:
            fetch_sector(sector)
        t.done()
    except Exception as e:
        print(f"  [!] sectors failed: {e}")
        t.done(ok=False)


def run_macro(no_live: bool = False):
    t = _Timer("macro snapshot")
    try:
        from fetch_macro_data import main as macro_main
        macro_main(no_live=no_live)
        t.done()
    except Exception as e:
        print(f"  [!] macro failed: {e}")
        t.done(ok=False)


def run_intel(ticker: str | None = None):
    t = _Timer("market intelligence")
    intel_script = BASE_DIR / "fetch_market_intelligence.py"
    if not intel_script.exists():
        print("  [~] fetch_market_intelligence.py not found — skipping")
        t.done()
        return
    try:
        import subprocess
        cmd = [sys.executable, str(intel_script)]
        if ticker:
            cmd += ["--ticker", ticker]
        result = subprocess.run(cmd, cwd=str(BASE_DIR))
        t.done(ok=result.returncode == 0)
    except Exception as e:
        print(f"  [!] intel failed: {e}")
        t.done(ok=False)


def run_tickers():
    t = _Timer("PSX tickers database")
    tickers_script = BASE_DIR / "update_psx_tickers.py"
    if not tickers_script.exists():
        print("  [~] update_psx_tickers.py not found — skipping")
        t.done()
        return
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(tickers_script)],
            cwd=str(BASE_DIR),
        )
        t.done(ok=result.returncode == 0)
    except Exception as e:
        print(f"  [!] tickers failed: {e}")
        t.done(ok=False)


def run_hourly(tickers: list[str] | None = None):
    t = _Timer("hourly market data aggregation")
    hourly_script = BASE_DIR / "archive_hourly_data.py"
    if not hourly_script.exists():
        print("  [~] archive_hourly_data.py not found — skipping")
        t.done()
        return
    try:
        import subprocess
        cmd = [sys.executable, str(hourly_script)]
        if tickers:
            cmd += ["--tickers"] + tickers
        result = subprocess.run(cmd, cwd=str(BASE_DIR))
        t.done(ok=result.returncode == 0)
    except Exception as e:
        print(f"  [!] hourly failed: {e}")
        t.done(ok=False)


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Refresh all PSX market data for AI agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--only",            nargs="+", choices=ALL_SECTIONS,
                        metavar="SECTION",   help="Run only these sections")
    parser.add_argument("--skip",            nargs="+", choices=ALL_SECTIONS,
                        metavar="SECTION",   help="Skip these sections")
    parser.add_argument("--reports-months",  type=int, default=6,
                        metavar="N",         help="Months of research reports to download (default: 6)")
    parser.add_argument("--force-content",   action="store_true",
                        help="Re-scrape article content even if already enriched")
    parser.add_argument("--no-live",         action="store_true",
                        help="Skip yfinance live prices in macro section")
    parser.add_argument("--ticker",          type=str, default=None,
                        help="Company ticker for market-intelligence company section")
    args = parser.parse_args()

    # Determine which sections to run
    sections = set(args.only) if args.only else set(ALL_SECTIONS)
    if args.skip:
        sections -= set(args.skip)
    # Preserve order
    run_order = [s for s in ALL_SECTIONS if s in sections]

    _header(f"PSX Market Data Refresh — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Sections: {', '.join(run_order)}")

    total_start = time.time()

    for section in run_order:
        _header(section.upper())
        if   section == "news":      run_news()
        elif section == "content":   run_content(force=args.force_content)
        elif section == "briefings": run_briefings()
        elif section == "reports":   run_reports(months=args.reports_months)
        elif section == "sectors":   run_sectors()
        elif section == "macro":     run_macro(no_live=args.no_live)
        elif section == "intel":     run_intel(ticker=args.ticker)
        elif section == "tickers":   run_tickers()
        elif section == "hourly":    run_hourly(tickers=[args.ticker] if args.ticker else None)

    elapsed = time.time() - total_start
    _header(f"DONE  ({elapsed/60:.1f} min)")
    print(f"  Sections completed: {', '.join(run_order)}")
    print(f"  Data ready in: {BASE_DIR / 'market_data'}")


if __name__ == "__main__":
    main()
