"""
learning/ledger.py
═══════════════════════════════════════════════════════════════════════
Durable, append-only recommendation + outcome ledger.

Unlike the 1-hour-TTL ``cache`` collection (``data/cache.py``), this is the
PERMANENT record the self-improvement flywheel reads from. Each completed
recommendation is logged with the price at the time of the call and the
forecast cone; ``score_recommendations.py`` later backfills the realized
forward returns (absolute + excess vs the KSE-100).

All writes are best-effort and MUST NEVER raise into the analysis pipeline —
a ledger failure can never be allowed to break a user's analysis.
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import (
    firebase_db,
    RECOMMENDATIONS_COLLECTION,
    ROUTING_VERSION,
)
from data.cache import sanitize_for_firestore

logger = logging.getLogger(__name__)


def _collection():
    """The recommendations collection ref, or None if Firestore is unavailable."""
    if firebase_db is None:
        return None
    return firebase_db.collection(RECOMMENDATIONS_COLLECTION)


def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _extract_agent_scores(report: Dict[str, Any]) -> Dict[str, Any]:
    """Pull lightweight numeric signals from each analyst sub-report (best-effort)."""

    def conf(key: str) -> Optional[float]:
        r = report.get(key) or {}
        if isinstance(r, dict):
            return _safe_float(r.get("confidence") or r.get("score") or r.get("risk_score"))
        return None

    return {
        "technical": conf("technical_report"),
        "fundamental": conf("fundamental_report"),
        "sentiment": conf("sentiment_report"),
        "risk": conf("risk_report"),
    }


def build_record(report: Dict[str, Any]) -> Dict[str, Any]:
    """Distil a full dossier into the lean ledger row (NOT the full text blobs)."""
    rec = report.get("recommendation") or {}
    quote = report.get("quote") or {}
    forecast = report.get("forecast") or {}
    price_at_call = _safe_float(quote.get("price")) or _safe_float(rec.get("current_price"))

    return {
        "symbol": report.get("symbol"),
        "company_name": report.get("company_name"),
        "sector": report.get("sector"),
        "routing_version": ROUTING_VERSION,
        "created_ts": time.time(),  # epoch float — drives the scorer's age math
        "as_of_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "price_at_call": price_at_call,
        "recommendation": rec.get("recommendation"),
        "confidence": _safe_float(rec.get("confidence")),
        "price_target_low": _safe_float(rec.get("price_target_low")),
        "price_target_high": _safe_float(rec.get("price_target_high")),
        "upside_pct": _safe_float(rec.get("upside_pct")),
        "time_horizon": rec.get("time_horizon"),
        "agent_scores": _extract_agent_scores(report),
        "thesis_summary": (rec.get("summary") or "")[:600],
        # Forecast median path (p50) per horizon, kept compact for later scoring
        "forecast_quantiles": forecast.get("horizons") if isinstance(forecast, dict) else None,
        "user_feedback": None,
        "outcomes": {},      # filled by score_recommendations.py
        "status": "pending",
    }


def record_recommendation(report: Dict[str, Any]) -> Optional[str]:
    """Append one recommendation to the ledger. Returns the new doc id, or None.

    Skips error dossiers and any call without a usable ``price_at_call`` (no
    anchor → no future return can be computed). Best-effort: all failures are
    logged and swallowed.
    """
    col = _collection()
    if col is None:
        return None

    rec = report.get("recommendation") or {}
    if not rec or not rec.get("recommendation"):
        return None  # nothing analytical to score

    try:
        record = sanitize_for_firestore(build_record(report))
        if not record.get("price_at_call"):
            logger.info("Ledger: skipping %s — no price_at_call to anchor outcomes.", record.get("symbol"))
            return None
        _, doc_ref = col.add(record)
        logger.info("Ledger: recorded %s for %s @ %.2f", doc_ref.id, record.get("symbol"), record["price_at_call"])
        return doc_ref.id
    except Exception as exc:  # never propagate into the pipeline
        logger.warning("Ledger write failed (non-fatal): %s", exc)
        return None


# ── Scorer support ───────────────────────────────────────────────────


def iter_unscored(max_age_days: int = 120) -> List[Dict[str, Any]]:
    """Ledger rows still needing scoring (status != ``scored_complete``).

    Bounded to ``max_age_days`` so the scorer doesn't sweep the entire history
    forever — once the longest horizon has elapsed there's nothing left to add.
    Each row is annotated with ``_id``.
    """
    col = _collection()
    if col is None:
        return []
    cutoff = time.time() - max_age_days * 86400
    rows: List[Dict[str, Any]] = []
    try:
        for doc in col.where("created_ts", ">=", cutoff).stream():
            data = doc.to_dict() or {}
            if data.get("status") == "scored_complete":
                continue
            data["_id"] = doc.id
            rows.append(data)
    except Exception as exc:
        logger.warning("Ledger unscored query failed: %s", exc)
    return rows


def update_outcomes(doc_id: str, outcomes: Dict[str, Any], status: str) -> bool:
    """Write backfilled realized outcomes and advance the row's status."""
    col = _collection()
    if col is None:
        return False
    try:
        col.document(doc_id).update({
            "outcomes": sanitize_for_firestore(outcomes),
            "status": status,
        })
        return True
    except Exception as exc:
        logger.warning("Ledger outcome update failed for %s: %s", doc_id, exc)
        return False


# ── Flywheel support (Phase 2 read path) ─────────────────────────────


def scored_history(symbol: str, sector: Optional[str] = None, limit: int = 40) -> List[Dict[str, Any]]:
    """Recent recommendations that already have ≥1 realized outcome.

    Prefers same-symbol history; if that's thin, widens to the sector so a
    rarely-analyzed name can still borrow a track record. Sorted newest-first.
    Single-field equality queries only (no composite index needed); ordering
    and the outcome filter are applied client-side.
    """
    col = _collection()
    if col is None:
        return []

    def _query(field: str, value: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            for doc in col.where(field, "==", value).stream():
                data = doc.to_dict() or {}
                if data.get("outcomes"):
                    out.append(data)
        except Exception as exc:
            logger.warning("Ledger scored_history query (%s=%s) failed: %s", field, value, exc)
        return out

    rows = _query("symbol", symbol.strip().upper())
    if len(rows) < 5 and sector:
        rows = rows + _query("sector", sector)
    # de-dup, newest first
    seen = set()
    unique = []
    for r in sorted(rows, key=lambda d: d.get("created_ts", 0.0), reverse=True):
        key = (r.get("symbol"), r.get("created_ts"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique[:limit]


def attach_feedback(doc_id: str, feedback: Dict[str, Any]) -> bool:
    """Record user feedback (thumbs / agree-disagree / note) against a call."""
    col = _collection()
    if col is None:
        return False
    try:
        col.document(doc_id).update({"user_feedback": sanitize_for_firestore(feedback)})
        return True
    except Exception as exc:
        logger.warning("Ledger feedback update failed for %s: %s", doc_id, exc)
        return False
