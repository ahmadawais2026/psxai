import requests

urls = [
    "https://scstrade.com",
    "https://www.scstrade.com/market/MS_FIPIAndLIPI.aspx",
    "https://mufap.com.pk",
    "https://mufap.com.pk/nav-report.php",
    "https://www.nccpl.com.pk"
]

for url in urls:
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, timeout=10)
        print(url, r.status_code)
    except Exception as e:
        print(url, e)
