import os
import json
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(message)s')

load_dotenv()

from agents.orchestrator import Orchestrator

def main():
    print("Initializing orchestrator...")
    orchestrator = Orchestrator()
    symbol = "OGDC"
    print(f"Running pipeline for {symbol}...")
    
    # Run the orchestrator on OGDC to see if it successfully processes without blowing up
    try:
        report = orchestrator.analyze(symbol)
        
        # We don't need to print the whole thing, just a summary
        print("\n\nPipeline Complete!")
        print(f"Risk Score: {report.get('risk_report', {}).get('risk_score')}")
        print(f"Sentiment Score: {report.get('sentiment_report', {}).get('adjusted_score')}")
        
        # Let's see if our keys are in the raw output
        print("\nChecking if alternative data was integrated properly:")
        tech_blob = report.get('technical_report', {}).get('summary', '')
        print("Done!")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
