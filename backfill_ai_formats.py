"""
backfill_ai_formats.py
======================
One-time script that converts all existing files in market_data/ into
AI-agent-readable formats:

  briefings/pdfs/*.pdf      → briefings/extracted/<name>.txt
  sectors/*/*.json          → sectors/*/<name>.csv
  news/latest_news.xlsx     → news/latest_news.json + latest_news.csv
  sectors/*/metadata.json   → skipped (not tabular data)
"""

import json
import os

import pandas as pd
import pdfplumber

MARKET_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_data")
PDFS_DIR        = os.path.join(MARKET_DATA_DIR, "briefings", "pdfs")
EXTRACTED_DIR   = os.path.join(MARKET_DATA_DIR, "briefings", "extracted")
SECTORS_DIR     = os.path.join(MARKET_DATA_DIR, "sectors")
NEWS_DIR        = os.path.join(MARKET_DATA_DIR, "news")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def save_txt(text, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  [OK] TXT  -> {filepath}")


def save_csv(df, filepath):
    df.to_csv(filepath, index=False, encoding="utf-8")
    print(f"  [OK] CSV  -> {filepath}")


def save_json(data, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  [OK] JSON -> {filepath}")


def pdf_to_txt(pdf_path, title):
    """Extract text and tables from a PDF and return a plain-text string."""
    lines = [f"TITLE: {title}", "=" * 60, ""]
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                lines.append(f"--- Page {page_num} ---")
                text = page.extract_text() or ""
                if text.strip():
                    lines.append(text.strip())
                tables = page.extract_tables() or []
                for t_idx, table in enumerate(tables, start=1):
                    if not table:
                        continue
                    lines.append(f"\n[Table {t_idx}]")
                    for row in table:
                        lines.append(" | ".join(
                            str(c).strip() if c is not None else "" for c in row
                        ))
                lines.append("")
    except Exception as e:
        lines.append(f"[ERROR extracting PDF: {e}]")
    return "\n".join(lines)


def json_to_df(filepath):
    """Load a JSON file and return a DataFrame, or None if not tabular."""
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return pd.DataFrame(data)
        if isinstance(data, dict):
            for key in ("data", "results", "items", "records", "list"):
                if key in data and isinstance(data[key], list) and data[key]:
                    return pd.DataFrame(data[key])
    except Exception:
        pass
    return None


# ─── Task 1: PDFs → TXT ───────────────────────────────────────────────────────

def backfill_pdfs():
    print("\n── PDFs → TXT ───────────────────────────────────────────────")
    if not os.path.isdir(PDFS_DIR):
        print("  [!] No pdfs/ folder found. Skipping.")
        return

    pdf_files = [f for f in os.listdir(PDFS_DIR) if f.lower().endswith(".pdf")]
    print(f"  Found {len(pdf_files)} PDF(s) in briefings/pdfs/")

    for fname in sorted(pdf_files):
        stem = fname[:-4]  # strip .pdf
        txt_path = os.path.join(EXTRACTED_DIR, stem + ".txt")

        if os.path.exists(txt_path):
            print(f"  [--] Already exists, skipping: {fname}")
            continue

        pdf_path = os.path.join(PDFS_DIR, fname)
        print(f"  [*] Extracting: {fname}")
        try:
            text = pdf_to_txt(pdf_path, stem.replace("_", " "))
            os.makedirs(EXTRACTED_DIR, exist_ok=True)
            save_txt(text, txt_path)
        except Exception as e:
            print(f"  [-] Failed: {e}")


# ─── Task 2: Sector JSON → CSV ────────────────────────────────────────────────

def backfill_sector_json():
    print("\n── Sector JSON → CSV ────────────────────────────────────────")
    if not os.path.isdir(SECTORS_DIR):
        print("  [!] No sectors/ folder found. Skipping.")
        return

    converted = 0
    for root, _, files in os.walk(SECTORS_DIR):
        for fname in sorted(files):
            if not fname.endswith(".json"):
                continue
            if "metadata" in fname:
                continue  # metadata is not tabular

            json_path = os.path.join(root, fname)
            csv_path  = json_path.replace(".json", ".csv")

            if os.path.exists(csv_path):
                print(f"  [--] Already exists, skipping: {fname}")
                continue

            print(f"  [*] Converting: {fname}")
            df = json_to_df(json_path)
            if df is not None and not df.empty:
                try:
                    save_csv(df, csv_path)
                    converted += 1
                except Exception as e:
                    print(f"  [-] Failed: {e}")
            else:
                print(f"  [!] Not tabular or empty — skipped: {fname}")

    print(f"  Converted {converted} sector JSON file(s) to CSV.")


# ─── Task 3: News XLSX → JSON + CSV ──────────────────────────────────────────

def backfill_news():
    print("\n── News XLSX → JSON + CSV ───────────────────────────────────")
    xlsx_path = os.path.join(NEWS_DIR, "latest_news.xlsx")
    if not os.path.exists(xlsx_path):
        print("  [!] latest_news.xlsx not found. Skipping.")
        return

    try:
        df = pd.read_excel(xlsx_path)
        json_path = os.path.join(NEWS_DIR, "latest_news.json")
        csv_path  = os.path.join(NEWS_DIR, "latest_news.csv")

        if not os.path.exists(json_path):
            save_json(df.to_dict(orient="records"), json_path)
        else:
            print(f"  [--] latest_news.json already exists, skipping.")

        if not os.path.exists(csv_path):
            save_csv(df, csv_path)
        else:
            print(f"  [--] latest_news.csv already exists, skipping.")

    except Exception as e:
        print(f"  [-] Failed to process news: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  AI Format Backfill")
    print("=" * 60)
    backfill_pdfs()
    backfill_sector_json()
    backfill_news()
    print("\n" + "=" * 60)
    print("  Backfill complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
