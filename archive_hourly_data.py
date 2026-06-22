"""
archive_hourly_data.py
=========================================================================
Downloads the current day's real-time intraday tick data from the PSX portal,
aggregates it into hourly OHLCV bars, merges it with existing history,
and saves a 7-day rolling history of hourly bars to Firestore.

Run this script daily at market close (e.g. 5:00 PM PKT) to build up history.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Force UTF-8 stdout/stderr
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from data.psx_portal import fetch_intraday_ticks
from data.intraday_aggregator import aggregate_ticks_to_hours, merge_hourly_bars
from data.psx_tickers import PSX_TICKERS


def archive_company_hourly(db, company: Dict[str, Any], dry_run: bool) -> tuple[int, int]:
    """Fetch, aggregate, and store hourly data for a single company."""
    symbol = str(company.get("symbol", "")).upper().strip()
    if not symbol:
        return 0, 0
        
    ticks = fetch_intraday_ticks(symbol)
    if not ticks:
        return 0, 0
        
    new_bars = aggregate_ticks_to_hours(ticks)
    if not new_bars:
        return 0, 0
        
    if dry_run:
        print(f"  [dry-run] {symbol}: generated {len(new_bars)} hourly bars for today.")
        return len(new_bars), 0
        
    # Read existing bars from Firestore
    doc_ref = db.collection("companies").document(symbol).collection("market").document("history_hourly")
    existing_bars = []
    try:
        doc = doc_ref.get()
        if doc.exists:
            existing_bars = (doc.to_dict() or {}).get("bars", [])
    except Exception as e:
        print(f"  [!] Failed to read existing hourly bars for {symbol}: {e}")
        
    # Merge and enforce 7-day retention
    merged_bars = merge_hourly_bars(existing_bars, new_bars, max_days=7)
    
    # Save back to Firestore
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "symbol": symbol,
        "period": "1w",
        "interval": "1h",
        "last_updated": now_str,
        "bars": merged_bars
    }
    
    try:
        doc_ref.set(payload)
        print(f"  [+] {symbol}: written {len(new_bars)} new bars (Total in Firestore: {len(merged_bars)} bars).")
        return len(new_bars), len(merged_bars)
    except Exception as e:
        print(f"  [-] {symbol}: failed to write to Firestore: {e}")
        return 0, 0


def main():
    ap = argparse.ArgumentParser(description="Archive hourly PSX data from intraday ticks.")
    ap.add_argument("--tickers", nargs="+", help="Specific tickers to archive.")
    ap.add_argument("--dry-run", action="store_true", help="Fetch and aggregate only; do not write to Firestore.")
    ap.add_argument("--workers", type=int, default=5, help="Number of concurrent workers.")
    args = ap.parse_args()

    print("=" * 64)
    print(f"  PSX Hourly Archiver — {'DRY RUN' if args.dry_run else 'LIVE WRITE'}")
    print("=" * 64)

    db = None
    if not args.dry_run:
        from config import firebase_db
        db = firebase_db
        if db is None:
            print("[-] Firestore client not initialized. Aborting.")
            return

    all_companies = []
    for sym, details in PSX_TICKERS.items():
        all_companies.append({
            "symbol": sym,
            "id": details.get("askanalyst_id"),
            "name": details.get("name"),
            "sector": details.get("sector")
        })

    if args.tickers:
        wanted = {t.upper() for t in args.tickers}
        companies = [c for c in all_companies if str(c.get("symbol", "")).upper() in wanted]
    else:
        companies = all_companies

    print(f"[+] Processing hourly data for {len(companies)} companies with {args.workers} workers.\n")

    stats = {"ok": 0, "failed": 0, "bars_written": 0}
    state_lock = threading.Lock()
    total = len(companies)

    def worker(i: int, company: Dict[str, Any]):
        symbol = str(company.get("symbol", "")).upper().strip()
        try:
            new_count, total_count = archive_company_hourly(db, company, args.dry_run)
            with state_lock:
                if new_count > 0:
                    stats["ok"] += 1
                    stats["bars_written"] += new_count
                else:
                    stats["failed"] += 1
        except Exception as e:
            print(f"  [-] {symbol}: ERROR in archiver: {e}")
            with state_lock:
                stats["failed"] += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(worker, i, c) for i, c in enumerate(companies, 1)]
        concurrent.futures.wait(futures)

    print("\n" + "=" * 64)
    print(f"  Done. Processed: {stats['ok'] + stats['failed']} | Success: {stats['ok']} | Empty/Failed: {stats['failed']} | New bars: {stats['bars_written']}")
    print("=" * 64)


if __name__ == "__main__":
    main()
