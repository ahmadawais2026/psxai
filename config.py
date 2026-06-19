import os
from dotenv import load_dotenv

load_dotenv()

# ── Gemini Configuration ──────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.1-pro"
GEMINI_TEMPERATURE = 0.5          # Balanced: detailed, flowing analysis while staying factual
GEMINI_MAX_OUTPUT_TOKENS = 100000  # Generous ceiling so deep reasoning + granular output is never truncated

# ── DeepSeek Configuration ────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-021611cb3eda434c97c3be1b9954127c")


# ── Firebase Configuration ────────────────────────────────────
import firebase_admin
from firebase_admin import credentials, firestore

# Clean up FIREBASE_CONFIG env var on Windows if it has bad escaping
firebase_config = os.environ.get("FIREBASE_CONFIG")
if firebase_config:
    cleaned = firebase_config.replace('\\"', '"')
    if cleaned.startswith('"') and cleaned.endswith('"') and cleaned.count('{') > 0:
        cleaned = cleaned[1:-1]
    os.environ["FIREBASE_CONFIG"] = cleaned

# Initialize Firebase App
firebase_db = None
try:
    if not firebase_admin._apps:
        cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app()
    firebase_db = firestore.client()
except Exception as e:
    print(f"[-] Failed to initialize Firebase Admin SDK: {e}")

# ── Flask Configuration ───────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = int(os.getenv("PORT", 5000))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# ── Cache TTLs (seconds) ─────────────────────────────────────
CACHE_TTL_QUOTE = 10              # 10 seconds (near real-time)
CACHE_TTL_HISTORY = 60 * 15       # 15 minutes
CACHE_TTL_FUNDAMENTALS = 60 * 60 * 24  # 24 hours
CACHE_TTL_NEWS = 60 * 60          # 1 hour

# ── PSX Configuration ────────────────────────────────────────
PSX_SUFFIX = ".KA"                # Yahoo Finance suffix for Karachi Stock Exchange
DEFAULT_CURRENCY = "PKR"
PSX_PORTAL_BASE = "https://dps.psx.com.pk"
CACHE_TTL_MARKET_WATCH = 15       # 15 seconds Cache TTL for Market Watch HTML page


# ── Analysis Configuration ────────────────────────────────────
HISTORY_PERIOD_DAILY = "1y"       # 1 year of daily data for technical analysis
HISTORY_PERIOD_HOURLY = "1mo"     # 1 month of hourly data for short-term
DEBATE_ROUNDS = 2                 # Bull vs Bear debate rounds

# ── Database ──────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "advisor_cache.db")
PORTFOLIO_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "portfolio.db")
