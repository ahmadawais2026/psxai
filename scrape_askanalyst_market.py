import os
import time
import requests
import json
import pandas as pd

# ═══════════════════════════════════════════════════════════════════════
#  CONFIG & ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════
BASE_API_URL = "https://api.askanalyst.com.pk/api"
OUTPUT_DIR = "market_data"

# Create output directories
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, "briefings"), exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, "sectors"), exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, "news"), exist_ok=True)


def safe_request(method, endpoint, payload=None, params=None):
    """Make requests with safety delay and exception handling."""
    url = f"{BASE_API_URL}/{endpoint}"
    time.sleep(1)  # 1 second defensive rate limit between request loops
    try:
        if method.upper() == "POST":
            r = requests.post(url, json=payload, params=params, timeout=10)
        else:
            r = requests.get(url, params=params, timeout=10)
            
        if r.status_code == 200:
            return r.json()
        else:
            print(f"    [-] {method} /{endpoint} failed with status {r.status_code}")
    except Exception as e:
        print(f"    [-] Exception requesting /{endpoint}: {e}")
    return None


def fetch_general_market_data():
    """Fetch general market summaries, news feed, and PDF catalogs."""
    print("\n==================================================")
    print("  Fetching General Market Data")
    print("==================================================")
    
    # 1. Morning Briefing Summary (flows, currency, commodities, etc.)
    print("[*] Fetching Morning Briefing Daily Summary (/morningbriefingchart)...")
    mb_chart = safe_request("GET", "morningbriefingchart")
    if mb_chart:
        file_path = os.path.join(OUTPUT_DIR, "briefings", "morning_briefing_summary.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(mb_chart, f, indent=4)
        print(f"    [OK] Saved morning briefing summary to {file_path}")

    # 2. General News & PSX Disclosures
    print("[*] Fetching General News/Disclosures (/news/all)...")
    news_data = safe_request("GET", "news/all", params={"page": 1, "postsperpage": 100})
    if news_data and "data" in news_data:
        df_news = pd.DataFrame(news_data["data"])
        file_path = os.path.join(OUTPUT_DIR, "news", "psx_disclosures_latest.xlsx")
        df_news.to_excel(file_path, index=False)
        print(f"    [OK] Saved 100 latest PSX disclosures to {file_path}")

    # 3. PDF Catalogs (Daily reports, Technical views, Roundups)
    catalogs = {
        "morningbriefing": "morning_briefing_pdfs.xlsx",
        "technicalresearch": "technical_research_pdfs.xlsx",
        "marketroundup": "market_roundup_pdfs.xlsx",
        "reports/3": "daily_market_reports.xlsx"
    }
    for endpoint, filename in catalogs.items():
        print(f"[*] Fetching catalogs from /{endpoint}...")
        cat_data = safe_request("GET", endpoint)
        if cat_data:
            if isinstance(cat_data, list):
                df = pd.DataFrame(cat_data)
            elif isinstance(cat_data, dict) and "data" in cat_data:
                df = pd.DataFrame(cat_data["data"])
            else:
                print(f"    [-] Unknown data structure for /{endpoint}")
                continue
            
            file_path = os.path.join(OUTPUT_DIR, "briefings", filename)
            df.to_excel(file_path, index=False)
            print(f"    [OK] Saved catalog to {file_path}")


def fetch_macro_indicators():
    """Fetch Macroeconomic databank catalog."""
    print("\n==================================================")
    print("  Fetching Macroeconomic Indicators")
    print("==================================================")
    print("[*] Fetching searchlist index (/searchlist)...")
    search_list = safe_request("GET", "searchlist")
    if search_list:
        file_path = os.path.join(OUTPUT_DIR, "macro_indicators_index.xlsx")
        df = pd.DataFrame(search_list)
        df.to_excel(file_path, index=False)
        print(f"    [OK] Saved macro index to {file_path}")


def fetch_cement_sector():
    """Fetch Cement sector pricing and dispatches."""
    print("\n==================================================")
    print("  Fetching Cement Sector Data")
    print("==================================================")
    metadata = safe_request("GET", "sector/cement")
    if not metadata:
        return
        
    dates = metadata.get("dates", {})
    companies = metadata.get("company", [])
    
    # Save metadata
    meta_path = os.path.join(OUTPUT_DIR, "sectors", "cement_metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)
    print(f"    [OK] Saved Cement sector metadata to {meta_path}")

    # Set up default POST payload date range
    weekly_dates = dates.get("weekly", [])
    if weekly_dates:
        # Sort or grab latest
        sdate = weekly_dates[-1].get("start_date")
        edate = weekly_dates[0].get("start_date")
    else:
        sdate = "2020-01-01"
        edate = "2026-06-04"

    # POST queries for Price & Sales data (using All Companies for simplicity/coverage)
    payloads = [
        {"name": "weekly_retail_prices", "payload": {
            "company": "All Companies", "sdate": sdate, "edate": edate,
            "indices": "All", "type": "price", "parameter": "retail_price",
            "frequency": "weekly", "growth": "value"
        }},
        {"name": "weekly_sales_dispatches", "payload": {
            "company": "All Companies", "sdate": sdate, "edate": edate,
            "indices": "All", "type": "sales", "parameter": "local_dispatches",
            "frequency": "weekly", "growth": "value"
        }}
    ]

    for p_info in payloads:
        name = p_info["name"]
        print(f"[*] Posting query for Cement: {name}...")
        resp = safe_request("POST", "sector/cement", payload=p_info["payload"])
        if resp:
            # Save raw json output
            file_path = os.path.join(OUTPUT_DIR, "sectors", f"cement_{name}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(resp, f, indent=4)
            print(f"    [OK] Saved {name} data to {file_path}")


def fetch_fertilizer_sector():
    """Fetch Fertilizer sector production/sales."""
    print("\n==================================================")
    print("  Fetching Fertilizer Sector Data")
    print("==================================================")
    metadata = safe_request("GET", "sector/fertilizer")
    if not metadata:
        return
        
    dates = metadata.get("dates", {})
    
    # Save metadata
    meta_path = os.path.join(OUTPUT_DIR, "sectors", "fertilizer_metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)
    print(f"    [OK] Saved Fertilizer sector metadata to {meta_path}")

    # Set up date range
    monthly_dates = dates.get("monthly", [])
    if monthly_dates:
        sdate = monthly_dates[-1].get("start_date")
        edate = monthly_dates[0].get("start_date")
    else:
        sdate = "2020-01-01"
        edate = "2026-06-04"

    # Query local Urea / DAP sales & production
    payloads = [
        {"name": "monthly_sales", "payload": {
            "company": "All Companies", "sdate": sdate, "edate": edate,
            "type": "sales", "product": "Urea", "parameter": "local_sales",
            "frequency": "monthly", "growth": "value"
        }},
        {"name": "monthly_production", "payload": {
            "company": "All Companies", "sdate": sdate, "edate": edate,
            "type": "production", "product": "Urea", "parameter": "production",
            "frequency": "monthly", "growth": "value"
        }}
    ]

    for p_info in payloads:
        name = p_info["name"]
        print(f"[*] Posting query for Fertilizer: {name}...")
        resp = safe_request("POST", "sector/fertilizer", payload=p_info["payload"])
        if resp:
            file_path = os.path.join(OUTPUT_DIR, "sectors", f"fertilizer_{name}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(resp, f, indent=4)
            print(f"    [OK] Saved {name} data to {file_path}")


def fetch_omc_sector():
    """Fetch OMC (Oil Marketing Companies) sales."""
    print("\n==================================================")
    print("  Fetching OMC Sector Data")
    print("==================================================")
    metadata = safe_request("GET", "sector/omc")
    if not metadata:
        return
        
    dates = metadata.get("dates", {})
    
    # Save metadata
    meta_path = os.path.join(OUTPUT_DIR, "sectors", "omc_metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)
    print(f"    [OK] Saved OMC sector metadata to {meta_path}")

    # Set up date range
    monthly_dates = dates.get("monthly", [])
    if monthly_dates:
        sdate = monthly_dates[-1].get("start_date")
        edate = monthly_dates[0].get("start_date")
    else:
        sdate = "2020-01-01"
        edate = "2026-06-04"

    # Query OMC product sales
    payload = {
        "company": "All Companies", "sdate": sdate, "edate": edate,
        "salestab": "productwise", "product": "Motor Spirit", "parameter": "sales",
        "frequency": "monthly", "growth": "value"
    }
    print("[*] Posting query for OMC Product Sales...")
    resp = safe_request("POST", "sector/omc", payload=payload)
    if resp:
        file_path = os.path.join(OUTPUT_DIR, "sectors", "omc_monthly_sales.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(resp, f, indent=4)
        print(f"    [OK] Saved OMC sales data to {file_path}")


def fetch_autos_sector():
    """Fetch Automobile sector sales & production."""
    print("\n==================================================")
    print("  Fetching Automobile Sector Data")
    print("==================================================")
    metadata = safe_request("GET", "sector/autos")
    if not metadata:
        return
        
    dates = metadata.get("dates", {})
    
    # Save metadata
    meta_path = os.path.join(OUTPUT_DIR, "sectors", "autos_metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)
    print(f"    [OK] Saved Autos sector metadata to {meta_path}")

    # Set up date range
    monthly_dates = dates.get("monthly", [])
    if monthly_dates:
        sdate = monthly_dates[-1].get("start_date")
        edate = monthly_dates[0].get("start_date")
    else:
        sdate = "2020-01-01"
        edate = "2026-06-04"

    # Query Auto sales
    payload = {
        "company": "All Companies", "sdate": sdate, "edate": edate,
        "salestab": "categorywise", "category": "Passenger Cars", "parameter": "sales",
        "frequency": "monthly", "growth": "value"
    }
    print("[*] Posting query for Auto Category Sales...")
    resp = safe_request("POST", "sector/autos", payload=payload)
    if resp:
        file_path = os.path.join(OUTPUT_DIR, "sectors", "autos_monthly_sales.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(resp, f, indent=4)
        print(f"    [OK] Saved Auto sales data to {file_path}")


def fetch_circulardebt_sector():
    """Fetch Circular Debt flow and stock information."""
    print("\n==================================================")
    print("  Fetching Circular Debt Sector Data")
    print("==================================================")
    metadata = safe_request("GET", "sector/circulardebt")
    if not metadata:
        return
        
    dates = metadata.get("dates", {})
    
    # Save metadata
    meta_path = os.path.join(OUTPUT_DIR, "sectors", "circulardebt_metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)
    print(f"    [OK] Saved Circular Debt sector metadata to {meta_path}")

    # Set up date range
    monthly_dates = dates.get("monthly", [])
    if monthly_dates:
        sdate = monthly_dates[-1].get("start_date")
        edate = monthly_dates[0].get("start_date")
    else:
        sdate = "2020-01-01"
        edate = "2026-06-04"

    # Query circular debt balances
    payload = {
        "sdate": sdate, "edate": edate,
        "type": "flow", "parameter": "value",
        "frequency": "monthly", "growth": "value"
    }
    print("[*] Posting query for Circular Debt Flow...")
    resp = safe_request("POST", "sector/circulardebt", payload=payload)
    if resp:
        file_path = os.path.join(OUTPUT_DIR, "sectors", "circulardebt_monthly_flow.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(resp, f, indent=4)
        print(f"    [OK] Saved Circular Debt flow data to {file_path}")


def main():
    print("==================================================")
    print("  AskAnalyst Sector & Market Data Downloader")
    print("==================================================")
    
    # Run fetch operations
    fetch_general_market_data()
    fetch_macro_indicators()
    fetch_cement_sector()
    fetch_fertilizer_sector()
    fetch_omc_sector()
    fetch_autos_sector()
    fetch_circulardebt_sector()
    
    print("\n[+] All market and sector downloads completed.")


if __name__ == "__main__":
    main()
