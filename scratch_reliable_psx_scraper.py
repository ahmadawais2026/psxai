import asyncio
from playwright.async_api import async_playwright

async def run():
    print("Starting Playwright to extract PSX Portal Announcements...")
    async with async_playwright() as p:
        # Launching headless browser
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Go to the specific company page (e.g. AGP)
        company = "AGP"
        url = f"https://dps.psx.com.pk/company/{company}"
        print(f"Navigating to {url} ...")
        
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(3000) # Give extra time for JS to render the tables
        
        # PSX portal uses tabs. The "Announcements" section usually has the class or ID we can look for
        # Or we can just extract all tables to find the announcements.
        tables = await page.query_selector_all('table')
        print(f"Found {len(tables)} tables on the page.")
        
        announcements = []
        for table in tables:
            text = await table.inner_text()
            # The announcements table usually contains "Subject" or dates like "2023"
            if "Subject" in text or "Date" in text:
                rows = await table.query_selector_all('tr')
                for row in rows:
                    cells = await row.query_selector_all('td')
                    if len(cells) >= 2:
                        date_text = await cells[0].inner_text()
                        subject_text = await cells[1].inner_text()
                        # If there's a third column, it might be the subject
                        if len(cells) >= 3:
                            subject_text = await cells[2].inner_text()
                        announcements.append(f"{date_text.strip()} | {subject_text.strip()}")
        
        print("\n--- Extracted Announcements ---")
        found_merger = False
        for ann in set(announcements):
            if "merger" in ann.lower() or "amalgamation" in ann.lower() or "agp" in ann.lower():
                print(f"[FOUND] {ann}")
                if "merger" in ann.lower() or "amalgamation" in ann.lower():
                    found_merger = True
            
        if not found_merger:
            print("\nCould not explicitly find 'merger' or 'amalgamation' in the recent rows.")
            print("The full list of recent announcements captured:")
            for ann in set(announcements)[:5]:
                print(f" - {ann}")

        await browser.close()

asyncio.run(run())
