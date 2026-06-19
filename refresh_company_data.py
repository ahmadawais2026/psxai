"""
refresh_company_data.py
=======================
Master script — refreshes company-level financial data for one or more
PSX-listed companies.

Primary source: Zakheera.com (paid subscription — richer historical data, styled Excel)
Fallback:       AskAnalyst API -> Yahoo Finance (cash flow only)

Fetches for each ticker:
  - Income Statement  (Zakheera, then AskAnalyst)
  - Balance Sheet     (Zakheera, then AskAnalyst)
  - Financial Ratios  (Zakheera, then AskAnalyst)
  - Cash Flow         (AskAnalyst -> Yahoo Finance fallback)
  - Latest Quote      (AskAnalyst)
  - News & Announcements (AskAnalyst)

Output: company_data/{TICKER}/{TICKER}_financials.xlsx  (beautifully styled)

Usage:
  python refresh_company_data.py --tickers OGDC LUCK PPL
  python refresh_company_data.py --sector cement
  python refresh_company_data.py --all
  python refresh_company_data.py --tickers OGDC --period annual
  python refresh_company_data.py --batch 14
  python refresh_company_data.py --list-sectors
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))


# ── helpers ────────────────────────────────────────────────────────────────────

def _header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def _load_tickers() -> dict:
    """Return PSX_TICKERS dict from data/psx_tickers.py."""
    from data.psx_tickers import PSX_TICKERS
    return PSX_TICKERS


def _tickers_for_sector(sector: str, tickers: dict) -> list[str]:
    sector_l = sector.lower()
    return [
        sym for sym, info in tickers.items()
        if sector_l in str(info.get("sector", "")).lower()
    ]


def _resolve_company_ids(symbols: list[str]) -> dict[str, Optional[int]]:
    """
    Return {symbol: askanalyst_id} for each symbol.
    Prefers the pre-mapped ID from PSX_TICKERS; falls back to API lookup.
    """
    from data.psx_tickers import PSX_TICKERS
    try:
        from scrape_askanalyst import get_company_id
    except ImportError:
        get_company_id = None

    ids: dict[str, Optional[int]] = {}
    need_lookup = []

    for sym in symbols:
        cid = PSX_TICKERS.get(sym, {}).get("askanalyst_id")
        if cid:
            ids[sym] = cid
        else:
            need_lookup.append(sym)

    if need_lookup and get_company_id:
        print(f"  Resolving {len(need_lookup)} company IDs via API...")
        for sym in need_lookup:
            try:
                result = get_company_id(sym)
                ids[sym] = result[0] if isinstance(result, tuple) else result
                if ids[sym]:
                    print(f"    {sym} -> id={ids[sym]}")
                else:
                    print(f"    {sym} -> NOT FOUND")
            except Exception as e:
                print(f"    {sym} -> lookup failed: {e}")
                ids[sym] = None
            time.sleep(1)

    return ids


# ── per-company fetch ──────────────────────────────────────────────────────────

def fetch_one(symbol: str, zakheera_session, zakheera_map: dict,
              period: str = "annual") -> bool:
    """
    Fetch all financial data for a single company and save to
    company_data/{symbol}/.  Returns True on success.

    Primary source: Zakheera (Income Statement, Balance Sheet, Ratios)
    Fallback:       AskAnalyst API -> Yahoo Finance (Cash Flow, Quote, News)
    """
    try:
        from scrape_zakheera_financials import (
            fetch_zakheera_statement,
            askanalyst_company_id,
            askanalyst_statement,
            parse_askanalyst_json,
            yfinance_cashflow,
            recompute_totals,
            save_beautiful_excel,
        )
    except ImportError as e:
        print(f"  [!] Cannot import scrape_zakheera_financials: {e}")
        return False

    import pandas as pd

    option_code = zakheera_map.get(symbol.upper())
    aa_id: Optional[int] = None

    def get_aa_id():
        nonlocal aa_id
        if aa_id is None:
            aa_id = askanalyst_company_id(symbol)
        return aa_id

    zk_period = "Q" if period == "quarter" else "Y"
    aa_period = period  # "annual" or "quarter"

    sheets: dict = {
        "Income Statement": None,
        "Balance Sheet":    None,
        "Cash Flow":        None,
        "Financial Ratios": None,
    }

    # ── Income Statement ─────────────────────────────────────────────────────
    print(f"    Income Statement ({period})...")
    if zakheera_session and option_code:
        sheets["Income Statement"] = fetch_zakheera_statement(
            zakheera_session, option_code, "i", zk_period)
        if sheets["Income Statement"] is not None:
            print("      [OK] Zakheera")
    if sheets["Income Statement"] is None:
        print("      [~] Trying AskAnalyst...")
        if get_aa_id():
            raw = askanalyst_statement("iss", aa_id, aa_period)
            sheets["Income Statement"] = parse_askanalyst_json(raw)
            if sheets["Income Statement"] is not None:
                print("      [OK] AskAnalyst")
    time.sleep(1.2)

    # ── Balance Sheet ─────────────────────────────────────────────────────────
    print(f"    Balance Sheet ({period})...")
    if zakheera_session and option_code:
        sheets["Balance Sheet"] = fetch_zakheera_statement(
            zakheera_session, option_code, "b", zk_period)
        if sheets["Balance Sheet"] is not None:
            print("      [OK] Zakheera")
    if sheets["Balance Sheet"] is None:
        print("      [~] Trying AskAnalyst...")
        if get_aa_id():
            raw = askanalyst_statement("bss", aa_id, aa_period)
            sheets["Balance Sheet"] = parse_askanalyst_json(raw)
            if sheets["Balance Sheet"] is not None:
                print("      [OK] AskAnalyst")
    time.sleep(1.2)

    # ── Financial Ratios ──────────────────────────────────────────────────────
    print(f"    Financial Ratios ({period})...")
    if zakheera_session and option_code:
        sheets["Financial Ratios"] = fetch_zakheera_statement(
            zakheera_session, option_code, "t", zk_period)
        if sheets["Financial Ratios"] is not None:
            print("      [OK] Zakheera")
    if sheets["Financial Ratios"] is None:
        print("      [~] Trying AskAnalyst...")
        if get_aa_id():
            raw = askanalyst_statement("ratio", aa_id, aa_period)
            sheets["Financial Ratios"] = parse_askanalyst_json(raw)
            if sheets["Financial Ratios"] is not None:
                print("      [OK] AskAnalyst")
    time.sleep(1.2)

    # ── Cash Flow (AskAnalyst -> Yahoo Finance) ───────────────────────────────
    print(f"    Cash Flow ({period})...")
    if get_aa_id():
        raw = askanalyst_statement("cf", aa_id, aa_period)
        if raw and isinstance(raw, dict):
            cf_list = raw.get("indirect") or raw.get("direct")
            sheets["Cash Flow"] = parse_askanalyst_json(cf_list)
            if sheets["Cash Flow"] is not None:
                print("      [OK] AskAnalyst")
    if sheets["Cash Flow"] is None:
        print("      [~] Trying Yahoo Finance...")
        sheets["Cash Flow"] = yfinance_cashflow(symbol, zk_period)
        if sheets["Cash Flow"] is not None:
            print("      [OK] Yahoo Finance")
    time.sleep(1.2)

    # ── Latest Quote & News (AskAnalyst) ─────────────────────────────────────
    from scrape_askanalyst import (
        fetch_share_price_quote, fetch_company_news, parse_statement_json
    )

    print("    Latest Quote...")
    if get_aa_id():
        quote_raw = fetch_share_price_quote(aa_id)
        if quote_raw and isinstance(quote_raw, dict):
            df_quote = pd.DataFrame(list(quote_raw.items()), columns=["Metric", "Value"])
            df_quote["Value"] = pd.to_numeric(df_quote["Value"], errors="coerce").fillna(df_quote["Value"])
            sheets["Latest Quote"] = df_quote
            print("      [OK] AskAnalyst")
    time.sleep(1.2)

    print("    News & Announcements...")
    if get_aa_id():
        news_raw = fetch_company_news(aa_id)
        if news_raw:
            df_news = pd.DataFrame(news_raw)
            if not df_news.empty:
                cols = [c for c in ["date", "title", "type", "status", "url"] if c in df_news.columns]
                sheets["News & Announcements"] = df_news[cols]
                print(f"      [OK] {len(df_news)} items")
    time.sleep(1.2)

    # ── Recompute totals ──────────────────────────────────────────────────────
    type_map = {
        "Income Statement": "income",
        "Balance Sheet":    "balance",
        "Cash Flow":        "cashflow",
    }
    for sheet_name, stype in type_map.items():
        if sheets.get(sheet_name) is not None:
            sheets[sheet_name] = recompute_totals(sheets[sheet_name], stype)

    # ── Check we got something ────────────────────────────────────────────────
    if not any(v is not None for v in sheets.values()):
        print(f"    [!] No data retrieved for {symbol}")
        return False

    # ── Save beautiful Excel ──────────────────────────────────────────────────
    out_dir = BASE_DIR / "company_data" / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = str(out_dir / f"{symbol}_financials.xlsx")

    suffix = 0
    while True:
        try:
            save_beautiful_excel(sheets, out_file)
            print(f"    [saved] {out_file}")
            return True
        except PermissionError:
            suffix += 1
            out_file = str(out_dir / f"{symbol}_financials_{suffix}.xlsx")
            if suffix > 10:
                print(f"    [-] File locked — giving up")
                return False


# ── batch definitions ──────────────────────────────────────────────────────────

BATCHES: dict[int, list[str]] = {
    1:  ["OGDC", "PPL", "POL", "MARI", "GHOUL"],
    2:  ["ENGRO", "LUCK", "FCCL", "CHCC", "PIOC", "MLCF"],
    3:  ["MCB", "UBL", "HBL", "ABL", "BAHL", "NBP"],
    4:  ["PSO", "SHEL", "APL", "HASCOL", "ATRL", "NRL"],
    5:  ["FFC", "EFERT", "FFBL", "DAWH", "FATIMA"],
    6:  ["INDU", "HCAR", "SAZEW", "PSMC", "GHNL"],
    7:  ["HUBC", "KAPCO", "NCPL", "LALPIR", "PKGP"],
    8:  ["SYS", "TRG", "NETSOL", "AVN", "TELE"],
    9:  ["GLAXO", "SEARL", "ABOT", "HINOON", "FEROZ"],
    10: ["NESTLE", "COLG", "UNILEVER", "ICI", "NATF"],
    11: ["KOHC", "ACPL", "DGKC", "POWER", "THCCL"],
    12: ["FABL", "MEBL", "SILK", "SNBL", "JSBL"],
    13: ["PKGS", "INIL", "GADT", "TREET", "GHCL"],
    14: ["NONS", "NPL", "NML", "PNSC", "PAEL", "PAPL", "PIBTL", "PIOC", "PKGS", "PKL",
         "PAKT", "PNSC", "POWER", "PTCL", "THCCL", "TREET", "UNITY", "WAVES", "WYETH", "PINL"],
}


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Refresh PSX company financial data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tickers",       nargs="+", metavar="SYMBOL",
                       help="Specific tickers (e.g. OGDC LUCK PPL)")
    group.add_argument("--sector",        metavar="SECTOR",
                       help="All companies in a sector (e.g. cement, banks)")
    group.add_argument("--batch",         type=int,  metavar="N",
                       help=f"Pre-defined batch number (1-{max(BATCHES)})")
    group.add_argument("--all",           action="store_true",
                       help="All companies in PSX_TICKERS database (~500)")
    group.add_argument("--list-sectors",  action="store_true",
                       help="List available sectors and exit")

    parser.add_argument("--period", choices=["quarter", "annual"], default="quarter",
                        help="Financial data period (default: quarter)")
    parser.add_argument("--delay",  type=float, default=1.2,
                        help="Seconds between API calls (default: 1.2)")

    args = parser.parse_args()

    tickers_db = _load_tickers()

    # --list-sectors
    if args.list_sectors:
        sectors: dict[str, int] = {}
        for info in tickers_db.values():
            s = info.get("sector", "Unknown")
            sectors[s] = sectors.get(s, 0) + 1
        print("Available sectors:")
        for s, count in sorted(sectors.items(), key=lambda x: -x[1]):
            print(f"  {count:4d}  {s}")
        return

    # Build target list
    if args.tickers:
        symbols = [t.upper() for t in args.tickers]
    elif args.sector:
        symbols = _tickers_for_sector(args.sector, tickers_db)
        if not symbols:
            print(f"[-] No tickers found for sector: {args.sector}")
            return
        print(f"[*] {len(symbols)} tickers in sector '{args.sector}': {', '.join(symbols[:10])}{'...' if len(symbols)>10 else ''}")
    elif args.batch:
        symbols = BATCHES.get(args.batch, [])
        if not symbols:
            print(f"[-] Batch {args.batch} not defined. Available: {sorted(BATCHES)}")
            return
        print(f"[*] Batch {args.batch}: {len(symbols)} tickers")
    elif args.all:
        symbols = list(tickers_db.keys())
        print(f"[*] ALL: {len(symbols)} tickers — this will take a long time")

    _header(f"Company Data Refresh — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Period:  {args.period}")
    print(f"  Tickers: {len(symbols)}")

    # Build Zakheera session once (shared across all companies)
    _header("ZAKHEERA LOGIN")
    try:
        from scrape_zakheera_financials import get_zakheera_session, get_zakheera_company_map
        zk_session = get_zakheera_session()
        if zk_session:
            print("  [OK] Logged in to Zakheera")
            zk_map = get_zakheera_company_map(zk_session)
            print(f"  [OK] {len(zk_map)} companies in Zakheera map")
        else:
            print("  [!] Zakheera login failed — will use AskAnalyst only")
            zk_map = {}
    except ImportError as e:
        print(f"  [!] scrape_zakheera_financials not available: {e}")
        zk_session = None
        zk_map = {}

    # Fetch each company
    _header("FETCHING FINANCIAL DATA")
    total_start = time.time()
    success = failed = 0

    for i, symbol in enumerate(symbols, 1):
        print(f"\n[{i}/{len(symbols)}] {symbol}")
        zk_hit = symbol.upper() in zk_map
        print(f"  Zakheera: {'found' if zk_hit else 'not in map — will use AskAnalyst'}")
        ok = fetch_one(symbol, zk_session, zk_map, period=args.period)
        if ok:
            success += 1
        else:
            failed += 1

    elapsed = time.time() - total_start
    _header(f"DONE  ({elapsed/60:.1f} min)")
    print(f"  Success:  {success}")
    print(f"  Failed:   {failed}")
    print(f"  Data in:  {BASE_DIR / 'company_data'}")


if __name__ == "__main__":
    main()
