import requests
import feedparser
import yfinance as yf
from bs4 import BeautifulSoup
import re

def test_rss_feeds(keyword="AGP"):
    print(f"\n--- Testing RSS Feeds for '{keyword}' ---")
    feeds = {
        "Profit by Pakistan Today": "https://profit.pakistantoday.com.pk/feed/",
        "Business Recorder": "https://www.brecorder.com/feeds/latest-news",
        "Mettis Global": "https://mettisglobal.news/feed/"
    }
    
    headers = {"User-Agent": "Mozilla/5.0"}
    
    for name, url in feeds.items():
        try:
            r = requests.get(url, headers=headers, timeout=10)
            feed = feedparser.parse(r.content)
            found = False
            for entry in feed.entries[:50]:  # scan latest 50
                if keyword.lower() in entry.title.lower() or keyword.lower() in entry.summary.lower():
                    print(f"[{name}] FOUND: {entry.title}")
                    print(f"Link: {entry.link}\n")
                    found = True
            if not found:
                print(f"[{name}] No news found for {keyword} in the latest entries.")
        except Exception as e:
            print(f"[{name}] Error fetching feed: {e}")

def test_yahoo_finance(ticker="AGP.KA"):
    print(f"\n--- Testing Yahoo Finance for {ticker} ---")
    try:
        agp = yf.Ticker(ticker)
        news = agp.news
        if news:
            for item in news[:5]:
                print(f"[Yahoo] {item.get('title')}")
                print(f"Link: {item.get('link')}\n")
        else:
            print(f"No news returned from Yahoo Finance for {ticker}.")
    except Exception as e:
        print(f"Error fetching Yahoo Finance news: {e}")

def test_psx_portal_scraping():
    print(f"\n--- Testing PSX Portal Announcements Scraping ---")
    url = "https://dps.psx.com.pk/announcements/companies"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # The PSX announcements table usually has the class 'tbl' or 'table'
        table = soup.find('table')
        if table:
            rows = table.find_all('tr')[1:10]  # Skip header, get top 
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 4:
                    date = cols[0].text.strip()
                    company = cols[1].text.strip()
                    subject = cols[2].text.strip()
                    print(f"[PSX Portal] {date} | {company} | {subject}")
        else:
            print("Could not find the announcements table on PSX portal.")
    except Exception as e:
        print(f"Error scraping PSX Portal: {e}")

def find_announcements_in_html():
    url = "https://dps.psx.com.pk/company/AGP"
    print(f"\n--- Testing company specific PSX Portal Scraping ({url}) ---")
    try:
        r = requests.get(url, timeout=10)
        html = r.text
        # Look for API endpoints in the JS or HTML
        endpoints = re.findall(r'https://dps\.psx\.com\.pk/?[a-zA-Z0-9/_-]*', html)
        api_endpoints = re.findall(r'/api/[a-zA-Z0-9/_-]+', html)
        print("API endpoints found:", set(api_endpoints))
    except Exception as e:
        print(e)


if __name__ == "__main__":
    test_rss_feeds("AGP")
    test_yahoo_finance("AGP.KA")
    test_psx_portal_scraping()
    find_announcements_in_html()
