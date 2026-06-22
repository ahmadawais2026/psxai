"""
backfill_firestore.py
═════════════════════════════════════════════════════════════════════════
Bulk backfill of PSX company data into Firestore using the existing
AskAnalyst / PSX DPS API pipeline (NOT HTML scraping — the data is
served as JSON and that path is faster, cleaner, and free).

For every company listed by AskAnalyst it writes, in the exact shape the
live app reads:

  companies/{SYMBOL}/financials/annual   → income/balance/cash-flow (list-of-rows)
  companies/{SYMBOL}/financials/quarter  → income/balance (list-of-rows)
  companies/{SYMBOL}/market/history_1y   → 12 months of daily OHLCV bars

Usage
-----
  # Fetch + parse a few tickers and print the doc shape — NO Firestore write
  python backfill_firestore.py --dry-run --tickers ABOT OGDC

  # Write a small real batch (tests Firestore connectivity end-to-end)
  python backfill_firestore.py --tickers ABOT OGDC LUCK

  # Full run over every company, skipping ones already written
  python backfill_firestore.py --all --skip-existing

  # Full run, limited to first N companies
  python backfill_firestore.py --all --limit 25
"""

from __future__ import annotations

import argparse
import concurrent.futures
import math
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# Windows consoles default to cp1252, which crashes on the "→" in our progress
# lines (UnicodeEncodeError) *after* a Firestore write has already happened —
# making successful writes look like failures. Force UTF-8 stdout/stderr.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scrape_askanalyst import (
    fetch_statement_data,
    fetch_cash_flow,
    parse_statement_json,
    parse_cash_flow_json,
)

BASE_API_URL = "https://api.askanalyst.com.pk/api"


# ── helpers ──────────────────────────────────────────────────────────────


def _clean(value: Any) -> Any:
    """Make a value Firestore-safe: NaN/NaT → None, numpy scalars → native."""
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
    except (TypeError, ValueError):
        pass
    # numpy scalar → python scalar
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return value.item()
        except Exception:
            return value
    return value


def _df_to_rows(df) -> List[Dict[str, Any]]:
    """Convert a parsed statement DataFrame to a Firestore list-of-rows,
    matching what _parse_firestore_financials_to_highlights expects."""
    if df is None or df.empty:
        return []
    rows = df.to_dict(orient="records")
    return [{k: _clean(v) for k, v in row.items()} for row in rows]


def get_company_list() -> List[Dict[str, Any]]:
    """Return AskAnalyst's full company list with ids/symbols/sectors."""
    r = requests.get(f"{BASE_API_URL}/companylistwithids", timeout=20)
    r.raise_for_status()
    return r.json()


def build_financials_doc(company_id: int, period: str) -> Dict[str, Any]:
    """Fetch + parse income, balance, and (annual only) cash-flow statements."""
    doc: Dict[str, Any] = {}

    is_raw = fetch_statement_data("iss", company_id, period)
    doc["income_statement"] = _df_to_rows(parse_statement_json(is_raw)) if is_raw else []

    bs_raw = fetch_statement_data("bss", company_id, period)
    doc["balance_sheet"] = _df_to_rows(parse_statement_json(bs_raw)) if bs_raw else []

    # AskAnalyst only serves cash flow at annual cadence.
    if period == "annual":
        cf_raw = fetch_cash_flow(company_id)
        doc["cash_flow"] = _df_to_rows(parse_cash_flow_json(cf_raw)) if cf_raw else []
    else:
        doc["cash_flow"] = []

    return doc


def _rows_to_bars(df, start, end, interval: str, fmt: str) -> List[Dict[str, Any]]:
    """Turn an OHLCV DataFrame into bar dicts for rows in [start, end).
    end=None means open-ended (up to now). Each bar carries its interval so
    consumers can tell the resolution apart."""
    if df is None or df.empty:
        return []
    import pandas as pd
    bars = []
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) if end is not None else None
    for ts, row in df.iterrows():
        t = pd.Timestamp(ts)
        if t.tz is not None:
            t = t.tz_localize(None)
        if t < start_ts or (end_ts is not None and t >= end_ts):
            continue
        bars.append({
            "date": t.strftime(fmt),
            "interval": interval,
            "open": _clean(float(row["Open"])) if row.get("Open") is not None else None,
            "high": _clean(float(row["High"])) if row.get("High") is not None else None,
            "low": _clean(float(row["Low"])) if row.get("Low") is not None else None,
            "close": _clean(float(row["Close"])) if row.get("Close") is not None else None,
            "volume": _clean(float(row["Volume"])) if row.get("Volume") is not None else None,
        })
    return bars


def build_history_bars(symbol: str) -> List[Dict[str, Any]]:
    """Build a 1-year daily OHLCV history (~251 bars) from the PSX DPS portal.

    Only daily is sourced: it's the one interval the PSX portal serves
    (`get_history`'s interval == "1d" path). PSX intraday/weekly are NOT
    available as APIs — the only non-daily source is Yahoo, which hangs on
    `.KAR` tickers, and nothing reads this doc back, so coarser tiers buy
    nothing. One fetch, no Yahoo, no resample."""
    from datetime import datetime, timedelta
    from data.market_data import get_history

    one_year = datetime.now() - timedelta(days=366)

    try:
        df = get_history(symbol, "1y", "1d")  # PSX DPS portal
    except Exception as e:
        print(f"      [history] daily fetch failed for {symbol}: {e}")
        return []

    bars = _rows_to_bars(df, one_year, None, "1d", "%Y-%m-%d")

    # Chronological order; dedupe identical timestamps.
    seen = set()
    out = []
    for b in sorted(bars, key=lambda x: x["date"]):
        if b["date"] in seen:
            continue
        seen.add(b["date"])
        out.append(b)
    return out


def doc_exists(db, symbol: str) -> bool:
    """True if this company already has a non-empty annual financials doc written."""
    try:
        ref = db.collection("companies").document(symbol).collection("financials").document("annual")
        doc = ref.get()
        if not doc.exists:
            return False
        data = doc.to_dict() or {}
        return len(data.get("income_statement", [])) > 0
    except Exception:
        return False


# ── main backfill ──────────────────────────────────────────────────────────


def backfill_company(db, company: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    symbol = str(company.get("symbol", "")).upper().strip()
    cid = company.get("id")
    name = company.get("name") or symbol
    sector = company.get("sector") or "N/A"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    annual = build_financials_doc(cid, "annual")
    quarter = build_financials_doc(cid, "quarter")
    bars = build_history_bars(symbol)

    annual_meta = {"symbol": symbol, "name": name, "sector": sector,
                   "period": "annual", "last_updated": now, **annual}
    quarter_meta = {"symbol": symbol, "name": name, "sector": sector,
                    "period": "quarter", "last_updated": now, **quarter}
    history_meta = {"symbol": symbol, "period": "1y", "interval": "1d",
                    "bars": bars, "last_updated": now}

    counts = {
        "symbol": symbol,
        "annual_is_rows": len(annual["income_statement"]),
        "annual_bs_rows": len(annual["balance_sheet"]),
        "annual_cf_rows": len(annual["cash_flow"]),
        "quarter_is_rows": len(quarter["income_statement"]),
        "quarter_bs_rows": len(quarter["balance_sheet"]),
        "history_bars": len(bars),
    }

    if not dry_run:
        base = db.collection("companies").document(symbol)
        base.set({"symbol": symbol, "name": name, "sector": sector,
                  "last_updated": now}, merge=True)
        base.collection("financials").document("annual").set(annual_meta)
        base.collection("financials").document("quarter").set(quarter_meta)
        base.collection("market").document("history_1y").set(history_meta)

    return counts


def main():
    ap = argparse.ArgumentParser(description="Backfill PSX company data to Firestore via the AskAnalyst API pipeline.")
    ap.add_argument("--all", action="store_true", help="Process every company in the AskAnalyst list.")
    ap.add_argument("--tickers", nargs="+", help="Specific tickers to process (overrides --all).")
    ap.add_argument("--limit", type=int, default=None, help="Cap the number of companies processed.")
    ap.add_argument("--dry-run", action="store_true", help="Fetch + parse only; do NOT write to Firestore.")
    ap.add_argument("--skip-existing", action="store_true", help="Skip companies that already have an annual doc.")
    ap.add_argument("--sleep", type=float, default=2.0, help="Delay between companies (seconds).")
    ap.add_argument("--workers", type=int, default=3, help="Number of concurrent worker threads.")
    args = ap.parse_args()

    print("=" * 64)
    print(f"  Firestore backfill — {'DRY RUN (no writes)' if args.dry_run else 'LIVE WRITE to aiforpsx'}")
    print("=" * 64)

    # Resolve Firestore client only when we actually need to write/skip-check.
    db = None
    if not args.dry_run or args.skip_existing:
        from config import firebase_db
        db = firebase_db
        if db is None:
            print("[-] Firestore client not initialized — check credentials. Aborting.")
            return
        # Connectivity probe so we fail fast rather than mid-run.
        try:
            next(db.collection("companies").limit(1).stream(), None)
            print("[+] Firestore reachable.")
        except Exception as e:
            print(f"[-] Firestore unreachable from this environment: {e}")
            print("    Run this script where Firestore is reachable (e.g. your deployed env), "
                  "or use --dry-run here to validate fetch + shape.")
            return

    try:
        all_companies = get_company_list()
    except Exception as e:
        print(f"[-] Failed to fetch company list: {e}")
        return
    print(f"[+] AskAnalyst lists {len(all_companies)} companies.")

    if args.tickers:
        wanted = {t.upper() for t in args.tickers}
        companies = [c for c in all_companies if str(c.get("symbol", "")).upper() in wanted]
        missing = wanted - {str(c.get("symbol", "")).upper() for c in companies}
        if missing:
            print(f"[!] Not found in AskAnalyst list: {sorted(missing)}")
    else:
        if not args.all:
            print("[-] Specify --all or --tickers. Nothing to do.")
            return
        companies = all_companies

    if args.limit:
        companies = companies[: args.limit]

    print(f"[+] Processing {len(companies)} companies with {args.workers} workers.\n")

    stats = {"ok": 0, "skipped": 0, "failed": 0}
    failures = []
    state_lock = threading.Lock()
    total = len(companies)

    def process_company(i: int, company: Dict[str, Any]):
        symbol = str(company.get("symbol", "")).upper().strip()
        if not symbol or not company.get("id"):
            return
        if args.skip_existing and db is not None and doc_exists(db, symbol):
            print(f"[{i}/{total}] {symbol}: already present — skipped.")
            with state_lock:
                stats["skipped"] += 1
            return
        try:
            counts = backfill_company(db, company, args.dry_run)
            print(f"[{i}/{total}] {symbol}: "
                  f"annual(IS {counts['annual_is_rows']}/BS {counts['annual_bs_rows']}/CF {counts['annual_cf_rows']}) "
                  f"quarter(IS {counts['quarter_is_rows']}/BS {counts['quarter_bs_rows']}) "
                  f"history({counts['history_bars']} bars)"
                  + ("" if args.dry_run else " → written"))
            with state_lock:
                stats["ok"] += 1
        except Exception as e:
            print(f"[{i}/{total}] {symbol}: FAILED — {e}")
            with state_lock:
                stats["failed"] += 1
                failures.append((symbol, str(e)))
        if args.sleep > 0:
            time.sleep(args.sleep)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        # Submit all tasks
        futures = {executor.submit(process_company, i, company): company for i, company in enumerate(companies, 1)}
        # Wait for all to complete
        concurrent.futures.wait(futures)

    print("\n" + "=" * 64)
    print(f"  Done. ok={stats['ok']}  skipped={stats['skipped']}  failed={stats['failed']}")
    if failures:
        print("  Failures:")
        for sym, err in failures:
            print(f"    {sym}: {err}")
    print("=" * 64)


if __name__ == "__main__":
    main()
