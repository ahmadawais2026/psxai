import asyncio
from playwright.async_api import async_playwright

async def run():
    print("Starting Playwright to capture PSX API...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        async def handle_response(response):
            try:
                # We want to catch XHR / Fetch requests that might contain JSON
                if response.request.resource_type in ['xhr', 'fetch'] and "json" in response.headers.get("content-type", ""):
                    url = response.url.lower()
                    if "dps.psx.com.pk" in url:
                        print(f"Captured API: {response.url}")
                        data = await response.text()
                        if "AGP" in data or "agp" in data.lower():
                            print(f"AGP mentioned in: {response.url}")
                            print(data[:300])
            except Exception as e:
                pass

        page.on("response", handle_response)
        
        # Navigate directly to the AGP company page to see what announcements it loads
        print("Navigating to https://dps.psx.com.pk/company/AGP")
        await page.goto("https://dps.psx.com.pk/company/AGP", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        
        # Navigate to the general announcements page
        print("Navigating to https://dps.psx.com.pk/announcements/companies")
        await page.goto("https://dps.psx.com.pk/announcements/companies", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        
        await browser.close()

asyncio.run(run())
