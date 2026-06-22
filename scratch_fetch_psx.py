import requests
import re
import json

def find_api_in_html():
    url = "https://dps.psx.com.pk/company/AGP"
    try:
        r = requests.get(url, timeout=10)
        html = r.text
        # Look for API endpoints in the JS or HTML
        endpoints = re.findall(r'https://dps\.psx\.com\.pk/?[a-zA-Z0-9/_-]*', html)
        print("Found possible URLs:")
        for ep in set(endpoints):
            print(ep)
            
        print("Checking for /api/")
        api_endpoints = re.findall(r'/api/[a-zA-Z0-9/_-]+', html)
        for ep in set(api_endpoints):
            print(ep)
    except Exception as e:
        print(e)

if __name__ == "__main__":
    find_api_in_html()
