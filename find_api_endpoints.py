import requests
import re

def find_api_endpoints():
    js_url = "https://askanalyst.com.pk/static/js/main.22aa3eca.js"
    print("[+] Downloading bundle...")
    r = requests.get(js_url)
    js = r.text
    
    print("[+] Searching for backend endpoints...")
    # Find all strings matching /api/something or endpoints in axios calls
    # Usually in React, endpoints are defined like axios.get("url") or fetch("url")
    # or just string literals like "/companylistwithids"
    # Let's find all string literals starting with a slash, containing word characters, up to 40 chars.
    paths = re.findall(r'[\'"](/[^/\\\'"\s]+(?:/[^/\\\'"\s]+)*)[\'"]', js)
    unique_paths = set(paths)
    
    print(f"[+] Found {len(unique_paths)} unique paths starting with /:")
    
    # Filter paths that might be API endpoints
    keywords = ["company", "financial", "statement", "ratio", "balance", "income", "cash", "data", "list"]
    api_paths = []
    for p in unique_paths:
        if any(kw in p.lower() for kw in keywords):
            api_paths.append(p)
            
    print(f"[+] Filtered {len(api_paths)} possible API endpoints:")
    for p in sorted(api_paths):
        print(f"    - {p}")

if __name__ == "__main__":
    find_api_endpoints()
