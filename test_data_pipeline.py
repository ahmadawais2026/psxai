import os
import sys
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from dotenv import load_dotenv
load_dotenv()

from data.sbp_easydata import get_macro_snapshot
from data.institutional_flows import get_full_flow_context
from data.retail_sentiment import get_retail_sentiment_snapshot

def main():
    print("==================================================")
    print(" TESTING ADVANCED ALTERNATIVE DATA PIPELINES      ")
    print("==================================================")
    
    print("\n1. Testing SBP EasyData (Macro Context)...")
    try:
        macro = get_macro_snapshot()
        print(f"Success: {macro}")
    except Exception as e:
        print(f"Failed: {e}")
        
    print("\n2. Testing Institutional Flows (FIPI/LIPI & MUFAP)...")
    try:
        flows = get_full_flow_context()
        print(f"Success: {flows}")
    except Exception as e:
        print(f"Failed: {e}")
        
    print("\n3. Testing Retail Sentiment (OGDC)...")
    try:
        sentiment = get_retail_sentiment_snapshot("OGDC")
        print(f"Success: {sentiment}")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    main()
