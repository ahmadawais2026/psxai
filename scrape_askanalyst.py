import os
import time
import requests
import pandas as pd

# ═══════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════
# Batch 7: Next 20 PSX companies (119 already scraped in Batches 1-6)
TICKERS = [
    # Engineering & Automobile Allied (4)
    "ADOS", "BCL", "CSAP", "DADX",
    # Food & Sugar (3)
    "BNL", "CHAS", "CLOV",
    # Chemicals & Refineries (3)
    "BUXL", "DOL", "DYNO",
    # Oil Marketing & Utilities (1)
    "BPL",
    # Paper, Board & Packaging (2)
    "CEPB", "CPPL",
    # Textile Composite & Spinning (2)
    "BHAT", "AKGL",
    # Investment Co / Securities (1)
    "DLL",
    # Insurance (3)
    "CENI", "EFUG", "EFUL",
]
BASE_API_URL = "https://api.askanalyst.com.pk/api"
PERIOD = "quarter"  # "annual" or "quarter"



def get_company_id(symbol):
    """Fetch the company list from the API and match the symbol to get the ID."""
    url = f"{BASE_API_URL}/companylistwithids"
    try:
        r = requests.get(url)
        if r.status_code == 200:
            companies = r.json()
            for c in companies:
                if c.get("symbol", "").upper() == symbol.upper():
                    return c.get("id"), c.get("name")
    except Exception as e:
        print(f"[-] Error fetching company list: {e}")
    return None, None


def fetch_statement_data(endpoint, company_id, period="annual"):
    """
    Fetch financial statements (Income Statement or Balance Sheet) via GET/POST lifecycle.
    """
    # 1. GET request to fetch available dates
    dates_url = f"{BASE_API_URL}/{endpoint}/{company_id}"
    try:
        r_get = requests.get(dates_url)
        if r_get.status_code != 200:
            print(f"    [-] GET dates failed for /{endpoint}/: {r_get.status_code}")
            return None
        
        dates_data = r_get.json().get("dates", {})
        dates_key = "quarter" if period == "quarter" else "annual"
        dates_list = dates_data.get(dates_key, [])
        if not dates_list:
            print(f"    [-] No {period} dates found for /{endpoint}/")
            return None
        
        # Sort dates to find the range
        sdate = dates_list[-1].get("start_date")
        edate = dates_list[0].get("start_date")
        
        # 2. POST request to retrieve the statement tables
        payload = {
            "company": {"id": company_id},
            "sdate": sdate,
            "edate": edate,
            "period": period
        }
        
        r_post = requests.post(dates_url, json=payload)
        if r_post.status_code == 200:
            return r_post.json()
        else:
            print(f"    [-] POST failed for /{endpoint}/: {r_post.status_code}")
    except Exception as e:
        print(f"    [-] Exception fetching /{endpoint}/: {e}")
    return None


def fetch_cash_flow(company_id):
    """Fetch Cash Flow statement from the GET /cf/{id} endpoint."""
    url = f"{BASE_API_URL}/cf/{company_id}"
    try:
        r = requests.get(url)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 500:
            print("    [!] WARNING: Cash Flow endpoint returned 500 Internal Server Error.")
            print("        (This is a known AskAnalyst API bug for banking sector companies like HBL).")
        else:
            print(f"    [-] Cash Flow request failed: Status code {r.status_code}")
    except Exception as e:
        print(f"    [-] Exception fetching Cash Flow: {e}")
    return None


def flatten_statement_list(raw_list):
    """Flattens nested statement lists (like Balance Sheets) into a flat list of metrics."""
    if not raw_list or not isinstance(raw_list, list):
        return []
        
    flat_list = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        sub_data = item.get("data", [])
        
        # Check if this item is a category folder or a flat metric.
        # A category folder has a list of dictionaries as 'data', where each dictionary represents a metric.
        is_category = False
        if isinstance(sub_data, list) and len(sub_data) > 0:
            first_sub = sub_data[0]
            if isinstance(first_sub, dict) and "data" in first_sub:
                is_category = True
                
        if is_category:
            # Add all sub-metrics to our flat list, optionally prefixing the metric label with the category
            category_name = item.get("label", "")
            for sub_item in sub_data:
                if isinstance(sub_item, dict):
                    # We create a copy to avoid mutating the original data
                    metric_copy = sub_item.copy()
                    sub_label = sub_item.get("label", "")
                    if category_name:
                        metric_copy["label"] = f"{category_name} - {sub_label}"
                    flat_list.append(metric_copy)
        else:
            # It's already a flat metric, add it directly
            flat_list.append(item)
            
    return flat_list


def parse_statement_json(statement_list):
    """Parse the statement JSON list into a pandas DataFrame."""
    statement_list = flatten_statement_list(statement_list)
    if not statement_list:
        return None
        
    rows = []
    all_periods = set()
    
    # First pass: collect all unique period labels to build columns
    for item in statement_list:
        data_points = item.get("data", [])
        if isinstance(data_points, list):
            for dp in data_points:
                # Check for year in either "year" or "label" key
                yr = dp.get("year") or dp.get("label")
                if yr:
                    all_periods.add(str(yr))
    
    # Sort periods chronologically
    # Quarterly labels are like "Mar-25", "Jun-25", "Sep-25", "Dec-25"
    # Annual labels are like "2008", "2009", etc.
    MONTH_ORDER = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                   "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
    
    def sort_key(label):
        label_str = str(label)
        if "-" in label_str:
            parts = label_str.split("-")
            if len(parts) == 2 and parts[0] in MONTH_ORDER:
                month_name, year_suffix = parts
                # Convert 2-digit year to 4-digit for proper sorting
                yr = int(year_suffix) + (2000 if int(year_suffix) < 50 else 1900)
                return (yr, MONTH_ORDER[month_name])
        # Fallback for plain year labels like "2008"
        try:
            return (int(label_str), 0)
        except ValueError:
            return (9999, label_str)
    
    sorted_periods = sorted(list(all_periods), key=sort_key)
    
    # Second pass: build rows
    for item in statement_list:
        label = item.get("label")
        unit = item.get("unit", "")
        row_dict = {"Metric": label, "Unit": unit}
        
        # Initialize periods to empty
        for p in sorted_periods:
            row_dict[p] = ""
            
        # Fill in values
        data_points = item.get("data", [])
        if isinstance(data_points, list):
            for dp in data_points:
                yr = dp.get("year") or dp.get("label")
                val = dp.get("value")
                if yr:
                    yr_str = str(yr)
                    if yr_str in row_dict:
                        row_dict[yr_str] = val
                
        rows.append(row_dict)
        
    return pd.DataFrame(rows)


def parse_cash_flow_json(cf_data):
    """Parse cash flow JSON (usually has direct/indirect keys) into a DataFrame."""
    if not cf_data or not isinstance(cf_data, dict):
        return None
        
    # Use indirect cash flow by default as it is standard
    cf_list = cf_data.get("indirect") or cf_data.get("direct")
    return parse_statement_json(cf_list)


def fetch_cash_flow_fallback(ticker, period="annual"):
    """Fallback to Yahoo Finance to fetch Cash Flow data if the primary API fails."""
    import yfinance as yf
    
    # Map AskAnalyst tickers to Yahoo Finance symbols if they mismatch
    symbol_map = {
        "WAFI": "SHEL"
    }
    mapped_ticker = symbol_map.get(ticker.upper(), ticker.upper())
    yf_symbol = f"{mapped_ticker}.KA"
    
    print(f"    [!] Attempting fallback to Yahoo Finance for {yf_symbol} ({period})...")
    try:
        stock = yf.Ticker(yf_symbol)
        cf = stock.quarterly_cashflow if period == "quarter" else stock.cashflow
        if cf is not None and not cf.empty:
            # Reset index to make metrics a column
            cf_df = cf.reset_index()
            cf_df.rename(columns={cf_df.columns[0]: "Metric"}, inplace=True)
            cf_df.insert(1, "Unit", "PKR (mn)")  # Default unit representation
            
            # Format and scale columns (Yahoo Finance reports raw numbers, we convert to Millions)
            for col in list(cf_df.columns[2:]):
                # Format header
                if period == "quarter":
                    date_str = str(col)[:7]  # e.g., '2025-09'
                else:
                    date_str = str(col).split("-")[0]  # e.g., '2025'
                cf_df.rename(columns={col: date_str}, inplace=True)
                
                # Scale values to Millions and handle empty cells
                cf_df[date_str] = pd.to_numeric(cf_df[date_str], errors='coerce') / 1_000_000
                cf_df[date_str] = cf_df[date_str].round(2).fillna("")
                
            print(f"    [OK] Successfully retrieved Cash Flow via Yahoo Finance fallback ({len(cf_df)} rows)")
            return cf_df
    except Exception as e:
        print(f"    [-] Yahoo Finance fallback failed: {e}")
    return None



def main():
    print("==================================================")
    print("  AskAnalyst.com.pk API Financial Data Scraper")
    print("==================================================")
    
    for ticker in TICKERS:
        ticker = ticker.upper()
        print(f"\n[+] Processing ticker: {ticker}")
        
        company_id, company_name = get_company_id(ticker)
        if not company_id:
            print(f"[-] Could not find company ID for ticker: {ticker}")
            continue
            
        print(f"[+] Found Company ID: {company_id} ({company_name})")
        
        # 1. Fetch Income Statement (endpoint: iss)
        print(f"[*] Fetching Income Statement ({PERIOD})...")
        is_raw = fetch_statement_data("iss", company_id, PERIOD)
        df_is = parse_statement_json(is_raw)
        
        # 2. Fetch Balance Sheet (endpoint: bss)
        print(f"[*] Fetching Balance Sheet ({PERIOD})...")
        bs_raw = fetch_statement_data("bss", company_id, PERIOD)
        df_bs = parse_statement_json(bs_raw)
        
        # 3. Fetch Cash Flow (endpoint: cf)
        print(f"[*] Fetching Cash Flow ({PERIOD})...")
        if PERIOD == "quarter":
            # AskAnalyst API does not support quarterly cash flow. Always fallback to Yahoo Finance.
            df_cf = fetch_cash_flow_fallback(ticker, PERIOD)
        else:
            cf_raw = fetch_cash_flow(company_id)
            df_cf = parse_cash_flow_json(cf_raw)
            if df_cf is None:
                df_cf = fetch_cash_flow_fallback(ticker, PERIOD)
        
        # 4. Save to Excel
        if (df_is is not None) or (df_bs is not None) or (df_cf is not None):
            # Create subfolder named after the ticker in the workspace root
            company_dir = os.path.join(".", ticker)
            os.makedirs(company_dir, exist_ok=True)
            
            output_file = os.path.join(company_dir, f"{ticker}_{PERIOD}_financials.xlsx")
            
            # Defensive check if file is locked (PermissionError)
            written = False
            suffix = 0
            while not written:
                try:
                    if suffix > 0:
                        output_file = os.path.join(company_dir, f"{ticker}_{PERIOD}_financials_{suffix}.xlsx")
                        
                    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
                        if df_is is not None:
                            df_is.to_excel(writer, sheet_name="Income Statement", index=False)
                        if df_bs is not None:
                            df_bs.to_excel(writer, sheet_name="Balance Sheet", index=False)
                        if df_cf is not None:
                            df_cf.to_excel(writer, sheet_name="Cash Flow", index=False)
                        else:
                            # Write an explanatory note sheet if cash flow failed
                            df_note = pd.DataFrame([{
                                "Status": "Unavailable",
                                "Reason": "AskAnalyst API returned 500 Internal Error (known bug for bank tickers like HBL)"
                            }])
                            df_note.to_excel(writer, sheet_name="Cash Flow (Unavailable)", index=False)
                    
                    # Print sheet addition info
                    if df_is is not None:
                        print(f"    [OK] Added Income Statement ({len(df_is)} rows)")
                    if df_bs is not None:
                        print(f"    [OK] Added Balance Sheet ({len(df_bs)} rows)")
                    if df_cf is not None:
                        print(f"    [OK] Added Cash Flow ({len(df_cf)} rows)")
                    else:
                        print("    [!] Added empty/note sheet for Cash Flow")
                        
                    print(f"[OK] Successfully saved to: {output_file}")
                    written = True
                except PermissionError:
                    suffix += 1
                    print(f"    [!] Warning: File {output_file} is locked. Retrying with a different name...")
                    if suffix > 10:
                        print("    [-] Error: Too many locked files. Exiting.")
                        break
        else:
            print(f"[-] Failed to fetch any financial statements for {ticker}")

        # Rate-limiting delay to avoid server blocks
        print("    [*] Cooling down for 10 seconds...")
        time.sleep(10)

    print("\n[+] Scraping task completed.")


if __name__ == "__main__":
    main()
