import os
import time
from typing import Optional

class MockAPIError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code

class MockFile:
    def __init__(self, name, state):
        self.name = name
        self.state = type('State', (), {'name': state})()
        self.error = None

class MockFilesClient:
    def upload(self, file: str):
        print(f"    [Mock] Uploading {file} to Gemini...")
        
        if "valid" in file:
            return MockFile("files/valid123", "ACTIVE")
        elif "empty" in file:
            # We assume it passes pre-flight but fails on upload
            raise MockAPIError("The document has no pages.", 400)
        elif "corrupt" in file:
            # Gemini might reject it with 400
            raise MockAPIError("Failed to parse PDF file.", 400)
        else:
            raise MockAPIError("Internal server error", 500)
            
    def get(self, name: str):
        return MockFile(name, "ACTIVE")

class MockClient:
    def __init__(self):
        self.files = MockFilesClient()

def create_test_files():
    os.makedirs("test_pdfs", exist_ok=True)
    
    # 1. Valid PDF
    valid_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n5 0 obj\n<< /Length 44 >>\nstream\nBT\n/F1 24 Tf\n100 700 Td\n(Hello World) Tj\nET\nendstream\nendobj\nxref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000223 00000 n \n0000000311 00000 n \ntrailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n404\n%%EOF\n"
    with open("test_pdfs/valid.pdf", "wb") as f:
        f.write(valid_pdf)
        
    # 2. Corrupt PDF
    with open("test_pdfs/corrupt.pdf", "wb") as f:
        f.write(b"%PDF-1.4\nThis is completely garbage data not a real PDF structure.")
        
    # 3. Empty PDF (0 bytes)
    with open("test_pdfs/empty.pdf", "wb") as f:
        f.write(b"")

def robust_upload_pdf(file_path: str, client) -> Optional[str]:
    """
    Robustly upload a PDF to Gemini, handling corrupt, empty, and 400 API errors.
    """
    # 1. Check if file exists
    if not os.path.exists(file_path):
        print(f"[Pre-flight Error] File not found: {file_path}")
        return None
        
    # 2. Pre-flight check for empty file
    if os.path.getsize(file_path) == 0:
        print(f"[Pre-flight Error] File is completely empty (0 bytes): {file_path}")
        return None
        
    # 3. Pre-flight check for PDF magic number (helps catch non-PDFs before upload)
    try:
        with open(file_path, "rb") as f:
            header = f.read(4)
            if header != b"%PDF":
                print(f"[Pre-flight Error] File does not appear to be a PDF (missing magic number): {file_path}")
                return None
    except Exception as e:
        print(f"[Pre-flight Error] Could not read file {file_path}: {e}")
        return None

    # Actual upload attempt with robust error handling
    try:
        # Note: In real code, use google.genai.errors.APIError
        # For this test script, we catch MockAPIError
        APIErrorClass = MockAPIError # replace with `google.genai.errors.APIError`
        
        uploaded_file = client.files.upload(file=file_path)
        
        # Wait for backend processing (if asynchronous)
        while uploaded_file.state.name == "PROCESSING":
            print("    [Status] Processing...", end="\r", flush=True)
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
            
        if uploaded_file.state.name == "FAILED":
            print(f"[Gemini Error] File processing failed on the backend: {uploaded_file.error}")
            return None
            
        print(f"[Success] Uploaded as {uploaded_file.name}")
        return uploaded_file.name
        
    except MockAPIError as e:  # Change to `except google.genai.errors.APIError as e:` in production
        err_msg = str(e).lower()
        if e.code == 400:
            if "no pages" in err_msg:
                print(f"[Gemini 400 Error] The document has no pages: {file_path}")
            else:
                print(f"[Gemini 400 Error] Bad request (likely corrupt/invalid PDF): {e}")
        elif e.code == 429:
            print(f"[Gemini 429 Error] Quota exceeded. Please back off and retry.")
        else:
            print(f"[Gemini API Error] Failed to upload {file_path}: {e}")
        return None
        
    except Exception as e:
        print(f"[Unexpected Error] {type(e).__name__}: {e}")
        return None

def main():
    create_test_files()
    client = MockClient()
    
    test_cases = [
        "test_pdfs/valid.pdf",
        "test_pdfs/corrupt.pdf",
        "test_pdfs/empty.pdf",
        "test_pdfs/nonexistent.pdf"
    ]
    
    print("="*60)
    print("  PSX PDF Ingestion - Error Handling Edge Cases Test")
    print("="*60)
    
    for file_path in test_cases:
        print(f"\n--- Testing: {file_path} ---")
        file_id = robust_upload_pdf(file_path, client)
        if file_id:
            print(f"-> Result: Successfully acquired File ID: {file_id}")
        else:
            print("-> Result: Upload failed gracefully and was handled.")

if __name__ == "__main__":
    main()
