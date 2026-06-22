import os
import time
import json
from reportlab.pdfgen import canvas
from google import genai
from google.genai import types

# Load config to get Vertex / GenAI settings
import config

TICKERS = ["OGDC", "SYS", "HUBC"]
PDF_DIR = "scratch/pdfs"
MODEL_NAME = "gemini-3.1-flash-lite" # As requested by user

mock_data = {
    "OGDC": {
        "company": "Oil and Gas Development Company Limited",
        "date": "2024-03-15",
        "eps": "12.5",
        "revenue": "105 Billion PKR",
        "highlights": "Strong production from Nashpa and Qadirpur fields. Announced an interim dividend of PKR 2.5 per share."
    },
    "SYS": {
        "company": "Systems Limited",
        "date": "2024-03-18",
        "eps": "8.2",
        "revenue": "15 Billion PKR",
        "highlights": "Significant growth in IT exports and software consulting. Won a major public sector contract in the Middle East."
    },
    "HUBC": {
        "company": "Hub Power Company Limited",
        "date": "2024-03-20",
        "eps": "18.4",
        "revenue": "85 Billion PKR",
        "highlights": "Thar energy project fully operational. Continued focus on renewable energy diversification."
    }
}

def generate_mock_pdf(ticker, data):
    os.makedirs(PDF_DIR, exist_ok=True)
    file_path = os.path.join(PDF_DIR, f"{ticker}_announcement.pdf")
    c = canvas.Canvas(file_path)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(100, 750, f"{data['company']} - Financial Announcement")
    c.setFont("Helvetica", 12)
    c.drawString(100, 700, f"Date: {data['date']}")
    c.drawString(100, 670, f"Earnings Per Share (EPS): {data['eps']}")
    c.drawString(100, 640, f"Total Revenue: {data['revenue']}")
    c.drawString(100, 610, "Key Highlights:")
    
    # Simple word wrap
    textobject = c.beginText(100, 590)
    textobject.setFont("Helvetica", 12)
    words = data['highlights'].split()
    line = ""
    for word in words:
        if len(line) + len(word) > 70:
            textobject.textLine(line)
            line = word + " "
        else:
            line += word + " "
    textobject.textLine(line)
    c.drawText(textobject)
    
    c.save()
    return file_path

def evaluate_gemini():
    print(f"--- Evaluating {MODEL_NAME} for PDF Extraction ---")
    
    if config.USE_VERTEX:
        client = genai.Client(vertexai=True, project=config.VERTEX_PROJECT, location=config.VERTEX_LOCATION)
    else:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        
    results = []
    
    prompt = """
    Extract the following information from this financial announcement PDF:
    - Company Name
    - Date
    - EPS (Earnings Per Share)
    - Revenue
    - Key Highlights
    
    Return the extracted data in JSON format only with keys: company, date, eps, revenue, highlights.
    """
    
    for ticker in TICKERS:
        print(f"\nProcessing {ticker}...")
        pdf_path = generate_mock_pdf(ticker, mock_data[ticker])
        print(f"  Generated mock PDF: {pdf_path}")
        
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
            
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        
        start_time = time.time()
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=[pdf_part, prompt],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json"
                )
            )
            latency = time.time() - start_time
            
            # Use fallback if token counts are unavailable depending on SDK version
            input_tokens = getattr(response.usage_metadata, "prompt_token_count", "N/A")
            output_tokens = getattr(response.usage_metadata, "candidates_token_count", "N/A")
            total_tokens = getattr(response.usage_metadata, "total_token_count", "N/A")
            
            try:
                extracted = json.loads(response.text)
                # Simple accuracy check
                expected = mock_data[ticker]
                acc_score = 0
                total_fields = 5
                
                if extracted.get('company', '').lower() in expected['company'].lower() or expected['company'].lower() in extracted.get('company', '').lower(): acc_score += 1
                if extracted.get('date') == expected['date']: acc_score += 1
                if str(expected['eps']) in str(extracted.get('eps', '')): acc_score += 1
                if str(expected['revenue']).split()[0] in str(extracted.get('revenue', '')): acc_score += 1
                hl = extracted.get('highlights', '')
                hl = ' '.join(hl) if isinstance(hl, list) else str(hl)
                if expected['highlights'].split()[0].lower() in hl.lower(): acc_score += 1
                
                accuracy = f"{(acc_score/total_fields)*100}% ({acc_score}/{total_fields})"
                
            except json.JSONDecodeError:
                extracted = "JSON Decode Error"
                accuracy = "0% (JSON parsing failed)"
                
            res = {
                "ticker": ticker,
                "latency_sec": round(latency, 2),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "accuracy": accuracy,
                "extracted": extracted
            }
            results.append(res)
            
            print(f"  Latency: {res['latency_sec']}s")
            print(f"  Tokens: {total_tokens} (In: {input_tokens}, Out: {output_tokens})")
            print(f"  Accuracy: {accuracy}")
            
        except Exception as e:
            print(f"  [!] Error processing {ticker}: {e}")

    print("\n--- Summary ---")
    for r in results:
        print(f"Ticker: {r['ticker']} | Latency: {r['latency_sec']}s | Tokens: {r['total_tokens']} | Acc: {r['accuracy']}")

if __name__ == "__main__":
    evaluate_gemini()
