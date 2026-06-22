import requests
import json
import feedparser
from bs4 import BeautifulSoup
import urllib.parse

def test_sarmaya_news(keyword="AGP"):
    print(f"\n--- Testing Sarmaya.pk for '{keyword}' ---")
    # Sarmaya doesn't have an official public API, but we can search its HTML or internal search API
    url = f"https://sarmaya.pk/psx/market/news/search?q={keyword}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        # Assuming it returns HTML with news items
        soup = BeautifulSoup(r.text, 'html.parser')
        # Typical classes on Sarmaya might vary, we'll just look for keyword in text
        found = False
        for text in soup.stripped_strings:
            if keyword.lower() in text.lower() and ('merger' in text.lower() or 'announcement' in text.lower()):
                print(f"[Sarmaya] FOUND Context: {text}")
                found = True
                break
        if not found:
            print("[Sarmaya] Specific merger news not found in direct text search.")
    except Exception as e:
        print(f"[Sarmaya] Error: {e}")

def test_investify_api(keyword="AGP"):
    print(f"\n--- Testing Investify.pk (Unofficial API probe) ---")
    url = f"https://api.investify.pk/api/company/news?symbol={keyword}"
    headers = {"User-Agent": "Investify App / 1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            print(f"[Investify] Success. Retrieved {len(data)} items.")
            for item in data[:3]:
                print(f" - {item.get('title', '')}")
        else:
            print(f"[Investify] Failed or Endpoint invalid. Status: {r.status_code}")
    except Exception as e:
        print(f"[Investify] Error: {e}")

def test_playwright_psx_dps():
    print(f"\n--- Testing PSX DPS via Playwright (to capture hidden API) ---")
    print("Requires: pip install playwright && playwright install")
    script = """
import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Listen for all network responses to find the JSON data API
        async def handle_response(response):
            if "announcement" in response.url.lower() and "json" in response.headers.get("content-type", ""):
                print(f"[Playwright] Found API URL: {response.url}")
                try:
                    data = await response.json()
                    print(f"[Playwright] Data sample: {str(data)[:200]}...")
                except Exception:
                    pass

        page.on("response", handle_response)
        
        print("Navigating to PSX Announcements...")
        await page.goto("https://dps.psx.com.pk/announcements/companies")
        await page.wait_for_timeout(5000) # Wait for network
        await browser.close()

asyncio.run(run())
"""
    print("Playwright script generated. Run it separately to capture the undocumented PSX API.")

if __name__ == "__main__":
    test_sarmaya_news("AGP")
    test_investify_api("AGP")
    test_playwright_psx_dps()
