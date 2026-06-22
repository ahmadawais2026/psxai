import requests
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def test_sarmaya_news(keyword="AGP"):
    print(f"\n--- Testing Sarmaya.pk for '{keyword}' ---")
    url = f"https://sarmaya.pk/psx/market/news/search?q={keyword}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=10)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Searching for links or divs containing news
        found = False
        for a in soup.find_all('a'):
            text = a.get_text(strip=True).lower()
            if keyword.lower() in text and ('merger' in text or 'announcement' in text):
                print(f"[Sarmaya] FOUND: {text}")
                print(f"Link: {a.get('href')}")
                found = True
        if not found:
            print("[Sarmaya] Specific news not found in search results HTML.")
    except Exception as e:
        print(f"[Sarmaya] Error: {e}")

def test_askanalyst_news(symbol="AGP"):
    print(f"\n--- Testing AskAnalyst API for '{symbol}' ---")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*"
    }
    # First get ID
    try:
        r = requests.get("https://api.askanalyst.com.pk/api/companylistwithids", headers=headers, timeout=10)
        companies = r.json()
        cid = None
        for c in companies:
            if c.get("symbol", "").upper() == symbol:
                cid = c.get("id")
                break
        
        if cid:
            print(f"AskAnalyst ID for {symbol} is {cid}")
            r2 = requests.get(f"https://api.askanalyst.com.pk/api/news/{cid}", headers=headers, timeout=10)
            data = r2.json()
            news_items = data.get("data", [])
            print(f"Found {len(news_items)} news items.")
            found_merger = False
            for n in news_items:
                title = n.get("title", "").lower()
                if "merger" in title or "amalgamation" in title:
                    print(f"[AskAnalyst] FOUND Merger Event: {n.get('date')} | {n.get('title')}")
                    found_merger = True
            if not found_merger:
                print("[AskAnalyst] Merger event dropped or not found in their feed.")
        else:
            print("Could not find company ID on AskAnalyst.")
    except Exception as e:
        print(f"[AskAnalyst] Error: {e}")

if __name__ == "__main__":
    test_sarmaya_news("AGP")
    test_askanalyst_news("AGP")
