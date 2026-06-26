"""
data/text_cleaning.py
═══════════════════════════════════════════════════════════════════════
Shared, dependency-free cleaner for broker-research text.

Broker PDFs embed charts whose rotated tick labels (e.g. "-lu J -p e S -v o N",
which is "Jul Sep Nov" rotated 90°) and data-point runs ("4 4 4 5 5 5 6 6 6")
extract as gibberish that pdfplumber concatenates both as whole junk lines and
inline onto prose lines.

Used at TWO points so a garbled excerpt is sanitized no matter where it comes
from:
  - extraction time (download_research_reports.py), and
  - read time (data/local_data.get_research_reports), so a stale/garbled .md
    bundled with the deployed function is still cleaned on the way into the
    prompt/PDF.

Pure `re` only — safe to import anywhere (no pdfplumber / heavy deps).
"""

from __future__ import annotations

import re


def _is_fragment_token(t: str) -> bool:
    """A token that looks like chart-axis debris rather than a real word:
    1-2 chars, pure digits, or a rotated tick like '-lu' / '-p'."""
    return len(t) <= 2 or t.isdigit() or bool(re.fullmatch(r"-[A-Za-z]{1,2}", t))


def _strip_inline_noise(line: str) -> str:
    """Remove RUNS of fragmented tokens from within a line, keeping the prose.

    A maximal run of >=5 consecutive fragment tokens (or any long pure-digit
    token) is dropped, so chart debris concatenated onto a prose line is removed
    without losing the surrounding sentence.
    """
    tokens = line.split()
    n = len(tokens)
    keep = [True] * n
    i = 0
    while i < n:
        if _is_fragment_token(tokens[i]):
            j = i
            while j < n and _is_fragment_token(tokens[j]):
                j += 1
            if j - i >= 5:           # a long fragment run = chart noise
                for k in range(i, j):
                    keep[k] = False
            i = j
        else:
            i += 1
    # Also drop any single very-long pure-digit blob (e.g. "44444455555555555566").
    for k, t in enumerate(tokens):
        if t.isdigit() and len(t) >= 6:
            keep[k] = False
    return " ".join(t for t, kp in zip(tokens, keep) if kp)


def clean_extracted_text(text: str) -> str:
    """Strip chart noise (rotated axis labels, data-point runs) from broker text.

    Strips inline fragment runs first, then drops any line whose residue is still
    mostly noise. Genuine prose (and frontmatter lines like '**Date:** ...') is
    preserved.
    """
    kept: list[str] = []
    for line in (text or "").split("\n"):
        stripped = _strip_inline_noise(line.strip())
        stripped = re.sub(r"\s{2,}", " ", stripped).strip()
        if not stripped:
            continue
        tokens = stripped.split()
        # Residue still mostly digits → chart data points / axis values.
        compact = stripped.replace(" ", "")
        if compact and sum(c.isdigit() for c in compact) / len(compact) > 0.6:
            continue
        # Residue still mostly 1-2 char tokens → fragmented axis labels.
        short = sum(1 for t in tokens if len(t) <= 2)
        if len(tokens) >= 4 and short / len(tokens) > 0.6:
            continue
        kept.append(stripped)
    return "\n".join(kept).strip()
