"""
learning/calibration.py
═══════════════════════════════════════════════════════════════════════
Track-record / calibration summaries for the Outcome-RAG flywheel.

Reads scored rows from the recommendation ledger and distils them into a SHORT,
human-readable block injected into the Portfolio Manager's prompt — "here is how
this system's past calls on this name/sector actually turned out vs the KSE-100."

Insight-governance discipline (per the research): return a compact distilled
string, never raw rows — raw logs cause context collapse. Be honest about small
samples: with ~70 names and months-long horizons this is a weak prior for a long
time, so the wording explicitly frames it as a prior, not a parameter.
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from config import OUTCOME_HORIZONS
from learning import ledger

logger = logging.getLogger(__name__)

_NO_RECORD = (
    "TRACK RECORD: No scored past calls for this name or sector yet — rely on "
    "first-principles analysis and do not over-anchor on prior conviction."
)

# Horizon preferred for headline excess-return / hit-rate stats.
_PRIMARY_HORIZON = "1m" if "1m" in OUTCOME_HORIZONS else next(iter(OUTCOME_HORIZONS), "1m")


def _mean(vals: List[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def build_calibration_context(symbol: str, sector: Optional[str] = None) -> str:
    """Build the Portfolio Manager's track-record block (best-effort).

    Returns a short multi-line string. On any failure or empty history, returns
    a neutral "no track record" notice so the prompt slot is always well-formed.
    """
    try:
        rows = ledger.scored_history(symbol, sector)
    except Exception as exc:
        logger.warning("Calibration lookup failed for %s: %s", symbol, exc)
        return _NO_RECORD

    if not rows:
        return _NO_RECORD

    # Collect decided (non-neutral) calls at the primary horizon.
    decided: List[bool] = []
    excess_at_primary: List[float] = []
    abs_at_primary: List[float] = []
    hi_conf_correct: List[bool] = []
    lo_conf_correct: List[bool] = []

    for r in rows:
        outc = (r.get("outcomes") or {}).get(_PRIMARY_HORIZON) or {}
        correct = outc.get("correct")
        excess = outc.get("excess_return_pct")
        ret = outc.get("return_pct")
        if excess is not None:
            excess_at_primary.append(excess)
        if ret is not None:
            abs_at_primary.append(ret)
        if correct is not None:
            decided.append(bool(correct))
            conf = r.get("confidence")
            if conf is not None:
                (hi_conf_correct if conf >= 8 else lo_conf_correct).append(bool(correct))

    lines = [
        f"TRACK RECORD — this system's prior calls on {symbol.upper()}"
        + (f" / {sector} sector" if sector else "")
        + f" (n={len(rows)} scored vs KSE-100, ~{_PRIMARY_HORIZON} horizon):"
    ]

    if decided:
        hr = round(100.0 * sum(decided) / len(decided))
        lines.append(f"- Directional hit-rate: {hr}% ({sum(decided)}/{len(decided)} decided calls correct).")

    avg_excess = _mean(excess_at_primary)
    if avg_excess is not None:
        lines.append(f"- Avg {_PRIMARY_HORIZON} excess return vs KSE-100: {avg_excess:+.1f}%.")
    else:
        avg_abs = _mean(abs_at_primary)
        if avg_abs is not None:
            lines.append(f"- Avg {_PRIMARY_HORIZON} return: {avg_abs:+.1f}% (benchmark unavailable).")

    # Over/under-confidence signal — only when both buckets have support.
    if len(hi_conf_correct) >= 3 and len(lo_conf_correct) >= 3:
        hi = 100.0 * sum(hi_conf_correct) / len(hi_conf_correct)
        lo = 100.0 * sum(lo_conf_correct) / len(lo_conf_correct)
        if hi + 5 < lo:
            lines.append(
                f"- CALIBRATION WARNING: high-confidence (≥8) calls hit only {hi:.0f}% "
                f"vs {lo:.0f}% for lower-confidence — past high conviction here was overconfident; temper it."
            )
        else:
            lines.append(f"- Calibration: high-confidence calls hit {hi:.0f}% vs {lo:.0f}% lower-confidence.")

    if len(rows) < 8:
        lines.append("- NOTE: small sample — treat as a weak prior, not a hard signal.")

    lines.append(
        "Apply as a prior: if the track record here is weak or contrary, widen your uncertainty "
        "and justify any high-conviction verdict against it."
    )
    return "\n".join(lines)
