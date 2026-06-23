"""
score_recommendations.py
═══════════════════════════════════════════════════════════════════════
Outcome scorer for the recommendation ledger — the back half of the learning
flywheel. Revisits each stored recommendation once a forward horizon has
elapsed and records the realized return, the KSE-100 benchmark return, and the
excess (alpha). Designed to run nightly (Cloud Scheduler / cron), mirroring the
other maintenance scripts (refresh_market_data.py, archive_hourly_data.py).

CRITICAL GUARD: operational failures (missing/empty price history) are NEVER
recorded as analytical misses — the row is simply left pending and retried on
the next run. Only real, observed prices produce an outcome.

Usage:
    python score_recommendations.py --dry-run          # compute & print, no writes
    python score_recommendations.py                     # score & persist
    python score_recommendations.py --limit 50 --sleep 1.0
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from config import OUTCOME_HORIZONS, BENCHMARK_SYMBOL
from data.market_data import get_history
from learning import ledger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("score_recommendations")

# Recommendation verb → directional expectation, for hit/miss labeling.
_BULLISH = {"STRONG BUY", "BUY", "ACCUMULATE"}
_BEARISH = {"SELL", "STRONG SELL", "TRIM"}


def _close_on_or_after(df: pd.DataFrame, when: pd.Timestamp) -> Tuple[Optional[float], Optional[pd.Timestamp]]:
    sub = df[df.index >= when]
    if len(sub):
        return float(sub["Close"].iloc[0]), sub.index[0]
    return None, None


def _close_on_or_before(df: pd.DataFrame, when: pd.Timestamp) -> Tuple[Optional[float], Optional[pd.Timestamp]]:
    sub = df[df.index <= when]
    if len(sub):
        return float(sub["Close"].iloc[-1]), sub.index[-1]
    return None, None


def _label_correct(recommendation: Optional[str], signed_return: Optional[float]) -> Optional[bool]:
    """Was the directional call right? Uses excess return when available.

    HOLD-type verdicts have no directional expectation → returns None (neutral).
    """
    if signed_return is None or not recommendation:
        return None
    verb = recommendation.strip().upper()
    if verb in _BULLISH:
        return signed_return > 0
    if verb in _BEARISH:
        return signed_return < 0
    return None  # HOLD / neutral


def score_row(row: Dict[str, Any], benchmark_df: Optional[pd.DataFrame], now_ts: float) -> Optional[Dict[str, Any]]:
    """Return (outcomes, status) for one ledger row, or None to skip (operational).

    Skips entirely (no write) when the stock's price history can't be loaded —
    that is an operational failure, not an analytical miss.
    """
    symbol = row.get("symbol")
    price_at_call = row.get("price_at_call")
    created_ts = row.get("created_ts")
    if not symbol or not price_at_call or not created_ts:
        return None

    stock_df = get_history(symbol)
    if stock_df is None or stock_df.empty or "Close" not in stock_df.columns:
        logger.info("  %s: price history unavailable — leaving pending (operational).", symbol)
        return None

    created_dt = pd.Timestamp(datetime.fromtimestamp(created_ts, tz=timezone.utc).replace(tzinfo=None))

    # Benchmark price at the time of the call (for excess return).
    bench_at_call = None
    if benchmark_df is not None and not benchmark_df.empty:
        bench_at_call, _ = _close_on_or_before(benchmark_df, created_dt)

    outcomes: Dict[str, Any] = dict(row.get("outcomes") or {})
    elapsed = 0
    for label, days in OUTCOME_HORIZONS.items():
        target_ts = created_ts + days * 86400
        if now_ts < target_ts:
            continue  # horizon not yet reached
        elapsed += 1
        if label in outcomes and outcomes[label].get("price") is not None:
            continue  # already scored

        target_dt = pd.Timestamp(datetime.fromtimestamp(target_ts, tz=timezone.utc).replace(tzinfo=None))
        fwd_price, fwd_date = _close_on_or_after(stock_df, target_dt)
        if fwd_price is None:
            # Horizon elapsed but no price bar yet available — retry next run.
            continue

        ret_pct = (fwd_price / price_at_call - 1.0) * 100.0
        excess_pct = None
        bench_ret_pct = None
        if bench_at_call:
            bench_fwd, _ = _close_on_or_after(benchmark_df, target_dt)
            if bench_fwd:
                bench_ret_pct = (bench_fwd / bench_at_call - 1.0) * 100.0
                excess_pct = ret_pct - bench_ret_pct

        signed = excess_pct if excess_pct is not None else ret_pct
        outcomes[label] = {
            "date": fwd_date.strftime("%Y-%m-%d") if fwd_date is not None else None,
            "price": round(fwd_price, 2),
            "return_pct": round(ret_pct, 2),
            "benchmark_return_pct": round(bench_ret_pct, 2) if bench_ret_pct is not None else None,
            "excess_return_pct": round(excess_pct, 2) if excess_pct is not None else None,
            "correct": _label_correct(row.get("recommendation"), signed),
        }

    if not outcomes:
        return None  # nothing scored yet

    scored = sum(1 for k in OUTCOME_HORIZONS if k in outcomes)
    status = "scored_complete" if (elapsed == len(OUTCOME_HORIZONS) and scored == len(OUTCOME_HORIZONS)) else "scored_partial"
    return {"outcomes": outcomes, "status": status}


def main() -> None:
    ap = argparse.ArgumentParser(description="Score pending recommendations against realized PSX prices.")
    ap.add_argument("--dry-run", action="store_true", help="Compute and print outcomes without writing to Firestore.")
    ap.add_argument("--limit", type=int, default=0, help="Max rows to process (0 = all).")
    ap.add_argument("--max-age-days", type=int, default=120, help="Ignore rows older than this many days.")
    ap.add_argument("--sleep", type=float, default=1.0, help="Pacing sleep (s) between unique-symbol fetches.")
    args = ap.parse_args()

    now_ts = time.time()
    rows = ledger.iter_unscored(max_age_days=args.max_age_days)
    if args.limit:
        rows = rows[: args.limit]
    logger.info("Scoring %d unscored recommendation(s)%s.", len(rows), " [DRY RUN]" if args.dry_run else "")

    benchmark_df = get_history(BENCHMARK_SYMBOL)
    if benchmark_df is None or benchmark_df.empty:
        logger.warning("Benchmark (%s) history unavailable — excess returns will be omitted.", BENCHMARK_SYMBOL)
        benchmark_df = None

    seen_symbols = set()
    updated = skipped = 0
    for row in rows:
        symbol = row.get("symbol")
        if symbol and symbol not in seen_symbols:
            seen_symbols.add(symbol)
            time.sleep(args.sleep)  # polite pacing on first fetch of each symbol

        result = score_row(row, benchmark_df, now_ts)
        if result is None:
            skipped += 1
            continue

        logger.info(
            "  %s [%s] → %s (%s)",
            symbol, row.get("recommendation"), result["status"],
            ", ".join(f"{k}:{v['return_pct']:+.1f}%" for k, v in result["outcomes"].items()),
        )
        if not args.dry_run:
            if ledger.update_outcomes(row["_id"], result["outcomes"], result["status"]):
                updated += 1
        else:
            updated += 1

    logger.info("Done. %d updated, %d skipped (pending/operational).", updated, skipped)


if __name__ == "__main__":
    main()
