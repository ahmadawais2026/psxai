"""
download_research_reports.py
════════════════════════════════════════════════════════════════
Download and extract research reports from the AskAnalyst catalog
for the last N months, saving full text as .md files in
market_data/briefings/extracted/.

Usage:
  python download_research_reports.py              # last 6 months
  python download_research_reports.py --months 3   # last 3 months
  python download_research_reports.py --months 12  # last 12 months
  python download_research_reports.py --all        # entire catalog
  python download_research_reports.py --sector cement
  python download_research_reports.py --ticker OGDC
  python download_research_reports.py --dry-run    # show what would download

Output:
  market_data/briefings/extracted/{slug}.md   — full report text
  market_data/briefings/download_log.json      — success/failure log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False
    print("[!] pdfplumber not installed — run: pip install pdfplumber")

# ── Paths ─────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
CATALOG_PATH = BASE_DIR / "market_data" / "briefings" / "catalogs" / "research_reports_catalog.xlsx"
OUTPUT_DIR  = BASE_DIR / "market_data" / "briefings" / "extracted"
LOG_PATH    = BASE_DIR / "market_data" / "briefings" / "download_log.json"
TMP_DIR     = BASE_DIR / "market_data" / "briefings" / ".tmp_pdfs"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
    "Referer": "https://www.askanalyst.com.pk/",
}

REQUEST_TIMEOUT = 30
RATE_LIMIT_SEC  = 0.8   # polite delay between downloads


# ── Helpers ───────────────────────────────────────────────────

def _sanitize_filename(text: str, max_len: int = 120) -> str:
    """Turn report title or slug into a safe filename."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = re.sub(r'[\s]+', '_', text.strip())
    text = re.sub(r'_+', '_', text).strip('_')
    return text[:max_len]


def _fix_pdf_url(raw_url: str) -> str:
    """URL-encode spaces and special chars in the path portion."""
    if not raw_url or not isinstance(raw_url, str):
        return ""
    raw_url = raw_url.strip()
    # Split at the protocol + host boundary
    if "://" in raw_url:
        proto, rest = raw_url.split("://", 1)
        if "/" in rest:
            host, path = rest.split("/", 1)
            # URL-encode only the path (not the host)
            path_encoded = quote(path, safe="/%?=&+")
            return f"{proto}://{host}/{path_encoded}"
    return raw_url


# Chart-noise cleaner now lives in data/text_cleaning.py (shared with the
# read-time path in data/local_data.get_research_reports). Alias keeps the
# existing call site below unchanged.
from data.text_cleaning import clean_extracted_text as _clean_extracted_text


def _extract_pdf_text(pdf_path: Path) -> str:
    """Extract all text from a PDF using pdfplumber, with chart-noise cleanup."""
    if not HAS_PDFPLUMBER:
        return ""
    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = _clean_extracted_text(page.extract_text() or "")
                if text:
                    pages.append(text)
    except Exception as e:
        print(f"    [!] PDF extract error: {e}")
        return ""
    return "\n\n".join(pages)


def _load_log() -> dict:
    if LOG_PATH.exists():
        try:
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"downloaded": {}, "failed": {}}


def _save_log(log: dict):
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def _already_extracted(slug: str) -> bool:
    """Return True if we already have a non-empty .md for this slug."""
    md_path = OUTPUT_DIR / f"{slug}.md"
    return md_path.exists() and md_path.stat().st_size > 200


# ── Core download logic ───────────────────────────────────────

def download_and_extract(row: pd.Series, log: dict, dry_run: bool = False) -> bool:
    """
    Download one PDF row from the catalog, extract text, save as .md.
    Returns True on success.
    """
    raw_url  = str(row.get("URL", "") or "").strip()
    title    = str(row.get("Title", "") or "").strip()
    slug_raw = str(row.get("slug", "") or "").strip()
    snippet  = str(row.get("content", "") or "").strip()
    sector   = str(row.get("sector_name", "") or "").strip()
    symbol   = str(row.get("company_symbol", "") or "").strip()
    rtype    = str(row.get("type", "") or "").strip()
    date_val = row.get("Date", "")

    # Build a clean slug for the output filename
    slug = _sanitize_filename(slug_raw or title)
    if not slug:
        slug = f"report_{int(row.get('id', 0))}"

    if _already_extracted(slug):
        print(f"    [skip] already extracted: {slug[:70]}")
        return True

    if not raw_url or not raw_url.lower().endswith(".pdf"):
        # Catalog row without a valid PDF URL — save snippet only if useful
        if len(snippet) > 100:
            _save_snippet_only(slug, title, snippet, sector, symbol, rtype, date_val)
            return True
        print(f"    [skip] no valid PDF URL: {title[:60]}")
        log["failed"][slug] = {"reason": "no_pdf_url", "url": raw_url}
        return False

    url = _fix_pdf_url(raw_url)

    if dry_run:
        print(f"    [dry-run] would download: {title[:70]}")
        return True

    # Download
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = TMP_DIR / f"{slug}.pdf"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
        if resp.status_code != 200:
            print(f"    [fail] HTTP {resp.status_code}: {title[:60]}")
            log["failed"][slug] = {"reason": f"http_{resp.status_code}", "url": url}
            return False

        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
            print(f"    [fail] not a PDF (content-type: {content_type}): {title[:60]}")
            log["failed"][slug] = {"reason": "not_pdf", "content_type": content_type, "url": url}
            return False

        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

    except Exception as e:
        print(f"    [fail] download error: {e} — {title[:60]}")
        log["failed"][slug] = {"reason": str(e), "url": url}
        return False

    # Extract text
    text = _extract_pdf_text(tmp_path)

    # Clean up temp file
    try:
        tmp_path.unlink()
    except Exception:
        pass

    if not text and len(snippet) < 100:
        print(f"    [fail] no text extracted: {title[:60]}")
        log["failed"][slug] = {"reason": "empty_extraction", "url": url}
        return False

    # Fall back to snippet if extraction failed but we have a snippet
    body = text if text else f"[Full text extraction failed — snippet only]\n\n{snippet}"

    # Build markdown
    date_str = str(date_val)[:10] if date_val else ""
    md_lines = [
        f"# {title}",
        "",
        f"**Date:** {date_str}  ",
        f"**Sector:** {sector}  ",
        f"**Company:** {symbol or 'N/A'}  ",
        f"**Type:** {rtype}  ",
        f"**Source:** {raw_url}  ",
        "",
        "---",
        "",
        body.strip(),
    ]
    md_content = "\n".join(md_lines)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{slug}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    words = len(body.split())
    print(f"    [OK] {title[:70]} ({words} words)")
    log["downloaded"][slug] = {
        "title": title, "date": date_str, "sector": sector,
        "symbol": symbol, "words": words, "url": raw_url,
    }
    return True


def _save_snippet_only(slug, title, snippet, sector, symbol, rtype, date_val):
    """Save catalog snippet as a minimal .md when no PDF is available."""
    date_str = str(date_val)[:10] if date_val else ""
    md_lines = [
        f"# {title}",
        "",
        f"**Date:** {date_str}  ",
        f"**Sector:** {sector}  ",
        f"**Company:** {symbol or 'N/A'}  ",
        f"**Type:** {rtype}  ",
        "",
        "---",
        "",
        snippet.strip(),
    ]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{slug}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print(f"    [snippet] {title[:70]}")


# ── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Download PSX research reports from AskAnalyst catalog")
    parser.add_argument("--months", type=int, default=6, help="Download last N months (default: 6)")
    parser.add_argument("--all",    action="store_true", help="Download entire catalog")
    parser.add_argument("--sector", type=str, default=None, help="Filter by sector name (partial match)")
    parser.add_argument("--ticker", type=str, default=None, help="Filter by company symbol")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be downloaded without downloading")
    parser.add_argument("--force",  action="store_true", help="Re-download even if .md already exists")
    args = parser.parse_args()

    if not CATALOG_PATH.exists():
        print(f"[-] Catalog not found: {CATALOG_PATH}")
        return

    if not HAS_PDFPLUMBER:
        print("[-] Install pdfplumber first: pip install pdfplumber")
        return

    # Load catalog
    df = pd.read_excel(CATALOG_PATH)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    total_catalog = len(df)

    # Date filter
    if not args.all:
        cutoff = datetime.now() - timedelta(days=args.months * 30)
        df = df[df["Date"] >= cutoff]

    # Sector filter
    if args.sector:
        mask = df["sector_name"].str.contains(args.sector, case=False, na=False)
        df = df[mask]

    # Ticker filter
    if args.ticker:
        mask = df["company_symbol"].str.upper() == args.ticker.upper()
        df = df[mask]

    # Sort newest first
    df = df.sort_values("Date", ascending=False).reset_index(drop=True)

    print("=" * 65)
    print("  PSX Research Report Downloader")
    print(f"  Catalog: {total_catalog} total reports")
    if not args.all:
        print(f"  Filter: last {args.months} months (since {cutoff.strftime('%Y-%m-%d')})")
    if args.sector:
        print(f"  Sector filter: {args.sector}")
    if args.ticker:
        print(f"  Ticker filter: {args.ticker.upper()}")
    print(f"  Matched: {len(df)} reports")
    print(f"  Output: {OUTPUT_DIR}")
    if args.dry_run:
        print("  Mode: DRY RUN")
    print("=" * 65)

    if len(df) == 0:
        print("  No reports matched the filters.")
        return

    # Show breakdown by type
    if "type" in df.columns:
        print("\nReport types in selection:")
        for rtype, count in df["type"].value_counts().items():
            print(f"  {rtype}: {count}")
    if "sector_name" in df.columns:
        print("\nSectors in selection:")
        for sector, count in df["sector_name"].value_counts().head(10).items():
            print(f"  {sector}: {count}")
    print()

    log = _load_log()
    if args.force:
        log["downloaded"] = {}
        log["failed"] = {}

    success = 0
    skipped = 0
    failed  = 0

    for i, (_, row) in enumerate(df.iterrows(), 1):
        title = str(row.get("Title", ""))[:60]
        date_str = str(row.get("Date", ""))[:10]
        print(f"[{i:3d}/{len(df)}] {date_str} — {title}")

        result = download_and_extract(row, log, dry_run=args.dry_run)

        if args.dry_run:
            continue

        if result:
            slug = _sanitize_filename(str(row.get("slug", "")) or title)
            if slug in log.get("downloaded", {}):
                success += 1
            else:
                skipped += 1  # was already extracted
        else:
            failed += 1

        _save_log(log)
        time.sleep(RATE_LIMIT_SEC)

    print("\n" + "=" * 65)
    if args.dry_run:
        print(f"  Dry run complete — {len(df)} reports would be downloaded")
    else:
        print(f"  Done!")
        print(f"  New downloads:  {success}")
        print(f"  Already existed: {skipped}")
        print(f"  Failed:          {failed}")
        total_md = len(list(OUTPUT_DIR.glob("*.md"))) if OUTPUT_DIR.exists() else 0
        print(f"  Total .md files: {total_md}")
        print(f"  Log: {LOG_PATH}")
    print("=" * 65)


if __name__ == "__main__":
    main()
