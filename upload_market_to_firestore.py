import os
import json
import pandas as pd
from datetime import datetime
from config import firebase_db

def upload_market_data():
    if firebase_db is None:
        print("[-] Firebase Firestore client is not initialized. Please verify credentials in .env")
        return
        
    market_data_dir = os.path.join(".", "market_data")
    if not os.path.exists(market_data_dir):
        print(f"[-] Directory {market_data_dir} does not exist.")
        return

    print("==================================================")
    print("  Uploading Market Intelligence to Firestore")
    print("==================================================")

    # 1. Morning Briefing Summary
    summary_path = os.path.join(market_data_dir, "briefings", "morning_briefing_summary.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary_data = json.load(f)
            # Save to market_intelligence/morning_briefing_summary
            doc_ref = firebase_db.collection("market_intelligence").document("morning_briefing_summary")
            doc_ref.set({
                "last_updated": datetime.utcnow().isoformat() + "Z",
                **summary_data
            })
            print("[OK] Uploaded morning briefing summary.")
        except Exception as e:
            print(f"[-] Failed to upload morning briefing summary: {e}")

    # 2. Latest News (Disclosures)
    news_path = os.path.join(market_data_dir, "news", "latest_news.xlsx")
    if os.path.exists(news_path):
        try:
            df = pd.read_excel(news_path)
            df = df.replace({pd.NA: None, pd.NaT: None})
            for col in df.columns:
                df[col] = df[col].apply(lambda x: None if pd.isna(x) else x)
            rows = df.to_dict(orient="records")
            doc_ref = firebase_db.collection("market_intelligence").document("latest_news")
            doc_ref.set({
                "last_updated": datetime.utcnow().isoformat() + "Z",
                "news_items": rows
            })
            print(f"[OK] Uploaded {len(rows)} news items to latest_news.")
        except Exception as e:
            print(f"[-] Failed to upload latest news: {e}")

    # 3. Macroeconomic Indicators Index
    macro_path = os.path.join(market_data_dir, "macro_indicators_index.xlsx")
    if os.path.exists(macro_path):
        try:
            df = pd.read_excel(macro_path)
            df = df.replace({pd.NA: None, pd.NaT: None})
            for col in df.columns:
                df[col] = df[col].apply(lambda x: None if pd.isna(x) else x)
            rows = df.to_dict(orient="records")
            doc_ref = firebase_db.collection("market_intelligence").document("macro_indicators_index")
            doc_ref.set({
                "last_updated": datetime.utcnow().isoformat() + "Z",
                "indicators": rows
            })
            print(f"[OK] Uploaded {len(rows)} macro indicators to index.")
        except Exception as e:
            print(f"[-] Failed to upload macro indicators index: {e}")

    # 4. PDF Catalogs (briefing list, technical research list, roundups, reports)
    catalogs = {
        "morning_briefing_pdfs.xlsx": "morning_briefing_catalogs",
        "technical_research_pdfs.xlsx": "technical_research_catalogs",
        "market_roundup_pdfs.xlsx": "market_roundup_catalogs",
        "research_reports_catalog.xlsx": "research_reports_catalogs"
    }
    for filename, doc_id in catalogs.items():
        filepath = os.path.join(market_data_dir, "briefings", filename)
        if os.path.exists(filepath):
            try:
                df = pd.read_excel(filepath)
                df = df.replace({pd.NA: None, pd.NaT: None})
                for col in df.columns:
                    df[col] = df[col].apply(lambda x: None if pd.isna(x) else x)
                rows = df.to_dict(orient="records")
                doc_ref = firebase_db.collection("market_intelligence").document(doc_id)
                doc_ref.set({
                    "last_updated": datetime.utcnow().isoformat() + "Z",
                    "items": rows
                })
                print(f"[OK] Uploaded catalog: {filename} ({len(rows)} entries).")
            except Exception as e:
                print(f"[-] Failed to upload catalog {filename}: {e}")

    # 5. Sector Metadata & Data
    sectors_dir = os.path.join(market_data_dir, "sectors")
    if os.path.exists(sectors_dir):
        sector_keys = ["cement", "fertilizer", "omc", "autos", "circulardebt"]
        for name in os.listdir(sectors_dir):
            filepath = os.path.join(sectors_dir, name)
            
            # Metadata json upload
            if name.endswith("_metadata.json"):
                sector_name = name.replace("_metadata.json", "")
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        meta_data = json.load(f)
                    doc_ref = firebase_db.collection("sectors").document(sector_name)
                    doc_ref.set({
                        "last_updated": datetime.utcnow().isoformat() + "Z",
                        **meta_data
                    })
                    print(f"[OK] Uploaded sector metadata for: {sector_name}.")
                except Exception as e:
                    print(f"[-] Failed to upload sector metadata for {sector_name}: {e}")
            
            # Data json upload (time-series)
            elif name.endswith(".json"):
                # Determine sector and dataset names
                matched_sector = None
                for key in sector_keys:
                    if name.startswith(key + "_"):
                        matched_sector = key
                        break
                
                if matched_sector:
                    dataset_name = name[len(matched_sector)+1 : -5]  # strip sector_ and .json
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            data_content = json.load(f)
                        
                        # Flatten nested arrays to a single flat list if it contains sublists
                        if isinstance(data_content, list) and len(data_content) > 0 and isinstance(data_content[0], list):
                            flat_list = []
                            for sublist in data_content:
                                if isinstance(sublist, list):
                                    flat_list.extend(sublist)
                                else:
                                    flat_list.append(sublist)
                            data_content = flat_list
                            
                        doc_ref = firebase_db.collection("sectors").document(matched_sector).collection("data").document(dataset_name)
                        doc_ref.set({
                            "last_updated": datetime.utcnow().isoformat() + "Z",
                            "data": data_content
                        })
                        print(f"[OK] Uploaded sector data: {matched_sector} -> {dataset_name} ({len(data_content)} items).")
                    except Exception as e:
                        print(f"[-] Failed to upload sector data for {matched_sector}/{dataset_name}: {e}")

    print("\n[+] Market intelligence data upload to Firestore completed.")

if __name__ == "__main__":
    upload_market_data()
