"""
AskAnalyst Market Intelligence Downloader
==========================================
Downloads market summaries, macro indicators, sector data, and PDF reports
from https://api.askanalyst.com.pk/api.

Output layout
─────────────
market_data/
  briefings/
    *.xlsx                   – PDF catalog indexes (title / date / URL)
    pdfs/                    – raw downloaded PDF files
    extracted/
      <title>.docx           – full text + tables in reading order (Word)
      <title>_tables.xlsx    – one worksheet per extracted table (Excel)
  news/
    psx_disclosures_latest.xlsx
  sectors/
    <sector>_metadata.json
    <sector>_<dataset>.json
    <sector>_<dataset>.xlsx  – flat export when data is tabular
  macro_indicators_index.xlsx

Dependencies
────────────
  pip install requests pandas openpyxl pdfplumber python-docx
"""

import io
import json
import os
import re
import time

import pandas as pd
import pdfplumber
import requests
from docx import Document


# ─── Config ───────────────────────────────────────────────────────────────────

BASE_API_URL    = "https://api.askanalyst.com.pk/api"
MARKET_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_data")
BRIEFINGS_DIR   = os.path.join(MARKET_DATA_DIR, "briefings")
PDFS_DIR        = os.path.join(BRIEFINGS_DIR,   "pdfs")
EXTRACTED_DIR   = os.path.join(BRIEFINGS_DIR,   "extracted")
SECTORS_DIR     = os.path.join(MARKET_DATA_DIR, "sectors")
NEWS_DIR        = os.path.join(MARKET_DATA_DIR, "news")

REQUEST_TIMEOUT = 30   # seconds — regular API calls
PDF_TIMEOUT     = 120  # seconds — PDF downloads can be large
DELAY           = 1    # seconds between every network call

_session = requests.Session()
_session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})


# ─── Directory setup ──────────────────────────────────────────────────────────

def setup_dirs():
    for d in [BRIEFINGS_DIR, PDFS_DIR, EXTRACTED_DIR, SECTORS_DIR, NEWS_DIR]:
        os.makedirs(d, exist_ok=True)


# ─── Network helpers ──────────────────────────────────────────────────────────

def api_get(path, params=None):
    url = f"{BASE_API_URL}/{path.lstrip('/')}"
    try:
        r = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        print(f"  [-] HTTP error GET {path}: {e}")
    except requests.exceptions.Timeout:
        print(f"  [-] Timeout GET {path}")
    except Exception as e:
        print(f"  [-] Error GET {path}: {e}")
    return None


def api_post(path, payload):
    url = f"{BASE_API_URL}/{path.lstrip('/')}"
    try:
        r = _session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        print(f"  [-] HTTP error POST {path}: {e}")
    except requests.exceptions.Timeout:
        print(f"  [-] Timeout POST {path}")
    except Exception as e:
        print(f"  [-] Error POST {path}: {e}")
    return None


def download_pdf_bytes(url):
    """Download a PDF URL and return raw bytes, or None on failure."""
    try:
        r = _session.get(url, timeout=PDF_TIMEOUT)
        r.raise_for_status()
        return r.content
    except requests.exceptions.Timeout:
        print(f"  [-] Timeout downloading PDF: {url}")
    except Exception as e:
        print(f"  [-] Failed to download PDF {url}: {e}")
    return None


# ─── Save helpers ─────────────────────────────────────────────────────────────

def save_json(data, filepath):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  [OK] JSON  -> {filepath}")
    except Exception as e:
        print(f"  [-] Failed to save JSON {filepath}: {e}")


def save_excel(df, filepath, sheet_name="Sheet1"):
    try:
        with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
        print(f"  [OK] Excel ({len(df)} rows) -> {filepath}")
    except Exception as e:
        print(f"  [-] Failed to save Excel {filepath}: {e}")


def sanitize_filename(name, max_len=80):
    """Strip characters that are illegal in filenames and truncate."""
    name = re.sub(r'[\\/*?:"<>|]', "", str(name))
    name = name.strip().replace(" ", "_")
    return name[:max_len] or "untitled"


# ─── Response normalisation ───────────────────────────────────────────────────

def flatten_to_df(data):
    """Best-effort conversion of an API response (list or dict) to a DataFrame."""
    if data is None:
        return None
    if isinstance(data, list):
        return pd.DataFrame(data) if data else None
    if isinstance(data, dict):
        for key in ("data", "results", "items", "records", "list", "posts"):
            if key in data and isinstance(data[key], list):
                return pd.DataFrame(data[key]) if data[key] else None
        return pd.DataFrame([data])
    return None


def normalise_pdf_df(df):
    """Rename common API column names to tidy Title / Date / URL columns."""
    rename = {}
    for col in df.columns:
        lc = col.lower()
        if lc in ("title", "name", "heading"):
            rename[col] = "Title"
        elif lc in ("date", "created_at", "createdat", "publish_date",
                    "published_at", "post_date"):
            rename[col] = "Date"
        elif lc in ("url", "link", "pdf", "pdf_url", "pdfurl",
                    "download_url", "file", "file_url"):
            rename[col] = "URL"
    return df.rename(columns=rename) if rename else df


# ─── PDF extraction ───────────────────────────────────────────────────────────

def _clean_table(raw_table):
    """Convert a pdfplumber raw table (list-of-lists with possible None) to clean strings."""
    return [
        [str(cell).strip() if cell is not None else "" for cell in row]
        for row in raw_table
        if any(cell is not None and str(cell).strip() for cell in row)
    ]


def extract_and_save_pdf(pdf_bytes, title, base_filename):
    """
    Extract all text and tables from a PDF.
    - Tables    → <base_filename>_tables.xlsx  (one sheet per table)
    - Full doc  → <base_filename>.docx         (text + tables in reading order)
    """
    all_pages = []   # list of {"page": n, "text": str, "tables": [clean_table]}

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                raw_tables = page.extract_tables() or []
                tables = [_clean_table(t) for t in raw_tables if t]
                text   = page.extract_text() or ""
                all_pages.append({"page": page_num, "text": text, "tables": tables})
    except Exception as e:
        print(f"  [-] pdfplumber could not read '{title}': {e}")
        return

    total_tables = sum(len(p["tables"]) for p in all_pages)
    total_chars  = sum(len(p["text"]) for p in all_pages)
    print(f"  [i] Extracted {total_tables} table(s), {total_chars} chars from '{title}'")

    # ── Excel: one sheet per table ────────────────────────────────────
    if total_tables:
        xl_path = os.path.join(EXTRACTED_DIR, base_filename + "_tables.xlsx")
        try:
            with pd.ExcelWriter(xl_path, engine="openpyxl") as writer:
                for page_info in all_pages:
                    for t_idx, table in enumerate(page_info["tables"], start=1):
                        if not table:
                            continue
                        headers = table[0]
                        rows    = table[1:] if len(table) > 1 else []
                        # Deduplicate header names (pdfplumber can emit blank/repeated headers)
                        seen, clean_headers = {}, []
                        for h in headers:
                            key = h or "Col"
                            if key in seen:
                                seen[key] += 1
                                key = f"{key}_{seen[key]}"
                            else:
                                seen[key] = 0
                            clean_headers.append(key)
                        df = pd.DataFrame(rows, columns=clean_headers)
                        sheet = f"P{page_info['page']}_T{t_idx}"[:31]
                        df.to_excel(writer, sheet_name=sheet, index=False)

                # "Full Text" sheet as a bonus
                text_rows = [
                    {"Page": p["page"], "Text": p["text"]}
                    for p in all_pages if p["text"].strip()
                ]
                if text_rows:
                    pd.DataFrame(text_rows).to_excel(writer, sheet_name="Full Text", index=False)

            print(f"  [OK] Excel tables -> {xl_path}")
        except Exception as e:
            print(f"  [-] Failed to save Excel for '{title}': {e}")

    # ── Word: text + tables in reading order ─────────────────────────
    word_path = os.path.join(EXTRACTED_DIR, base_filename + ".docx")
    try:
        doc = Document()
        doc.add_heading(title, level=0)

        for page_info in all_pages:
            doc.add_heading(f"Page {page_info['page']}", level=2)

            # Text content (split into paragraphs)
            for line in page_info["text"].splitlines():
                line = line.strip()
                if line:
                    doc.add_paragraph(line)

            # Tables for this page
            for t_idx, table in enumerate(page_info["tables"], start=1):
                if not table:
                    continue
                doc.add_heading(f"Table {t_idx}", level=3)
                n_cols = max(len(row) for row in table)
                word_tbl = doc.add_table(rows=len(table), cols=n_cols)
                word_tbl.style = "Table Grid"
                for r_i, row in enumerate(table):
                    for c_i, cell_val in enumerate(row):
                        if c_i < n_cols:
                            cell = word_tbl.cell(r_i, c_i)
                            cell.text = cell_val
                            # Bold the header row
                            if r_i == 0:
                                for run in cell.paragraphs[0].runs:
                                    run.bold = True
                doc.add_paragraph()  # spacing after table

        doc.save(word_path)
        print(f"  [OK] Word doc  -> {word_path}")
    except Exception as e:
        print(f"  [-] Failed to save Word doc for '{title}': {e}")


# ─── Section 1: General Market Summaries & PDF Catalogs ───────────────────────

def fetch_morning_briefing_chart():
    print("[*] Morning Briefing Chart (daily snapshot)...")
    data = api_get("/morningbriefingchart")
    time.sleep(DELAY)
    if data is None:
        print("  [-] Skipped.")
        return
    save_json(data, os.path.join(BRIEFINGS_DIR, "morning_briefing_summary.json"))


def fetch_news(count=50):
    print(f"[*] PSX Latest News (last {count})...")
    data = api_get("/news/all", params={"page": 1, "postsperpage": count})
    time.sleep(DELAY)
    if data is None:
        print("  [-] Skipped.")
        return
    df = flatten_to_df(data)
    if df is None or df.empty:
        print("  [-] Skipped: could not parse response into a table.")
        return
    save_excel(df, os.path.join(NEWS_DIR, "latest_news.xlsx"), sheet_name="News")


def fetch_pdf_catalog(endpoint, out_filename, max_pdfs=100):
    """
    1. Fetch the full JSON catalog → save as Excel index (all entries).
    2. Download, validate, and extract the most recent `max_pdfs` PDFs.
       Each PDF is saved raw to briefings/pdfs/ and its content extracted to
       briefings/extracted/ as both a Word doc and an Excel tables file.
    """
    print(f"[*] PDF catalog: /{endpoint}  (downloading latest {max_pdfs} PDFs)...")
    data = api_get(f"/{endpoint}")
    time.sleep(DELAY)
    if data is None:
        print("  [-] Skipped.")
        return

    df = flatten_to_df(data)
    if df is None or df.empty:
        print("  [-] Skipped: could not parse response into a table.")
        return

    df = normalise_pdf_df(df)

    # Save the full catalog index regardless of how many PDFs we download
    save_excel(df, os.path.join(BRIEFINGS_DIR, out_filename), sheet_name="PDFs")
    print(f"  [i] Catalog has {len(df)} entries — downloading latest {min(max_pdfs, len(df))}.")

    url_col   = "URL"   if "URL"   in df.columns else None
    title_col = "Title" if "Title" in df.columns else None

    if url_col is None:
        print("  [!] No URL column found — skipping PDF downloads.")
        return

    downloaded = 0
    for _, row in df.iterrows():
        if downloaded >= max_pdfs:
            break

        url   = row.get(url_col)
        title = str(row.get(title_col, "untitled")) if title_col else "untitled"

        if not url or (isinstance(url, float) and pd.isna(url)):
            continue

        # Strip any trailing .pdf the API bakes into the title field
        title_clean = re.sub(r"\.pdf$", "", title, flags=re.IGNORECASE).strip()

        print(f"  [{downloaded + 1}/{max_pdfs}] {title_clean[:70]}...")
        pdf_bytes = download_pdf_bytes(str(url))
        time.sleep(DELAY)

        if pdf_bytes is None:
            continue

        # Validate: must start with %PDF and be a reasonable size
        if len(pdf_bytes) < 5000:
            print(f"  [!] Skipping — only {len(pdf_bytes)} bytes (not a valid PDF).")
            continue
        if not pdf_bytes.startswith(b"%PDF"):
            print(f"  [!] Skipping — no PDF header (server returned an error page).")
            continue

        # Save raw PDF
        raw_path = os.path.join(PDFS_DIR, sanitize_filename(title_clean) + ".pdf")
        try:
            with open(raw_path, "wb") as f:
                f.write(pdf_bytes)
        except Exception as e:
            print(f"  [-] Could not save raw PDF: {e}")

        extract_and_save_pdf(pdf_bytes, title_clean, sanitize_filename(title_clean))
        time.sleep(DELAY)
        downloaded += 1

    print(f"  [OK] {downloaded} PDF(s) downloaded and extracted.")


# ─── Section 2: Macroeconomic Indicators Index ────────────────────────────────

def fetch_macro_indicators():
    print("[*] Macroeconomic Indicators Index (/searchlist)...")
    data = api_get("/searchlist")
    time.sleep(DELAY)
    if data is None:
        print("  [-] Skipped.")
        return
    df = flatten_to_df(data)
    if df is None or df.empty:
        print("  [-] Skipped: could not parse response.")
        return
    save_excel(df, os.path.join(MARKET_DATA_DIR, "macro_indicators_index.xlsx"),
               sheet_name="Indicators")


# ─── Section 3: Sector-Specific Fundamentals ──────────────────────────────────

SECTOR_POST_CONFIGS = {
    "cement": [
        {"key": "retail_prices",    "period": "weekly",  "label": "weekly_retail_prices"},
        {"key": "local_dispatches", "period": "weekly",  "label": "weekly_local_dispatches"},
    ],
    "fertilizer": [
        {"key": "urea_production",  "period": "monthly", "label": "urea_monthly_production"},
        {"key": "urea_sales",       "period": "monthly", "label": "urea_monthly_sales"},
    ],
    "omc": [
        {"key": "motor_spirit",     "period": "monthly", "label": "motor_spirit_monthly_sales"},
        {"key": "hsd",              "period": "monthly", "label": "hsd_monthly_sales"},
    ],
    "autos": [
        {"key": "passenger_cars",   "period": "monthly", "label": "passenger_car_monthly_sales"},
    ],
    "circulardebt": [
        {"key": "circular_debt",    "period": "monthly", "label": "circular_debt_monthly_flows"},
    ],
}


def fetch_sector(sector):
    print(f"\n[+] Sector: {sector.upper()}")
    endpoint = f"/sector/{sector}"

    # Step 1 – GET baseline metadata
    print(f"  [*] GET {endpoint} (metadata)...")
    metadata = api_get(endpoint)
    time.sleep(DELAY)
    if metadata is None:
        print(f"  [-] No metadata returned. Skipping {sector}.")
        return
    save_json(metadata, os.path.join(SECTORS_DIR, f"{sector}_metadata.json"))

    # Step 2 – POST for each configured dataset
    # Build the payload using the actual date range and company object from the metadata
    configs = SECTOR_POST_CONFIGS.get(sector, [])
    if not configs:
        print(f"  [!] No POST configs defined for {sector}. Metadata only.")
        return

    for cfg in configs:
        label  = cfg["label"]
        period = cfg["period"]

        # Pull date range from metadata; fall back to a 2-year window if absent
        dates_list = metadata.get("dates", {}).get(period, [])
        sdate = dates_list[-1]["start_date"] if dates_list else "2023-01-01"
        edate = dates_list[0]["start_date"]  if dates_list else "2026-06-01"

        # Use the first company entry from metadata (typically "All Companies")
        company = metadata.get("company", [{}])[0]

        payload = {
            "company": company,
            "sdate":   sdate,
            "edate":   edate,
            "period":  period,
        }

        print(f"  [*] POST {endpoint} -> {label} ({sdate} to {edate})...")
        result = api_post(endpoint, payload)
        time.sleep(DELAY)

        if result is None:
            print(f"  [-] No data for {label}.")
            continue

        save_json(result, os.path.join(SECTORS_DIR, f"{sector}_{label}.json"))

        df = flatten_to_df(result)
        if df is not None and not df.empty:
            save_excel(df, os.path.join(SECTORS_DIR, f"{sector}_{label}.xlsx"),
                       sheet_name=label[:31])


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  AskAnalyst Market Intelligence Downloader")
    print("=" * 60)

    setup_dirs()

    print("\n=== Section 1: Market Summaries & PDFs ===")
    fetch_morning_briefing_chart()
    fetch_news(count=50)
    fetch_pdf_catalog("morningbriefing",   "morning_briefing_pdfs.xlsx",       max_pdfs=3)
    fetch_pdf_catalog("technicalresearch", "technical_research_pdfs.xlsx",     max_pdfs=3)
    fetch_pdf_catalog("marketroundup",     "market_roundup_pdfs.xlsx",         max_pdfs=3)
    fetch_pdf_catalog("reports/1000",      "research_reports_catalog.xlsx",    max_pdfs=3)

    print("\n=== Section 2: Macroeconomic Indicators ===")
    fetch_macro_indicators()

    print("\n=== Section 3: Sector Fundamentals ===")
    for sector in ["cement", "fertilizer", "omc", "autos", "circulardebt"]:
        fetch_sector(sector)

    print("\n" + "=" * 60)
    print("  Download complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
