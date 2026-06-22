import os
import pdfplumber

def extract_text_from_pdf(pdf_path: str) -> str:
    \"\"\"
    Fallback mechanism for extracting text from a PSX PDF without relying on Gemini's multimodal capabilities.
    Attempts to read the embedded text layer using pdfplumber.
    If the text is too sparse, it raises a ValueError indicating OCR may be required.
    \"\"\"
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f\"PDF not found: {pdf_path}\")

    total_text = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Extract text from each page, keeping layout where possible
            text = page.extract_text(x_tolerance=1, y_tolerance=1)
            if text:
                total_text.append(text)
    
    full_text = \"\\n\\n\".join(total_text)
    
    # Heuristic: if a multi-page PDF yields very few characters, it's likely scanned/image-based
    if len(full_text.strip()) < 100:
        raise ValueError(\"Extracted text is very sparse. The PDF might be image-based and require OCR.\")
        
    return full_text

def test_fallback(pdf_path: str):
    \"\"\"
    Utility to test the extraction and print metrics.
    \"\"\"
    try:
        text = extract_text_from_pdf(pdf_path)
        print(f\"SUCCESS: Extracted {len(text)} characters from {os.path.basename(pdf_path)}\")
        print(\"Preview:\\n\", text[:500])
        return True
    except Exception as e:
        print(f\"FAILURE: {e}\")
        return False

if __name__ == \"__main__\":
    # Default test file if run standalone
    import sys
    if len(sys.argv) > 1:
        test_fallback(sys.argv[1])
    else:
        sample_path = r\"E:\\Investment Advisor\\Sample research reports\\Mughal Steels.pdf\"
        test_fallback(sample_path)
