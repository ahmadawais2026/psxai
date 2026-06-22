import logging
logging.basicConfig(level=logging.DEBUG)
from data.institutional_flows import get_fipi_lipi_scstrade, get_mufap_aum_snapshot
from data.retail_sentiment import get_google_trends_signal, get_reddit_sentiment

print("FIPI/LIPI:")
print(get_fipi_lipi_scstrade())

print("\nMUFAP AUM:")
print(get_mufap_aum_snapshot())

print("\nGoogle Trends OGDC:")
print(get_google_trends_signal(["OGDC"]))

print("\nReddit OGDC:")
print(get_reddit_sentiment("OGDC"))
