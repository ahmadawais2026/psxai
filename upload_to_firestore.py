import os
import pandas as pd
from datetime import datetime
from config import firebase_db

def upload_company_data():
    if firebase_db is None:
        print("[-] Firebase Firestore client is not initialized. Please verify credentials in .env")
        return
        
    company_data_dir = os.path.join(".", "company_data")
    if not os.path.exists(company_data_dir):
        print(f"[-] Directory {company_data_dir} does not exist.")
        return

    print("==================================================")
    print("  Uploading Financial Data to Firestore")
    print("==================================================")

    # Get all company folders
    tickers = [d for d in os.listdir(company_data_dir) if os.path.isdir(os.path.join(company_data_dir, d))]
    print(f"[+] Found {len(tickers)} company folders in company_data/")

    uploaded_count = 0
    for ticker in sorted(tickers):
        ticker_path = os.path.join(company_data_dir, ticker)
        # Search for financials excel files
        excel_files = [f for f in os.listdir(ticker_path) if f.endswith("_financials.xlsx")]
        if not excel_files:
            continue
            
        excel_file = excel_files[0]
        excel_path = os.path.join(ticker_path, excel_file)
        
        # Determine period from filename
        period = "quarter" if "quarter" in excel_file else "annual"
        print(f"\n[*] Processing ticker {ticker} ({period})...")
        
        try:
            xls = pd.ExcelFile(excel_path)
            sheets_data = {}
            
            # Read and convert each sheet
            for sheet_name in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet_name)
                # Clean up NaN / Null values so Firestore JSON serialization doesn't fail
                df = df.replace({pd.NA: None, pd.NaT: None})
                # Convert numeric types from numpy to native Python float/int
                for col in df.columns:
                    df[col] = df[col].apply(lambda x: None if pd.isna(x) else x)
                
                # Convert sheet rows to list of dicts
                rows = df.to_dict(orient="records")
                # Format sheet names to valid Firestore field names (lowercase, underscores)
                key_name = sheet_name.lower().replace(" ", "_").replace("&", "and")
                sheets_data[key_name] = rows
            
            # Prepare Firestore Document payload
            doc_data = {
                "symbol": ticker.upper(),
                "period": period,
                "last_updated": datetime.utcnow().isoformat() + "Z",
                **sheets_data
            }
            
            # Save to: companies/{ticker}/financials/{period}
            doc_ref = firebase_db.collection("companies").document(ticker.upper()).collection("financials").document(period)
            doc_ref.set(doc_data)
            
            # Also update the parent company profile/info if it exists
            # We can grab basic info from the Latest Quote sheet if present
            company_doc_ref = firebase_db.collection("companies").document(ticker.upper())
            company_doc_ref.set({
                "symbol": ticker.upper(),
                "last_updated": datetime.utcnow().isoformat() + "Z"
            }, merge=True)
            
            print(f"    [OK] Uploaded {len(sheets_data)} sheets to Firestore for {ticker}")
            uploaded_count += 1
            
        except Exception as e:
            print(f"    [-] Failed to process/upload {ticker}: {e}")

    print(f"\n[+] Successfully uploaded data for {uploaded_count} companies to Firestore.")

if __name__ == "__main__":
    upload_company_data()
